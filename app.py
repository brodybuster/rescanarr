#!/usr/bin/env python3

import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

from logging_setup import setup_application_logger

CONFIG_PATH = Path("/config/config.yaml")


@dataclass
class AppConfig:
    radarr_url: str
    api_key: str
    ignore_tag_name: str = "ignore"
    count: int = 10
    minimum_age_days: int = 0
    search_cooldown_days: int = 30
    dry_run: bool = False
    cron: str = "0 * * * *"
    request_timeout: int = 60


def parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False

    raise ValueError(
        f"Invalid boolean value for '{field_name}': {value!r}. "
        "Use true or false."
    )


def format_movie_date_for_log(date_value: Optional[str]) -> str:
    if not date_value:
        return "missing"

    try:
        parsed = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    except ValueError:
        return str(date_value)

    local_dt = parsed.astimezone()
    return local_dt.strftime("%m-%d-%Y %I:%M:%S %p %Z")


def parse_iso_datetime(date_value: Optional[str]) -> Optional[datetime]:
    if not date_value:
        return None

    try:
        return datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_movie_date_added(movie: dict[str, Any]) -> Optional[str]:
    return (movie.get("movieFile") or {}).get("dateAdded")


def get_movie_last_search_time(movie: dict[str, Any]) -> Optional[str]:
    value = movie.get("lastSearchTime")
    if value is None:
        return None
    return str(value)


def is_old_enough_for_search(movie: dict[str, Any], min_age_days: int) -> bool:
    if min_age_days <= 0:
        return True

    date_added = parse_iso_datetime(get_movie_date_added(movie))
    if date_added is None:
        return True

    min_allowed_date = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    return date_added <= min_allowed_date


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    required = ["radarr_url", "api_key"]
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ValueError(f"Missing required config keys: {', '.join(missing)}")

    count = int(raw.get("count", 10))
    if count <= 0:
        raise ValueError("Config key 'count' must be greater than 0")

    minimum_age_days_raw = raw.get("minimum_age_days", 0)
    minimum_age_days = int(minimum_age_days_raw)
    if minimum_age_days < 0:
        raise ValueError(
            "Config key 'minimum_age_days' must be greater than or equal to 0"
        )

    search_cooldown_days_raw = raw.get("search_cooldown_days", 30)
    search_cooldown_days = int(search_cooldown_days_raw)
    if search_cooldown_days < 0:
        raise ValueError(
            "Config key 'search_cooldown_days' must be greater than or equal to 0"
        )

    dry_run = parse_bool(raw.get("dry_run", False), "dry_run")

    return AppConfig(
        radarr_url=str(raw["radarr_url"]).rstrip("/"),
        api_key=str(raw["api_key"]),
        ignore_tag_name=str(raw.get("ignore_tag_name", "ignore")),
        count=count,
        minimum_age_days=minimum_age_days,
        search_cooldown_days=search_cooldown_days,
        dry_run=dry_run,
        cron=str(raw.get("cron", "0 * * * *")),
        request_timeout=int(raw.get("request_timeout", 60)),
    )


def config_to_dict(config: AppConfig) -> dict[str, object]:
    values = asdict(config)
    values["api_key"] = "***"
    return values


def get_cron_schedule(config: AppConfig) -> str | None:
    return config.cron


def setup_logging(config_path: Path) -> tuple[logging.Logger, Path]:
    logger, log_file = setup_application_logger(
        app_name="rescanarr",
        log_filename="rescanarr.log",
        log_dir=config_path.parent / "logs",
    )

    return logger, log_file


class RadarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Api-Key": api_key,
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def get(self, path: str) -> Any:
        response = self.session.get(self._url(path), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.session.post(self._url(path), json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_tags(self) -> list[dict[str, Any]]:
        return self.get("/api/v3/tag")

    def get_queue(self) -> Any:
        return self.get("/api/v3/queue")

    def get_movies(self) -> list[dict[str, Any]]:
        return self.get("/api/v3/movie")

    def search_movie(self, movie_id: int) -> dict[str, Any]:
        return self.post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})


def get_tag_id_by_name(tag_name: str, tags: list[dict[str, Any]]) -> Optional[int]:
    for tag in tags:
        if tag.get("label") == tag_name:
            return int(tag["id"])
    return None


