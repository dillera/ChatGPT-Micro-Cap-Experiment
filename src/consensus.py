"""Multi-LLM consensus engine with adversarial bull/bear prompting.

Queries GPT-5.4-mini (bull role) and Claude Sonnet 4.6 (bear role),
validates responses via Pydantic structured output, and applies veto
consensus logic. Both models must agree on action + symbol with
confidence >= threshold for a trade to be approved.

Requirements covered: AIDC-01, AIDC-02, AIDC-03, AIDC-04
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from loguru import logger
from openai import OpenAI

import yfinance as yf

from src.config import get_settings
from src.db import get_db
from src.models import (
    ConsensusResult,
    OptionsAnalysis,
    OptionsConsensusResult,
    SpreadRecommendation,
    TradingAnalysis,
    TradeRecommendation,
)
from src.otc_filter import validate_symbols
from src.prompts import (
    BEAR_SYSTEM,
    BULL_SYSTEM,
    DISCOVERY_SYSTEM,
    OPTIONS_BEAR_SYSTEM,
    OPTIONS_BULL_SYSTEM,
    build_discovery_prompt,
    build_options_prompt,
    build_user_prompt,
)


def query_bull(prompt: str) -> TradingAnalysis:
    """Query GPT (via OpenRouter) with bull (aggressive) role.

    Uses OpenAI native structured output via parse().
    """
    settings = get_settings()
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    completion = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": BULL_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=TradingAnalysis,
        temperature=settings.consensus_temperature,
        max_completion_tokens=settings.consensus_max_tokens,
    )
    return completion.choices[0].message.parsed


def query_bear(prompt: str) -> TradingAnalysis:
    """Query Claude (via OpenRouter) with bear (skeptical) role.

    Uses OpenAI-compatible structured output via parse().
    """
    settings = get_settings()
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    completion = client.chat.completions.parse(
        model=settings.anthropic_model,
        messages=[
            {"role": "system", "content": BEAR_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=TradingAnalysis,
        temperature=settings.consensus_temperature,
        max_completion_tokens=settings.consensus_max_tokens,
    )
    return completion.choices[0].message.parsed


def evaluate_consensus(
    bull: TradingAnalysis,
    bear: TradingAnalysis,
    min_confidence: float = 0.5,
    soft_consensus_penalty: float = 0.80,
) -> tuple[list[TradeRecommendation], list[str]]:
    """Apply consensus logic with hard and soft paths.

    Hard consensus: both models agree on the same action → approved at min(confidence).
    Soft consensus: bull says BUY, bear says HOLD (not SELL) → approved at
                    bull_confidence * soft_consensus_penalty.
    Bear SELL veto: if bear says SELL on any BUY candidate → always rejected.

    Args:
        bull: Analysis from the bull (GPT) model.
        bear: Analysis from the bear (Claude) model.
        min_confidence: Minimum confidence for an approved trade.
        soft_consensus_penalty: Confidence multiplier for soft consensus trades.

    Returns:
        Tuple of (approved_trades, disagreed_symbols).
    """
    approved: list[TradeRecommendation] = []
    disagreed: list[str] = []

    bull_map = {r.symbol: r for r in bull.recommendations}
    bear_map = {r.symbol: r for r in bear.recommendations}

    all_symbols = set(bull_map.keys()) | set(bear_map.keys())

    for symbol in sorted(all_symbols):
        b = bull_map.get(symbol)
        r = bear_map.get(symbol)

        # Only one model mentioned it — skip
        if b is None or r is None:
            logger.info("No consensus on {}: only one model mentioned it", symbol)
            disagreed.append(symbol)
            continue

        # Hard consensus: both models agree on the same action
        if b.action == r.action:
            min_conf = min(b.confidence, r.confidence)
            if min_conf < min_confidence:
                logger.info(
                    "Low confidence on {}: bull={:.2f}, bear={:.2f}",
                    symbol, b.confidence, r.confidence,
                )
                disagreed.append(symbol)
                continue
            approved.append(
                TradeRecommendation(
                    action=b.action,
                    symbol=symbol,
                    confidence=min_conf,
                    stop_loss_pct=max(b.stop_loss_pct, r.stop_loss_pct),
                    reasoning=f"HARD CONSENSUS | BULL: {b.reasoning} | BEAR: {r.reasoning}",
                )
            )
            logger.info(
                "Hard consensus on {}: action={}, confidence={:.2f}",
                symbol, b.action, min_conf,
            )
            continue

        # Soft consensus: bull BUY + bear HOLD → approve with penalty
        # Bear SELL is a hard veto — never override it
        if b.action == "BUY" and r.action == "HOLD":
            soft_conf = round(b.confidence * soft_consensus_penalty, 3)
            if soft_conf < min_confidence:
                logger.info(
                    "Soft consensus on {} below threshold: bull={:.2f} * {:.0%} = {:.2f}",
                    symbol, b.confidence, soft_consensus_penalty, soft_conf,
                )
                disagreed.append(symbol)
                continue
            approved.append(
                TradeRecommendation(
                    action="BUY",
                    symbol=symbol,
                    confidence=soft_conf,
                    stop_loss_pct=max(b.stop_loss_pct, r.stop_loss_pct),
                    reasoning=f"SOFT CONSENSUS (bear neutral) | BULL: {b.reasoning} | BEAR: {r.reasoning}",
                )
            )
            logger.info(
                "Soft consensus on {}: bull BUY ({:.2f}) + bear HOLD → approved at {:.2f}",
                symbol, b.confidence, soft_conf,
            )
            continue

        # All other disagreements (bear SELL, action mismatch, etc.)
        logger.info(
            "Disagreement on {}: bull={}, bear={}",
            symbol, b.action, r.action,
        )
        disagreed.append(symbol)
        continue
        logger.info(
            "Consensus on {}: action={}, confidence={:.2f}",
            symbol,
            b.action,
            min_conf,
        )

    return approved, disagreed


def _log_llm_call(
    conn,
    model: str,
    prompt: str,
    raw_response: str,
    parsed_ok: bool,
    parse_error: str | None = None,
) -> int:
    """Log an LLM API call to the llm_audit table.

    Returns:
        The inserted row id.
    """
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    cursor = conn.execute(
        """INSERT INTO llm_audit (called_at, model, prompt_hash, raw_response, parsed_ok, parse_error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            model,
            prompt_hash,
            raw_response,
            1 if parsed_ok else 0,
            parse_error,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _log_consensus(
    conn,
    gpt4_audit_id: int,
    claude_audit_id: int,
    agreed: list[str],
    disagreed: list[str],
) -> int:
    """Log a consensus decision to the consensus_decisions table.

    Returns:
        The inserted row id.
    """
    cursor = conn.execute(
        """INSERT INTO consensus_decisions
           (decided_at, gpt4_audit_id, claude_audit_id, agreed_tickers, disagreed_tickers, trades_executed)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (
            datetime.now(timezone.utc).isoformat(),
            gpt4_audit_id,
            claude_audit_id,
            ",".join(agreed),
            ",".join(disagreed),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def run_consensus_cycle(
    positions: list[dict],
    buying_power: float,
    candidates: list[str],
) -> ConsensusResult:
    """Run a full consensus cycle: query both LLMs, evaluate, log.

    If either LLM API call fails, the entire cycle aborts (no single-model
    fallback). This is by design -- AIDC-04.

    Args:
        positions: Current portfolio positions (AccountSnapshot.positions format).
        buying_power: Available buying power.
        candidates: Ticker symbols to analyse.

    Returns:
        ConsensusResult with approved trades and full analysis.

    Raises:
        Exception: If either LLM API call fails.
    """
    settings = get_settings()
    prompt = build_user_prompt(positions, buying_power, candidates)

    # Query bull (GPT-5.4-mini) -- abort on failure
    logger.info("Querying bull model ({})", settings.openai_model)
    bull = query_bull(prompt)
    logger.info("Bull analysis received: {} recommendations", len(bull.recommendations))
    for r in bull.recommendations:
        logger.info("  BULL  {} -> {} (conf={:.2f})", r.symbol, r.action, r.confidence)

    # Query bear (Claude Sonnet 4.6) -- abort on failure
    logger.info("Querying bear model ({})", settings.anthropic_model)
    bear = query_bear(prompt)
    logger.info("Bear analysis received: {} recommendations", len(bear.recommendations))
    for r in bear.recommendations:
        logger.info("  BEAR  {} -> {} (conf={:.2f})", r.symbol, r.action, r.confidence)

    # Log both raw responses to llm_audit
    conn = get_db()
    bull_audit_id = _log_llm_call(
        conn,
        model=settings.openai_model,
        prompt=prompt,
        raw_response=bull.model_dump_json(),
        parsed_ok=True,
    )
    bear_audit_id = _log_llm_call(
        conn,
        model=settings.anthropic_model,
        prompt=prompt,
        raw_response=bear.model_dump_json(),
        parsed_ok=True,
    )

    # Evaluate consensus
    approved, disagreed = evaluate_consensus(
        bull, bear, settings.min_confidence, settings.soft_consensus_penalty
    )

    # Collect all symbols seen
    all_symbols = sorted(
        set(r.symbol for r in bull.recommendations)
        | set(r.symbol for r in bear.recommendations)
    )

    # Log consensus decision
    agreed_symbols = [t.symbol for t in approved]
    _log_consensus(conn, bull_audit_id, bear_audit_id, agreed_symbols, disagreed)

    logger.info(
        "Consensus complete: {} approved, {} disagreed",
        len(approved),
        len(disagreed),
    )

    return ConsensusResult(
        approved_trades=approved,
        bull_analysis=bull,
        bear_analysis=bear,
        all_symbols_seen=all_symbols,
        disagreed_symbols=disagreed,
    )


def _query_bull_discovery(prompt: str) -> TradingAnalysis:
    """Query GPT (via OpenRouter) with DISCOVERY_SYSTEM prompt for ticker proposals."""
    settings = get_settings()
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    completion = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": DISCOVERY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=TradingAnalysis,
        temperature=settings.consensus_temperature,
        max_completion_tokens=settings.consensus_max_tokens,
    )
    return completion.choices[0].message.parsed


def _query_bear_discovery(prompt: str) -> TradingAnalysis:
    """Query Claude (via OpenRouter) with DISCOVERY_SYSTEM prompt for ticker proposals."""
    settings = get_settings()
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    completion = client.chat.completions.parse(
        model=settings.anthropic_model,
        messages=[
            {"role": "system", "content": DISCOVERY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=TradingAnalysis,
        temperature=settings.consensus_temperature,
        max_completion_tokens=settings.consensus_max_tokens,
    )
    return completion.choices[0].message.parsed


def query_options_bull(prompt: str) -> OptionsAnalysis:
    """Query GPT (via OpenRouter) with OPTIONS_BULL_SYSTEM for spread direction."""
    settings = get_settings()
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    completion = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": OPTIONS_BULL_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=OptionsAnalysis,
        temperature=settings.consensus_temperature,
        max_completion_tokens=settings.consensus_max_tokens,
    )
    return completion.choices[0].message.parsed


def query_options_bear(prompt: str) -> OptionsAnalysis:
    """Query Claude (via OpenRouter) with OPTIONS_BEAR_SYSTEM for spread direction."""
    settings = get_settings()
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    completion = client.chat.completions.parse(
        model=settings.anthropic_model,
        messages=[
            {"role": "system", "content": OPTIONS_BEAR_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=OptionsAnalysis,
        temperature=settings.consensus_temperature,
        max_completion_tokens=settings.consensus_max_tokens,
    )
    return completion.choices[0].message.parsed


def evaluate_options_consensus(
    bull: OptionsAnalysis,
    bear: OptionsAnalysis,
    min_confidence: float = 0.5,
    soft_consensus_penalty: float = 0.80,
) -> tuple[list[SpreadRecommendation], bool]:
    """Apply consensus logic to options spread recommendations.

    Hard consensus: both models agree on BUY_CALL_SPREAD or BUY_PUT_SPREAD.
    Soft consensus: one says BUY_*_SPREAD, other says HOLD → approved at 80% confidence.
    Veto: one says the opposite direction → rejected.

    Returns (approved_trades, disagreed).
    """
    bull_recs = bull.recommendations
    bear_recs = bear.recommendations

    if not bull_recs or not bear_recs:
        logger.info("Options consensus: one or both models returned no recommendations")
        return [], True

    # Use the first recommendation from each (single-trade focus)
    b = bull_recs[0]
    r = bear_recs[0]

    logger.info("Options consensus — BULL: {} conf={:.2f} | BEAR: {} conf={:.2f}",
                b.action, b.confidence, r.action, r.confidence)

    # Hard veto: opposite directions
    opposite = {
        "BUY_CALL_SPREAD": "BUY_PUT_SPREAD",
        "BUY_PUT_SPREAD": "BUY_CALL_SPREAD",
    }
    if r.action == opposite.get(b.action):
        logger.info("Options consensus: veto — bear chose opposite direction ({})", r.action)
        return [], True

    # Hard consensus: both agree on same spread direction
    if b.action == r.action and b.action != "HOLD":
        min_conf = min(b.confidence, r.confidence)
        if min_conf < min_confidence:
            logger.info("Options hard consensus below threshold: {:.2f}", min_conf)
            return [], True
        approved = SpreadRecommendation(
            action=b.action,
            symbol=b.symbol,
            confidence=min_conf,
            spread_width=b.spread_width,
            target_dte=b.target_dte,
            reasoning=f"HARD CONSENSUS | BULL: {b.reasoning} | BEAR: {r.reasoning}",
            entry_window=b.entry_window,
        )
        logger.info("Options hard consensus: {} at {:.2f}", b.action, min_conf)
        return [approved], False

    # Soft consensus: one says BUY, other says HOLD
    if b.action != "HOLD" and r.action == "HOLD":
        soft_conf = round(b.confidence * soft_consensus_penalty, 3)
        if soft_conf < min_confidence:
            logger.info("Options soft consensus below threshold: {:.2f}", soft_conf)
            return [], True
        approved = SpreadRecommendation(
            action=b.action,
            symbol=b.symbol,
            confidence=soft_conf,
            spread_width=b.spread_width,
            target_dte=b.target_dte,
            reasoning=f"SOFT CONSENSUS (bear neutral) | BULL: {b.reasoning} | BEAR: {r.reasoning}",
            entry_window=b.entry_window,
        )
        logger.info("Options soft consensus: {} at {:.2f}", b.action, soft_conf)
        return [approved], False

    logger.info("Options consensus: no agreement (bull={}, bear={})", b.action, r.action)
    return [], True


def run_options_consensus_cycle(
    ctx: "MarketContext",
    signal: "StrategySignal",
    chain_summary: dict,
    daily_realized_pnl: float,
    trades_today: int,
    max_trades: int,
    buying_power: float,
) -> OptionsConsensusResult:
    """Run a full options consensus cycle for a given strategy signal.

    Queries both LLMs, evaluates consensus, logs to llm_audit and
    consensus_decisions tables (same tables as equity consensus).

    Args:
        ctx: Current market context.
        signal: The mechanical strategy signal triggering this cycle.
        chain_summary: Available strikes near ATM from the options chain.
        daily_realized_pnl: Today's realized P&L so far.
        trades_today: Trades already taken today.
        max_trades: Max allowed trades today.
        buying_power: Available buying power.

    Returns:
        OptionsConsensusResult with approved_trades list.
    """
    from src.market_context import MarketContext
    from src.options_strategy import StrategySignal

    settings = get_settings()
    prompt = build_options_prompt(
        ctx=ctx,
        signal=signal,
        chain_summary=chain_summary,
        daily_realized_pnl=daily_realized_pnl,
        trades_today=trades_today,
        max_trades=max_trades,
        daily_target=settings.options_daily_profit_target,
        buying_power=buying_power,
    )

    logger.info("Querying options bull model ({})", settings.openai_model)
    bull = query_options_bull(prompt)
    logger.info("Options bull: {} recs", len(bull.recommendations))

    logger.info("Querying options bear model ({})", settings.anthropic_model)
    bear = query_options_bear(prompt)
    logger.info("Options bear: {} recs", len(bear.recommendations))

    conn = get_db()
    bull_audit_id = _log_llm_call(
        conn, settings.openai_model, prompt, bull.model_dump_json(), True
    )
    bear_audit_id = _log_llm_call(
        conn, settings.anthropic_model, prompt, bear.model_dump_json(), True
    )

    approved, disagreed = evaluate_options_consensus(
        bull, bear, settings.min_confidence, settings.soft_consensus_penalty
    )

    agreed_symbols = [t.symbol for t in approved]
    _log_consensus(conn, bull_audit_id, bear_audit_id, agreed_symbols, [] if not disagreed else [signal.symbol])

    logger.info(
        "Options consensus complete: {} approved, disagreed={}",
        len(approved), disagreed,
    )

    return OptionsConsensusResult(
        approved_trades=approved,
        bull_analysis=bull,
        bear_analysis=bear,
        disagreed=disagreed,
    )


def run_discovery_cycle(
    positions: list[dict],
    watchlist: list[str],
    buying_power: float,
) -> list[str]:
    """Ask both LLMs to propose new micro-cap ticker candidates.

    Queries both models with DISCOVERY_SYSTEM, extracts proposed symbols,
    validates each via yfinance exchange lookup + OTC filter, and returns
    accepted ticker strings. Non-fatal: any failure returns empty list.

    Args:
        positions: Current portfolio positions (to avoid duplicates).
        watchlist: Active watchlist symbols (to avoid duplicates).
        buying_power: Available buying power (context for LLMs).

    Returns:
        List of validated ticker strings proposed by either LLM.
    """
    try:
        settings = get_settings()
        prompt = build_discovery_prompt(positions, watchlist, buying_power)

        # Query both models; collect all proposed symbols
        proposed: set[str] = set()
        try:
            bull_result = _query_bull_discovery(prompt)
            for rec in bull_result.recommendations:
                proposed.add(rec.symbol.upper().strip())
        except Exception as e:
            logger.warning("Discovery: bull model failed: {}", e)

        try:
            bear_result = _query_bear_discovery(prompt)
            for rec in bear_result.recommendations:
                proposed.add(rec.symbol.upper().strip())
        except Exception as e:
            logger.warning("Discovery: bear model failed: {}", e)

        if not proposed:
            logger.info("Discovery: no symbols proposed by either model")
            return []

        logger.info("Discovery: {} symbols proposed: {}", len(proposed), sorted(proposed))

        # Validate each symbol via yfinance exchange lookup
        symbols_with_exchanges: list[tuple[str, str | None]] = []
        for sym in proposed:
            try:
                info = yf.Ticker(sym).info
                exchange = info.get("exchange")
            except Exception:
                exchange = None
            symbols_with_exchanges.append((sym, exchange))

        accepted, rejected = validate_symbols(symbols_with_exchanges)
        if rejected:
            logger.info("Discovery: {} tickers rejected by OTC filter: {}", len(rejected), rejected)
        logger.info("Discovery: {} validated tickers accepted: {}", len(accepted), accepted)

        return accepted

    except Exception as e:
        logger.warning("Discovery cycle failed (non-fatal): {}", e)
        return []
