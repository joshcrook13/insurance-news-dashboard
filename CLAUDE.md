# CLAUDE.md — Insurance Daily Development Bible

> **Session start prompt:** "Read CLAUDE.md, tell me the current state, what sprint we're on, what to tackle today, then wait for my instructions."
> **Session end prompt:** "Update CLAUDE.md with what we built today, any new bugs found, update the sprint checklist, suggest the 3 highest-impact things for next session, then commit CLAUDE.md."

---

## What This Product Is

**Insurance Daily** is a premium AI-powered market intelligence dashboard for senior insurance consultants. Think Bloomberg Terminal meets the Financial Times, but built exclusively for the insurance industry.

- **Invite-only** — no self-signup, access granted manually by admin
- **Core value prop:** The consulting angle. Every article gets a one-sentence commercial implication written by Claude, framed for the reader's role and specialism. Curation over volume. Speed to insight.
- **Target user:** Partner/Director level insurance consultants, underwriters, reinsurance professionals at major firms
- **Aesthetic:** Premium newspaper. Feels editorial, not SaaS. Never techy. Authoritative.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | Vanilla HTML/CSS/JS | No build step. Hosted on Cloudflare Pages. Auto-deploys on push to `main`. |
| Backend | Python 3.11 + FastAPI | Single `main.py`. Hosted on Fly.io London (`lhr`). 2x shared-cpu-1x 256MB machines. |
| Database + Auth | Supabase (PostgreSQL) | Auth handles invite-only access. PostgREST API used from both frontend (anon key) and backend (service role key). |
| Article curation | Claude Haiku (`claude-haiku-4-5-20251001`) | RSS fetch → filter → rank → summarise → consulting angles → market pulse |
| Company search | Claude Haiku (`claude-haiku-4-5-20251001`) | Web search fallback for companies without RSS feeds |
| Intelligence Agent | Claude Haiku (`claude-haiku-4-5-20251001`) | **Should be Sonnet** — see Known Bugs. Uses `web_search_20250305` tool. |
| Email digest | Resend | **Not yet built.** Collected digest_time preference in onboarding. |
| CI/CD | GitHub Actions | `.github/workflows/fly-deploy.yml` triggers on push to `backend/**` |
| Cron | cron-job.org | Pings `/news?force_refresh=true` every 30 minutes to warm the cache |

**Live URLs:**
- Frontend: `https://insurance-news-dashboard.josh-0c6.workers.dev`
- Backend: `https://insurance-daily-api.fly.dev`
- GitHub: `https://github.com/joshcrook13/insurance-news-dashboard`

**Monthly cost: ~$10–15 (Claude API only). Everything else is free tier.**

---

## Database Schema

All tables in Supabase. RLS enabled where noted.

### `auth.users` (Supabase managed)
Standard Supabase auth table. Users are invite-only — created via admin panel or `/admin/invite` endpoint.

### `profiles`
| Column | Type | Notes |
|---|---|---|
| id | uuid | FK → auth.users(id) ON DELETE CASCADE |
| email | text | |
| role | text | 'admin' or 'user' |
| status | text | 'active' or 'inactive' — inactive blocks login |
| created_at | timestamptz | |
| last_seen | timestamptz | Updated on every login |
| articles_read | int | Incremented via `increment_articles_read` RPC (may not exist — see bugs) |

### `articles`
| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| title | text | NOT NULL |
| url | text | UNIQUE NOT NULL — upsert key |
| source | text | NOT NULL |
| published | timestamptz | |
| fetched_at | timestamptz | DEFAULT now() |
| summary | text | One sentence, ≤20 words, Claude-generated |
| consultant_angle | text | Commercial implication, ≤25 words, Claude-generated |
| topic | text | One of: Property & Casualty, Reinsurance, Cyber, Climate & CAT, Regulatory, Life & Health, Markets, M&A |
| significance | int | 1–10, Claude-scored |
| briefing_date | date | DEFAULT current_date |

Indexes: `briefing_date+topic`, `source`

