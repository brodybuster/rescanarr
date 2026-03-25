# Rescanarr

Rescanarr is a lightweight service that periodically triggers Radarr searches for a rotating subset of movies to determine if there are higher quality movies available.

It is designed for large Radarr libraries where manual rescans or Radarr's built-in search behavior becomes inefficient.

Rescanarr guarantees that every eligible movie eventually receives a search without repeatedly searching the same titles within a single sweep cycle.

## How It Works

Rescanarr operates using **sweep cycles**.

A sweep cycle works like this:

1. Fetch the entire Radarr movie library
2. Identify **base eligible movies**
3. Exclude movies already processed in the current cycle
4. Select x number of movies with the oldest dateAdded
5. Trigger Radarr searches
6. Apply a `checked` tag
7. Continue until no selectable movies remain
8. Remove `checked` from all currently checked movies
9. Immediately start a new sweep cycle

This guarantees:

- No repeated searches during a cycle
- Full coverage of eligible movies
- Automatic cycle reset
- Continuous operation

## Eligibility Rules

A movie is **base eligible** if:

- `monitored == true`
- `status == released`
- it does **not** have the ignore tag
- its `dateAdded` is at least `min_age` days old

A movie is **selectable** if:

- it is base eligible
- it does **not** have the checked tag

When the selectable pool reaches zero while base eligible movies still exist, Rescanarr resets the sweep by removing the checked tag from **all currently checked movies in the library**, then continues in the same run.

## Features

- No WebGUI
- No Bloated features
- Automatic sweep reset
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

checked_tag_name: "checked"
ignore_tag_name: "ignore"

count: 3
min_age: 0
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
| `checked_tag_name` | Tag used to track sweep progress |
| `ignore_tag_name` | Tag used to exclude movies from sweeps |
| `count` | Number of movies selected each run |
| `min_age` | Minimum age in days that a movie's `dateAdded` must be before it can be searched |
| `dry_run` | Simulate actions without modifying Radarr |
| `cron` | Cron schedule for sweep runs |
| `request_timeout` | Radarr API timeout in seconds |
