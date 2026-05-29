"""Presentation-style standalone plot: home 3110D panel circuit power.

Single figure, large fonts, clean palette, legend with real circuit labels
sourced from config/ecoflow_panel_circult_summary.csv. Flat-zero and
(Empty) circuits are dropped to keep the legend slide-ready.

Usage:
    venv/bin/python3 tests/plot_3110d_circuits.py [--date YYYY-MM-DD]

Output:
    tests/3110d_circuits_<date>.png
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
DISPLAY_NAME = "Example Unit"   # Anonymized label shown on the plot
LABEL_CSV = Path(__file__).resolve().parents[1] / "config" / "ecoflow_panel_circult_summary.csv"

QUERY = """
SELECT pc.channel_num, pcr.ts, pcr.power_w
FROM   panel_circuit_readings pcr
JOIN   homes          h  ON pcr.home_id    = h.home_id
JOIN   panel_circuits pc ON pcr.circuit_id = pc.circuit_id
WHERE  h.home_name = %(home)s
  AND  pcr.ts >= %(start)s AND pcr.ts < %(end)s
ORDER BY pc.channel_num, pcr.ts;
"""

HOME_LOAD_QUERY = """
SELECT spr.ts, spr.home_load_w
FROM   smart_panel_readings spr
JOIN   homes h ON spr.home_id = h.home_id
WHERE  h.home_name = %(home)s
  AND  spr.ts >= %(start)s AND spr.ts < %(end)s
ORDER BY spr.ts;
"""


def load_labels() -> dict[int, str]:
    df = pd.read_csv(LABEL_CSV)
    df = df[df["home_name"] == HOME].copy()
    df["channel_num"] = pd.to_numeric(df["circuit_id"], errors="coerce").astype("Int64")
    df["circuit_label"] = df["circuit_label"].fillna("").astype(str).str.strip()
    return {int(r.channel_num): (r.circuit_label or f"Circuit {int(r.channel_num)}")
            for r in df.itertuples(index=False) if pd.notna(r.channel_num)}


def fetch(date: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    # Define the 24-hour window in Pacific time, send tz-aware bounds.
    start = pd.Timestamp(date, tz="America/Los_Angeles")
    end = start + pd.Timedelta(days=1)
    params = {"home": HOME, "start": start, "end": end}
    with psycopg2.connect(**DB) as conn:
        df = pd.read_sql(QUERY, conn, params=params)
        home_df = pd.read_sql(HOME_LOAD_QUERY, conn, params=params)
    for f in (df, home_df):
        f["ts"] = pd.to_datetime(f["ts"], utc=True).dt.tz_convert("America/Los_Angeles")
    df["power_w"] = pd.to_numeric(df["power_w"], errors="coerce")
    home_df["home_load_w"] = pd.to_numeric(home_df["home_load_w"], errors="coerce")
    return df, home_df, start, end


def plot(df: pd.DataFrame, home_df: pd.DataFrame, labels: dict[int, str], date: str,
         xlim: tuple[pd.Timestamp, pd.Timestamp], out_path: Path) -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.titlesize": 18,
        "axes.titleweight": "bold",
        "axes.labelsize": 14,
        "xtick.labelsize": 12,
        "ytick.labelsize": 12,
        "legend.fontsize": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })

    # Keep only channels with meaningful activity and not labeled "(Empty)".
    active_channels = []
    for ch, g in df.groupby("channel_num"):
        label = labels.get(int(ch), f"Circuit {ch}")
        if "(Empty)" in label:
            continue
        if g["power_w"].max() < 5.0:    # < 5 W peak is effectively idle
            continue
        active_channels.append(int(ch))
    active_channels.sort()

    fig, ax = plt.subplots(figsize=(15, 7.5))
    cmap = plt.get_cmap("tab10")

    # Sort series by daily energy (largest first) so the legend leads with
    # the most important loads — better story for an audience.
    series = []
    for ch in active_channels:
        g = df[df["channel_num"] == ch].sort_values("ts")
        hours = (g["ts"].diff().dt.total_seconds() / 3600.0).fillna(0)
        energy_kwh = float((g["power_w"].fillna(0) * hours).sum()) / 1000.0
        series.append((energy_kwh, ch, g))
    series.sort(key=lambda x: -x[0])

    for i, (energy_kwh, ch, g) in enumerate(series):
        label = f"Ch{ch}  {labels.get(ch, f'Circuit {ch}')}  ({energy_kwh:.2f} kWh)"
        ax.plot(g["ts"], g["power_w"], lw=1.6, color=cmap(i % 10), label=label)

    # Whole-home load overlay (from smart_panel_readings).
    home_energy_kwh = 0.0
    home_peak_w = 0.0
    if not home_df.empty:
        h = home_df.sort_values("ts")
        hours = (h["ts"].diff().dt.total_seconds() / 3600.0).fillna(0)
        home_energy_kwh = float((h["home_load_w"].fillna(0) * hours).sum()) / 1000.0
        home_peak_w = float(h["home_load_w"].max())
        ax.plot(h["ts"], h["home_load_w"],
                color="black", lw=2.4, alpha=0.85, zorder=10,
                label=f"Whole-home load  ({home_energy_kwh:.2f} kWh)")

    ax.set_title(f"{DISPLAY_NAME} — Smart Panel Circuit Power on {date}", pad=14)
    ax.set_ylabel("Circuit power (W)")
    ax.set_xlabel("Time of day (America/Los_Angeles)")
    ax.grid(True, axis="y", alpha=0.3)
    # Lock x-axis to a full 24h Pacific-time window: 00:00 → 24:00.
    ax.set_xlim(xlim[0], xlim[1])
    la_tz = xlim[0].tz
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=la_tz))
    ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1, tz=la_tz))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=la_tz))
    ax.margins(x=0)

    leg = ax.legend(
        loc="center left", bbox_to_anchor=(1.01, 0.5),
        frameon=False, title="Circuits (ranked by daily kWh)",
        title_fontsize=12,
    )
    leg._legend_box.align = "left"

    # Subtle footer with totals
    total_kwh = sum(e for e, _, _ in series)
    peak_w = df["power_w"].max()
    footer = (
        f"Active circuits: {len(series)}   •   "
        f"Circuit energy: {total_kwh:.1f} kWh   •   "
        f"Peak circuit power: {peak_w:,.0f} W"
    )
    if not home_df.empty:
        footer += (
            f"   •   Whole-home energy: {home_energy_kwh:.1f} kWh"
            f"   •   Peak home load: {home_peak_w:,.0f} W"
        )
    fig.text(0.01, 0.005, footer, fontsize=10, color="#555555")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-05-19")
    args = p.parse_args()

    out_path = Path(__file__).resolve().parent / f"example_unit_circuits_{args.date}.png"
    print(f"Fetching {DISPLAY_NAME} (db: {HOME}) circuit + home-load data for {args.date} (Pacific time) ...")
    df, home_df, start, end = fetch(args.date)
    if df.empty:
        print("  no circuit data — aborting")
        return 1
    print(f"  window: {start}  →  {end}")
    print(f"  circuit rows: {len(df):,}   home-load rows: {len(home_df):,}")
    labels = load_labels()
    plot(df, home_df, labels, args.date, (start, end), out_path)
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
