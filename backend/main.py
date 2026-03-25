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


# ── Cache logic ─────────────────────────────────────────────────────────────

def get_or_build_news(force_refresh: bool = False) -> dict:
    now = time.time()
    if not force_refresh and _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        logger.info("Serving from cache")
        return _cache["data"]

    articles = fetch_rss_articles()
    result = call_claude_api(articles)
    if result is None:
        result = build_fallback(articles)

    result["fetched_at"] = datetime.utcnow().isoformat() + "Z"
    _cache["data"] = result
    _cache["ts"] = now
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
    {"name": "Swiss Re",  "initials": "SR",  "color": "#1D4ED8", "type": "Reinsurer",       "url": "https://www.swissre.com",   "feed_url": "https://www.swissre.com/rss/press-releases.rss"},
    {"name": "Munich Re", "initials": "MR",  "color": "#7C3AED", "type": "Reinsurer",       "url": "https://www.munichre.com",  "feed_url": "https://www.munichre.com/en/media-relations/news/rss.html"},
    {"name": "AIG",       "initials": "AIG", "color": "#DC2626", "type": "Primary Insurer", "url": "https://www.aig.com",       "feed_url": "https://newsroom.aig.com/rss/aig-press-releases.xml"},
    {"name": "Chubb",     "initials": "CB",  "color": "#2D7A4F", "type": "Primary Insurer", "url": "https://www.chubb.com",     "feed_url": "https://news.chubb.com/rss/news-releases.xml"},
    {"name": "Allianz",   "initials": "AZ",  "color": "#C4922A", "type": "Primary Insurer", "url": "https://www.allianz.com",  "feed_url": "https://www.allianz.com/en/press/news/_rss.html"},
]

_companies_cache: dict = {"data": None, "ts": 0.0}
COMPANIES_CACHE_TTL = 3600  # 1 hour


def fetch_company_releases(company: dict) -> list:
    releases = []
    feed_url = company.get("feed_url", "")
    if not feed_url:
        return releases
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:10]:
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
            releases.append({"title": title, "url": url, "published": pub_date, "summary": summary})
    except Exception as e:
        logger.warning(f"Company feed failed for {company['name']}: {e}")
    return releases


@app.get("/companies")
async def get_companies(force_refresh: bool = Query(False)):
    now = time.time()
    if not force_refresh and _companies_cache["data"] and (now - _companies_cache["ts"]) < COMPANIES_CACHE_TTL:
        logger.info("Serving companies from cache")
        return _companies_cache["data"]

    result = []
    for company in COMPANIES:
        releases = fetch_company_releases(company)
        result.append({
            "name":         company["name"],
            "initials":     company["initials"],
            "color":        company["color"],
            "type":         company["type"],
            "url":          company["url"],
            "releases":     releases,
            "release_count": len(releases),
            "last_updated": releases[0]["published"] if releases else None,
        })

    response = {"companies": result, "fetched_at": datetime.utcnow().isoformat() + "Z"}
    _companies_cache["data"] = response
    _companies_cache["ts"]   = now
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
