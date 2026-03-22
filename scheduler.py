#!/usr/bin/env python3
"""Simple cron-driven scheduler for the RescanArr one-shot worker."""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime
from typing import Optional

import requests
from croniter import croniter

import app

shutdown_requested = False


def handle_shutdown(signum: int, _frame: Optional[object]) -> None:
    global shutdown_requested
    shutdown_requested = True
    signame = signal.Signals(signum).name
    logging = getattr(handle_shutdown, "logger", None)
    if logging is not None:
        logging.info("Received %s, shutting down scheduler", signame)


def current_time() -> datetime:
    return datetime.now().astimezone()


def sleep_until(next_run: datetime, logger) -> bool:
    while not shutdown_requested:
        remaining_seconds = (next_run - current_time()).total_seconds()
        if remaining_seconds <= 0:
            return True

        time.sleep(min(remaining_seconds, 1))

    logger.info("Shutdown requested before next scheduled run")
    return False


def main() -> int:
    try:
        initial_config = app.load_config(app.CONFIG_PATH)
        logger, log_file = app.setup_logging(app.CONFIG_PATH)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    handle_shutdown.logger = logger
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info("Config loaded")
    logger.info("Log file %s", log_file)

    last_config = initial_config

    while not shutdown_requested:
        try:
            current_config = app.load_config(app.CONFIG_PATH)
        except Exception:
            logger.exception("Config reload failed")
            time.sleep(5)
            continue

        if current_config != last_config:
            previous_values = app.asdict(last_config)
            current_values = app.asdict(current_config)
            logger.info("Config reloaded from scheduler")
            for field_name in current_values:
                if current_values[field_name] != previous_values[field_name]:
                    logger.info(
                        " Config changed - %s: %r -> %r",
                        field_name,
                        previous_values[field_name],
                        current_values[field_name],
                    )
            last_config = current_config

        try:
            base_time = current_time()
            croniter(current_config.cron, base_time)
            next_run = croniter(current_config.cron, base_time).get_next(datetime)
        except Exception:
            logger.exception("Invalid cron expression '%s'", current_config.cron)
            time.sleep(5)
            continue

        logger.info("Configured cron schedule: %s", current_config.cron)
        logger.info("Next scheduled run at %s", next_run.strftime("%Y-%m-%d %H:%M:%S %Z"))

        if not sleep_until(next_run, logger):
            break

        try:
            run_config = app.load_config(app.CONFIG_PATH)
            if run_config != last_config:
                previous_values = app.asdict(last_config)
                current_values = app.asdict(run_config)
                logger.info("Config reloaded from run start")
                for field_name in current_values:
                    if current_values[field_name] != previous_values[field_name]:
                        logger.info(
                            " Config changed - %s: %r -> %r",
                            field_name,
                            previous_values[field_name],
                            current_values[field_name],
                        )
                last_config = run_config

            app.run_once(run_config, logger)
        except requests.HTTPError as exc:
            logger.error("HTTP error: %s", exc)
            if exc.response is not None:
                logger.error("Response status: %s", exc.response.status_code)
                logger.error("Response body: %s", exc.response.text)
        except Exception:
            logger.exception("Run failed")

    logger.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