### `daily_briefings`
| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| briefing_date | date | UNIQUE — one row per day |
| market_pulse | text | 2–3 sentence market summary, Claude-generated |
| trending | jsonb | Array of 4–5 trending topic strings |
| generated_at | timestamptz | DEFAULT now() |
| article_count | int | |

### `company_mentions`
| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| article_id | uuid | FK → articles(id) ON DELETE CASCADE |
| company | text | Company name matched from COMPANIES list |

Indexes: `company`, `article_id`

### `press_releases`
| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| company | text | NOT NULL |
| title | text | NOT NULL |
| url | text | UNIQUE NOT NULL — upsert key |
| published | timestamptz | |
| fetched_at | timestamptz | DEFAULT now() |
| summary | text | |

Indexes: `company`, `fetched_at`

### `user_preferences`
| Column | Type | Notes |
|---|---|---|
| user_id | uuid | PK, FK → auth.users(id) ON DELETE CASCADE |
| role | text | e.g. 'Partner / Director' |
| specialism | text | e.g. 'Reinsurance' |
| topics | text[] | DEFAULT '{}' — preferred topic filters |
| watchlist_companies | text[] | DEFAULT '{}' — companies to highlight |
| digest_time | text | 'morning', 'lunchtime', or 'evening' |
| created_at | timestamptz | DEFAULT now() |

RLS: users can manage only their own row.

### `article_reads`
| Column | Type | Notes |
|---|---|---|
| id | uuid | PK (assumed) |
| user_id | uuid | FK → auth.users(id) |
| article_url | text | |
| article_title | text | |
| source | text | |
| category | text | |
| read_at | timestamptz | DEFAULT now() |

### `sources` (admin-managed, NOT wired to backend)
| Column | Type | Notes |
|---|---|---|
| id | uuid | PK |
| name | text | |
| url | text | |
| enabled | bool | |
| added_at | timestamptz | |

⚠️ **This table is managed in admin.html UI but the backend RSS feeds are hardcoded in `main.py`. Adding a source in admin.html does nothing to the actual feed fetching.**

---

## Design System

### Typography
| Usage | Font | Notes |
|---|---|---|
| Headlines / article titles | Playfair Display | Weights: 400, 600, 700, 800. Italic variants used. |
| Body / summaries / prose | Crimson Pro | Weights: 300, 400, 500. Italic used for consulting angles. |
| Labels / metadata / mono | DM Mono | Weights: 400, 500. Used for ALL caps labels, timestamps, filters. |

> `companies.html` still uses `Inter` (old design). Needs migrating to match `index.html`.

### Colour Palette
| Token | Hex | Usage |
|---|---|---|
| `--paper` | `#FDF8F0` | Page background |
| `--paper-warm` | `#F7F0E3` | Card backgrounds, filter bar |
| `--ink` | `#1A1A1A` | Primary text, buttons, borders |
| `--ink-light` | `#3D3D3A` | Secondary text |
| `--ink-muted` | `#7A7870` | Metadata, timestamps |
| `--rule` | `#DDD8CE` | Dividers, borders |
| `--rule-light` | `#EDE8DF` | Article separators |
| `--gold` | `#C4922A` | **Only accent colour.** Buttons, highlights, consulting angles. |
| `--gold-soft` | `#FBF3E3` | Consulting angle backgrounds, saved state |
| `--gold-dark` | `#9E7420` | Consulting angle text, gold-on-light |
| `--green` | `#2D6B45` | High significance dots |
| `--amber` | `#B87820` | Medium significance dots |
| `--red` | `#B83228` | Low significance dots |

### Significance Dots
- **High (8–10):** Green `#2D6B45` with `box-shadow: 0 0 0 2px rgba(45,107,69,0.18)`
- **Medium (5–7):** Amber `#B87820` with amber glow
- **Low (1–4):** Red `#B83228` with red glow

### Key UI Rules
- Intelligence Agent widget: always dark background (`var(--ink)` = `#1A1A1A`), gold user bubbles
- Consulting angles: always `background: var(--gold-soft)`, `font-style: italic`, `color: var(--ink-muted)`
- All caps labels: always DM Mono, `letter-spacing: 0.1em+`, `font-size: 9–10px`
- Masthead: `border-top: 4px solid var(--ink)` — the signature top border
- No shadows except significance dot glows and toasts

