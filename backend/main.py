from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import re
import os
import json
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Insurance News Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", title.lower()).strip()


def titles_similar(t1: str, t2: str, threshold: float = 0.55) -> bool:
    """Return True if two titles share enough words to be the same story."""
    w1 = set(normalize_title(t1).split()) - {"the", "a", "an", "of", "in", "to", "and", "for", "is", "on", "at"}
    w2 = set(normalize_title(t2).split()) - {"the", "a", "an", "of", "in", "to", "and", "for", "is", "on", "at"}
    if not w1 or not w2:
        return False
    return len(w1 & w2) / min(len(w1), len(w2)) >= threshold


DATE_FORMATS = [
    "%B %d, %Y",   # March 20, 2026
    "%b %d, %Y",   # Mar 20, 2026
    "%Y-%m-%d",    # 2026-03-20
    "%d %B %Y",    # 20 March 2026
    "%d %b %Y",    # 20 Mar 2026
    "%m/%d/%Y",    # 03/20/2026
    "%d/%m/%Y",    # 20/03/2026
]

def parse_date(date_str: str):
    """Parse a date string into a datetime, or return None."""
    if not date_str:
        return None
    clean = date_str.strip()
    # Try ISO datetime first
    try:
        return datetime.fromisoformat(clean.rstrip("Z").replace("T", " ")[:19])
    except Exception:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt)
        except Exception:
            continue
    return None


def extract_date_from_url(url: str):
    """Extract date from URL patterns like /2026/03/25/ common on IJ/CM/CJ."""
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url or "")
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    return None


def best_date(article: dict):
    """Return the best available datetime for an article."""
    dt = parse_date(article.get("date", ""))
    if dt:
        return dt
    return extract_date_from_url(article.get("url", ""))


def recency_bonus(article: dict) -> float:
    """Return up to +4.0 for today's articles, decaying over 7 days. -1.0 if no date at all."""
    dt = best_date(article)
    if not dt:
        return -1.0  # penalise articles with no date signal
    age_days = (datetime.utcnow() - dt).total_seconds() / 86400
    if age_days < 0:
        age_days = 0
    if age_days > 7:
        return 0.0
    return round(4.0 * max(0.0, 1.0 - age_days / 7.0), 2)


def first_int(text: str) -> int:
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0


