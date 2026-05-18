"""Daily $100 profit target state machine.

Tracks realized P&L from closed spread positions and enforces:
  - Stop trading when target hit (>= $100)
  - Stop trading when daily loss limit hit (<= -$150)
  - Max 3 trades per day

All functions accept an optional `conn` for test injection.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from src.config import get_settings
from src.db import get_db
from src.models import DailyTargetState, SpreadPosition

ET = ZoneInfo("America/New_York")


def _today_str() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def get_today_target(conn=None) -> DailyTargetState:
    """Read or create today's target row in daily_options_target.

    Creates a fresh row if one doesn't exist for today.
    Returns a DailyTargetState dataclass.
    """
    settings = get_settings()
    own_conn = conn is None
    if own_conn:
        conn = get_db()

    try:
        today = _today_str()
        row = conn.execute(
            "SELECT * FROM daily_options_target WHERE target_date = ?", (today,)
        ).fetchone()

        if row is None:
            now = datetime.now(ET).isoformat()
            conn.execute(
                """INSERT INTO daily_options_target
                   (target_date, target_amount, realized_pnl, unrealized_pnl,
                    trades_today, max_trades, target_hit, stop_loss_hit, updated_at)
                   VALUES (?, ?, 0.0, 0.0, 0, ?, 0, 0, ?)""",
                (today, settings.options_daily_profit_target, settings.options_max_trades_per_day, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM daily_options_target WHERE target_date = ?", (today,)
            ).fetchone()

        return DailyTargetState(
            id=row["id"],
            target_date=row["target_date"],
            target_amount=row["target_amount"],
            realized_pnl=row["realized_pnl"],
            unrealized_pnl=row["unrealized_pnl"],
            trades_today=row["trades_today"],
            max_trades=row["max_trades"],
            target_hit=bool(row["target_hit"]),
            stop_loss_hit=bool(row["stop_loss_hit"]),
        )
    finally:
        if own_conn:
            conn.close()


def update_target_pnl(
    realized_delta: float,
    increment_trades: bool = True,
    conn=None,
) -> DailyTargetState:
    """Add realized P&L from a just-closed spread to today's target row.

    Sets target_hit=True if realized_pnl >= target_amount.
    Sets stop_loss_hit=True if realized_pnl <= -options_daily_stop_loss.
    Optionally increments trades_today (set increment_trades=False when called
    at entry time to just count the trade without P&L update).

    Returns updated DailyTargetState.
    """
    settings = get_settings()
    own_conn = conn is None
    if own_conn:
        conn = get_db()

    try:
        state = get_today_target(conn)
        new_pnl = state.realized_pnl + realized_delta
        new_trades = state.trades_today + (1 if increment_trades else 0)
        new_target_hit = new_pnl >= state.target_amount
        new_stop_hit = new_pnl <= -(settings.options_daily_stop_loss)

        now = datetime.now(ET).isoformat()
        conn.execute(
            """UPDATE daily_options_target
               SET realized_pnl = ?, trades_today = ?,
                   target_hit = ?, stop_loss_hit = ?, updated_at = ?
               WHERE target_date = ?""",
            (new_pnl, new_trades, int(new_target_hit), int(new_stop_hit), now, state.target_date),
        )
        conn.commit()

        if new_target_hit and not state.target_hit:
            logger.info("Daily target HIT: ${:.2f} >= ${:.2f}", new_pnl, state.target_amount)
        if new_stop_hit and not state.stop_loss_hit:
            logger.warning("Daily stop loss HIT: ${:.2f} <= -${:.2f}", new_pnl, settings.options_daily_stop_loss)

        return get_today_target(conn)
    finally:
        if own_conn:
            conn.close()


def increment_trade_count(conn=None) -> DailyTargetState:
    """Increment trades_today without changing P&L (called when a new spread opens)."""
    return update_target_pnl(0.0, increment_trades=True, conn=conn)


def should_take_trade(state: DailyTargetState, window: str) -> tuple[bool, str]:
    """Gate: should we attempt a new trade given the current daily state?

    Returns (allow: bool, reason: str).
    """
    if state.target_hit:
        return False, f"Daily target already hit (${state.realized_pnl:.2f})"
    if state.stop_loss_hit:
        return False, f"Daily stop loss already hit (${state.realized_pnl:.2f})"
    if state.trades_today >= state.max_trades:
        return False, f"Max trades reached ({state.trades_today}/{state.max_trades})"
    return True, f"OK — {state.trades_today}/{state.max_trades} trades, P&L=${state.realized_pnl:.2f}"


def get_dynamic_profit_target(spread_type: str, now_et: datetime | None = None) -> float:
    """Return the profit-close threshold for a spread based on type and time of day.

    Debit spreads (CALL_DEBIT, PUT_DEBIT):
      Theta works against you — close aggressively early.
      before 11:00 → 50%  |  11:00–13:00 → 40%  |  after 13:00 → 30%

    Credit spreads (CALL_CREDIT, PUT_CREDIT):
      Theta works for you — let it cook early, lock in late.
      before 12:00 → 25%  |  12:00–14:00 → 40%  |  after 14:00 → 60%
    """
    from datetime import time as dtime

    settings = get_settings()
    if now_et is None:
        now_et = datetime.now(ET)
    t = now_et.time()

    is_credit = spread_type in ("CALL_CREDIT", "PUT_CREDIT")

    if is_credit:
        if t < dtime(12, 0):
            return settings.options_credit_target_early
        if t < dtime(14, 0):
            return settings.options_credit_target_midday
        return settings.options_credit_target_late
    else:
        if t < dtime(11, 0):
            return settings.options_debit_target_early
        if t < dtime(13, 0):
            return settings.options_debit_target_midday
        return settings.options_debit_target_late


def compute_spread_pnl(
    spread: SpreadPosition,
    current_long_mid: float,
    current_short_mid: float,
) -> float:
    """Compute current unrealized P&L for an open spread position in dollars.

    P&L = (current_net_value - debit_paid) * contracts * 100
    current_net_value = long_mid - short_mid (in dollars per share)
    debit_paid is already in dollars per share.
    """
    current_net_value = current_long_mid - current_short_mid
    return (current_net_value - spread.debit_paid) * spread.contracts * 100