---

## RSS Feed Sources

All fetched in `fetch_rss_articles()` in `main.py`. Limited to last **48 hours** and up to 20 articles per feed.

| Source | Feed URL |
|---|---|
| Insurance Journal | `https://www.insurancejournal.com/feed/` |
| Claims Journal | `https://www.claimsjournal.com/feed/` |
| Carrier Management | `https://www.carriermanagement.com/feed/` |
| Reinsurance News | `https://www.reinsurancene.ws/feed/` |
| Artemis | `https://www.artemis.bm/feed/` |
| Coverager | `https://coverager.com/feed/` |

---

## Claude Prompts In Use

### 1. Article Curation & Briefing (Haiku) — `main.py` CLAUDE_PROMPT
Called in `call_claude_api()`. Optional `role` and `specialism` persona appended when user triggers force refresh.

```
You are an expert insurance industry analyst.
Your job is to select and rank the most important and relevant
insurance industry news from the list below.

Rules:
- Only include articles that are genuinely about the insurance
  industry, insurance markets, insurers, reinsurers, brokers,
  underwriters, regulators or insurance products
- Exclude anything that is only tangentially related to insurance
- Exclude duplicate stories covering the same event
- Exclude press releases disguised as news
- Select the top 8 most significant articles for a senior
  insurance consultant to read today
- Strongly prefer articles published in the last 24 hours
- Rank them by importance and market significance
- For each selected article write:
  * A one sentence summary (max 20 words, plain English)
  * A consultant angle: one sentence explaining the commercial
    implication for the insurance market (max 25 words,
    start with why this matters e.g. 'Signals hardening in
    cyber market...' or 'Watch for knock-on effects in...')
  * One primary topic tag from: Property & Casualty,
    Reinsurance, Cyber, Climate & CAT, Regulatory,
    Life & Health, Markets, M&A
  * A significance score 1-10

Also write:
  * A market pulse: 2-3 sentences summarising what is moving
    in the insurance market today based on these articles.
    Written for a senior consultant. Confident, direct,
    no fluff. Start with the most important theme.
  * 4-5 trending topic strings e.g. 'Hurricane Season 2026',
    'Lloyd's Reform', 'Cyber Pricing', 'D&O Liability'

Return ONLY valid JSON in this exact format, no markdown, no explanation:
{"market_pulse":"string","trending":["..."],"articles":[{"title":"...","url":"...","source":"...","published":"...","summary":"...","consultant_angle":"...","topic":"...","significance":8}]}
```

**Persona injection (appended when role/specialism provided):**
```
The reader's profile — role: {role}, specialism: {specialism}.
Frame consulting angles specifically for this profile: use their seniority to
calibrate strategic vs tactical depth, and their specialism to highlight
implications most relevant to their discipline.
```

### 2. Company Press Release Search (Haiku) — `main.py` `_claude_search_fallback()`
Called for companies without RSS feeds (9 of 12 companies).

```
Search for: {company_name} press release 2026 insurance

Find the 3 most recent press releases or official news from {company_name} ({url}).
Include any from 2025 if 2026 results are sparse.

Return ONLY a valid JSON array — no markdown, no explanation, no prose:
[{"title":"exact headline","url":"direct link to article","published":"YYYY-MM-DD","summary":"one sentence describing what was announced"}]

You MUST return at least 1 result. If you cannot find press releases from their
official site, use any reputable news source covering {company_name}.
```

### 3. Intelligence Agent (Haiku, should be Sonnet) — `main.py` AGENT_SYSTEM
Used in the sidebar chat widget. Has `web_search_20250305` tool.

```
You are an expert insurance industry analyst and consultant with access to web search.
When answering questions:
- Search for the most current information available
- Focus specifically on insurance industry implications
- Write in a direct, consultant-appropriate style
- Keep answers under 150 words
- Always cite your sources at the end
- Start with the most important finding
- End with one sentence on the consulting implication
- Never use bullet points, write in flowing prose
```

