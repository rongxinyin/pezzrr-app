"""Pull smart-home panel circuit time-series for a given date and plot/summarize.

Usage:
    venv/bin/python3 tests/plot_panel_circuits.py [--date YYYY-MM-DD] [--homes 3110A,3110D,900H]

Outputs (written next to this script):
    panel_circuits_<date>.png        — one subplot per home, all 12 circuits overlaid
    panel_circuits_<date>_stats.csv  — per-home per-circuit power characteristics
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

LABEL_CSV = Path(__file__).resolve().parents[1] / "config" / "ecoflow_panel_circult_summary.csv"

QUERY = """
SELECT  h.home_name,
        pc.channel_num,
        COALESCE(pc.circuit_name, 'Circuit ' || pc.channel_num) AS circuit_name,
        pcr.ts,
        pcr.power_w
FROM    panel_circuit_readings pcr
JOIN    homes          h  ON pcr.home_id    = h.home_id
JOIN    panel_circuits pc ON pcr.circuit_id = pc.circuit_id
WHERE   h.home_name = ANY(%(homes)s)
  AND   pcr.ts >= %(start)s
  AND   pcr.ts <  %(end)s
ORDER BY h.home_name, pc.channel_num, pcr.ts;
"""


def load_circuit_labels() -> dict[tuple[str, int], str]:
    """Return {(home_name, channel_num) -> human label} from the config CSV."""
    if not LABEL_CSV.exists():
        return {}
    labels = pd.read_csv(LABEL_CSV)
    labels["channel_num"] = pd.to_numeric(labels["circuit_id"], errors="coerce")
    labels = labels.dropna(subset=["channel_num"])
    labels["channel_num"] = labels["channel_num"].astype(int)
    labels["circuit_label"] = labels["circuit_label"].fillna("").astype(str).str.strip()
    return {
        (row.home_name, row.channel_num): row.circuit_label or f"Circuit {row.channel_num}"
        for row in labels.itertuples(index=False)
    }


def fetch(homes: list[str], date: str) -> pd.DataFrame:
    start = pd.Timestamp(date)
    end = start + pd.Timedelta(days=1)
    with psycopg2.connect(**DB) as conn:
        df = pd.read_sql(
            QUERY, conn,
            params={"homes": homes, "start": start, "end": end},
        )
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/Los_Angeles")
        df["power_w"] = pd.to_numeric(df["power_w"], errors="coerce")
        # Override circuit_name with the human-readable label from the config CSV.
        labels = load_circuit_labels()
        if labels:
            df["circuit_name"] = [
                labels.get((h, int(c)), name)
                for h, c, name in zip(df["home_name"], df["channel_num"], df["circuit_name"])
            ]
    return df


def plot(df: pd.DataFrame, homes: list[str], date: str, out_path: Path) -> None:
    n = len(homes)
    fig, axes = plt.subplots(n, 1, figsize=(14, 4 * n), sharex=True)
    if n == 1:
        axes = [axes]

    cmap = plt.get_cmap("tab20")

    for ax, home in zip(axes, homes):
        sub = df[df["home_name"] == home]
        if sub.empty:
            ax.text(0.5, 0.5, f"No data for {home} on {date}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=12, color="crimson")
            ax.set_title(f"Home {home}  —  no readings")
            continue

        # Stable channel ordering (1..12) and consistent colors
        for ch, g in sub.groupby("channel_num"):
            label = f"ch{ch} — {g['circuit_name'].iloc[0]}"
            ax.plot(g["ts"], g["power_w"],
                    label=label, linewidth=0.9,
                    color=cmap((int(ch) - 1) % 20))

        ax.set_title(f"Home {home}  —  panel circuit power on {date}")
        ax.set_ylabel("Power (W)")
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.legend(loc="upper right", ncol=2, fontsize=7, framealpha=0.85)

    axes[-1].set_xlabel("Time (America/Los_Angeles)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby(["home_name", "channel_num", "circuit_name"])["power_w"]
    stats = g.agg(
        n_samples="count",
        mean_w="mean",
        median_w="median",
        std_w="std",
        min_w="min",
        max_w="max",
        p95_w=lambda s: s.quantile(0.95),
    ).reset_index()

    # Energy via trapezoidal integration (W * hours -> Wh)
    energy_rows = []
    for (home, ch, name), sub in df.groupby(["home_name", "channel_num", "circuit_name"]):
        sub = sub.dropna(subset=["power_w"]).sort_values("ts")
        if len(sub) < 2:
            energy_wh = 0.0
        else:
            hours = (sub["ts"].diff().dt.total_seconds() / 3600.0).fillna(0)
            energy_wh = float((sub["power_w"] * hours).sum())
        energy_rows.append({
            "home_name": home, "channel_num": ch, "circuit_name": name,
            "energy_kwh": energy_wh / 1000.0,
        })
    energy = pd.DataFrame(energy_rows)

    out = stats.merge(energy, on=["home_name", "channel_num", "circuit_name"])
    num_cols = ["mean_w", "median_w", "std_w", "min_w", "max_w", "p95_w", "energy_kwh"]
    out[num_cols] = out[num_cols].round(2)
    return out.sort_values(["home_name", "channel_num"]).reset_index(drop=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="2026-05-19", help="YYYY-MM-DD")
    p.add_argument("--homes", default="3110A,3110D,900H",
                   help="Comma-separated home_name values")
    args = p.parse_args()

    homes = [h.strip() for h in args.homes.split(",") if h.strip()]
    out_dir = Path(__file__).resolve().parent
    png = out_dir / f"panel_circuits_{args.date}.png"
    csv = out_dir / f"panel_circuits_{args.date}_stats.csv"

    print(f"Fetching circuit data for {homes} on {args.date} ...")
    df = fetch(homes, args.date)
    print(f"  rows: {len(df):,}")

    found = sorted(df["home_name"].unique().tolist()) if not df.empty else []
    missing = [h for h in homes if h not in found]
    if missing:
        print(f"  WARNING: no readings on {args.date} for: {missing}")

    plot(df, homes, args.date, png)
    print(f"  wrote {png}")

    stats = summarize(df)
    if stats.empty:
        print("  no stats produced (empty data)")
    else:
        stats.to_csv(csv, index=False)
        print(f"  wrote {csv}")
        print("\nPer-circuit power characteristics:\n")
        with pd.option_context("display.max_rows", None,
                               "display.max_columns", None,
                               "display.width", 200):
            print(stats.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
