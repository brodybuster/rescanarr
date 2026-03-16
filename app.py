#!/usr/bin/env python3

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

CONFIG_PATH = Path("/config/config.yaml")


@dataclass
class AppConfig:
    radarr_url: str
    api_key: str
    checked_tag_name: str = "checked"
    ignore_tag_name: str = "ignore"
    count: int = 10
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

    dry_run = parse_bool(raw.get("dry_run", False), "dry_run")

    return AppConfig(
        radarr_url=str(raw["radarr_url"]).rstrip("/"),
        api_key=str(raw["api_key"]),
        checked_tag_name=str(raw.get("checked_tag_name", "checked")),
        ignore_tag_name=str(raw.get("ignore_tag_name", "ignore")),
        count=count,
        dry_run=dry_run,
        cron=str(raw.get("cron", "0 * * * *")),
        request_timeout=int(raw.get("request_timeout", 60)),
    )


def setup_logging(config_path: Path) -> tuple[logging.Logger, Path]:
    config_dir = config_path.parent
    log_dir = config_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "rescanarr.log"

    formatter = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

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

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    logger = logging.getLogger("rescanarr")
    logger.setLevel(logging.INFO)

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

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.session.put(self._url(path), json=payload, timeout=self.timeout)
        response.raise_for_status()
        if response.text.strip():
            return response.json()
        return None

    def get_tags(self) -> list[dict[str, Any]]:
        return self.get("/api/v3/tag")

    def create_tag(self, label: str) -> int:
        result = self.post("/api/v3/tag", {"label": label})
        return int(result["id"])

    def get_movies(self) -> list[dict[str, Any]]:
        return self.get("/api/v3/movie")

    def search_movie(self, movie_id: int) -> dict[str, Any]:
        return self.post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})

    def apply_tag_operation(self, movie_ids: list[int], tag_id: int, operation: str) -> None:
        if not movie_ids:
            return

        if operation not in {"add", "remove"}:
            raise ValueError(f"Unsupported tag operation: {operation}")

        payload = {
            "movieIds": movie_ids,
            "tags": [tag_id],
            "applyTags": operation,
        }
        self.put("/api/v3/movie/editor", payload)

    def apply_tag_to_movies(self, movie_ids: list[int], tag_id: int) -> None:
        self.apply_tag_operation(movie_ids, tag_id, "add")

    def remove_tag_from_movies(self, movie_ids: list[int], tag_id: int) -> None:
        self.apply_tag_operation(movie_ids, tag_id, "remove")


def get_tag_id_by_name(tag_name: str, tags: list[dict[str, Any]]) -> Optional[int]:
    for tag in tags:
        if tag.get("label") == tag_name:
            return int(tag["id"])
    return None


def is_base_eligible(movie: dict[str, Any], ignore_tag_id: Optional[int]) -> bool:
    tags = movie.get("tags") or []

    if movie.get("monitored") is not True:
        return False
    if movie.get("status") != "released":
        return False
    if ignore_tag_id is not None and ignore_tag_id in tags:
        return False

    return True


def is_selectable(
    movie: dict[str, Any],
    checked_tag_id: int,
    ignore_tag_id: Optional[int],
) -> bool:
    tags = movie.get("tags") or []

    if not is_base_eligible(movie, ignore_tag_id):
        return False
    if checked_tag_id in tags:
        return False

    return True


def compute_stats(
    movies: list[dict[str, Any]],
    checked_tag_id: int,
    ignore_tag_id: Optional[int],
) -> dict[str, int]:
    stats = {
        "total": 0,
        "not_monitored": 0,
        "not_released": 0,
        "ignored": 0,
        "base_eligible": 0,
        "already_checked": 0,
        "selectable": 0,
        "checked_anywhere": 0,
    }

    for movie in movies:
        stats["total"] += 1
        tags = movie.get("tags") or []

        if checked_tag_id in tags:
            stats["checked_anywhere"] += 1

        if movie.get("monitored") is not True:
            stats["not_monitored"] += 1
            continue

        if movie.get("status") != "released":
            stats["not_released"] += 1
            continue

        if ignore_tag_id is not None and ignore_tag_id in tags:
            stats["ignored"] += 1
            continue

        stats["base_eligible"] += 1

        if checked_tag_id in tags:
            stats["already_checked"] += 1
            continue

        stats["selectable"] += 1

    return stats


def get_base_eligible_movies(
    movies: list[dict[str, Any]],
    ignore_tag_id: Optional[int],
) -> list[dict[str, Any]]:
    eligible = []

    for movie in movies:
        if not is_base_eligible(movie, ignore_tag_id):
            continue

        eligible.append(
            {
                "id": int(movie["id"]),
                "title": str(movie.get("title", "Unknown")),
                "year": movie.get("year", "Unknown"),
                "date_added": (movie.get("movieFile") or {}).get("dateAdded"),
            }
        )

    return eligible


