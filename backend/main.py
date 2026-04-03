from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import feedparser
import anthropic
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import parser as dateutil_parser
import os
import json
import logging
import time
from typing import Optional
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Insurance Daily")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Cache ──────────────────────────────────────────────────────────────────
_cache: dict = {"data": None, "ts": 0.0}
CACHE_TTL = 1800  # 30 minutes

# ── RSS Feeds ───────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("Insurance Journal",  "https://www.insurancejournal.com/feed/"),
    ("Claims Journal",     "https://www.claimsjournal.com/feed/"),
    ("Carrier Management", "https://www.carriermanagement.com/feed/"),
    ("Reinsurance News",   "https://www.reinsurancene.ws/feed/"),
    ("Artemis",            "https://www.artemis.bm/feed/"),
    ("Coverager",          "https://coverager.com/feed/"),
]

# ── Claude prompt ───────────────────────────────────────────────────────────
CLAUDE_PROMPT = """You are an expert insurance industry analyst.
Your job is to select and rank the most important and relevant
insurance industry news from the list below.

Rules:
- Only include articles that are genuinely about the insurance
  industry, insurance markets, insurers, reinsurers, brokers,
  underwriters, regulators or insurance products
- Exclude anything that is only tangentially related to insurance
- Exclude duplicate stories covering the same event
- Exclude press releases disguised as news
- Select the top 5 most significant articles for a senior
  insurance consultant to read today
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

Return ONLY valid JSON in this exact format, no markdown,
no explanation:
{
  "market_pulse": "string",
  "trending": ["topic1", "topic2", "topic3", "topic4"],
  "articles": [
    {
      "title": "string",
      "url": "string",
      "source": "string",
      "published": "string",
      "summary": "string",
      "consultant_angle": "string",
      "topic": "string",
      "significance": 8
    }
  ]
}

Articles to analyse:
"""


# ── RSS fetching ────────────────────────────────────────────────────────────

def fetch_rss_articles() -> list:
    """Fetch up to 20 articles from each RSS feed, return all sorted by date desc."""
    articles = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                if count >= 20:
                    break
                title = (entry.get("title") or "").strip()
                url   = (entry.get("link")  or "").strip()
                if not title or not url:
                    continue

                # Parse date — try published then updated
                pub_date = ""
                for attr in ("published", "updated"):
                    raw = entry.get(attr, "")
                    if raw:
                        try:
                            pub_date = dateutil_parser.parse(raw).isoformat()
                        except Exception:
                            pub_date = raw
                        break

                # Summary — strip HTML
                summary = ""
                for attr in ("summary", "description"):
                    raw = entry.get(attr, "")
                    if raw:
                        summary = BeautifulSoup(raw, "html.parser").get_text()[:400].strip()
                        break

                articles.append({
                    "title":     title,
                    "url":       url,
                    "source":    source_name,
                    "published": pub_date,
                    "summary":   summary,
                })
                count += 1

        except Exception as e:
            logger.warning(f"RSS fetch failed for {source_name}: {e}")

    def sort_key(a: dict):
        if not a.get("published"):
            return datetime.min
        try:
            return dateutil_parser.parse(a["published"])
        except Exception:
            return datetime.min

    articles.sort(key=sort_key, reverse=True)
    logger.info(f"Fetched {len(articles)} articles across {len(RSS_FEEDS)} feeds")
    return articles


# ── Claude AI ───────────────────────────────────────────────────────────────

