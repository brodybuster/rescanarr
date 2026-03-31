# Rescanarr

Rescanarr is a lightweight service that periodically triggers Radarr searches for a rotating subset of movies to determine if there are higher quality movies available.

It is designed for large Radarr libraries where manual rescans or Radarr's built-in search behavior becomes inefficient.

## How It Works

Rescanarr operates using a **cooldown-based backlog search model**.

Each run works like this:

1. Fetch the entire Radarr movie library
2. Identify base-eligible movies
3. Exclude movies newer than `minimum_age_days`
4. Exclude movies whose Radarr `lastSearchTime` is newer than `search_cooldown_days`
5. Prioritize movies that have never been searched, then movies with the oldest `lastSearchTime`
6. Trigger searches for up to `count` movies

This keeps Rescanarr focused on stale backlog searches instead of repeatedly re-searching the same titles too soon.

In practice:

- Fresh library additions are left alone for a while via `minimum_age_days`
- Recently searched movies are cooled down via `search_cooldown_days`
- Older titles with no recent search activity float to the top

## Eligibility Rules

A movie is **base eligible** if:

- `monitored == true`
- `status == released`
- it does **not** have the ignore tag
- its `dateAdded` is at least `minimum_age_days` days old

A movie is **selectable** if:

- it is base eligible
- its `lastSearchTime` is missing, or older than `search_cooldown_days`

## Features

- No WebGUI
- No Bloated features
- Radarr `lastSearchTime` cooldown support
- Ignore tag support
- Cron-based scheduling
- Config reload support
- Non-root container runtime
- Environment variable support (`PUID`, `PGID`, `TZ`)
- File logging with rotation

## Docker Image

Published image:

`ghcr.io/brodybuster/rescanarr`

Example tags:

- `ghcr.io/brodybuster/rescanarr:latest`
- `ghcr.io/brodybuster/rescanarr:0.1.0`

## Quick Start

Create a configuration directory:

```bash
mkdir -p config
```

Create `config/config.yaml`:

```yaml
radarr_url: "http://radarr:7878"
api_key: "YOUR_API_KEY"

ignore_tag_name: "ignore"

count: 3
# Skip movies whose file was added recently.
# Good for letting RSS and normal Radarr behavior handle fresh downloads first.
minimum_age_days: 14
# Skip movies that Radarr has searched recently, even if they are older library items.
# Good for avoiding repeated backfill searches that usually come up empty.
search_cooldown_days: 30
dry_run: false

cron: "*/20 * * * *"

request_timeout: 60
```

## Docker Compose

Example `docker-compose.yml`:

```yaml
services:
  rescanarr:
    image: ghcr.io/brodybuster/rescanarr:latest
    container_name: rescanarr
    restart: unless-stopped

    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: "America/New_York"

    volumes:
      - ./config:/config

    logging:
      driver: json-file
      options:
        max-size: "5m"
```

Start the container:

```bash
docker compose up -d
```

## Environment Variables

| Variable | Description |
|---|---|
| `PUID` | Runtime user ID |
| `PGID` | Runtime group ID |
| `TZ` | Timezone inside the container |

Example:

```yaml
environment:
  PUID: "1000"
  PGID: "1000"
  TZ: "America/New_York"
```

## Configuration

| Option | Description |
|---|---|
| `radarr_url` | Radarr base URL |
| `api_key` | Radarr API key |
| `ignore_tag_name` | Tag used to exclude movies from sweeps |
| `count` | Number of movies selected each run |
| `minimum_age_days` | Minimum age in days that a movie's `dateAdded` must be before it can be searched |
| `search_cooldown_days` | Minimum number of days since Radarr last searched the movie before Rescanarr will search it again |
| `dry_run` | Simulate actions without modifying Radarr |
| `cron` | Cron schedule for sweep runs |
| `request_timeout` | Radarr API timeout in seconds |
