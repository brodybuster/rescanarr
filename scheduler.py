#!/usr/bin/env python3
"""Generic cron-driven scheduler for one-shot app workers."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional

from logging_setup import current_time, log_banner


shutdown_requested = False
CONFIG_POLL_INTERVAL_SECONDS = 60


def load_app(app_module: str):
    try:
        return importlib.import_module(app_module)
    except ModuleNotFoundError:
        sys.exit(f"Unable to import app module '{app_module}'.")


def handle_shutdown(signum: int, _frame: Optional[object]) -> None:
    global shutdown_requested
    shutdown_requested = True
    signame = signal.Signals(signum).name
    logger = getattr(handle_shutdown, "logger", None)
    if logger is not None:
        logger.info("Received %s, shutting down scheduler", signame)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a one-shot app worker on a cron schedule."
    )
    parser.add_argument(
        "--app-module",
        default="app",
        help="Python module name for the one-shot worker. Defaults to app.",
    )
    parser.add_argument(
        "--config-path",
        help="Override the worker config path. Defaults to the app module CONFIG_PATH.",
    )
    return parser.parse_args()


def log_config_changes(logger, app, previous_config, current_config, source: str) -> None:
    previous_values = config_to_dict(app, previous_config)
    current_values = config_to_dict(app, current_config)
    logger.info("Config reloaded from %s", source)
    for field_name in sorted(set(previous_values) | set(current_values)):
        previous_value = previous_values.get(field_name)
        current_value = current_values.get(field_name)
        if current_value != previous_value:
            logger.info(
                " Config changed - %s: %r -> %r",
                field_name,
                previous_value,
                current_value,
            )


def wait_for_next_run(app, config_path, logger, current_config, next_run: datetime):
    next_config_check = time.monotonic() + CONFIG_POLL_INTERVAL_SECONDS

    while not shutdown_requested:
        remaining_seconds = (next_run - current_time()).total_seconds()
        if remaining_seconds <= 0:
            return "run", current_config

        if time.monotonic() >= next_config_check:
            next_config_check = time.monotonic() + CONFIG_POLL_INTERVAL_SECONDS
            try:
                reloaded_config = app.load_config(config_path)
            except Exception:
                logger.exception("Config reload failed while waiting for next scheduled run")
            else:
                if reloaded_config != current_config:
                    log_config_changes(
                        logger,
                        app,
                        current_config,
                        reloaded_config,
                        "wait loop",
                    )
                    return "recompute", reloaded_config

        time.sleep(min(remaining_seconds, 1))

    logger.info("Shutdown requested before next scheduled run")
    return "shutdown", current_config


def config_to_dict(app, config) -> dict[str, object]:
    if hasattr(app, "config_to_dict"):
        return app.config_to_dict(config)
    if dataclasses.is_dataclass(config):
        return dataclasses.asdict(config)
    if isinstance(config, dict):
        return dict(config)
    if hasattr(config, "__dict__"):
        return dict(vars(config))
    return {"value": repr(config)}


def get_cron_schedule(app, config) -> str | None:
    if hasattr(app, "get_cron_schedule"):
        return app.get_cron_schedule(config)
    if isinstance(config, dict):
        return config.get("cron_schedule") or config.get("cron")
    if hasattr(config, "cron_schedule"):
        return config.cron_schedule
    if hasattr(config, "cron"):
        return config.cron
    return None


def resolve_config_path(app, cli_config_path: str | None):
    if cli_config_path:
        return cli_config_path
    if hasattr(app, "CONFIG_PATH"):
        return app.CONFIG_PATH
    sys.exit("App module must define CONFIG_PATH or pass --config-path.")


def get_app_version() -> str | None:
    value = os.environ.get("APP_VERSION", "").strip()
    return value or None


def main() -> int:
    args = parse_args()
    app = load_app(args.app_module)
    config_path = resolve_config_path(app, args.config_path)

    try:
        initial_config = app.load_config(config_path)
        logger, log_target = app.setup_logging(config_path)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    handle_shutdown.logger = logger
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info("Config loaded")
    logger.info("Log target %s", log_target)
    app_version = get_app_version()
    if app_version:
        logger.info("App version: %s", app_version)

    last_config = initial_config
    while not shutdown_requested:
        try:
            current_config = app.load_config(config_path)
        except Exception:
            logger.exception("Config reload failed")
            time.sleep(5)
            continue

        if current_config != last_config:
            log_config_changes(logger, app, last_config, current_config, "scheduler")
            last_config = current_config

        try:
            cron_schedule = get_cron_schedule(app, current_config)
            if not cron_schedule:
                logger.info("No CRON_SCHEDULE set; running once and exiting")
                log_banner(logger, "RUN START")
                try:
                    app.run_once(current_config, logger)
                finally:
                    log_banner(logger, "RUN END")
                return 0

            from croniter import croniter

            base_time = current_time()
            next_run = croniter(cron_schedule, base_time).get_next(datetime)
        except Exception:
            logger.exception("Invalid cron expression '%s'", cron_schedule)
            time.sleep(5)
            continue

        logger.info("Configured cron schedule: %s", cron_schedule)
        logger.info("Next scheduled run at %s", next_run.isoformat(timespec="seconds"))

        wait_result, current_config = wait_for_next_run(
            app,
            config_path,
            logger,
            current_config,
            next_run,
        )
        if wait_result == "shutdown":
            break
        if wait_result == "recompute":
            last_config = current_config
            continue

        try:
            run_config = app.load_config(config_path)
            if run_config != last_config:
                log_config_changes(logger, app, last_config, run_config, "run start")
                last_config = run_config

            log_banner(logger, "RUN START")
            try:
                app.run_once(run_config, logger)
            finally:
                log_banner(logger, "RUN END")
        except Exception:
            logger.exception("Run failed")

    logger.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