def get_selectable_movies(
    movies: list[dict[str, Any]],
    checked_tag_id: int,
    ignore_tag_id: Optional[int],
) -> list[dict[str, Any]]:
    selectable = []

    for movie in movies:
        if not is_selectable(movie, checked_tag_id, ignore_tag_id):
            continue

        selectable.append(
            {
                "id": int(movie["id"]),
                "title": str(movie.get("title", "Unknown")),
                "year": movie.get("year", "Unknown"),
                "date_added": (movie.get("movieFile") or {}).get("dateAdded"),
            }
        )

    return selectable


def get_checked_movie_ids(
    movies: list[dict[str, Any]],
    checked_tag_id: int,
) -> list[int]:
    checked_movie_ids: list[int] = []

    for movie in movies:
        tags = movie.get("tags") or []
        if checked_tag_id in tags:
            checked_movie_ids.append(int(movie["id"]))

    return checked_movie_ids


def select_oldest_movies(candidate_movies: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if not candidate_movies:
        return []

    sorted_movies = sorted(candidate_movies, key=lambda movie: movie.get("date_added") or "")

    return sorted_movies[:count]


def maybe_reset_sweep(
    config: AppConfig,
    logger: logging.Logger,
    client: RadarrClient,
    checked_tag_id: int,
    checked_movie_ids: list[int],
    base_eligible_movies: list[dict[str, Any]],
    selectable_movies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not base_eligible_movies:
        logger.info("Reset condition not met: no base-eligible movies exist")
        return selectable_movies

    if selectable_movies:
        logger.info(
            "Reset condition not met: selectable pool still has %s candidate(s)",
            len(selectable_movies),
        )
        return selectable_movies

    logger.info("Reset condition met: base eligible > 0 and selectable == 0")
    logger.info("Starting automatic sweep reset")
    logger.info(
        "Checked tag currently exists on %s movie(s) across the full library",
        len(checked_movie_ids),
    )

    if not checked_movie_ids:
        logger.info("No movies currently have the checked tag, so there is nothing to remove")
        refreshed_selectable = list(base_eligible_movies)
        logger.info("Post-reset selectable movie objects collected: %s", len(refreshed_selectable))
        return refreshed_selectable

    if config.dry_run:
        logger.info(
            "[DRY RUN] Would remove checked tag '%s' from %s movie(s) across the full library",
            config.checked_tag_name,
            len(checked_movie_ids),
        )
        logger.info("[DRY RUN] Simulating new sweep cycle after reset")
    else:
        logger.info(
            "Removing checked tag '%s' from %s movie(s) across the full library...",
            config.checked_tag_name,
            len(checked_movie_ids),
        )
        client.remove_tag_from_movies(checked_movie_ids, checked_tag_id)
        logger.info("Checked tag removed from all currently checked movie(s)")
        logger.info("Sweep reset complete")
        logger.info("New sweep cycle started within the current run")

    refreshed_selectable = list(base_eligible_movies)
    logger.info("Post-reset selectable movie objects collected: %s", len(refreshed_selectable))
    return refreshed_selectable


def run_once(config: AppConfig, logger: logging.Logger) -> None:
    logger.info("======================================================================")
    logger.info("RUN START %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("======================================================================")
    logger.info("RescanArr Starting")
    logger.info("Radarr URL: %s", config.radarr_url)
    logger.info("Checked tag: %s", config.checked_tag_name)
    logger.info("Ignore tag: %s", config.ignore_tag_name)
    logger.info("Count: %s", config.count)
    logger.info("Dry run: %s", config.dry_run)
    logger.info("Cron: %s", config.cron)

    client = RadarrClient(
        base_url=config.radarr_url,
        api_key=config.api_key,
        timeout=config.request_timeout,
    )

    logger.info("Fetching Radarr tags...")
    tags = client.get_tags()
    logger.info("Fetched %s tag(s)", len(tags))

    checked_tag_id = get_tag_id_by_name(config.checked_tag_name, tags)
    if checked_tag_id is None:
        if config.dry_run:
            logger.info(
                "[DRY RUN] Checked tag '%s' does not exist; would create it",
                config.checked_tag_name,
            )
            checked_tag_id = -999001
        else:
            logger.info(
                "Checked tag '%s' not found; creating it",
                config.checked_tag_name,
            )
            checked_tag_id = client.create_tag(config.checked_tag_name)
            logger.info(
                "Created checked tag '%s' with id=%s",
                config.checked_tag_name,
                checked_tag_id,
            )
    else:
        logger.info(
            "Using existing checked tag '%s' with id=%s",
            config.checked_tag_name,
            checked_tag_id,
        )

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

    stats = compute_stats(movies, checked_tag_id, ignore_tag_id)
    logger.info("Filter summary:")
    logger.info(" Total library movies: %s", stats["total"])
    logger.info(" Excluded - not monitored: %s", stats["not_monitored"])
    logger.info(" Excluded - not released: %s", stats["not_released"])
    logger.info(" Excluded - ignore tag: %s", stats["ignored"])
    logger.info(" Base eligible: %s", stats["base_eligible"])
    logger.info(" Excluded - already checked within base-eligible pool: %s", stats["already_checked"])
    logger.info(" Selectable this cycle: %s", stats["selectable"])
    logger.info(" Checked tag present anywhere in library: %s", stats["checked_anywhere"])

    logger.info("Building sweep pools...")
    checked_movie_ids = get_checked_movie_ids(movies, checked_tag_id)
    base_eligible_movies = get_base_eligible_movies(movies, ignore_tag_id)
    selectable_movies = get_selectable_movies(movies, checked_tag_id, ignore_tag_id)

    logger.info("Checked movie ids collected across full library: %s", len(checked_movie_ids))
    logger.info("Base eligible movie objects collected: %s", len(base_eligible_movies))
    logger.info("Selectable movie objects collected: %s", len(selectable_movies))

    selectable_movies = maybe_reset_sweep(
        config=config,
        logger=logger,
        client=client,
        checked_tag_id=checked_tag_id,
        checked_movie_ids=checked_movie_ids,
        base_eligible_movies=base_eligible_movies,
        selectable_movies=selectable_movies,
    )

    logger.info("Selectable pool after reset evaluation: %s", len(selectable_movies))
    logger.info(
        "Selecting up to %s oldest selectable movie(s) by movie file date...",
        config.count,
    )
    selected_movies = select_oldest_movies(selectable_movies, config.count)

    if not selected_movies:
        logger.info("No selectable movies found. Exiting.")
        logger.info("======================================================================")
        logger.info("RUN END %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("======================================================================")
        return

    logger.info("Selected %s movie(s):", len(selected_movies))
    for movie in selected_movies:
        logger.info(
            " - %s (%s) [id=%s] date_added=%s",
            movie["title"],
            movie["year"],
            movie["id"],
            movie.get("date_added") or "missing",
        )

    if config.dry_run:
        logger.info("[DRY RUN] Would initiate %s search(es)", len(selected_movies))
        logger.info(
            "[DRY RUN] Would apply checked tag '%s' to selected movies",
            config.checked_tag_name,
        )
        logger.info("Dry run complete")
        logger.info("======================================================================")
        logger.info("RUN END %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("======================================================================")
        return

    logger.info("Initiating searches...")
    selected_ids: list[int] = []
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
            selected_ids.append(movie["id"])
    except Exception as exc:
        search_error = exc
        search_error_traceback = exc.__traceback__

    if selected_ids:
        logger.info(
            "Applying checked tag '%s' to %s successfully queued movie(s)...",
            config.checked_tag_name,
            len(selected_ids),
        )
        client.apply_tag_to_movies(selected_ids, checked_tag_id)
        logger.info("Checked tag applied successfully to completed searches")

    if search_error is not None:
        raise search_error.with_traceback(search_error_traceback)

    logger.info("RescanArr Finished")
    logger.info("======================================================================")
    logger.info("RUN END %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("======================================================================")


def main() -> int:
    try:
        initial_config = load_config(CONFIG_PATH)
        logger, log_file = setup_logging(CONFIG_PATH)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    logger.info("Config loaded")
    logger.info("Log file %s", log_file)

    try:
        initial_trigger = CronTrigger.from_crontab(initial_config.cron)
    except Exception as exc:
        logger.error("Invalid cron expression '%s': %s", initial_config.cron, exc)
        return 1

    scheduler = BlockingScheduler()
    state = {"cron": initial_config.cron}

    def run_job() -> None:
        try:
            current_config = load_config(CONFIG_PATH)
            run_once(current_config, logger)
        except requests.HTTPError as exc:
            logger.error("HTTP error: %s", exc)
            if exc.response is not None:
                logger.error("Response status: %s", exc.response.status_code)
                logger.error("Response body: %s", exc.response.text)
        except Exception:
            logger.exception("Run failed")

    def reload_schedule() -> None:
        try:
            cfg = load_config(CONFIG_PATH)
            if cfg.cron != state["cron"]:
                new_trigger = CronTrigger.from_crontab(cfg.cron)
                scheduler.reschedule_job("rescanarr_job", trigger=new_trigger)
                logger.info("Cron updated: %s -> %s", state["cron"], cfg.cron)
                state["cron"] = cfg.cron
        except Exception:
            logger.exception("Config reload failed")

    scheduler.add_job(
        run_job,
        trigger=initial_trigger,
        id="rescanarr_job",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        reload_schedule,
        trigger="interval",
        seconds=60,
        id="config_watcher",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Scheduler started with cron %s", initial_config.cron)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
