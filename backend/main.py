from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime
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
    urls = [
        "https://www.insurancejournal.com/news/",
        "https://www.insurancejournal.com/",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            articles = _extract_articles(
                soup,
                source_name="Insurance Journal",
                base_url="https://www.insurancejournal.com",
                item_selectors=[
                    "article",
                    "div.widget-story",
                    "li.article-list__item",
                    "div.article-list-item",
                    "div.featured-article",
                    "div.news-list__item",
                    ".IJ-article",
                ],
                fallback_href_pattern="/news/",
            )
            if articles:
                break
        except Exception as e:
            logger.error(f"Insurance Journal ({url}): {e}")
    logger.info(f"Insurance Journal scraped: {len(articles)}")
    return articles


def scrape_business_insurance() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    urls = [
        "https://www.businessinsurance.com/",
        "https://www.businessinsurance.com/section/news",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            articles = _extract_articles(
                soup,
                source_name="Business Insurance",
                base_url="https://www.businessinsurance.com",
                item_selectors=[
                    "article",
                    "div.article-item",
                    "div.story",
                    "li.headline-list__item",
                    "div.summary-news-item",
                    ".article-summary",
                ],
                fallback_href_pattern="article",
            )
            if articles:
                break
        except Exception as e:
            logger.error(f"Business Insurance ({url}): {e}")
    logger.info(f"Business Insurance scraped: {len(articles)}")
    return articles


def scrape_carrier_management() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    try:
        resp = requests.get("https://www.carriermanagement.com/news/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        articles = _extract_articles(
            soup,
            source_name="Carrier Management",
            base_url="https://www.carriermanagement.com",
            item_selectors=[
                "article",
                "div.article-feature",
                "div.news-list-item",
                "li.article",
            ],
            fallback_href_pattern="/news/",
        )
    except Exception as e:
        logger.error(f"Carrier Management: {e}")
    logger.info(f"Carrier Management scraped: {len(articles)}")
    return articles


def scrape_claims_journal() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    urls = [
        "https://www.claimsjournal.com/news/national/",
        "https://www.claimsjournal.com/",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            articles = _extract_articles(
                soup,
                source_name="Claims Journal",
                base_url="https://www.claimsjournal.com",
                item_selectors=[
                    "article",
                    "div.article-feature",
                    "div.widget-story",
                    "li.article-list__item",
                ],
                fallback_href_pattern="/news/",
            )
            if articles:
                break
        except Exception as e:
            logger.error(f"Claims Journal ({url}): {e}")
    logger.info(f"Claims Journal scraped: {len(articles)}")
    return articles


def scrape_insurance_business_mag() -> List[Dict[str, Any]]:
    articles: List[Dict[str, Any]] = []
    urls = [
        "https://www.insurancebusinessmag.com/us/",
        "https://www.insurancebusinessmag.com/us/news/",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # IBM uses plain <a> links — harvest all news links
            seen: set = set()
            for link in soup.select("a[href*='/us/news/']"):
                href = link.get("href", "")
                title = link.get_text(strip=True)
                if len(title) < 20 or href in seen:
                    continue
                seen.add(href)
                full_href = make_absolute(href, "https://www.insurancebusinessmag.com")
                articles.append({
                    "title": title,
                    "url": full_href,
                    "source": "Insurance Business",
                    "date": "",
                    "summary": "",
                    "is_trending": False,
                    "comment_count": 0,
                })
            if articles:
                break
        except Exception as e:
            logger.error(f"Insurance Business Mag ({url}): {e}")
    logger.info(f"Insurance Business Mag scraped: {len(articles)}")
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

        # Page-position prominence bonus (earlier = more prominent)
        positions = [idx for idx, a in enumerate(all_articles) if a in group]
        if positions:
            score += max(0.0, 2.0 - (min(positions) / 20.0))

        if not reasons:
            reasons.append("Latest news")

        scored.append({
            "title": best["title"],
            "source": " & ".join(sources[:2]) if len(sources) > 1 else sources[0],
            "sources": sources,
            "date": best["date"],
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
    ]

    results: dict = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in scrapers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                logger.error(f"{name} failed: {e}")
                results[name] = []

    ij  = results.get("scrape_insurance_journal", [])
    bi  = results.get("scrape_business_insurance", [])
    cm  = results.get("scrape_carrier_management", [])
    cj  = results.get("scrape_claims_journal", [])
    ibm = results.get("scrape_insurance_business_mag", [])

    all_articles = ij + bi + cm + cj + ibm

    # Score + pick top 10 before enriching (avoids fetching pages we discard)
    top_10 = score_and_rank(all_articles)

    # Fetch summaries in parallel for articles that don't have one
    top_10 = enrich_summaries(top_10)

    return {
        "articles": top_10,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "stats": {
            "total_scraped": len(all_articles),
            "insurance_journal": len(ij),
            "business_insurance": len(bi),
            "carrier_management": len(cm),
            "claims_journal": len(cj),
            "insurance_business_mag": len(ibm),
        },
    }


# ---------------------------------------------------------------------------
# Categorisation — AI (Claude Haiku) with keyword fallback
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Property & Casualty": ["property","casualty","liability","home","fire","flood","theft","damage","p&c","dwelling","homeowner"],
    "Reinsurance":         ["reinsurance","reinsurer","retrocession","treaty","facultative","swiss re","munich re","cedent","lloyd's"],
    "Markets":             ["investment","acquisition","merger","premium","rate","pricing","revenue","profit","loss ratio","combined ratio","ipo"],
    "Cyber":               ["cyber","ransomware","data breach","hack","malware","technology","digital"," ai ","cloud","phishing"],
    "Climate & CAT":       ["climate","catastrophe"," cat ","flood","wildfire","hurricane","tornado","earthquake","storm","esg","net zero"],
    "Life & Health":       ["life","health","medical","mortality","longevity","annuity","pension","benefit","wellness","mental health"],
    "Regulatory":          ["naic","fca","regulatory","regulation","compliance","legislation","bill"," law ","ruling","mandate","solvency"],
    "Commercial":          ["commercial","corporate","enterprise","workers compensation","employer","sme"],
    "Motor":               ["motor","auto","vehicle"," car ","truck","fleet"," ev ","autonomous","telematics","road"],
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
        "You are an insurance industry expert. Categorise each article into one or more of these exact categories:\n"
        "Property & Casualty, Reinsurance, Markets, Cyber, Climate & CAT, Life & Health, Regulatory, Commercial, Motor\n\n"
        "Rules:\n"
        "- Each article MUST have at least one category\n"
        "- Each article can have multiple categories if genuinely relevant\n"
        "- Be precise — only assign categories that clearly match\n"
        "- Return ONLY valid JSON, no explanation, no markdown\n\n"
        f"Articles: {articles_json}\n\n"
        'Return format: [{"id": 0, "categories": ["Markets", "Reinsurance"]}, ...]'
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