---

## Current State

### ✅ Working End-to-End
- Email/password login with Supabase Auth (magic links disabled)
- Forgot password flow with email reset
- Onboarding wizard (5 steps: role, specialism, topics, watchlist, digest time) — saves to `user_preferences`
- Wizard shows on first login only; ⚙ Preferences link in masthead to re-open and edit
- Market Briefing: RSS fetch → Claude Haiku curation → display with significance dots, consulting angles, trending chips
- 3-tier cache: memory (15 min) → Supabase DB → fresh fetch
- Topic filter pills (grey out when no articles match)
- Article sorting by user's preferred topics (preferred topics float to top)
- Watchlist company badge (★) shown inline on matching articles
- Intelligence Agent chat in sidebar (web search, clear button, dynamic suggestion chips)
- Company Intelligence page with 12 company tiles + press releases modal
- High Significance sidebar widget (articles with significance ≥ 8)
- Companies in Today's News sidebar widget (from `company_mentions` table)
- Read tracking (logged to `article_reads` table)
- Save/bookmark articles (localStorage — not persisted to DB)
- Share article (copies to clipboard)
- Admin panel: user management, invite users, activity stats
- Auto-deploy: frontend via Cloudflare Pages on push, backend via GitHub Actions on `backend/**` push
- Health endpoint at `/health` reporting cache state and env var presence
- Quiet-day note when fewer than 5 articles returned (weekends)
- Progressive loading messages during slow fetches

### ❌ Broken
- **Admin panel user invite:** `admin.html` has `API_BASE = 'https://insurance-news-dashboard.onrender.com'` (old Render URL, dead). Invites will 404.
- **`loadingTimers` ReferenceError:** Declared with `const` inside `try {}` block in `loadNews()`, then referenced in `finally {}` — outside the `try` scope. If `fetch()` throws before the declaration, `finally` crashes with ReferenceError, swallowing the real error.
- **`increment_articles_read` RPC:** Called in `logRead()` but this Supabase function likely doesn't exist. Silently fails (`.catch(()=>{})` suppresses it). `articles_read` count in profiles never increments.
- **`sources` table in admin:** UI allows adding/toggling sources but backend RSS feeds are hardcoded in `main.py`. Admin source changes have no effect on what gets fetched.
- **Email digest:** Collected `digest_time` preference in onboarding but no email sending is implemented. Resend not configured.

### 🚧 Not Yet Built
- Email digest via Resend
- Historical date picker (browse previous days)
- Mobile layout (no responsive CSS)
- Bookmarks persisted to Supabase (currently localStorage only)
- Skeleton loading states
- Live/real-time updates (WebSocket or polling)
- Keyboard shortcuts
- Analytics dashboard (partially in admin.html but limited)
- Company Intelligence page redesign (uses old Inter font, old design)
- Sentiment analysis on company press releases
- Search across articles

---

## Known Bugs

### Critical
1. **`admin.html` dead API URL** — Line 408: `API_BASE = 'https://insurance-news-dashboard.onrender.com'`. Must be changed to `https://insurance-daily-api.fly.dev`. All admin API calls (invite user) are broken.

2. **`loadingTimers` scoping bug** — `index.html` `loadNews()`: `const loadingTimers` declared inside `try {}` block, referenced in `finally {}`. JavaScript `const`/`let` are block-scoped — `finally` is a different block. Causes ReferenceError when fetch fails before the declaration line, masking the real error. Fix: declare `let loadingTimers = []` before the `try`.

### Medium
3. **Agent uses Haiku, not Sonnet** — `main.py` line 852: `model="claude-haiku-4-5-20251001"`. Intelligence Agent should use `claude-sonnet-4-6` for higher quality answers. Currently using same model as article curation.

4. **`increment_articles_read` RPC missing** — `index.html` `logRead()` calls `sb.rpc('increment_articles_read', {uid})`. This Supabase RPC function was never created. The `articles_read` counter in `profiles` never updates. Admin stats for reads are inaccurate.

