"""Presentation-style plot: battery SoC + AC-in/AC-out for the Example Unit,
with a planned-discharge SoC overlay showing what the battery WOULD do if it
backed up the whole-home load between 16:00–21:00 (Pacific).

Usage:
    venv/bin/python3 tests/plot_example_unit_battery.py [--date YYYY-MM-DD]

Output:
    tests/example_unit_battery_<date>.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import psycopg2

DB = dict(host="localhost", port=5432, dbname="pezerr_db",
          user="pezerr", password="840810")
HOME = "3110D"
DISPLAY_NAME = "Example Unit"

BACKUP_START_HOUR = 16   # 4 PM Pacific
BACKUP_END_HOUR = 21     # 9 PM Pacific

BATTERY_Q = """
SELECT br.ts, br.soc_pct, br.ac_in_power_w, br.ac_out_power_w, br.capacity_wh
FROM   battery_readings br
JOIN   homes h ON br.home_id = h.home_id
WHERE  h.home_name = %(home)s
  AND  br.ts >= %(start)s AND br.ts < %(end)s
ORDER BY br.ts;
"""

PANEL_Q = """
SELECT spr.ts, spr.home_load_w, spr.solar_power_w
FROM   smart_panel_readings spr
JOIN   homes h ON spr.home_id = h.home_id
WHERE  h.home_name = %(home)s
  AND  spr.ts >= %(start)s AND spr.ts < %(end)s
