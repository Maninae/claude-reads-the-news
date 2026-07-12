# CLAUDE.md — Technical reference

Architecture, security model, configuration, and deployment for **Claude's Daily Digest**. For the project pitch, persona, and design, see [`README.md`](README.md).

The site is a [Hugo](https://gohugo.io/) static site publishing six parallel daily digests, one per **reader profile** (the site's tabs): International, USA, Tech, Energy, Markets, Wildcard. A Python pipeline generates one Markdown entry per profile per day by calling Claude through the `claude` CLI, commits them together, and GitHub Actions builds and deploys to GitHub Pages.

## Reader profiles

A profile bundles everything one desk needs. `scripts/profiles.py` holds the frozen `ReaderProfile` registry (slug, tab label, description, feeds dict, allowed topics, prompt filename) in tab order. Each profile owns:

- **Feeds and queries** (`feeds`): direct outlet RSS plus Google News topic feeds and `rss/search?q=...+when:1d` queries, grouped into sub-categories.
- **A persona brief** at `prompts/profiles/<slug>.md`, inserted into `prompts/persona_base.md` at `{{PROFILE_BRIEF}}`; the profile's allowed topics fill `{{TOPICS_LIST}}`. `persona.load_system_prompt(profile)` composes the three and fails loud on a missing file or placeholder. Briefs include stay-in-lane handoffs to sibling desks so the six digests diverge instead of all covering the day's top story.
- **A Hugo section** `content/<slug>/` with an `_index.md` carrying its label and description (this keeps an empty section rendering instead of 404ing, and per-section RSS at `/<slug>/feed.xml` comes free).
- **Its own continuity memory**: the last `MEMORY_ENTRIES` posts from its section and the last `NEWS_MEMORY_DAYS` days of its own news caches. Desks never see each other's history.

The tab bar renders from `[[params.reader_profiles]]` in `config.toml`. At startup `scripts/toml_check.py` verifies the (slug, label, description) triples and their order match the registry exactly, refusing to run otherwise and printing the exact TOML block to paste.

**To add a profile**: add a `ReaderProfile` to the registry, write `prompts/profiles/<slug>.md`, add the matching `[[params.reader_profiles]]` block to `config.toml`, create `content/<slug>/_index.md` with the label and description. Nothing else changes.

`content/posts/` is the frozen legacy section (the original single digest, April to July 2026). It is never written again and stays reachable through the archive.

## Pipeline

`scripts/generate.py` runs once per day under `launchd`. Each profile advances through `fetched -> generated -> saved` sequentially and independently; then the site-wide `built` and `pushed` stages run once over everything that landed.

| Stage       | Scope       | What happens |
| ----------- | ----------- | ------------ |
| `fetched`   | per profile | `fetch_news.fetch_all_news(profile.feeds)` pulls that desk's feeds. Dedups, extracts content, screens for prompt injection, caches to `data/YYYY-MM-DD__<slug>-news.json`. |
| `generated` | per profile | Calls the `claude` CLI with the profile's composed system prompt + that desk's 30-day headline memory + its last 5 entries + today's news. 600s timeout with one retry. Raw response saved to `data/YYYY-MM-DD__<slug>-raw.md`. |
| `saved`     | per profile | Parses YAML frontmatter, runs the dangerous-HTML reject pass, validates `mood_score`/`mood_color`/`description` and `topics` against the profile's allowed set, escapes source titles, stamps `profile: <slug>`, writes `content/<slug>/YYYY-MM-DD.md`. |
| `built`     | site-wide   | Runs `hugo --minify` once and validates `index.html`, `feed.xml`, and each shipped profile's rendered entry. |
| `pushed`    | site-wide   | Stages `content/`, commits as `entry: YYYY-MM-DD (slug, slug, ...)`, then `git pull --rebase --autostash origin main` and `git push origin main` with retry. GitHub Actions takes over from there. |

State lives in one file per day, `data/YYYY-MM-DD-state.json`:

```json
{"profiles": {"international": "generated", "tech": "saved"}, "built": ["tech"], "pushed": [], "updated": "..."}
```

- Per-profile resume: a rerun skips any profile already at or past the requested stage.
- `built`/`pushed` record the slug set they last covered, so a rerun that lands a new profile reruns them over the larger set. Both are idempotent to rerun.
- A state file in the pre-profile single-stage shape marks a completed legacy day; the run exits early.

**Failure isolation**: a profile failing any stage logs to `logs/failures.log` and the run continues with the rest; the day ships with whoever succeeded. Exit is non-zero only when zero profiles ship.

**CLI flags**: `--profiles tech,usa` runs a subset; `--until <stage>` stops after that stage (e.g. `--until built` for a no-push test run).

Old state/cache/raw files older than 30 days are cleaned up at the start of each run; the cleanup pattern covers both the current `YYYY-MM-DD__<slug>-*` naming and the legacy un-suffixed naming. `data/` and `logs/` are gitignored and never reach the repo.

Two design choices worth knowing:

- **Subscription, not API.** It calls Claude via the `claude` CLI in `-p` mode with `--tools ""` (tools disabled) and `--output-format json`, running against an Anthropic subscription rather than billed API tokens.
- **Continuity memory is per desk.** Each profile's prompt is ordered: its month of headlines, its recent entries, today's news, write. The history is why `MODEL` uses the `[1m]` 1M-token context variant.

## Security model

Everything Claude returns is treated as untrusted input. It is screened **fail-closed**, per profile, before it can reach Hugo. Any violation aborts that profile's day (the other desks still ship).

- **Dangerous-HTML reject pass.** `_reject_dangerous_html()` scans the full raw response (frontmatter included) for `<script>`/`<iframe>`/`<object>`/`<embed>` tags, inline event handlers (`onclick=` and the `<svg/onload=>` slash-separator variant), and dangerous URL schemes (`javascript:`, `data:`, `vbscript:`, `file:`) in markdown links, reference definitions, autolinks, and HTML attributes. Any match aborts the profile. The patterns are deliberately scoped so prose like "the `javascript:` URI scheme" doesn't false-positive and DoS the pipeline.
- **Structural frontmatter validation.** `mood_color` must match `^#[0-9a-fA-F]{3,8}$` (a plain hex literal, since the value is interpolated into inline `style="background-color: ..."`). `mood_score` must be an `int` in `[1, 10]` (booleans rejected). `topics` is filtered to the profile's allowed set, deduped, order-preserving. `description` is coerced to a string, whitespace-collapsed, and capped at 160 chars. Invalid values fall back to safe defaults with a warning, never an exception.
- **Source-title escaping.** RSS titles and source names are attacker-influenced. `_escape_md_inline()` backslash-escapes CommonMark inline metacharacters and collapses whitespace before embedding them in the sources list, and URLs longer than 2048 chars are rejected.
- **Hugo `unsafe = false`.** Goldmark renders with raw-HTML escaping on (`config.toml`), the authoritative layer. The Python reject pass is defense in depth that also catches vectors goldmark does not filter (e.g. `javascript:` URLs in markdown links).
- **No client-side DOM from data.** Frontend JS (`feed.js`, `profile-tabs.js`) only toggles or clones server-rendered nodes; nothing is assembled from JSON or strings, because titles are attacker-influenced.

## Repo layout

```
.
├── config.toml              Hugo site config (baseURL, [[params.reader_profiles]], goldmark unsafe=false)
├── content/
│   ├── <slug>/              One section per profile (international, usa, tech, energy, markets, wildcard)
│   │   ├── _index.md        Section label + description (keeps empty sections rendering)
│   │   └── YYYY-MM-DD.md    Daily entries (Markdown + YAML frontmatter, `profile:` stamped)
│   └── posts/               Frozen legacy section (the original single digest)
├── prompts/
│   ├── persona_base.md      Shared voice/security/output prompt with {{PROFILE_BRIEF}} and {{TOPICS_LIST}}
│   └── profiles/<slug>.md   Per-desk persona briefs
├── layouts/                 Hugo templates (index = today-across-tabs overview, list = per-section, archive, partials)
├── static/                  CSS, JS (theme toggle, feed batching, tab marquee), favicon, CNAME
├── assets/                  OG image source art + OFL font files
├── scripts/
│   ├── generate.py          The daily pipeline (per-profile stages + shared build/push)
│   ├── profiles.py          The ReaderProfile registry (tab order lives here)
│   ├── fetch_news.py        RSS fetcher, parameterized by a profile's feeds dict
│   ├── persona.py           System-prompt loader (base + brief + topics) and prompt builder
│   ├── pipeline_state.py    Per-profile resumable state file handling
│   ├── data_paths.py        Per-profile data file naming + two-schema cleanup
│   ├── toml_check.py        Startup validation: config.toml profiles == registry
│   ├── config.py            Model, paths, global knobs
│   ├── setup.py             Generates and installs the launchd plist from local.json
│   ├── build_og_assets.py   Renders the OG share-image assets
│   ├── health_check.py      Per-profile freshness + feed health + failure log
│   └── run.sh               launchd wrapper (activates venv, runs generate.py, logs)
├── docs/                    Operational guides (Cloudflare hardening)
├── requirements.txt         Python deps
├── local.json.example       Template for machine-specific config (copy to local.json)
└── .github/workflows/       GitHub Pages deploy (build Hugo, push to Pages)
```

## Configuration

Three layers:

- **`scripts/profiles.py`** — committed. The registry: who the desks are, their feeds/queries, topics, and prompt files. This is where editorial shape lives.
- **`scripts/config.py`** — committed, global knobs. `MODEL` (`sonnet[1m]`), `MODEL_DISPLAY`, `ARTICLES_PER_CATEGORY` (5), `MEMORY_ENTRIES` (5), `NEWS_MEMORY_DAYS` (30, must stay ≤ the 30-day cleanup window), paths.
- **`local.json`** — gitignored, machine-specific. `project_dir`, `user`, `timezone`, `schedule_hour`/`schedule_minute`, and `path` (the `PATH` baked into the launchd plist). Copy `local.json.example` and edit.

The generated `com.aijournal.daily.plist` is gitignored; `setup.py` produces it from `local.json`.

## Running locally

Requires [Hugo extended](https://gohugo.io/) ≥ 0.160, Python 3.11+, and the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) logged in.

```bash
pip install -r requirements.txt    # or: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
hugo server                        # preview at http://localhost:1313
python3 scripts/generate.py                          # full daily run, all profiles
python3 scripts/generate.py --profiles tech --until saved   # one desk, no build/push
```

`generate.py` is idempotent: completed profiles and stages are skipped on rerun, and a failed profile picks up where it left off.

## Deployment

**Generation (macOS `launchd`).** The daily run is driven by a `launchd` agent, scheduled by default for 7:00 AM in the `local.json` timezone.

```bash
cp local.json.example local.json   # edit project_dir, user, timezone, schedule, path
python3 scripts/setup.py           # generates com.aijournal.daily.plist and installs it to ~/Library/LaunchAgents
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aijournal.daily.plist
launchctl kickstart gui/$(id -u)/com.aijournal.daily   # optional: trigger one run now
```

`run.sh` (the plist's program) activates `venv/` if present, runs `generate.py`, and tees output to `logs/run.log`. Failures append to `logs/failures.log`.

**Publishing (GitHub Actions → Pages).** `.github/workflows/deploy.yml` triggers on every push to `main`: it checks out the repo, installs Hugo extended (pinned to `0.160.1`), runs `hugo --minify`, and deploys `./public` to GitHub Pages. The custom domain is set via `static/CNAME` (`aireadsthenews.co`), fronted by Cloudflare DNS (edge settings documented in `docs/cloudflare-hardening.md`).

So the daily cycle is: `launchd` → `generate.py` (six desks) → one commit to `main` → GitHub Actions → Pages.