def get_queue_records(queue_payload: Any) -> list[dict[str, Any]]:
    if isinstance(queue_payload, list):
        return [item for item in queue_payload if isinstance(item, dict)]

    if isinstance(queue_payload, dict):
        records = queue_payload.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]

    return []


def is_base_eligible(
    movie: dict[str, Any],
    ignore_tag_id: Optional[int],
    min_age_days: int,
) -> bool:
    tags = movie.get("tags") or []

    if movie.get("monitored") is not True:
        return False
    if movie.get("status") != "released":
        return False
    if ignore_tag_id is not None and ignore_tag_id in tags:
        return False
    if not is_old_enough_for_search(movie, min_age_days):
        return False

    return True


def was_searched_recently(movie: dict[str, Any], search_cooldown_days: int) -> bool:
    if search_cooldown_days <= 0:
        return False

    last_search_time = parse_iso_datetime(get_movie_last_search_time(movie))
    if last_search_time is None:
        return False

    min_allowed_search_time = datetime.now(timezone.utc) - timedelta(days=search_cooldown_days)
    return last_search_time > min_allowed_search_time


def compute_stats(
    movies: list[dict[str, Any]],
    ignore_tag_id: Optional[int],
    min_age_days: int,
    search_cooldown_days: int,
) -> dict[str, int]:
    stats = {
        "total": 0,
        "not_monitored": 0,
        "not_released": 0,
        "ignored": 0,
        "too_recent": 0,
        "base_eligible": 0,
        "searched_recently": 0,
        "never_searched": 0,
        "selectable": 0,
    }

    for movie in movies:
        stats["total"] += 1
        tags = movie.get("tags") or []

        if movie.get("monitored") is not True:
            stats["not_monitored"] += 1
            continue

        if movie.get("status") != "released":
            stats["not_released"] += 1
            continue

        if ignore_tag_id is not None and ignore_tag_id in tags:
            stats["ignored"] += 1
            continue

        if not is_old_enough_for_search(movie, min_age_days):
            stats["too_recent"] += 1
            continue

        stats["base_eligible"] += 1

        if get_movie_last_search_time(movie) is None:
            stats["never_searched"] += 1

        if was_searched_recently(movie, search_cooldown_days):
            stats["searched_recently"] += 1
            continue

        stats["selectable"] += 1

    return stats


def get_selectable_movies(
    movies: list[dict[str, Any]],
    ignore_tag_id: Optional[int],
    min_age_days: int,
    search_cooldown_days: int,
) -> list[dict[str, Any]]:
    selectable = []

    for movie in movies:
        if not is_base_eligible(movie, ignore_tag_id, min_age_days):
            continue
        if was_searched_recently(movie, search_cooldown_days):
            continue

        selectable.append(
            {
                "id": int(movie["id"]),
                "title": str(movie.get("title", "Unknown")),
                "year": movie.get("year", "Unknown"),
                "date_added": get_movie_date_added(movie),
                "last_search_time": get_movie_last_search_time(movie),
            }
        )

    return selectable