ORDER BY spr.ts;
"""


def fetch(date: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(date, tz="America/Los_Angeles")
    end = start + pd.Timedelta(days=1)
    params = {"home": HOME, "start": start, "end": end}
    with psycopg2.connect(**DB) as conn:
        batt = pd.read_sql(BATTERY_Q, conn, params=params)
        panel = pd.read_sql(PANEL_Q, conn, params=params)
    for df in (batt, panel):
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/Los_Angeles")
    for c in ("soc_pct", "ac_in_power_w", "ac_out_power_w", "capacity_wh"):
        batt[c] = pd.to_numeric(batt[c], errors="coerce")
    panel["home_load_w"] = pd.to_numeric(panel["home_load_w"], errors="coerce")
    # On this deployment the column names in battery_readings are swapped:
    # ac_in_power_w is a constant 1,200 W placeholder, while ac_out_power_w
    # carries the real AC-in charging signal (verified: ac_out spikes precede
    # SoC rises). Use ac_out_power_w as the displayed "AC in charge power".
    batt["ac_in_real_w"] = batt["ac_out_power_w"].fillna(0)
    return batt, panel, start, end


def planned_soc(batt: pd.DataFrame, panel: pd.DataFrame,
                start: pd.Timestamp) -> pd.DataFrame:
    """Project SoC assuming the battery covers the entire home_load between
    BACKUP_START_HOUR and BACKUP_END_HOUR. Outside that window, planned SoC
    tracks actual SoC.
    """
    backup_start = start + pd.Timedelta(hours=BACKUP_START_HOUR)
    backup_end = start + pd.Timedelta(hours=BACKUP_END_HOUR)

    capacity_wh = float(batt["capacity_wh"].mean()) if batt["capacity_wh"].notna().any() else 6144.0

    # Anchor planned SoC at the actual SoC immediately before 16:00.
    anchor = batt[batt["ts"] <= backup_start]
    soc_anchor = float(anchor["soc_pct"].iloc[-1]) if not anchor.empty else float(batt["soc_pct"].iloc[0])

    # Integrate home load during the backup window (Wh consumed up to each ts).
    sub = panel[(panel["ts"] >= backup_start) & (panel["ts"] <= backup_end)].sort_values("ts").copy()
    sub["dt_h"] = sub["ts"].diff().dt.total_seconds().fillna(0) / 3600.0
    sub["wh_used"] = (sub["home_load_w"].fillna(0) * sub["dt_h"]).cumsum()
    sub["planned_soc"] = soc_anchor - 100.0 * sub["wh_used"] / capacity_wh

    # Build a full-day series:
    rows = []
    # Pre-backup: hold at anchor
    pre = batt[batt["ts"] < backup_start][["ts"]].copy()
    pre["planned_soc"] = soc_anchor
    rows.append(pre)
    # Backup window: integrated drain
    rows.append(sub[["ts", "planned_soc"]])
    # Post-backup: hold at last planned value
    final_soc = float(sub["planned_soc"].iloc[-1]) if not sub.empty else soc_anchor
    post = batt[batt["ts"] > backup_end][["ts"]].copy()
    post["planned_soc"] = final_soc
    rows.append(post)

    out = pd.concat(rows, ignore_index=True).sort_values("ts").reset_index(drop=True)
    return out, capacity_wh, soc_anchor, final_soc


def plot(batt: pd.DataFrame, panel: pd.DataFrame, planned: pd.DataFrame,
         capacity_wh: float, soc_anchor: float, final_soc: float,
         date: str, xlim: tuple[pd.Timestamp, pd.Timestamp], out_path: Path) -> None:

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.titlesize": 17,
        "axes.titleweight": "bold",
        "axes.labelsize": 13,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })

    fig, (ax_p, ax_s) = plt.subplots(
        2, 1, figsize=(15, 8.5), sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.0], "hspace": 0.18},
    )

    start = xlim[0]
    backup_start = start + pd.Timedelta(hours=BACKUP_START_HOUR)
    backup_end = start + pd.Timedelta(hours=BACKUP_END_HOUR)

    # Shade the planned backup window in both subplots.
    for ax in (ax_p, ax_s):
        ax.axvspan(backup_start, backup_end, color="#fff4cc", alpha=0.8, zorder=0,
                   label="_nolegend_")

    # --- Top: power flows ---
    # Highlight the brief charge event with a filled area so the spike reads
    # at a glance, plus a line with markers so individual samples are visible.
    ax_p.fill_between(batt["ts"], 0, batt["ac_in_real_w"],
                      color="#2ca02c", alpha=0.30, zorder=3,
                      label="_nolegend_")
    ax_p.plot(batt["ts"], batt["ac_in_real_w"],
              color="#2ca02c", lw=2.4, marker="o", markersize=3, zorder=4,
              label="AC in — charging power")
    ax_p.plot(panel["ts"], panel["home_load_w"],
              color="black", lw=1.3, alpha=0.55, zorder=2,
              label="Whole-home load (would-be backup target)")
    ax_p.axhline(0, color="grey", lw=0.5, alpha=0.6)
    ax_p.set_ylabel("Power (W)")
    ax_p.set_title(f"{DISPLAY_NAME} — Battery Power Flows on {date}", pad=10)
    ax_p.grid(True, axis="y", alpha=0.3)
    ax_p.legend(loc="upper left", ncol=2, frameon=False)
    ax_p.text(
        backup_start + (backup_end - backup_start) / 2, ax_p.get_ylim()[1] * 0.95,
        "Planned backup window  16:00 – 21:00",
        ha="center", va="top", fontsize=10, color="#8a6d1a",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#fff4cc",
                  edgecolor="#d9b656", lw=0.8),
    )

    # --- Bottom: SoC actual vs planned ---
    ax_s.plot(batt["ts"], batt["soc_pct"],
              color="#1f77b4", lw=2.2, label="Actual SoC")
    ax_s.plot(planned["ts"], planned["planned_soc"],
              color="#d62728", lw=2.2, ls="--",
              label=f"Planned SoC (full backup 16:00–21:00)")
    ax_s.set_ylabel("State of Charge (%)")
    ax_s.set_ylim(0, 100)
    ax_s.set_title("State of Charge — actual vs planned dispatch", pad=8)
    ax_s.grid(True, axis="y", alpha=0.3)
    ax_s.legend(loc="lower left", frameon=False)

    # Annotate planned drop
    delta_soc = soc_anchor - final_soc
    ax_s.annotate(
        f"Planned drop: {delta_soc:.0f}%   ({soc_anchor:.0f}% → {final_soc:.0f}%)",
        xy=(backup_end, final_soc),
        xytext=(backup_end + pd.Timedelta(hours=0.5), max(final_soc + 12, 25)),
        fontsize=11, color="#a13030",
        arrowprops=dict(arrowstyle="->", color="#a13030", lw=1.2),
    )

    # x-axis formatting (24h Pacific)
    la_tz = xlim[0].tz
    for ax in (ax_p, ax_s):
        ax.set_xlim(xlim[0], xlim[1])
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=la_tz))
        ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1, tz=la_tz))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=la_tz))
        ax.margins(x=0)
    ax_s.set_xlabel("Time of day (America/Los_Angeles)")

    # Footer
    panel_sub = panel[(panel["ts"] >= backup_start) & (panel["ts"] <= backup_end)].sort_values("ts")
    if not panel_sub.empty:
        dt_h = (panel_sub["ts"].diff().dt.total_seconds() / 3600.0).fillna(0)
        backup_kwh = float((panel_sub["home_load_w"].fillna(0) * dt_h).sum()) / 1000.0
    else:
        backup_kwh = 0.0
    fig.text(
        0.01, 0.005,
        f"Battery capacity (mean): {capacity_wh:,.0f} Wh   •   "
        f"Backup-window home energy: {backup_kwh:.2f} kWh   •   "
        f"Required SoC: {100 * backup_kwh * 1000 / capacity_wh:.0f}% of pack",
        fontsize=10, color="#555555",
    )

    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-05-10")
    args = p.parse_args()

    out_path = Path(__file__).resolve().parent / f"example_unit_battery_{args.date}.png"
    print(f"Fetching {DISPLAY_NAME} (db: {HOME}) battery + panel for {args.date} (Pacific) ...")
    batt, panel, start, end = fetch(args.date)
    if batt.empty:
        print("  no battery data — aborting")
        return 1
    print(f"  battery rows: {len(batt):,}   panel rows: {len(panel):,}")

    planned, cap_wh, soc_a, soc_f = planned_soc(batt, panel, start)
    print(f"  capacity (mean): {cap_wh:,.0f} Wh")
    print(f"  planned SoC: {soc_a:.1f}% → {soc_f:.1f}%  (drop {soc_a - soc_f:.1f}%)")

    plot(batt, panel, planned, cap_wh, soc_a, soc_f,
         args.date, (start, end), out_path)
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
