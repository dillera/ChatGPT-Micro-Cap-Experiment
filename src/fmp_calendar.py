"""FMP Economic Calendar — macro event pre-flight gate.

Uses Financial Modeling Prep's economic_calendar endpoint to identify
high-impact macro events scheduled for today (FOMC, CPI, NFP, PCE, GDP,
etc.) and flag the day as a macro-risk day.

On macro-risk days the options cycle blocks:
  - Iron condor (premium selling into uncertainty)
  - Credit spreads (short premium)
Directional debit spreads are allowed but confidence is penalized.

Endpoint: GET /stable/economic-calendar
  Requires FMP starter plan (~$19/mo). The free tier returns a 403/empty.

Graceful degradation:
  - No API key → macro_risk_day=False, no blocking
  - Paywalled / request failure → same, with a warning log
  - Empty response → no events today, proceed normally

Usage:
  from src.fmp_calendar import fetch_macro_risk

  risk = fetch_macro_risk()          # uses today's date
  if risk.is_risk_day:
      print(risk.events)             # list of blocking event dicts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from loguru import logger

ET = ZoneInfo("America/New_York")

# FMP stable base URL (v3 was deprecated Aug 2025)
_BASE = "https://financialmodelingprep.com/stable"


@dataclass
class MacroRisk:
    is_risk_day: bool
    events: list[dict] = field(default_factory=list)  # blocking events found today
    source: str = "fmp"   # "fmp", "unavailable", or "disabled"


def fetch_macro_risk(date_str: str | None = None) -> MacroRisk:
    """Fetch today's high-impact macro events from FMP economic calendar.

    Args:
        date_str: ISO date string (YYYY-MM-DD). Defaults to today ET.

    Returns:
        MacroRisk with is_risk_day=True and events list if any blocking
        events are found. Returns MacroRisk(is_risk_day=False) on any
        error or when the API key is missing/free-tier.
    """
    from src.config import get_settings
    settings = get_settings()

    if not settings.fmp_api_key:
        logger.debug("FMP: no API key configured — macro gate disabled")
        return MacroRisk(is_risk_day=False, source="disabled")

    today = date_str or datetime.now(ET).strftime("%Y-%m-%d")

    try:
        import requests
        resp = requests.get(
            f"{_BASE}/economic-calendar",
            params={"from": today, "to": today, "apikey": settings.fmp_api_key},
            timeout=5,
        )
    except Exception as e:
        logger.warning("FMP economic calendar: request failed — {} — proceeding without gate", e)
        return MacroRisk(is_risk_day=False, source="unavailable")

    # Paywalled response is plain text, not JSON
    if resp.status_code != 200 or not resp.content:
        logger.warning(
            "FMP economic calendar: HTTP {} — likely free-tier restriction. "
            "Upgrade to starter plan to enable macro gate.",
            resp.status_code,
        )
        return MacroRisk(is_risk_day=False, source="unavailable")

    try:
        data = resp.json()
    except Exception:
        # Plain-text error message from FMP ("Restricted Endpoint...")
        logger.warning(
            "FMP economic calendar: non-JSON response — free-tier restriction. "
            "Upgrade to starter plan to enable macro gate."
        )
        return MacroRisk(is_risk_day=False, source="unavailable")

    if not isinstance(data, list):
        logger.warning("FMP economic calendar: unexpected response shape — {}", type(data))
        return MacroRisk(is_risk_day=False, source="unavailable")

    blocking = _find_blocking_events(data, settings)

    if blocking:
        names = ", ".join(e.get("event", "?") for e in blocking)
        logger.warning(
            "FMP: MACRO RISK DAY — {} high-impact event(s) today: {}",
            len(blocking), names,
        )
    else:
        logger.info("FMP: no blocking macro events today ({})", today)

    return MacroRisk(
        is_risk_day=len(blocking) > 0,
        events=blocking,
        source="fmp",
    )


def _find_blocking_events(events: list[dict], settings) -> list[dict]:
    """Filter to high-impact events that match the configured block list."""
    block_keywords = [k.lower() for k in settings.fmp_block_events]
    min_impact = settings.fmp_min_impact.lower()
    blocking = []

    for ev in events:
        impact = (ev.get("impact") or "").lower()
        name = (ev.get("event") or "").lower()
        country = (ev.get("country") or "").upper()

        # Only US events matter for SPY/QQQ/IWM
        if country not in ("US", "USD", ""):
            continue

        if impact != min_impact:
            continue

        if any(kw in name for kw in block_keywords):
            blocking.append(ev)

    return blocking


def macro_gate_reason(risk: MacroRisk) -> str:
    """Return a human-readable string describing which events are blocking."""
    if not risk.is_risk_day:
        return ""
    names = [e.get("event", "unknown") for e in risk.events]
    times = [e.get("date", "") for e in risk.events]
    parts = [f"{n} ({t})" if t else n for n, t in zip(names, times)]
    return f"macro risk day — high-impact events: {', '.join(parts)}"
