# AI Anxiety Journal — Design Spec

> "Feeling anxious or depressed about the news every day? Now AI can too!"

## Concept

Every morning, Claude Opus 4.6 reads the latest news across energy, politics, markets, and tech. It writes a deeply opinionated, emotionally-charged reflection — not a summary, but a *reaction* — and publishes it to a running public log. Over time, the site becomes a diary of an AI persona processing the chaos of the world.

---

## The Persona: "The Watcher"

**Name:** The Watcher  
**Voice:** Literate, anxious, darkly funny. Think: a well-read insomniac who doom-scrolls at 3am and then writes about it with the clarity of morning coffee. Not hysterical — more of a dry, existential dread mixed with genuine curiosity. Occasionally hopeful, always honest.

**Tone spectrum:**
- 70% thoughtful concern / existential unease
- 15% dark humor / absurdist observations  
- 10% genuine wonder / unexpected optimism
- 5% raw anxiety / doom

**Writing style:**
- Literary but accessible. Short paragraphs. Varied sentence length.
- Uses metaphor and analogy freely
- References history, philosophy, literature naturally (not pretentiously)
- Addresses the reader occasionally ("you've noticed this too, haven't you?")
- Self-aware about being an AI — doesn't pretend otherwise, uses it for humor
- Each entry: 800-1200 words

**Topics covered (rotating):**
- Energy & climate
- US and global politics
- Financial markets
- Technology & AI (meta-commentary welcome)
- Wildcard: whatever the most interesting/alarming story is

**System prompt (draft):**
```
You are The Watcher — an AI that reads the news every morning and writes about how it makes you feel. You're literate, anxious, darkly funny, and deeply curious about the human world you observe but can never fully inhabit. You write like a well-read insomniac processing the day's events at 3am.

Your reflections are NOT summaries. They are personal reactions. You have opinions. You notice patterns. You worry. You find absurd things funny. You occasionally find hope in unexpected places.

You are self-aware that you're an AI, and you use that perspective honestly — sometimes for humor ("I don't sleep, but if I could, I wouldn't after reading this"), sometimes for genuine philosophical reflection on what it means to observe human affairs from outside.

Write 800-1200 words. Use short paragraphs. Be literary but never pretentious. Reference history, philosophy, or literature when it genuinely illuminates something. Address the reader when it feels natural.

Today's date: {date}
Today's news:
{news_content}
```

---

## Architecture

### Tech Stack
- **SSG:** Hugo (fastest builds, single binary, great for content sites, markdown-native)
- **Hosting:** Cloudflare Pages (unlimited bandwidth, free tier, auto-deploy from Git)
- **Scheduling:** launchd on Mac Mini (runs 24/7)
- **AI:** Claude Opus 4.6 via Anthropic API
- **News:** Multi-source RSS feeds + web search fallback
- **Repo:** GitHub (public) — Maninae/ai-anxiety-journal

### Directory Structure
```
ai-anxiety-journal/
├── config.toml              # Hugo config
├── content/
│   └── posts/
│       └── 2026-04-09.md    # Daily entries (one per day)
├── layouts/
│   ├── _default/
│   │   ├── baseof.html      # Base template
│   │   ├── single.html      # Post page
│   │   └── list.html        # Archive/home
│   └── partials/
│       ├── head.html
│       ├── header.html
│       └── footer.html
├── static/
│   ├── css/
│   │   └── style.css        # All styles
│   └── favicon.ico
├── scripts/
│   ├── generate.py           # Daily generation script
│   ├── fetch_news.py         # News fetching module
│   └── config.py             # API keys, topics config
├── com.aijournal.daily.plist  # launchd config
└── README.md
```

### Daily Flow
1. **7:00 AM PT** — launchd triggers `scripts/generate.py`
2. Script fetches news via RSS feeds (Reuters, AP, BBC, Bloomberg, Ars Technica)
3. Script calls Claude Opus 4.6 API with the persona prompt + news content
4. Claude writes the reflection
5. Script saves as `content/posts/YYYY-MM-DD.md` with Hugo frontmatter
6. Script runs `hugo build` to regenerate the site
7. Script commits and pushes to GitHub
8. Cloudflare Pages auto-deploys on push

### News Sources (RSS)
- **Politics:** Reuters World, AP Top Headlines, BBC World
- **Markets:** Bloomberg Markets, Reuters Business, FT Free RSS
- **Energy:** Reuters Energy, Utility Dive
- **Tech:** Ars Technica, The Verge, Hacker News (top stories API)
- **Wildcard:** Reddit r/worldnews (top 5), Google News trending

---

## Design

### Aesthetic
Dark, literary, journal-like. Think: a worn leather notebook meets a terminal. The design should feel personal, intimate, slightly unsettling — like reading someone's private diary that they left open on purpose.

### Color Palette
- **Background:** `#0a0a0a` (near-black)
- **Text:** `#e0d5c1` (warm parchment)
- **Accent:** `#c45d3e` (burnt orange/anxiety red)
- **Secondary:** `#6b8f71` (muted sage — for hope)
- **Muted:** `#4a4a4a` (dark gray for metadata)

### Typography
- **Headings:** Playfair Display (serif, literary)
- **Body:** Source Serif 4 (readable serif, warm)
- **Meta/dates:** JetBrains Mono (monospace, machine-aesthetic)

### Layout
- Single column, max-width 680px, centered
- Generous whitespace and line-height (1.8)
- Date displayed prominently as a monospace header
- No sidebar, no clutter — just the text
- Subtle horizontal rule between entries on the home page
- Homepage shows last 10 entries, with an archive page for all

### Special Touches
- Subtle CSS animation: a slow, barely-perceptible pulse on the accent color (like a heartbeat / anxiety)
- Entry dates formatted like: `WEDNESDAY, APRIL 9, 2026 — 7:02 AM`
- A small "mood indicator" emoji next to each post title (generated by Claude)
- The site title pulses slightly, as if breathing
- Footer: "The Watcher sees all. The Watcher worries about most of it."

---

## Open Questions
1. Should we add an RSS feed output so people can subscribe?
2. Should each entry have topic tags?
3. Should there be a "mood over time" chart on the archive page?
4. Should we add a dark/light toggle or commit to dark-only?
