"""
Per-home energy reports (docs/DASHBOARD_DESIGN.md §13.7) — analyst/admin only.

Daily and monthly reports reuse the analytics dataset (analytics.energy_dataset)
and render to PDF (WeasyPrint) or CSV (pandas). /reports/export serves a CSV of
the daily series over an arbitrary range.
"""

from __future__ import annotations

import calendar
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..auth import User, require
from ..models import EnergyAnalytics
from .analytics import energy_dataset, home_timezone

router = APIRouter(prefix="/api/v1", tags=["reports"])

# Reports aggregate across homes, so gate at fleet_analyst (operator is below).
_analyst = require("fleet_analyst")


def _days_frame(data: EnergyAnalytics) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "date": d.date.isoformat(),
            "home_load_kwh": d.home_load_kwh,
            "solar_gen_kwh": d.solar_gen_kwh,
            "grid_import_kwh": d.grid_import_kwh,
            "grid_export_kwh": d.grid_export_kwh,
            "peak_demand_kw": d.peak_demand_kw,
            "self_consumption_pct": d.self_consumption_pct,
            "estimated_cost_usd": d.estimated_cost_usd,
        }
        for d in data.days
    ])


def _csv_response(data: EnergyAnalytics, filename: str) -> Response:
    buf = io.StringIO()
    _days_frame(data).to_csv(buf, index=False)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


def _num(v, suffix=""):
    return "—" if v is None else f"{v:,.2f}{suffix}"


def _report_html(data: EnergyAnalytics, title: str, period: str) -> str:
    t = data.totals
    rows = "".join(
        f"<tr><td>{d.date.isoformat()}</td>"
        f"<td class='n'>{_num(d.home_load_kwh)}</td>"
        f"<td class='n'>{_num(d.solar_gen_kwh)}</td>"
        f"<td class='n'>{_num(d.grid_import_kwh)}</td>"
        f"<td class='n'>{_num(d.peak_demand_kw)}</td>"
        f"<td class='n'>{_num(d.self_consumption_pct, '%')}</td>"
        f"<td class='n'>{'—' if d.estimated_cost_usd is None else f'${d.estimated_cost_usd:,.2f}'}</td></tr>"
        for d in data.days
    ) or "<tr><td colspan='7' class='muted'>No data in this period.</td></tr>"

    circ = "".join(
        f"<tr><td>{c.circuit_name or ('Channel ' + str(c.channel_num))}</td>"
        f"<td class='n'>{_num(c.energy_kwh)}</td></tr>"
        for c in data.circuits[:10]
    ) or "<tr><td colspan='2' class='muted'>No circuit data.</td></tr>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  @page {{ size: letter; margin: 1.6cm; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1c1c1c; font-size: 11px; }}
  h1 {{ font-size: 19px; margin: 0 0 2px; }}
  .sub {{ color: #6b6b6b; margin-bottom: 18px; font-size: 12px; }}
  .kpis {{ display: flex; gap: 10px; margin-bottom: 18px; }}
  .kpi {{ flex: 1; border: 0.5px solid #d8d8d8; border-radius: 6px; padding: 10px 12px; }}
  .kpi .label {{ color: #6b6b6b; font-size: 10px; }}
  .kpi .val {{ font-size: 17px; font-weight: 600; margin-top: 3px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 18px; }}
  th, td {{ text-align: left; padding: 5px 7px; border-bottom: 0.5px solid #e4e4e4; }}
  th {{ color: #6b6b6b; font-weight: 500; font-size: 10px; text-transform: uppercase; }}
  td.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .muted {{ color: #9a9a9a; text-align: center; }}
  h2 {{ font-size: 13px; margin: 6px 0 6px; }}
</style></head><body>
  <h1>{title}</h1>
  <div class="sub">{data.home_name or ('Home ' + str(data.home_id))} · {period}</div>
  <div class="kpis">
    <div class="kpi"><div class="label">Home load</div><div class="val">{_num(t.home_load_kwh)} kWh</div></div>
    <div class="kpi"><div class="label">Solar gen</div><div class="val">{_num(t.solar_gen_kwh)} kWh</div></div>
    <div class="kpi"><div class="label">Peak demand</div><div class="val">{_num(t.peak_demand_kw)} kW</div></div>
    <div class="kpi"><div class="label">Self-consumption</div><div class="val">{_num(t.self_consumption_pct, '%')}</div></div>
    <div class="kpi"><div class="label">Est. cost</div><div class="val">{'—' if t.estimated_cost_usd is None else f'${t.estimated_cost_usd:,.2f}'}</div></div>
  </div>
  <h2>Daily breakdown</h2>
  <table>
    <thead><tr><th>Date</th><th>Load kWh</th><th>Solar kWh</th><th>Import kWh</th><th>Peak kW</th><th>Self-cons</th><th>Cost</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>Top circuits by energy</h2>
  <table>
    <thead><tr><th>Circuit</th><th>Energy kWh</th></tr></thead>
    <tbody>{circ}</tbody>
  </table>
</body></html>"""


def _pdf_response(html: str, filename: str) -> Response:
    from weasyprint import HTML  # lazy: heavy import, only on PDF requests

    pdf = HTML(string=html).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


@router.get("/reports/daily")
async def report_daily(
    home_id: int = Query(...),
    day: date = Query(..., description="Report date (YYYY-MM-DD)"),
    format: Literal["pdf", "csv"] = Query("pdf"),
    user: User = Depends(_analyst),
):
    tz = ZoneInfo(await home_timezone(home_id))
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    data = await energy_dataset(home_id, start, end)
    name = f"energy_{home_id}_{day.isoformat()}"
    period = day.strftime("%B %-d, %Y")
    if format == "csv":
        return _csv_response(data, name)
    return _pdf_response(_report_html(data, "Daily energy report", period), name)


@router.get("/reports/monthly")
async def report_monthly(
    home_id: int = Query(...),
    month: str = Query(..., description="Report month (YYYY-MM)"),
    format: Literal["pdf", "csv"] = Query("pdf"),
    user: User = Depends(_analyst),
):
    try:
        year, mon = (int(x) for x in month.split("-"))
        first = date(year, mon, 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    last_day = calendar.monthrange(year, mon)[1]
    tz = ZoneInfo(await home_timezone(home_id))
    start = datetime.combine(first, time.min, tzinfo=tz)
    end = datetime.combine(date(year, mon, last_day), time.min, tzinfo=tz) + timedelta(days=1)
    data = await energy_dataset(home_id, start, end)
    name = f"energy_{home_id}_{month}"
    period = first.strftime("%B %Y")
    if format == "csv":
        return _csv_response(data, name)
    return _pdf_response(_report_html(data, "Monthly energy report", period), name)


@router.get("/reports/export")
async def report_export(
    home_id: int = Query(...),
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    format: Literal["csv", "pdf"] = Query("csv"),
    user: User = Depends(_analyst),
):
    start = date_from if date_from.tzinfo else date_from.replace(tzinfo=timezone.utc)
    end = date_to if date_to.tzinfo else date_to.replace(tzinfo=timezone.utc)
    if start >= end:
        raise HTTPException(status_code=422, detail="`from` must be before `to`")
    data = await energy_dataset(home_id, start, end)
    name = f"energy_{home_id}_{start.date().isoformat()}_{end.date().isoformat()}"
    period = f"{start.date().isoformat()} → {end.date().isoformat()}"
    if format == "pdf":
        return _pdf_response(_report_html(data, "Energy report", period), name)
    return _csv_response(data, name)
