# RescanArr

RescanArr is a lightweight service that periodically triggers Radarr searches across your movie library to discover upgrades.

Instead of relying only on Radarr’s built-in upgrade logic, RescanArr continuously sweeps the library and forces searches for randomly selected eligible movies.

The goal is to gradually rescan the entire library over time and discover higher quality releases.

---

## How It Works

On a scheduled interval RescanArr will:

1. Fetch all movies from Radarr  
2. Determine which movies are eligible  
3. Randomly select a configured number of movies  
4. Trigger a Radarr search for each selected movie  
5. Tag those movies as `checked` so they are not searched again in the same sweep

Over time this performs a rolling scan across the entire library.

---

## Eligibility Rules

A movie is **base eligible** if:

- `monitored = true`
- `status = released`
- it does **not** have the `ignore` tag

A movie is **selectable** if:

- it is base eligible
- it does **not** have the `checked` tag

Only selectable movies are chosen for searches.
