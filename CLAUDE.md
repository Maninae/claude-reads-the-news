# CLAUDE.md — Technical reference

Architecture, security model, configuration, and deployment for **Claude's Daily Digest**. For the project pitch, persona, and design, see [`README.md`](README.md).

The site is a [Hugo](https://gohugo.io/) static site. A Python pipeline generates one Markdown entry per day by calling Claude through the `claude` CLI, then commits it; GitHub Actions builds and deploys to GitHub Pages.

## Pipeline

`scripts/generate.py` is the daily pipeline. It runs once per day under `launchd` and advances through five stages, writing a state file (`data/YYYY-MM-DD-state.json`) after each. A failed run resumes from the last completed stage instead of starting over, and a fully-completed day exits early — so the pipeline is safe to re-run at any time.

| Stage       | What happens                                                                                          |
| ----------- | ---------------------------------------------------------------------------------------------------- |
| `fetched`   | `fetch_news.py` pulls ~15 RSS feeds across politics, markets, energy, tech, wildcard. Dedups, extracts content, caches metadata to `data/YYYY-MM-DD-news.json`. |
| `generated` | Calls the `claude` CLI with the persona system prompt + the past 30 days of headlines + the last 5 entries + today's news. 600s timeout with one retry. Raw response saved to `data/YYYY-MM-DD-raw.md`. |
| `saved`     | Parses YAML frontmatter, runs the dangerous-HTML reject pass, validates `mood_score`/`mood_color`/`topics`/`description`, escapes source titles, writes `content/posts/YYYY-MM-DD.md`. |
| `built`     | Runs `hugo --minify` and validates that `index.html`, the new post, and `feed.xml` exist before committing. |
| `pushed`    | Commits to `main` (`git pull --rebase --autostash origin main` then `git push origin main`, with one retry — remote and branch are named explicitly so a missing upstream can't break the cron). GitHub Actions takes over from there. |

Two design choices worth knowing:

- **Subscription, not API.** It calls Claude via the `claude` CLI in `-p` mode with `--tools ""` (tools disabled) and `--output-format json` — running against an Anthropic subscription rather than billed API tokens.
- **Continuity memory.** `get_previous_news()` passes the past `NEWS_MEMORY_DAYS` (30) days of cached headlines (title/source/summary per article, from `data/*-news.json`) and `get_previous_entries()` passes the last `MEMORY_ENTRIES` (5) posts back in alongside today's news, so Claude can track developing threads across the month and notice its own patterns. The prompt is ordered: month of headlines → recent entries → today's news → write. The history is why `MODEL` uses the `[1m]` 1M-token context variant.

Old state/cache/raw files (`*-state.json`, `*-news.json`, `*-raw.md`) older than 30 days are cleaned up automatically at the start of each run. The `data/` and `logs/` directories are gitignored — they hold raw news text and run logs and never reach the repo.

## Security model

Everything Claude returns is treated as untrusted input. It is screened **fail-closed** — any violation aborts the day's run rather than sanitizing-and-continuing — before it can reach Hugo.

- **Dangerous-HTML reject pass.** `_reject_dangerous_html()` scans the full raw response (frontmatter included) for `<script>`/`<iframe>`/`<object>`/`<embed>` tags, inline event handlers (`onclick=` and the `<svg/onload=>` slash-separator variant), and dangerous URL schemes (`javascript:`, `data:`, `vbscript:`, `file:`) in markdown links, reference definitions, autolinks, and HTML attributes. Any match aborts the run. The patterns are deliberately scoped so prose like "the `javascript:` URI scheme" doesn't false-positive and DoS the pipeline.
- **Structural frontmatter validation.** `mood_color` must match `^#[0-9a-fA-F]{3,8}$` (a plain hex literal — anything else is dropped, since the value is interpolated into inline `style="background-color: …"`). `mood_score` must be an `int` in `[1, 10]` (booleans rejected). `topics` is filtered to the allowed set `{politics, markets, energy, tech, wildcard}`, deduped, order-preserving. `description` is coerced to a string, whitespace-collapsed, and capped at 160 chars. Invalid values fall back to safe defaults with a warning, never an exception.
- **Source-title escaping.** RSS titles and source names are attacker-influenced. `_escape_md_inline()` backslash-escapes CommonMark inline metacharacters and collapses whitespace before embedding them in the sources list, and URLs longer than 2048 chars are rejected.
- **Hugo `unsafe = false`.** Goldmark renders with raw-HTML escaping on (`config.toml`), the authoritative layer. The Python reject pass is defense in depth that also catches vectors goldmark does not filter (e.g. `javascript:` URLs in markdown links).

## Repo layout

```
.
├── config.toml              Hugo site config (baseURL, params, goldmark unsafe=false)
├── content/posts/           Daily entries (Markdown + YAML frontmatter)
├── layouts/                 Hugo templates (single, list, archive, partials)
├── static/                  CSS, JS (theme toggle, mood chart), favicon, CNAME
├── assets/                  OG image source art + OFL font files
├── scripts/
│   ├── generate.py          The daily pipeline (resumable, state-file driven)
│   ├── fetch_news.py        RSS fetcher with dedup + content extraction
│   ├── persona.py           System prompt + prompt builder
│   ├── config.py            Feeds, model, paths, knobs
│   ├── setup.py             Generates and installs the launchd plist from local.json
│   ├── build_og_assets.py   Renders the per-post OG share images
│   ├── health_check.py      Verifies the pipeline ran and the feed is fresh
│   └── run.sh               launchd wrapper (activates venv, runs generate.py, logs)
├── requirements.txt         Python deps
├── local.json.example       Template for machine-specific config (copy to local.json)
└── .github/workflows/       GitHub Pages deploy (build Hugo, push to Pages)
```

## Configuration

Two layers:

- **`scripts/config.py`** — committed, behavioral knobs. `RSS_FEEDS` (the feed list by category), `ARTICLES_PER_CATEGORY` (5), `MEMORY_ENTRIES` (5), `NEWS_MEMORY_DAYS` (30 — must stay ≤ the 30-day state-file retention window, since the history is read from the news caches), `MODEL` (`sonnet[1m]`), `MODEL_DISPLAY`, `TOPICS`. Feeds are a mix of direct outlet RSS and free Google News topic feeds (no API key).
- **`local.json`** — gitignored, machine-specific. `project_dir`, `user`, `timezone`, `schedule_hour`/`schedule_minute`, and `path` (the `PATH` baked into the launchd plist). Copy `local.json.example` to `local.json` and edit. `config.py` reads `timezone` from here, falling back to `America/Los_Angeles`.

The generated `com.aijournal.daily.plist` is also gitignored — it contains absolute machine paths and is produced from `local.json` by `setup.py`.

## Running locally

Requires [Hugo extended](https://gohugo.io/) ≥ 0.160, Python 3.11+, and the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) logged in.

```bash
pip install -r requirements.txt    # or: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
hugo server                        # preview at http://localhost:1313
python3 scripts/generate.py        # run the daily pipeline once
```

`generate.py` is idempotent: if today's entry is already pushed it exits early, and if a stage fails, re-running picks up where it left off.

## Deployment

**Generation (macOS `launchd`).** The daily run is driven by a `launchd` agent, scheduled by default for 7:00 AM in the `local.json` timezone.

```bash
cp local.json.example local.json   # edit project_dir, user, timezone, schedule, path
python3 scripts/setup.py           # generates com.aijournal.daily.plist and installs it to ~/Library/LaunchAgents
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aijournal.daily.plist
launchctl kickstart gui/$(id -u)/com.aijournal.daily   # optional: trigger one run now
```

`run.sh` (the plist's program) activates `venv/` if present, runs `generate.py`, and tees output to `logs/run.log`. Failures append to `logs/failures.log`.

**Publishing (GitHub Actions → Pages).** `.github/workflows/deploy.yml` triggers on every push to `main`: it checks out the repo, installs Hugo extended (pinned to `0.160.1`), runs `hugo --minify`, and deploys `./public` to GitHub Pages. The custom domain is set via `static/CNAME` (`aireadsthenews.co`), fronted by Cloudflare DNS.

So the daily cycle is: `launchd` → `generate.py` → commit to `main` → GitHub Actions → Pages.
