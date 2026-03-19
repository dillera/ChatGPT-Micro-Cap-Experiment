"""Circuit breaker state machine with SQLite persistence.

Prevents catastrophic loss spirals by enforcing two independent halts:
- HALTED_DAILY: daily loss exceeds max_daily_loss_pct (auto-resets next calendar day)
- HALTED_DRAWDOWN: drawdown from ATH exceeds max_drawdown_pct (requires manual override)

All state persists in the circuit_breaker and daily_snapshots SQLite tables
so a process restart cannot accidentally reset a tripped breaker.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from src.config import get_settings
from src.db import get_db
from src.models import CircuitBreakerState, DailySnapshot

if TYPE_CHECKING:
    from src.broker import AccountSnapshot


def get_cb_status() -> CircuitBreakerState:
    """Read circuit breaker state from SQLite.

    If HALTED_DAILY and the trip date is before today, auto-reset to ACTIVE
    (daily loss limit resets each calendar day). HALTED_DRAWDOWN never
    auto-resets -- it requires OVERRIDE_CIRCUIT_BREAKER=1 in the environment.
    """
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM circuit_breaker WHERE id=1").fetchone()
        if not row:
            return CircuitBreakerState()

        status = row["status"]
        tripped_at = row["tripped_at"]

        # HALTED_DAILY auto-resets on new calendar day
        if status == "HALTED_DAILY" and tripped_at:
            tripped_date = tripped_at[:10]  # YYYY-MM-DD
            today = datetime.now().strftime("%Y-%m-%d")
            if tripped_date < today:
                reset_ts = datetime.now().isoformat()
                conn.execute(
                    "UPDATE circuit_breaker SET status='ACTIVE', reset_at=? WHERE id=1",
                    (reset_ts,),
                )
                conn.commit()
                logger.info(
                    "HALTED_DAILY auto-reset (tripped {}, now {})",
                    tripped_date,
                    today,
                )
                return CircuitBreakerState(status="ACTIVE", reset_at=reset_ts)

        return CircuitBreakerState(
            status=status,
            tripped_at=tripped_at,
            reason=row["reason"],
            reset_at=row["reset_at"],
        )
    finally:
        conn.close()


def evaluate_circuit_breaker(
    opening_nlv: float,
    current_nlv: float,
) -> CircuitBreakerState:
    """Evaluate whether a circuit breaker trip condition is met.

    Args:
        opening_nlv: Net liquidating value at start of trading day.
        current_nlv: Net liquidating value after latest trade/update.

    Returns:
        Current CircuitBreakerState (possibly newly tripped).
    """
    cb = get_cb_status()

    # Already tripped -- nothing to evaluate
    if cb.status != "ACTIVE":
        return cb

    settings = get_settings()

    # --- Daily loss check (OPER-03) ---
    if opening_nlv > 0:
        daily_pnl_pct = (current_nlv - opening_nlv) / opening_nlv
        if daily_pnl_pct < -settings.max_daily_loss_pct:
            reason = (
                f"Daily loss {daily_pnl_pct:.2%} exceeds "
                f"-{settings.max_daily_loss_pct:.0%} threshold "
                f"(opening={opening_nlv:.2f}, current={current_nlv:.2f})"
            )
            return _trip("HALTED_DAILY", reason)

    # --- Drawdown check (OPER-04) ---
    conn = get_db()
    try:
        row = conn.execute("SELECT MAX(peak_equity) AS peak FROM daily_snapshots").fetchone()
        peak = row["peak"] if row and row["peak"] is not None else opening_nlv
    finally:
        conn.close()

    # Update peak if current value exceeds it
    peak = max(peak, current_nlv)

    if peak > 0:
        drawdown_pct = (peak - current_nlv) / peak
        if drawdown_pct > settings.max_drawdown_pct:
            reason = (
                f"Drawdown {drawdown_pct:.2%} exceeds "
                f"{settings.max_drawdown_pct:.0%} threshold "
                f"(peak={peak:.2f}, current={current_nlv:.2f})"
            )
            return _trip("HALTED_DRAWDOWN", reason)

    return cb


def _trip(new_status: str, reason: str) -> CircuitBreakerState:
    """Persist a circuit breaker trip to SQLite and return updated state."""
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE circuit_breaker SET status=?, tripped_at=?, reason=? WHERE id=1",
            (new_status, now, reason),
        )
        conn.commit()
        logger.error("CIRCUIT BREAKER TRIPPED: {} -- {}", new_status, reason)
    finally:
        conn.close()
    return CircuitBreakerState(status=new_status, tripped_at=now, reason=reason)


def record_daily_snapshot(
    snapshot: AccountSnapshot,
    previous_nlv: float | None = None,
) -> DailySnapshot:
    """Record today's equity snapshot in daily_snapshots table.

    Computes peak equity (all-time high) and drawdown percentage.

    Args:
        snapshot: Live account snapshot from the broker.
        previous_nlv: Previous day's NLV for P&L calc. If None, fetched from DB.

    Returns:
        DailySnapshot dataclass with computed fields.
    """
    conn = get_db()
    try:
        # Get previous peak equity
        row = conn.execute("SELECT MAX(peak_equity) AS peak FROM daily_snapshots").fetchone()
        previous_peak = row["peak"] if row and row["peak"] is not None else snapshot.net_liquidating_value

        new_peak = max(previous_peak, snapshot.net_liquidating_value)
        drawdown_pct = (new_peak - snapshot.net_liquidating_value) / new_peak if new_peak > 0 else 0.0

        # Positions value
        positions_value = sum(p["market_value"] for p in snapshot.positions)

        # Daily P&L
        if previous_nlv is not None:
            daily_pnl = snapshot.net_liquidating_value - previous_nlv
            daily_pnl_pct = daily_pnl / previous_nlv if previous_nlv > 0 else 0.0
        else:
            # Try to get yesterday's snapshot
            yesterday_row = conn.execute(
                "SELECT total_equity FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
            ).fetchone()
            if yesterday_row:
                prev = yesterday_row["total_equity"]
                daily_pnl = snapshot.net_liquidating_value - prev
                daily_pnl_pct = daily_pnl / prev if prev > 0 else 0.0
            else:
                daily_pnl = 0.0
                daily_pnl_pct = 0.0

        today = datetime.now().strftime("%Y-%m-%d")

        conn.execute(
            """INSERT OR REPLACE INTO daily_snapshots
               (snapshot_date, total_equity, cash_balance, positions_value,
                daily_pnl, daily_pnl_pct, peak_equity, drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                today,
                snapshot.net_liquidating_value,
                snapshot.cash_balance,
                positions_value,
                daily_pnl,
                daily_pnl_pct,
                new_peak,
                drawdown_pct,
            ),
        )
        conn.commit()
        logger.info(
            "Daily snapshot recorded: equity={:.2f}, peak={:.2f}, drawdown={:.2%}, pnl={:.2%}",
            snapshot.net_liquidating_value,
            new_peak,
            drawdown_pct,
            daily_pnl_pct,
        )

        return DailySnapshot(
            snapshot_date=today,
            total_equity=snapshot.net_liquidating_value,
            cash_balance=snapshot.cash_balance,
            positions_value=positions_value,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            peak_equity=new_peak,
            drawdown_pct=drawdown_pct,
        )
    finally:
        conn.close()
