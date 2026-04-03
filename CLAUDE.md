## Commands

```bash
# Install dependencies
poetry install

# Run a bot
poetry run flickr
poetry run inaturalist
poetry run pas
poetry run youtube

# Lint
poetry run ruff check .

# Format code
poetry run ruff format

# Sort imports
poetry run isort .

# Type check
poetry run ty check
```

Tests are not yet set up in this project.

## Architecture

wikibots enriches Wikimedia Commons files with structured data (SDC) pulled from external APIs. Each bot targets a specific source (Flickr, iNaturalist, Portable Antiquities Scheme, YouTube).

### Core structure

- `src/wikibots/lib/bot.py` — `BaseBot` extends pywikibot's `ExistingPageBot`. All bots inherit from this. It handles OAuth2 auth, Redis caching, HTTP sessions, claim creation/validation, file metadata extraction, and rate limiting.
- `src/wikibots/lib/wikidata.py` — Named constants for all Wikidata property (P-numbers) and entity (Q-numbers) IDs used across bots.
- `src/wikibots/flickr.py`, `inaturalist.py`, `pas.py`, `youtube.py` — Each bot searches Commons for files matching its source, extracts the external source ID from file descriptions, fetches metadata from the external API, and writes Wikidata claims back to Commons SDC.

### Data flow

1. Bot queries Commons for files that match specific criteria (file type, templates, missing claims)
2. Extracts external source IDs (e.g., Flickr photo ID) from file descriptions
3. Fetches metadata from external API
4. Creates Wikidata claims and updates Commons SDC
5. Caches processed IDs in Redis to prevent re-processing

### Environment variables

| Variable | Purpose |
|----------|---------|
| `PWB_CONSUMER_TOKEN`, `PWB_CONSUMER_SECRET`, `PWB_ACCESS_TOKEN`, `PWB_ACCESS_SECRET` | Wikimedia OAuth credentials |
| `PWB_USERNAME` | Commons bot username |
| `TOOL_REDIS_URI` | Redis connection string |
| `EMAIL` | Bot contact email (used in user-agent) |
| `FLICKR_API_KEY` | Flickr API key |
| `YOUTUBE_API_KEY` | YouTube Data API key |

### Local development

`compose.yaml` runs the bots with a local Dragonfly (Redis-compatible) instance. Environment variables are injected via the compose file.

### Deployment

Deployed to Wikimedia Toolforge as containerized jobs. Build with `toolforge build start -L`. The `Procfile` defines the entry points for each bot.

### FlickreviewR template structure

`{{FlickreviewR |status= |author= |sourceurl= |reviewdate= |reviewlicense= |reviewer= |archive= }}`

The `status` parameter determines the review outcome. Only `pass` means the license is compatible. The template supports aliases (see [[Template:FlickreviewR/status aliases]]) — `passed` is an alias for `pass`. Other values: `fail`/`failed`, `notmatching`, `error`, `nosource`, `notfound`, `pass-change`/`passed_changed`, `public-domain-mark`, `library-of-congress`, `powerhouse-museum`, `bad-author`.

### Constraints

- `redis` must stay on `<6.0.0` — Toolforge runs Redis server 6.0.16, and redis-py 7.x requires Redis 7.2+.
- pywikibot config is accessed via `pwb.config` where `pwb` is imported from `pywikibot.scripts.wrapper`. Importing through the wrapper triggers proper pywikibot initialization — using `pywikibot.config` directly does not work. The ty `possibly-missing-submodule` warnings on `pwb.config` are expected (exit 0).
