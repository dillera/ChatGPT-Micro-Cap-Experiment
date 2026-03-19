from __future__ import annotations

import sys
from pathlib import Path
from loguru import logger


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    logger.remove()  # Remove default handler

    # Stdout handler -- human-readable
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # File handler -- structured text for analysis
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_path / "trading_bot.log"),
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="10 MB",
        retention="30 days",
        compression="gz",
    )

    # File handler -- JSON for structured analysis
    logger.add(
        str(log_path / "trading_bot.jsonl"),
        level=log_level,
        serialize=True,
        rotation="10 MB",
        retention="30 days",
        compression="gz",
    )
