"""
Live integration tests against the production Render API.
These hit the real https://insurance-news-dashboard.onrender.com endpoints.

Run all:     pytest test_live.py -v
Skip live:   pytest -m "not live"
"""
import pytest
import requests
from datetime import datetime, timezone

BASE_URL = "https://insurance-news-dashboard.onrender.com"

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Session-scoped fixture — fetch /news once, reuse across all news tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def live_news():
    resp = requests.get(f"{BASE_URL}/news", timeout=60)
    return resp


# ===========================================================================
# GET /health
# ===========================================================================

def test_live_health_reachable():
    resp = requests.get(f"{BASE_URL}/health", timeout=60)
    assert resp.status_code == 200


def test_live_health_status_ok():
    resp = requests.get(f"{BASE_URL}/health", timeout=30)
    assert resp.json()["status"] == "ok"


def test_live_health_time_is_recent():
    resp = requests.get(f"{BASE_URL}/health", timeout=30)
    time_str = resp.json()["time"].rstrip("Z")
    dt = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = abs((now - dt).total_seconds())
    assert diff < 60, f"Server time {dt} is more than 60s from now {now}"


# ===========================================================================
# GET /news
# ===========================================================================

def test_live_news_returns_200(live_news):
    assert live_news.status_code == 200


def test_live_news_has_required_keys(live_news):
    data = live_news.json()
    for key in ("articles", "fetched_at", "stats"):
        assert key in data


def test_live_news_articles_is_list(live_news):
    assert isinstance(live_news.json()["articles"], list)


def test_live_news_articles_capped_at_10(live_news):
    assert len(live_news.json()["articles"]) <= 10


@pytest.mark.xfail(reason="Scrapers may time out on free-tier cold start")
def test_live_news_articles_non_empty(live_news):
    assert len(live_news.json()["articles"]) > 0


def test_live_news_article_schema(live_news):
    articles = live_news.json()["articles"]
    for a in articles:
        for key in ("title", "source", "sources", "url",
                    "relevance_score", "is_trending", "source_count", "trending_reason"):
            assert key in a, f"Article missing key: {key}"


def test_live_news_scores_sorted_descending(live_news):
    scores = [a["relevance_score"] for a in live_news.json()["articles"]]
    assert scores == sorted(scores, reverse=True)


def test_live_news_stats_total_scraped_non_negative(live_news):
    assert live_news.json()["stats"]["total_scraped"] >= 0


def test_live_news_fetched_at_utc(live_news):
    fetched_at = live_news.json()["fetched_at"]
    assert fetched_at.endswith("Z")
    datetime.fromisoformat(fetched_at.rstrip("Z"))


# ===========================================================================
# POST /categorise
# ===========================================================================

def test_live_categorise_response_schema():
    resp = requests.post(
        f"{BASE_URL}/categorise",
        json={"articles": [{"id": 0, "title": "Ransomware Cyber Attack Insurance Claim", "summary": "Data breach hack"}]},
        timeout=30,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "method" in data
    assert data["method"] in ("ai", "keywords", "none")


def test_live_categorise_empty_returns_none_method():
    resp = requests.post(f"{BASE_URL}/categorise", json={"articles": []}, timeout=30)
    data = resp.json()
    assert data["results"] == []
    assert data["method"] == "none"


def test_live_categorise_cyber_article():
    resp = requests.post(
        f"{BASE_URL}/categorise",
        json={"articles": [{"id": 0, "title": "Ransomware Attack Hits Insurance Carrier", "summary": "Cybercriminals targeted data breach"}]},
        timeout=30,
    )
    result = resp.json()["results"][0]
    assert "Cyber" in result["categories"], f"Expected Cyber in {result['categories']}"


def test_live_categorise_multiple_articles_returns_all_ids():
    payload = {
        "articles": [
            {"id": 0, "title": "Hurricane Catastrophe CAT Season Losses", "summary": ""},
            {"id": 1, "title": "Flood Insurance Regulatory Reform Bill Mandate", "summary": ""},
            {"id": 2, "title": "Motor Fleet Telematics Auto EV", "summary": ""},
        ]
    }
    resp = requests.post(f"{BASE_URL}/categorise", json=payload, timeout=30)
    result_ids = {r["id"] for r in resp.json()["results"]}
    assert result_ids == {0, 1, 2}