def select_oldest_movies(candidate_movies: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if not candidate_movies:
        return []

    minimum_datetime = datetime.min.replace(tzinfo=timezone.utc)

    def movie_sort_key(movie: dict[str, Any]):
        last_search_time = parse_iso_datetime(movie.get("last_search_time"))
        date_added = parse_iso_datetime(movie.get("date_added"))
        return (
            last_search_time is not None,
            last_search_time or minimum_datetime,
            date_added or minimum_datetime,
            str(movie.get("title", "")).lower(),
        )

    sorted_movies = sorted(candidate_movies, key=movie_sort_key)

    return sorted_movies[:count]


def run_once(config: AppConfig, logger: logging.Logger) -> None:
    logger.info("Rescanarr Starting")
    logger.info("Radarr URL: %s", config.radarr_url)
    logger.info("Ignore tag: %s", config.ignore_tag_name)
    logger.info("Count: %s", config.count)
    logger.info("Minimum age: %s day(s)", config.minimum_age_days)
    logger.info(
        "Search cooldown: %s day(s)",
        config.search_cooldown_days,
    )
    logger.info("Dry run: %s", config.dry_run)
    logger.info("Cron: %s", config.cron)

    client = RadarrClient(
        base_url=config.radarr_url,
        api_key=config.api_key,
        timeout=config.request_timeout,
    )

    logger.info("Checking Radarr queue...")
    queue_payload = client.get_queue()
    queue_records = get_queue_records(queue_payload)
    logger.info("Found %s item(s) in Radarr queue", len(queue_records))
    if queue_records:
        logger.info("Radarr queue is not empty; aborting this run and waiting for the next schedule")
        return

    logger.info("Fetching Radarr tags...")
    tags = client.get_tags()
    logger.info("Fetched %s tag(s)", len(tags))

    ignore_tag_id = get_tag_id_by_name(config.ignore_tag_name, tags)
    if ignore_tag_id is not None:
        logger.info(
            "Using existing ignore tag '%s' with id=%s",
            config.ignore_tag_name,
            ignore_tag_id,
        )
    else:
        logger.info(
            "Ignore tag '%s' not found; ignore filtering disabled",
            config.ignore_tag_name,
        )

    logger.info("Fetching Radarr movies...")
    movies = client.get_movies()
    logger.info("Fetched %s movie(s)", len(movies))

    stats = compute_stats(
        movies,
        ignore_tag_id,
        config.minimum_age_days,
        config.search_cooldown_days,
    )
    logger.info("Filter summary:")
    logger.info(" Total library movies: %s", stats["total"])
    logger.info(" Excluded - not monitored: %s", stats["not_monitored"])
    logger.info(" Excluded - not released: %s", stats["not_released"])
    logger.info(" Excluded - ignore tag: %s", stats["ignored"])
    logger.info(" Excluded - newer than min age: %s", stats["too_recent"])
    logger.info(" Base eligible: %s", stats["base_eligible"])
    logger.info(
        " Excluded - searched within minimum interval: %s",
        stats["searched_recently"],
    )
    logger.info(" Never searched in Radarr: %s", stats["never_searched"])
    logger.info(" Selectable this run: %s", stats["selectable"])

    logger.info("Building candidate pool...")
    selectable_movies = get_selectable_movies(
        movies,
        ignore_tag_id,
        config.minimum_age_days,
        config.search_cooldown_days,
    )

    logger.info("Selectable movie objects collected: %s", len(selectable_movies))
    logger.info(
        "Selecting up to %s eligible movie(s) by oldest last search time...",
        config.count,
    )
    selected_movies = select_oldest_movies(selectable_movies, config.count)

    if not selected_movies:
        logger.info("No selectable movies found. Exiting.")
        return

    logger.info("Selected %s movie(s):", len(selected_movies))
    for movie in selected_movies:
        logger.info(
            " - %s (%s)  [Last Search: %s] [Date Added: %s]",
            movie["title"],
            movie["year"],
            format_movie_date_for_log(movie.get("last_search_time")),
            format_movie_date_for_log(movie.get("date_added")),
        )

    if config.dry_run:
        logger.info("[DRY RUN] Would initiate %s search(es)", len(selected_movies))
        logger.info("Dry run complete")
        return

    logger.info("Initiating searches...")
    search_error: Optional[Exception] = None
    search_error_traceback = None

    try:
        for index, movie in enumerate(selected_movies, start=1):
            logger.info(
                "[%s/%s] Starting search for %s (%s) [id=%s]",
                index,
                len(selected_movies),
                movie["title"],
                movie["year"],
                movie["id"],
            )
            response = client.search_movie(movie["id"])
            command_id = response.get("id", "unknown")
            command_name = response.get("name", "unknown")
            logger.info(
                "[%s/%s] Search command accepted: name=%s id=%s",
                index,
                len(selected_movies),
                command_name,
                command_id,
            )
    except Exception as exc:
        search_error = exc
        search_error_traceback = exc.__traceback__

    if search_error is not None:
        raise search_error.with_traceback(search_error_traceback)

    logger.info("Rescanarr Finished")


def main() -> int:
    try:
        config = load_config(CONFIG_PATH)
        logger, log_file = setup_logging(CONFIG_PATH)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    logger.info("Config loaded")
    logger.info("Log file %s", log_file)

    try:
        run_once(config, logger)
    except requests.HTTPError as exc:
        logger.error("HTTP error: %s", exc)
        if exc.response is not None:
            logger.error("Response status: %s", exc.response.status_code)
            logger.error("Response body: %s", exc.response.text)
        return 1
    except Exception:
        logger.exception("Run failed")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
