"""Structured JSON run log writer for autonomous trading cycles.

Writes a full state snapshot after every cycle to run_logs/ for audit,
debugging, and Phase 5 tuning. Each log file is a self-contained JSON
document with all cycle inputs and outputs.

Requirements covered: LOGS-01
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.config import PROJECT_ROOT

RUN_LOG_DIR = PROJECT_ROOT / "run_logs"


def write_run_log(cycle_result: dict) -> Path:
    """Write a structured JSON run log for this cycle.

    Args:
        cycle_result: The dict returned by run_cycle().

    Returns:
        Path to the written log file.
    """
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    filename = f"cycle_{now.strftime('%Y-%m-%d')}_{now.strftime('%H%M%S')}.json"
    log_path = RUN_LOG_DIR / filename

    log_entry = {
        "version": "1.0",
        "timestamp": cycle_result.get("timestamp", now.isoformat()),
        "status": cycle_result.get("status"),
        "dry_run": cycle_result.get("dry_run"),
        "account": cycle_result.get("account"),
        "stop_loss_results": cycle_result.get("stop_loss_results"),
        "consensus": cycle_result.get("consensus"),
        "order_results": cycle_result.get("order_results"),
        "circuit_breaker": cycle_result.get("circuit_breaker"),
        "post_trade_nlv": cycle_result.get("post_trade_nlv"),
        "daily_snapshot": cycle_result.get("daily_snapshot"),
    }

    with open(log_path, "w") as f:
        json.dump(log_entry, f, indent=2, default=str)

    logger.info("Run log written: {}", log_path)
    return log_path