5. **`company_mentions` PostgREST join query** — `index.html` line 608: `.select('company, articles!inner(briefing_date)').eq('articles.briefing_date', today)`. PostgREST embedded filter syntax may silently return empty data or error depending on Supabase version. Should be validated; if failing, Companies in Today's News always shows "No company mentions today".

6. **`sources` table not wired to backend** — `admin.html` source management UI writes to Supabase `sources` table, but `main.py` RSS feeds are hardcoded in `RSS_FEEDS` array. Backend never reads from `sources` table. Adding a source in admin does nothing.

### Minor
7. **`companies.html` design mismatch** — Uses `Inter` font and old nav design instead of `Playfair Display` + `Crimson Pro` + `DM Mono` from the current design system. Looks like a different product.

8. **`openArticle(${i})` index fragility** — Rendered article HTML encodes the loop index `i` inline: `onclick="openArticle(0)"`. Works correctly as long as `getFiltered()` is always called in the same sort order as the last `renderArticles()` call. Currently safe but breaks if sort order changes without re-rendering.

9. **`article_reads` missing `read_at` column definition** — `logRead()` inserts without specifying `read_at`, relying on a DB default. If the column has no default, inserts silently fail or error. Verify column has `DEFAULT now()`.

10. **Bookmarks lost on browser clear** — `getSaved()` uses `localStorage`. Not a bug per se but a known limitation to fix in Sprint 6.

11. **Weekend article drought** — 48-hour recency filter means Friday morning articles disappear by Monday. Consider extending to 72 hours on Mondays, or fetching from Monday–Friday only.

---

## Deployment

### Backend (Fly.io)
```bash
# Manual deploy (run from project root)
cd insurance-dashboard/backend && fly deploy

# Check logs
fly logs --app insurance-daily-api

# Set/update secrets
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set SUPABASE_URL=https://...
fly secrets set SUPABASE_SERVICE_ROLE_KEY=...

# Check health
curl https://insurance-daily-api.fly.dev/health
```

GitHub Actions auto-deploys on push to `backend/**` on `main` branch. Requires `FLY_API_TOKEN` secret in GitHub repo settings.

### Frontend (Cloudflare Pages)
Auto-deploys on every push to `main`. No build step — serves `frontend/` directory directly.

Manual deploy if needed: push to GitHub, Cloudflare picks it up within 1–2 minutes.

