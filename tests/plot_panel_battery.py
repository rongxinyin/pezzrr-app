"""Plot smart-home panel power (grid / solar / home load) and battery power
(AC in / AC out / SoC) per home on a given date.

Usage:
    venv/bin/python3 tests/plot_panel_battery.py [--date YYYY-MM-DD] [--homes 3110A,3110D,900H]

Output:
    tests/panel_battery_<date>.png
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

PANEL_Q = """
SELECT h.home_name, spr.ts,
       spr.grid_power_w, spr.solar_power_w, spr.home_load_w
FROM   smart_panel_readings spr
JOIN   homes h ON spr.home_id = h.home_id
WHERE  h.home_name = ANY(%(homes)s)
  AND  spr.ts >= %(start)s AND spr.ts < %(end)s
ORDER BY h.home_name, spr.ts;
"""

BATTERY_Q = """
SELECT h.home_name, br.ts,
       br.ac_in_power_w, br.ac_out_power_w, br.soc_pct
FROM   battery_readings br
JOIN   homes h ON br.home_id = h.home_id
WHERE  h.home_name = ANY(%(homes)s)
  AND  br.ts >= %(start)s AND br.ts < %(end)s
ORDER BY h.home_name, br.ts;
"""


def fetch(homes: list[str], date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = pd.Timestamp(date)
    end = start + pd.Timedelta(days=1)
    params = {"homes": homes, "start": start, "end": end}
    with psycopg2.connect(**DB) as conn:
        panel = pd.read_sql(PANEL_Q, conn, params=params)
        battery = pd.read_sql(BATTERY_Q, conn, params=params)
    for df in (panel, battery):
        if not df.empty:
            df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/Los_Angeles")
            for c in df.columns:
                if c not in ("home_name", "ts"):
                    df[c] = pd.to_numeric(df[c], errors="coerce")
    # Derive net battery power: + = charging from AC, - = discharging to home
    if not battery.empty:
        battery["net_power_w"] = battery["ac_in_power_w"].fillna(0) - battery["ac_out_power_w"].fillna(0)
    return panel, battery


def plot(panel: pd.DataFrame, battery: pd.DataFrame,
         homes: list[str], date: str, out_path: Path) -> None:
    n = len(homes)
    fig, axes = plt.subplots(n, 2, figsize=(16, 3.6 * n), sharex="col")
    if n == 1:
        axes = axes.reshape(1, 2)

    for i, home in enumerate(homes):
        ax_p, ax_b = axes[i, 0], axes[i, 1]

        # --- Smart panel power ---
        sub = panel[panel["home_name"] == home]
        if sub.empty:
            ax_p.text(0.5, 0.5, f"No panel data for {home}",
                      ha="center", va="center", transform=ax_p.transAxes,
                      fontsize=11, color="crimson")
        else:
            ax_p.plot(sub["ts"], sub["home_load_w"],  label="Home load", color="#1f77b4", lw=1.0)
            ax_p.plot(sub["ts"], sub["grid_power_w"], label="Grid (+imp/-exp)",
                      color="#d62728", lw=1.0)
            ax_p.plot(sub["ts"], sub["solar_power_w"], label="Solar", color="#ff7f0e", lw=1.0)
            ax_p.axhline(0, color="grey", lw=0.5, alpha=0.6)
            ax_p.legend(loc="upper right", fontsize=8, ncol=3, framealpha=0.85)
        ax_p.set_title(f"{home} — Smart Panel power")
        ax_p.set_ylabel("Power (W)")
        ax_p.grid(True, alpha=0.3)
        ax_p.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

        # --- Battery power + SoC ---
        sub = battery[battery["home_name"] == home]
        if sub.empty:
            ax_b.text(0.5, 0.5, f"No battery data for {home}",
                      ha="center", va="center", transform=ax_b.transAxes,
                      fontsize=11, color="crimson")
        else:
            ax_b.plot(sub["ts"], sub["ac_in_power_w"],
                      label="AC in (charge)", color="#2ca02c", lw=1.0)
            ax_b.plot(sub["ts"], sub["ac_out_power_w"],
                      label="AC out (discharge)", color="#9467bd", lw=1.0)
            ax_b.plot(sub["ts"], sub["net_power_w"],
                      label="Net (+chg/-dis)", color="#17becf", lw=1.2, linestyle="--")
            ax_b.axhline(0, color="grey", lw=0.5, alpha=0.6)
            ax_b.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.85)

            ax_soc = ax_b.twinx()
            ax_soc.plot(sub["ts"], sub["soc_pct"], color="black", lw=1.0, alpha=0.6)
            ax_soc.set_ylabel("SoC (%)", color="black")
            ax_soc.set_ylim(0, 100)
            ax_soc.tick_params(axis="y", labelsize=8)
        ax_b.set_title(f"{home} — Battery storage power")
        ax_b.set_ylabel("Power (W)")
        ax_b.grid(True, alpha=0.3)
        ax_b.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    axes[-1, 0].set_xlabel("Time (America/Los_Angeles)")
    axes[-1, 1].set_xlabel("Time (America/Los_Angeles)")
    fig.suptitle(f"Smart panel & battery storage power — {date}", y=1.005, fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def summarize(panel: pd.DataFrame, battery: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for home, sub in panel.groupby("home_name"):
        sub = sub.sort_values("ts")
        hours = (sub["ts"].diff().dt.total_seconds() / 3600.0).fillna(0)
        rows.append({
            "home_name": home,
            "panel_n": len(sub),
            "home_load_mean_w": round(sub["home_load_w"].mean(), 2),
            "home_load_max_w":  round(sub["home_load_w"].max(),  2),
            "home_energy_kwh":  round((sub["home_load_w"].fillna(0) * hours).sum() / 1000, 3),
            "solar_energy_kwh": round((sub["solar_power_w"].fillna(0) * hours).sum() / 1000, 3),
            "grid_import_kwh":  round((sub["grid_power_w"].clip(lower=0).fillna(0) * hours).sum() / 1000, 3),
            "grid_export_kwh":  round((-sub["grid_power_w"].clip(upper=0).fillna(0) * hours).sum() / 1000, 3),
        })
    panel_stats = pd.DataFrame(rows).set_index("home_name")

    rows = []
    for home, sub in battery.groupby("home_name"):
        sub = sub.sort_values("ts")
        hours = (sub["ts"].diff().dt.total_seconds() / 3600.0).fillna(0)
        rows.append({
            "home_name": home,
            "batt_n": len(sub),
            "soc_start_pct": round(sub["soc_pct"].iloc[0], 1) if len(sub) else None,
            "soc_end_pct":   round(sub["soc_pct"].iloc[-1], 1) if len(sub) else None,
            "ac_charge_kwh":    round((sub["ac_in_power_w"].fillna(0)  * hours).sum() / 1000, 3),
            "ac_discharge_kwh": round((sub["ac_out_power_w"].fillna(0) * hours).sum() / 1000, 3),
        })
    batt_stats = pd.DataFrame(rows).set_index("home_name")

    return panel_stats.join(batt_stats, how="outer").reset_index()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-05-10")
    p.add_argument("--homes", default="3110A,3110D,900H")
    args = p.parse_args()

    homes = [h.strip() for h in args.homes.split(",") if h.strip()]
    out_dir = Path(__file__).resolve().parent
    png = out_dir / f"panel_battery_{args.date}.png"
    csv = out_dir / f"panel_battery_{args.date}_stats.csv"

    print(f"Fetching panel + battery data for {homes} on {args.date} ...")
    panel, battery = fetch(homes, args.date)
    print(f"  panel rows: {len(panel):,}   battery rows: {len(battery):,}")

    plot(panel, battery, homes, args.date, png)
    print(f"  wrote {png}")

    stats = summarize(panel, battery)
    if not stats.empty:
        stats.to_csv(csv, index=False)
        print(f"  wrote {csv}\n")
        with pd.option_context("display.width", 200, "display.max_columns", None):
            print(stats.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
