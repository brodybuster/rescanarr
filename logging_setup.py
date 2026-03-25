#!/usr/bin/env python3
"""Shared logging helpers for scheduled container apps."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_LOG_DIR = Path("/config/logs")


class IsoFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        tz_name = os.environ.get("TZ")
        if tz_name:
            try:
                dt = datetime.fromtimestamp(record.created, ZoneInfo(tz_name))
            except Exception:
                dt = datetime.fromtimestamp(record.created).astimezone()
        else:
            dt = datetime.fromtimestamp(record.created).astimezone()

        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")


def current_time() -> datetime:
    tz_name = os.environ.get("TZ")
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now().astimezone()


def setup_application_logger(
    app_name: str,
    log_filename: str | None = None,
    log_dir: Path | None = None,
    stdout_enabled: bool = True,
) -> tuple[logging.Logger, Path]:
    logger = logging.getLogger(app_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = IsoFormatter("[%(asctime)s] %(message)s")

    if stdout_enabled:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    effective_log_dir = (log_dir or DEFAULT_LOG_DIR).expanduser()
    effective_log_dir.mkdir(parents=True, exist_ok=True)

    effective_filename = log_filename or f"{app_name}.log"
    log_file = effective_log_dir / effective_filename

    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    file_handler.suffix = "%Y-%m-%d"
    logger.addHandler(file_handler)

    return logger, log_file


def log_banner(logger: logging.Logger, label: str, timestamp: datetime | None = None) -> None:
    formatted = (timestamp or current_time()).isoformat(timespec="seconds")
    logger.info("======================================================================")
    logger.info("%s %s", label, formatted)
    logger.info("======================================================================")