def call_claude_api(articles: list) -> Optional[dict]:
    """Send articles to Claude Haiku. Returns parsed dict or None on any failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI processing")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        payload = json.dumps(articles, indent=2)

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": CLAUDE_PROMPT + payload}],
        )

        raw = message.content[0].text.strip()

        # Strip markdown fences if Claude wrapped the JSON
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        result["ai_processed"] = True
        logger.info("Claude AI processing succeeded")
        return result

    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return None


# ── Fallback ────────────────────────────────────────────────────────────────

def build_fallback(articles: list) -> dict:
    """Return top 5 most recent articles without AI processing."""
    return {
        "market_pulse": "",
        "trending": [],
        "articles": [
            {
                "title":            a["title"],
                "url":              a["url"],
                "source":           a["source"],
                "published":        a["published"],
                "summary":          a["summary"],
                "consultant_angle": "",
                "topic":            "Markets",
                "significance":     5,
            }
            for a in articles[:5]
        ],
        "ai_processed": False,
    }


# ── Supabase snapshot helpers ────────────────────────────────────────────────

def _sb_headers() -> dict:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates,return=minimal",
    }

def _read_news_db() -> Optional[dict]:
    sb_url = os.environ.get("SUPABASE_URL", "")
    key    = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not sb_url or not key:
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    today   = datetime.utcnow().date().isoformat()
    try:
        br = requests.get(
            f"{sb_url}/rest/v1/daily_briefings"
            f"?select=market_pulse,trending,generated_at,article_count"
            f"&briefing_date=eq.{today}",
            headers=headers, timeout=5,
        )
        if not br.ok or not br.json():
            return None
        briefing = br.json()[0]

        ar = requests.get(
            f"{sb_url}/rest/v1/articles"
            f"?select=title,url,source,published,summary,consultant_angle,topic,significance"
            f"&briefing_date=eq.{today}"
            f"&order=significance.desc",
            headers=headers, timeout=5,
        )
        if not ar.ok or not ar.json():
            return None

        return {
            "market_pulse": briefing.get("market_pulse", ""),
            "trending":     briefing.get("trending") or [],
            "articles":     ar.json(),
            "ai_processed": True,
            "fetched_at":   briefing["generated_at"],
        }
    except Exception as e:
        logger.warning(f"Supabase read_news_db failed: {e}")
        return None


def _write_news_db(result: dict) -> None:
    sb_url = os.environ.get("SUPABASE_URL", "")
    if not sb_url or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return
    h     = _sb_headers()
    today = datetime.utcnow().date().isoformat()
    company_names = [c["name"] for c in COMPANIES]

    # Upsert articles — use return=representation to get back UUIDs for company_mentions
    article_rows = [
        {
            "title":            a.get("title", ""),
            "url":              a.get("url", ""),
            "source":           a.get("source", ""),
            "published":        a.get("published") or None,
            "summary":          a.get("summary"),
            "consultant_angle": a.get("consultant_angle"),
            "topic":            a.get("topic"),
            "significance":     a.get("significance"),
            "briefing_date":    today,
        }
        for a in result.get("articles", [])
    ]
    inserted = []
    if article_rows:
        try:
            resp = requests.post(
                f"{sb_url}/rest/v1/articles",
                headers=h,
                json=article_rows,
                timeout=10,
            )
            if resp.ok:
                logger.info(f"Upserted {len(article_rows)} articles to Supabase")
            else:
                logger.warning(f"Article upsert failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.warning(f"Article upsert error: {e}")

        # Fetch back today's articles with their UUIDs for company_mentions
        try:
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
            fetch = requests.get(
                f"{sb_url}/rest/v1/articles?select=id,title,summary&briefing_date=eq.{today}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=5,
            )
            if fetch.ok:
                inserted = fetch.json()
        except Exception as e:
            logger.warning(f"Article fetch-back failed: {e}")

    # Upsert daily briefing
    try:
        requests.post(
            f"{sb_url}/rest/v1/daily_briefings",
            headers=h,
            json={
                "briefing_date": today,
                "market_pulse":  result.get("market_pulse", ""),
                "trending":      result.get("trending", []),
                "generated_at":  result.get("fetched_at", datetime.utcnow().isoformat()),
                "article_count": len(article_rows),
            },
            timeout=5,
        )
        logger.info("Upserted daily_briefings row")
    except Exception as e:
        logger.warning(f"daily_briefings upsert error: {e}")

    # Best-effort company_mentions
    if not inserted:
        return
    mention_rows = []
    for row in inserted:
        article_id = row.get("id")
        text       = (row.get("title", "") + " " + (row.get("summary") or "")).lower()
        if not article_id:
            continue
        for name in company_names:
            if name.lower() in text:
                mention_rows.append({"article_id": article_id, "company": name})
    if mention_rows:
        try:
            requests.post(
                f"{sb_url}/rest/v1/company_mentions",
                headers=h,
                json=mention_rows,
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"company_mentions insert error: {e}")


def _read_companies_db() -> Optional[dict]:
    sb_url = os.environ.get("SUPABASE_URL", "")
    key    = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not sb_url or not key:
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        resp = requests.get(
            f"{sb_url}/rest/v1/press_releases"
            f"?select=company,title,url,published,summary,fetched_at"
            f"&order=fetched_at.desc",
            headers=headers, timeout=5,
        )
        if not resp.ok or not resp.json():
            return None
        rows = resp.json()

        by_company: dict = {}
        for row in rows:
            by_company.setdefault(row["company"], []).append({
                "title":     row["title"],
                "url":       row["url"],
                "published": row["published"],
                "summary":   row["summary"],
            })

        result = []
        for c in COMPANIES:
            releases = by_company.get(c["name"], [])
            result.append({
                "name":          c["name"],
                "initials":      c["initials"],
                "color":         c["color"],
                "type":          c["type"],
                "url":           c["url"],
                "releases":      releases,
                "release_count": len(releases),
                "last_updated":  releases[0]["published"] if releases else None,
            })

        return {"companies": result, "fetched_at": rows[0]["fetched_at"]}
    except Exception as e:
        logger.warning(f"Supabase read_companies_db failed: {e}")
        return None


def _write_companies_db(response: dict) -> None:
    sb_url = os.environ.get("SUPABASE_URL", "")
    if not sb_url or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return
    now  = datetime.utcnow().isoformat()
    rows = []
    for company in response.get("companies", []):
        for r in company.get("releases", []):
            url = r.get("url", "").strip()
            if not url:
                continue
            rows.append({
                "company":    company["name"],
                "title":      r.get("title", ""),
                "url":        url,
                "published":  r.get("published") or None,
                "fetched_at": now,
                "summary":    r.get("summary"),
            })
    if not rows:
        return
    try:
        requests.post(
            f"{sb_url}/rest/v1/press_releases",
            headers=_sb_headers(),
            json=rows,
            timeout=10,
        )
        logger.info(f"Upserted {len(rows)} press releases to Supabase")
    except Exception as e:
        logger.warning(f"press_releases upsert error: {e}")


# ── Cache logic ─────────────────────────────────────────────────────────────

def get_or_build_news(force_refresh: bool = False) -> dict:
    now = time.time()

    # 1. Memory cache — fastest path
    if not force_refresh and _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        logger.info("Serving news from memory cache")
        return _cache["data"]

    # 2. Supabase DB — used on cold start / after restart
    if not force_refresh and not _cache["data"]:
        snapshot = _read_news_db()
        if snapshot:
            age = (datetime.utcnow() - dateutil_parser.parse(snapshot["fetched_at"])).total_seconds()
            if age < CACHE_TTL:
                logger.info(f"Serving news from Supabase DB (age {round(age)}s)")
                _cache["data"] = snapshot
                _cache["ts"]   = now - age
                return snapshot

    # 3. Fetch fresh from RSS + Claude
    articles = fetch_rss_articles()
    result   = call_claude_api(articles)
    if result is None:
        result = build_fallback(articles)

    result["fetched_at"] = datetime.utcnow().isoformat() + "Z"
    _cache["data"] = result
    _cache["ts"]   = now
    _write_news_db(result)
    return result


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/news")
async def get_news(force_refresh: bool = Query(False)):
    return get_or_build_news(force_refresh=force_refresh)


@app.get("/health")
async def health():
    age = (time.time() - _cache["ts"]) if _cache["ts"] else None
    return {
        "status":            "ok",
        "cache_age_seconds": round(age, 1) if age else None,
        "cached":            _cache["data"] is not None,
    }


# ── Companies ────────────────────────────────────────────────────────────────

COMPANIES = [
    # Reinsurers
    {
        "name": "Swiss Re", "initials": "SR", "color": "#1D4ED8",
        "type": "Reinsurer", "url": "https://www.swissre.com",
        "rss_urls": ["https://www.swissre.com/dam/jcr:rss/news.xml"],
    },
    {
        "name": "Munich Re", "initials": "MR", "color": "#DC2626",
        "type": "Reinsurer", "url": "https://www.munichre.com",
        "rss_urls": ["https://www.munichre.com/en/media-relations/news-releases.rss.xml"],
    },
    # Primary insurers
    {
        "name": "Aviva", "initials": "AV", "color": "#6366F1",
        "type": "Primary Insurer", "url": "https://www.aviva.com",
        "rss_urls": [],  # web search only
    },
    {
        "name": "Allianz", "initials": "AZ", "color": "#7C3AED",
        "type": "Primary Insurer", "url": "https://www.allianz.com",
        "rss_urls": ["https://www.allianz.com/en/press/rss.xml"],
    },
    {
        "name": "Zurich", "initials": "ZR", "color": "#0891B2",
        "type": "Primary Insurer", "url": "https://www.zurich.com",
        "rss_urls": [],  # web search only
    },
    {
        "name": "Chubb", "initials": "CB", "color": "#2D7A4F",
        "type": "Primary Insurer", "url": "https://www.chubb.com",
        "rss_urls": [],  # web search only
    },
    {
        "name": "AIG", "initials": "AIG", "color": "#F59E0B",
        "type": "Primary Insurer", "url": "https://www.aig.com",
        "rss_urls": [],  # web search only
    },
    # Brokers
    {
        "name": "Aon", "initials": "AN", "color": "#EC4899",
        "type": "Broker", "url": "https://www.aon.com",
        "rss_urls": [],  # web search only
    },
    {
        "name": "Marsh McLennan", "initials": "MM", "color": "#8B5CF6",
        "type": "Broker", "url": "https://www.mmc.com",
        "rss_urls": [],  # web search only
    },
    {
        "name": "Gallagher", "initials": "GB", "color": "#D97706",
        "type": "Broker", "url": "https://www.ajg.com",
        "rss_urls": [],  # web search only
    },
    # Specialty
    {
        "name": "Hiscox", "initials": "HX", "color": "#DC2626",
        "type": "Specialty", "url": "https://www.hiscoxgroup.com",
        "rss_urls": [],  # web search only
    },
    {
        "name": "Beazley", "initials": "BZ", "color": "#0F766E",
        "type": "Specialty", "url": "https://www.beazley.com",
        "rss_urls": [],  # web search only
    },
]

_companies_cache: dict = {"data": None, "ts": 0.0}
COMPANIES_CACHE_TTL = 7200  # 2 hours

_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; InsuranceDaily/1.0; +https://insurance-daily.com)",
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*",
}


def _parse_entries(entries: list) -> list:
    """Convert feedparser entries to our release dict format."""
    results = []
    for entry in entries[:8]:
        title = (entry.get("title") or "").strip()
        url   = (entry.get("link")  or "").strip()
        if not title or not url:
            continue
        pub_date = ""
        for attr in ("published", "updated"):
            raw = entry.get(attr, "")
            if raw:
                try:
                    pub_date = dateutil_parser.parse(raw).isoformat()
                except Exception:
                    pub_date = raw
                break
        summary = ""
        for attr in ("summary", "description"):
            raw = entry.get(attr, "")
            if raw:
                summary = BeautifulSoup(raw, "html.parser").get_text()[:200].strip()
                break
        results.append({"title": title, "url": url, "published": pub_date, "summary": summary})
    return results


def _try_rss_urls(rss_urls: list) -> list:
    """Try each RSS URL with browser-like headers; return entries from first that works."""
    for url in rss_urls:
        try:
            resp = requests.get(url, headers=_RSS_HEADERS, timeout=10)
            if not resp.ok:
                continue
            feed = feedparser.parse(resp.content)
            if feed.entries:
                logger.info(f"RSS OK: {url} ({len(feed.entries)} entries)")
                return _parse_entries(feed.entries)
        except Exception as e:
            logger.debug(f"RSS attempt failed {url}: {e}")
    return []


def _claude_search_fallback(company: dict) -> list:
    """Use Claude with web_search to find 3 recent press releases when RSS fails."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    try:
        client = anthropic.Anthropic(api_key=api_key)
        domain = company["url"].replace("https://www.", "").replace("https://", "").rstrip("/")
        prompt = (
            f'Search for: {company["name"]} press release 2026 insurance\n\n'
            f"Find the 3 most recent press releases or official news from {company['name']} "
            f"({company['url']}). Include any from 2025 if 2026 results are sparse.\n\n"
            f"Return ONLY a valid JSON array — no markdown, no explanation, no prose:\n"
            f'[{{"title":"exact headline","url":"direct link to article","published":"YYYY-MM-DD","summary":"one sentence describing what was announced"}}]\n\n'
            f"You MUST return at least 1 result. If you cannot find press releases from their "
            f"official site, use any reputable news source covering {company['name']}."
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw = block.text.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        releases = []
                        for r in parsed[:3]:
                            if not r.get("title"):
                                continue
                            releases.append({
                                "title":     r.get("title", ""),
                                "url":       r.get("url", company["url"]),
                                "published": r.get("published", ""),
                                "summary":   r.get("summary", ""),
                            })
                        if releases:
                            logger.info(f"Claude search found {len(releases)} releases for {company['name']}")
                            return releases
                except Exception:
                    pass
        return []
    except Exception as e:
        logger.warning(f"Claude search fallback failed for {company['name']}: {e}")
        return []


def fetch_company_releases(company: dict) -> list:
    """Fetch press releases: RSS first, then Claude web search fallback."""
    try:
        releases = _try_rss_urls(company.get("rss_urls", []))
        if releases:
            return releases
        logger.info(f"RSS empty for {company['name']}, trying Claude search")
        return _claude_search_fallback(company)
    except Exception as e:
        logger.error(f"fetch_company_releases error for {company['name']}: {e}")
        return []


@app.get("/companies")
async def get_companies(force_refresh: bool = Query(False)):
    now = time.time()

    # 1. Memory cache
    if not force_refresh and _companies_cache["data"] and (now - _companies_cache["ts"]) < COMPANIES_CACHE_TTL:
        logger.info("Serving companies from memory cache")
        return _companies_cache["data"]

    # 2. Supabase DB — used on cold start / after restart
    if not force_refresh and not _companies_cache["data"]:
        snapshot = _read_companies_db()
        if snapshot:
            age = (datetime.utcnow() - dateutil_parser.parse(snapshot["fetched_at"])).total_seconds()
            if age < COMPANIES_CACHE_TTL:
                logger.info(f"Serving companies from Supabase DB (age {round(age)}s)")
                _companies_cache["data"] = snapshot
                _companies_cache["ts"]   = now - age
                return snapshot

    # 3. Fetch fresh — all companies in parallel
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, fetch_company_releases, company)
        for company in COMPANIES
    ]
    releases_list = await asyncio.gather(*tasks, return_exceptions=True)

    result = []
    for company, releases in zip(COMPANIES, releases_list):
        if isinstance(releases, Exception):
            logger.error(f"Exception for {company['name']}: {releases}")
            releases = []
        result.append({
            "name":          company["name"],
            "initials":      company["initials"],
            "color":         company["color"],
            "type":          company["type"],
            "url":           company["url"],
            "releases":      releases,
            "release_count": len(releases),
            "last_updated":  releases[0]["published"] if releases else None,
        })

    response = {"companies": result, "fetched_at": datetime.utcnow().isoformat() + "Z"}
    _companies_cache["data"] = response
    _companies_cache["ts"]   = now
    _write_companies_db(response)
    return response