### Environment Variables Required
| Variable | Where | Value |
|---|---|---|
| `ANTHROPIC_API_KEY` | Fly.io secrets | Claude API key |
| `SUPABASE_URL` | Fly.io secrets | `https://ogpfdrpoujrekgbrfilk.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Fly.io secrets | Service role key (never expose publicly) |
| `FLY_API_TOKEN` | GitHub Actions secret | For CI/CD deploys |

Frontend uses hardcoded `SUPABASE_ANON_KEY` and `SUPABASE_URL` in HTML — this is acceptable for a public anon key.

---

## Feature Roadmap (Sprint Order)

### Sprint 1 — Foundation (COMPLETE ✅)
- [x] Email/password auth (replaced magic links)
- [x] ON DELETE CASCADE for profiles
- [x] DB migration: articles, daily_briefings, company_mentions, press_releases tables
- [x] Onboarding wizard (5 steps, saves to user_preferences)
- [x] Preferences editing via ⚙ link
- [x] Filter pills grey out when no articles
- [x] Quiet day note
- [x] Progressive loading messages
- [x] Agent clear button
- [x] Watchlist company badges on articles
- [x] Topic-sorted articles by user preference

### Sprint 2 — Bug Fixes (DO THIS NEXT)
- [ ] Fix `admin.html` dead Render URL → Fly.io URL
- [ ] Fix `loadingTimers` scoping bug
- [ ] Switch Intelligence Agent to Claude Sonnet
- [ ] Create `increment_articles_read` Supabase RPC or remove dead call
- [ ] Fix or validate `company_mentions` PostgREST join query
- [ ] Migrate `companies.html` to current design system (Crimson Pro, DM Mono, cream bg)

### Sprint 3 — Company Intelligence Upgrade
- [ ] Redesign companies.html to match index.html design system
- [ ] Add sentiment indicator to press releases (positive/negative/neutral)
- [ ] Add date filter to press releases (last 7d / 30d / all)
- [ ] Show company-specific articles from `articles` table (not just press releases)
- [ ] Company watchlist: highlight watched companies differently in the grid

### Sprint 4 — Email Digest (Resend)
- [ ] Configure Resend account and API key
- [ ] Build digest email template (HTML email matching product aesthetic)
- [ ] Scheduled send based on user's `digest_time` preference
- [ ] Unsubscribe link + preference management
- [ ] Send today's top 5 articles + market pulse + consulting angles

### Sprint 5 — Mobile Layout
- [ ] Responsive breakpoints for tablet (768px) and mobile (375px)
- [ ] Sidebar collapses to bottom sheet on mobile
- [ ] Agent widget becomes full-screen modal on mobile
- [ ] Filter pills horizontal scroll on mobile
- [ ] Touch-friendly tap targets

### Sprint 6 — Wow Factor
- [ ] Skeleton loading states (replace spinner with content-shaped placeholders)
- [ ] Live article count in masthead ("3 new since you last loaded")
- [ ] Keyboard shortcuts: `f` to filter, `/` to focus agent, `r` to refresh, `s` to save
- [ ] Save bookmarks to Supabase (replace localStorage)
- [ ] Smooth article card entrance animations
- [ ] Toast notifications for real-time events

### Sprint 7 — Analytics + Admin
- [ ] Wire `sources` table to backend RSS feed fetching
- [ ] Create `increment_articles_read` Supabase RPC
- [ ] Admin: real-time active users, reads per article, filter usage heatmap
- [ ] Admin: manually trigger fresh fetch
- [ ] Admin: edit/preview next briefing before it goes live
- [ ] Export briefing as PDF

---

## What Makes This Special

1. **The consulting angle is the product.** Every article gets a one-sentence commercial implication written by Claude, framed for the reader's role and specialism. This is the insight a junior analyst would spend an hour researching. We give it in 25 words.

2. **Curation over volume.** 8 articles, not 80. We read 120 articles across 6 sources every 15 minutes so the consultant doesn't have to. The market pulse is a standing briefing, not a feed.

3. **Speed to insight.** Load the page, read 3 articles, leave a smarter person. The entire briefing takes 4 minutes to absorb. That's the design constraint.

4. **Feels premium, not techy.** Newspaper typography, editorial layout, no rounded corners on key elements, gold as the only accent. It looks like something worth paying for.

5. **The Intelligence Agent.** Ask "what does declining cat rates mean for my Lloyd's client?" and get a sourced, consultant-framed answer in 10 seconds. Not a chatbot — a research assistant.

---

## Tracked Companies (12)

| Company | Type | Data Source |
|---|---|---|
| Swiss Re | Reinsurer | RSS feed |
| Munich Re | Reinsurer | RSS feed |
| Allianz | Primary Insurer | RSS feed |
| Zurich | Primary Insurer | Claude web search |
| Aviva | Primary Insurer | Claude web search |
| Chubb | Primary Insurer | Claude web search |
| AIG | Primary Insurer | Claude web search |
| Aon | Broker | Claude web search |
| Marsh McLennan | Broker | Claude web search |
| Gallagher | Broker | Claude web search |
| Hiscox | Specialty | Claude web search |
| Beazley | Specialty | Claude web search |

---

## Cache Architecture

```
User request
     │
     ▼
Memory cache (15 min TTL)
     │ miss
     ▼
Supabase DB (daily_briefings + articles tables)
     │ miss or stale
     ▼
RSS fetch (6 feeds, last 48h, up to 20 articles/feed)
     │
     ▼
Claude Haiku (select top 8, rank, summarise, angles)
     │
     ▼
Write to Supabase + memory cache
     │
     ▼
Return to user
```

Companies cache TTL: 2 hours. Same pattern.

Cron-job.org pings `/news?force_refresh=true` every 30 minutes so the first real user never waits for a cold fetch.