def make_absolute(href: str, base: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return href


def harvest_links(
    soup: BeautifulSoup,
    base_url: str,
    source_name: str,
    href_pattern: str,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """Harvest article links whose href matches href_pattern regex."""
    seen: set = set()
    articles: List[Dict[str, Any]] = []
    for link in soup.find_all("a", href=True):
        href = make_absolute(link.get("href", ""), base_url)
        if not re.search(href_pattern, href):
            continue
        title = link.get_text(strip=True)
        if len(title) < 20:
            parent = link.find_parent(["h1", "h2", "h3", "h4"])
            if parent:
                title = parent.get_text(strip=True)
        if len(title) < 20 or href in seen:
            continue
        seen.add(href)
        articles.append({
            "title": title, "url": href, "source": source_name,
            "date": "", "summary": "", "is_trending": False, "comment_count": 0,
        })
        if len(articles) >= limit:
            break
    return articles


def _extract_articles(
    soup: BeautifulSoup,
    source_name: str,
    base_url: str,
    item_selectors: List[str],
    fallback_href_pattern: str,
) -> List[Dict[str, Any]]:
    """Generic extractor: try structured selectors then fall back to links."""
    articles: List[Dict[str, Any]] = []

    items: List = []
    for sel in item_selectors:
        items = soup.select(sel)
        if items:
            break

    if items:
        for item in items[:25]:
            title_el = item.select_one("h1, h2, h3, h4, .title, .headline, [class*='title'], [class*='headline']")
            link_el = item.select_one("a[href]")
            date_el = item.select_one("time, [class*='date'], [class*='time'], [datetime]")
            summary_el = item.select_one("p, .summary, .excerpt, .teaser, [class*='summary'], [class*='excerpt']")
            comment_el = item.select_one("[class*='comment']")

            title = (title_el or link_el or item).get_text(strip=True)
            if not title or len(title) < 15:
                continue

            href = make_absolute(link_el.get("href", "") if link_el else "", base_url)
            date_str = date_el.get_text(strip=True) if date_el else (
                date_el["datetime"] if date_el and date_el.get("datetime") else ""
            )
            summary = summary_el.get_text(strip=True)[:300] if summary_el else ""

            comment_count = 0
            if comment_el:
                comment_count = first_int(comment_el.get_text())

            classes_str = " ".join(item.get("class", [])).lower()
            is_trending = bool(
                item.select_one("[class*='trend'], [class*='feature'], [class*='pin'], [class*='hot'], [class*='popular'], [class*='top']")
                or any(kw in classes_str for kw in ("trend", "feature", "pin", "hot", "popular", "top-story"))
            )

            articles.append({
                "title": title,
                "url": href,
                "source": source_name,
                "date": date_str,
                "summary": summary,
                "is_trending": is_trending,
                "comment_count": comment_count,
            })
    else:
        # Fallback: raw link harvest
        seen: set = set()
        for link in soup.select(f"a[href*='{fallback_href_pattern}']")[:30]:
            href = make_absolute(link.get("href", ""), base_url)
            title = link.get_text(strip=True)
            if len(title) > 20 and href not in seen:
                seen.add(href)
                articles.append({
                    "title": title,
                    "url": href,
                    "source": source_name,
                    "date": "",
                    "summary": "",
                    "is_trending": False,
                    "comment_count": 0,
                })

    return articles


# ---------------------------------------------------------------------------
# Per-source scrapers
# ---------------------------------------------------------------------------

def scrape_insurance_journal() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.insurancejournal.com/news/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # IJ URLs contain /news/<region>/YYYY/MM/DD/
        articles = harvest_links(soup, "https://www.insurancejournal.com",
                                 "Insurance Journal", r"/news/\w+/\d{4}/\d{2}/\d{2}/")
    except Exception as e:
        logger.error(f"Insurance Journal: {e}")
    logger.info(f"Insurance Journal: {len(articles)}")
    return articles


def scrape_business_insurance() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.businessinsurance.com/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Elementor-based site — extract from post cards
        seen: set = set()
        for card in soup.select(".elementor-post"):
            a = card.select_one(".elementor-post__title a, h2 a, h3 a, a[href]")
            if not a:
                continue
            href = make_absolute(a.get("href", ""), "https://www.businessinsurance.com")
            title = a.get_text(strip=True)
            date_el = card.select_one(".elementor-post__meta-data, time, [class*='date']")
            date_str = date_el.get_text(strip=True) if date_el else ""
            if len(title) < 20 or href in seen:
                continue
            seen.add(href)
            articles.append({"title": title, "url": href, "source": "Business Insurance",
                             "date": date_str, "summary": "", "is_trending": False, "comment_count": 0})
        # Fallback if Elementor selectors miss
        if not articles:
            articles = harvest_links(soup, "https://www.businessinsurance.com",
                                     "Business Insurance", r"businessinsurance\.com/article/")
    except Exception as e:
        logger.error(f"Business Insurance: {e}")
    logger.info(f"Business Insurance: {len(articles)}")
    return articles


def scrape_carrier_management() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.carriermanagement.com/news/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        articles = harvest_links(soup, "https://www.carriermanagement.com",
                                 "Carrier Management", r"/(?:news|features)/\d{4}/\d{2}/\d{2}/")
    except Exception as e:
        logger.error(f"Carrier Management: {e}")
    logger.info(f"Carrier Management: {len(articles)}")
    return articles


def scrape_claims_journal() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.claimsjournal.com/news/national/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        articles = harvest_links(soup, "https://www.claimsjournal.com",
                                 "Claims Journal", r"/news/\w+/\d{4}/\d{2}/\d{2}/")
    except Exception as e:
        logger.error(f"Claims Journal: {e}")
    logger.info(f"Claims Journal: {len(articles)}")
    return articles


def scrape_insurance_business_mag() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.insurancebusinessmag.com/us/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        articles = harvest_links(soup, "https://www.insurancebusinessmag.com",
                                 "Insurance Business", r"/us/news/.*\.aspx")
    except Exception as e:
        logger.error(f"Insurance Business Mag: {e}")
    logger.info(f"Insurance Business Mag: {len(articles)}")
    return articles


def scrape_property_casualty_360() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.propertycasualty360.com/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # PC360 URLs contain /YYYY/MM/DD/
        articles = harvest_links(soup, "https://www.propertycasualty360.com",
                                 "PropertyCasualty360", r"propertycasualty360\.com/\d{4}/\d{2}/\d{2}/")
    except Exception as e:
        logger.error(f"PropertyCasualty360: {e}")
    logger.info(f"PropertyCasualty360: {len(articles)}")
    return articles


def scrape_risk_and_insurance() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://riskandinsurance.com/news/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        articles = harvest_links(soup, "https://riskandinsurance.com",
                                 "Risk & Insurance", r"riskandinsurance\.com/[a-z0-9\-]{20,}/")
    except Exception as e:
        logger.error(f"Risk & Insurance: {e}")
    logger.info(f"Risk & Insurance: {len(articles)}")
    return articles


# ---------------------------------------------------------------------------
# Summary enrichment — fetch meta description from article pages in parallel
# ---------------------------------------------------------------------------

def fetch_meta_description(url: str) -> str:
    """Fetch og:description or description meta tag from an article URL."""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for prop in ("og:description", "description", "twitter:description"):
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                return tag["content"].strip()[:300]
    except Exception:
        pass
    return ""


def enrich_summaries(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill in missing summaries by fetching article pages in parallel."""
    need_summary = [a for a in articles if not a["summary"] and a["url"]]
    if not need_summary:
        return articles

    with ThreadPoolExecutor(max_workers=10) as pool:
        future_to_article = {pool.submit(fetch_meta_description, a["url"]): a for a in need_summary}
        for future in as_completed(future_to_article):
            article = future_to_article[future]
            try:
                summary = future.result()
                if summary:
                    article["summary"] = summary
            except Exception:
                pass
    return articles


# ---------------------------------------------------------------------------
# Scoring & deduplication
# ---------------------------------------------------------------------------

def score_and_rank(all_articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not all_articles:
        return []

    # Group articles that cover the same story
    groups: List[List[Dict]] = []
    used: set = set()

    for i, article in enumerate(all_articles):
        if i in used:
            continue
        group = [article]
        used.add(i)
        for j, other in enumerate(all_articles):
            if j in used:
                continue
            if titles_similar(article["title"], other["title"]):
                group.append(other)
                used.add(j)
        groups.append(group)

    max_comments = max((a["comment_count"] for a in all_articles), default=1) or 1

    scored: List[Dict[str, Any]] = []
    for group in groups:
        # Pick the representative with the richest summary
        best = max(group, key=lambda a: len(a["summary"]))
        sources = list({a["source"] for a in group})

        score = 1.0
        reasons: List[str] = []

        # Multi-source bonus (strongest signal)
        if len(sources) > 1:
            score += len(sources) * 5.0
            reasons.append(f"Reported by {len(sources)} sources: {', '.join(sources)}")

        # Trending / featured bonus
        trending_count = sum(1 for a in group if a["is_trending"])
        if trending_count:
            score += trending_count * 3.0
            reasons.append("Featured / trending")

        # Comment engagement bonus
        max_comments_in_group = max(a["comment_count"] for a in group)
        if max_comments_in_group > 0:
            score += (max_comments_in_group / max_comments) * 4.0
            reasons.append(f"{max_comments_in_group} comments")

        # Recency bonus — today's articles score up to +4.0, decays over 7 days
        score += recency_bonus(best)

        # Page-position prominence bonus (earlier = more prominent)
        positions = [idx for idx, a in enumerate(all_articles) if a in group]
        if positions:
            score += max(0.0, 2.0 - (min(positions) / 20.0))

        if not reasons:
            reasons.append("Latest news")

        # Use URL-derived date if no text date
        display_date = best["date"]
        if not display_date:
            dt = extract_date_from_url(best["url"])
            if dt:
                display_date = dt.strftime("%b %d, %Y")

        scored.append({
            "title": best["title"],
            "source": " & ".join(sources[:2]) if len(sources) > 1 else sources[0],
            "sources": sources,
            "date": display_date,
            "summary": best["summary"],
            "url": best["url"],
            "relevance_score": round(score, 1),
            "trending_reason": " · ".join(reasons),
            "is_trending": any(a["is_trending"] for a in group) or len(sources) > 1,
            "source_count": len(sources),
        })

    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    return scored[:10]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/news")
async def get_news():
    # Scrape all 5 sources in parallel
    scrapers = [
        scrape_insurance_journal,
        scrape_business_insurance,
        scrape_carrier_management,
        scrape_claims_journal,
        scrape_insurance_business_mag,
        scrape_property_casualty_360,
        scrape_risk_and_insurance,
    ]

    results: dict = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in scrapers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                logger.error(f"{name} failed: {e}")
                results[name] = []

    ij   = results.get("scrape_insurance_journal", [])
    bi   = results.get("scrape_business_insurance", [])
    cm   = results.get("scrape_carrier_management", [])
    cj   = results.get("scrape_claims_journal", [])
    ibm  = results.get("scrape_insurance_business_mag", [])
    pc   = results.get("scrape_property_casualty_360", [])
    ri   = results.get("scrape_risk_and_insurance", [])

    all_articles = ij + bi + cm + cj + ibm + pc + ri

    # Drop articles older than 14 days
    def is_fresh(a):
        dt = best_date(a)
        if dt is None:
            return True
        return (datetime.utcnow() - dt).days <= 14

    all_articles = [a for a in all_articles if is_fresh(a)]

    # Score + pick top 10 before enriching
    top_10 = score_and_rank(all_articles)

    # Fetch summaries in parallel for articles that don't have one
    top_10 = enrich_summaries(top_10)

    return {
        "articles": top_10,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "stats": {
            "total_scraped":        len(all_articles),
            "insurance_journal":    len(ij),
            "business_insurance":   len(bi),
            "carrier_management":   len(cm),
            "claims_journal":       len(cj),
            "insurance_business_mag": len(ibm),
            "property_casualty_360": len(pc),
            "risk_and_insurance":   len(ri),
        },
    }


# ---------------------------------------------------------------------------
# Categorisation — AI (Claude Haiku) with keyword fallback
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Property & Casualty": ["property insurance","casualty","homeowner","home insurance","liability insurance","fire damage","flood claim","dwelling","p&c","contents insurance","buildings insurance","personal lines","renters insurance"],
    "Reinsurance":         ["reinsurance","reinsurer","retrocession","treaty","facultative","swiss re","munich re","hannover re","cedent","lloyd's","retrocessionaire","cession"],
    "Markets":             ["acquisition","merger","ipo","loss ratio","combined ratio","underwriting profit","underwriting loss","rate hardening","rate softening","premium growth","capacity","investment return","quarterly results","annual results","market hardening"],
    "Cyber":               ["cyber insurance","ransomware","data breach","cyber attack","cyber risk","cyber liability","technology insurance","cyber claim","phishing","malware","hacking"],
    "Climate & CAT":       ["catastrophe","hurricane","wildfire","tornado","earthquake","typhoon","flood loss","storm damage","nat cat","climate risk","esg","climate change","extreme weather","severe convective"],
    "Life & Health":       ["life insurance","health insurance","mortality","longevity","annuity","pension","life assurance","critical illness","income protection","employee benefit","group life","medical insurance","long-term care"],
    "Regulatory":          ["naic","fca","pra","eiopa","regulation","compliance","legislation","solvency ii","ifrs 17","insurance bill","regulatory","enforcement","licensing","government","congress","senate"],
    "Commercial":          ["commercial insurance","workers compensation","employers liability","professional indemnity","directors and officers","d&o","public liability","commercial property","sme","trade credit","surety"],
    "Motor":               ["motor insurance","auto insurance","car insurance","fleet insurance","telematics","electric vehicle insurance","autonomous vehicle","van insurance","road risk","motor claim","auto claim"],
}

def keyword_categorise(articles: List[Dict]) -> List[Dict]:
    results = []
    for a in articles:
        text = (" " + (a.get("title","") + " " + a.get("summary","")) + " ").lower()
        cats = [cat for cat, keys in CATEGORY_KEYWORDS.items() if any(k in text for k in keys)]
        results.append({"id": a["id"], "categories": cats or ["Markets"]})
    return results


def ai_categorise(articles: List[Dict], api_key: str) -> List[Dict]:
    client = anthropic.Anthropic(api_key=api_key)
    articles_json = json.dumps([{"id": a["id"], "title": a["title"], "summary": a["summary"]} for a in articles])
    prompt = (
        "You are a senior insurance industry analyst. Categorise each article into one or more of these exact categories:\n\n"
        "Property & Casualty — home, commercial property, liability, fire, flood, theft, personal lines, P&C\n"
        "Reinsurance — treaty/facultative reinsurance, retrocession, cedents, Swiss Re, Munich Re, Lloyd's\n"
        "Markets — M&A, IPOs, rate changes, combined ratios, underwriting results, capacity, investment returns, financial results\n"
        "Cyber — ransomware, data breach, cyber insurance products, cyber attack, technology liability\n"
        "Climate & CAT — natural catastrophes, hurricanes, wildfires, floods, earthquakes, nat cat, climate risk, ESG\n"
        "Life & Health — life insurance, health insurance, mortality, longevity, annuities, pensions, employee benefits\n"
        "Regulatory — NAIC, FCA, PRA, legislation, compliance, solvency, government policy, insurance bills\n"
        "Commercial — commercial lines, workers comp, professional indemnity, D&O, SME, trade credit, surety\n"
        "Motor — motor/auto insurance, telematics, EV insurance, fleet, autonomous vehicles\n\n"
        "Rules:\n"
        "- Assign only categories that clearly and directly match the article content\n"
        "- Most articles should have 1-2 categories maximum\n"
        "- Do NOT assign Markets unless the article is specifically about financial results, M&A, or rate movements\n"
        "- Do NOT default to Markets — if nothing fits well, use the closest single category\n"
        "- Return ONLY valid JSON array, no explanation, no markdown\n\n"
        f"Articles: {articles_json}\n\n"
        'Return format: [{"id": 0, "categories": ["Cyber"]}, ...]'
    )
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    # Strip markdown code fences if model adds them
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    return json.loads(raw)


@app.post("/categorise")
def categorise(body: dict):
    articles = body.get("articles", [])
    if not articles:
        return {"results": [], "method": "none"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            results = ai_categorise(articles, api_key)
            logger.info("Categorisation: AI")
            return {"results": results, "method": "ai"}
        except Exception as e:
            logger.error(f"AI categorisation failed, using keywords: {e}")

    results = keyword_categorise(articles)
    logger.info("Categorisation: keywords")
    return {"results": results, "method": "keywords"}


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}


class InviteRequest(BaseModel):
    email: str


@app.post("/admin/invite")
async def admin_invite(req: InviteRequest):
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    supabase_url     = os.environ.get("SUPABASE_URL")
    if not service_role_key or not supabase_url:
        raise HTTPException(status_code=503, detail="SUPABASE_SERVICE_ROLE_KEY or SUPABASE_URL not configured on server")
    resp = requests.post(
        f"{supabase_url}/auth/v1/admin/users",
        headers={
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
            "Content-Type": "application/json",
        },
        json={"email": req.email, "invite": True},
        timeout=15,
    )
    if not resp.ok:
        detail = resp.json().get("msg") or resp.json().get("message") or resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return {"ok": True, "email": req.email}