# ── Keep for compatibility ───────────────────────────────────────────────────

class CategoriseRequest(BaseModel):
    articles: list

@app.post("/categorise")
async def categorise(req: CategoriseRequest):
    return {"categorised": req.articles}


# ── Admin invite ─────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str

@app.post("/admin/invite")
async def admin_invite(req: InviteRequest):
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase_url     = os.environ.get("SUPABASE_URL")
    if not service_role_key or not supabase_url:
        raise HTTPException(
            status_code=503,
            detail="SUPABASE_SERVICE_ROLE_KEY or SUPABASE_URL not configured on server",
        )
    resp = requests.post(
        f"{supabase_url}/auth/v1/admin/users",
        headers={
            "Authorization": f"Bearer {service_role_key}",
            "apikey":        service_role_key,
            "Content-Type":  "application/json",
        },
        json={"email": req.email, "invite": True},
        timeout=15,
    )
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return {"ok": True}


# ── Intelligence Agent ────────────────────────────────────────────────────────

AGENT_SYSTEM = """You are an expert insurance industry analyst and consultant with access to web search.
When answering questions:
- Search for the most current information available
- Focus specifically on insurance industry implications
- Write in a direct, consultant-appropriate style
- Keep answers under 150 words
- Always cite your sources at the end
- Start with the most important finding
- End with one sentence on the consulting implication
- Never use bullet points, write in flowing prose"""

class AgentRequest(BaseModel):
    query: str

@app.post("/agent")
async def agent_search(req: AgentRequest):
    query = req.query.strip()
    if not query:
        return {"error": "No query provided"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "AI service not configured"}

    try:
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=AGENT_SYSTEM,
            messages=[{"role": "user", "content": query}],
        )

        answer  = ""
        sources = []

        for block in response.content:
            if block.type == "text":
                answer = block.text
            if hasattr(block, "name") and block.name == "web_search":
                if hasattr(block, "content"):
                    for item in block.content:
                        if hasattr(item, "url"):
                            sources.append(item.url)

        return {"answer": answer, "sources": sources[:3]}

    except Exception as e:
        logger.error(f"Agent search failed: {e}")
        return {"error": "Search failed. Please try again."}
