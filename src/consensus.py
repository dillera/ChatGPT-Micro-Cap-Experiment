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
from src.models import ConsensusResult, TradingAnalysis, TradeRecommendation
from src.otc_filter import validate_symbols
from src.prompts import BEAR_SYSTEM, BULL_SYSTEM, DISCOVERY_SYSTEM, build_discovery_prompt, build_user_prompt


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
    min_confidence: float = 0.6,
) -> tuple[list[TradeRecommendation], list[str]]:
    """Apply veto consensus: both models must agree on action + symbol.

    Args:
        bull: Analysis from the bull (GPT) model.
        bear: Analysis from the bear (Claude) model.
        min_confidence: Minimum confidence from BOTH models for approval.

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

        if b is None or r is None:
            logger.info(
                "No consensus on {}: only one model mentioned it", symbol
            )
            disagreed.append(symbol)
            continue

        if b.action != r.action:
            logger.info(
                "Disagreement on {}: bull={}, bear={}",
                symbol,
                b.action,
                r.action,
            )
            disagreed.append(symbol)
            continue

        min_conf = min(b.confidence, r.confidence)
        if min_conf < min_confidence:
            logger.info(
                "Low confidence on {}: bull={}, bear={}",
                symbol,
                b.confidence,
                r.confidence,
            )
            disagreed.append(symbol)
            continue

        # Consensus reached -- use min confidence and max stop_loss (more conservative)
        approved.append(
            TradeRecommendation(
                action=b.action,
                symbol=symbol,
                confidence=min_conf,
                stop_loss_pct=max(b.stop_loss_pct, r.stop_loss_pct),
                reasoning=f"BULL: {b.reasoning} | BEAR: {r.reasoning}",
            )
        )
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

    # Query bear (Claude Sonnet 4.6) -- abort on failure
    logger.info("Querying bear model ({})", settings.anthropic_model)
    bear = query_bear(prompt)
    logger.info("Bear analysis received: {} recommendations", len(bear.recommendations))

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
    approved, disagreed = evaluate_consensus(bull, bear, settings.min_confidence)

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
