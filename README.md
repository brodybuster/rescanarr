<p align="center">
  <img src="assets/rescanarr_logo.png" width="130">
</p>

# RescanArr

RescanArr is a lightweight automation service that periodically triggers Radarr searches across a movie library using a randomized sweep model.

Instead of relying solely on Radarr’s built-in upgrade behavior, RescanArr continuously cycles through the library and re-checks movies for potential upgrades. Movies that are searched are tagged so they are not selected again during the same sweep cycle. Once the entire library has been processed, the checked tag is removed, and the sweep starts over.

---

# Docker Image

A prebuilt Docker image is **not yet available**.

For now, the container must be built locally using the provided Dockerfile.

```bash
git clone https://github.com/brodybuster/rescanarr.git
cd rescanarr
docker build -t rescanarr .
```

An official container image will be published in a future release.

---

# Features

Current capabilities implemented in RescanArr:

- Docker Container Deployment with PUID:GUID:TZ
- Radarr API integration
- Randomized upgrade sweep across the library
- Tag-based tracking of processed movies
- Ignore tag support to permanently exclude movies
- Configurable sweep size (`count`)
- Cron-based scheduling
- Persistent logging to disk
- Dry-run mode for testing

---

# How It Works

Each scheduled run performs the following steps:

1. Fetch all movies from Radarr  
2. Filter movies that are eligible to participate in the sweep  
3. Randomly select a subset of those movies  
4. Trigger a Radarr search for each selected movie  
5. Apply a `checked` tag so the movie is not searched again during the same sweep cycle  
6. Remove `checked` tag after entire library has been processed

This creates a rolling upgrade sweep across the entire library.

---

# Eligibility Model

RescanArr uses a two-stage filtering model.

## Base Eligible

Movies that can participate in the sweep at all.

Rules:

- `monitored == true`
- `status == released`
- not tagged with the configured ignore tag

## Selectable

Movies that can be selected during the current sweep cycle.

Rules:

- base eligible
- not tagged with the `checked` tag

Only selectable movies are randomly chosen for search.

---

# Planned Features

The following features are planned but not yet implemented.

## Sonarr Integration

Future versions of RescanArr are planned to support **Sonarr**, enabling the same randomized upgrade sweep behavior for television series.

This will allow periodic searches for eligible series and episodes using the same tag-based sweep model currently used for Radarr movies.

---

# Configuration

Example `config.yaml`:

```yaml
radarr_url: "http://radarr:7878"
api_key: "YOUR_API_KEY"

checked_tag_name: "checked"
ignore_tag_name: "ignore"

count: 10
dry_run: true

cron: "0 * * * *"

request_timeout: 60
```

---

# Logging

Logs are written to:

```
/config/logs/rescanarr_YYYY-MM-DD.log
```

and also output to container stdout.

---

# License

MIT
