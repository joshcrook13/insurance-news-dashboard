"""
FastAPI endpoint tests using TestClient.
Scrapers and Anthropic API are fully mocked — no network calls.
"""
import pytest
import requests
from datetime import datetime
from unittest.mock import MagicMock
from conftest import MockResponse, ARTICLE_HTML_STRUCTURED, scraper_side_effect


# ===========================================================================
# GET /health
# ===========================================================================

def test_health_returns_200(client):
    assert client.get("/health").status_code == 200


def test_health_status_ok(client):
    assert client.get("/health").json()["status"] == "ok"


def test_health_has_time_field(client):
    data = client.get("/health").json()
    assert "time" in data
    assert data["time"].endswith("Z")
    # Parseable as datetime
    datetime.fromisoformat(data["time"].rstrip("Z"))


# ===========================================================================
# GET /news
# ===========================================================================

def test_news_returns_200(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    assert client.get("/news").status_code == 200


def test_news_response_schema(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    data = client.get("/news").json()
    assert "articles" in data
    assert "fetched_at" in data
    assert "stats" in data
    assert data["fetched_at"].endswith("Z")
    for key in ("total_scraped", "insurance_journal", "business_insurance",
                "carrier_management", "claims_journal", "insurance_business_mag"):
        assert key in data["stats"]


def test_news_articles_is_list(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    data = client.get("/news").json()
    assert isinstance(data["articles"], list)


def test_news_articles_max_10(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    data = client.get("/news").json()
    assert len(data["articles"]) <= 10


def test_news_article_schema(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    articles = client.get("/news").json()["articles"]
    if articles:
        a = articles[0]
        for key in ("title", "source", "sources", "date", "summary", "url",
                    "relevance_score", "trending_reason", "is_trending", "source_count"):
            assert key in a, f"Missing key: {key}"


def test_news_all_scrapers_fail_returns_empty(client, mocker):
    mocker.patch("main.requests.get", side_effect=requests.ConnectionError("timeout"))
    data = client.get("/news").json()
    assert data["articles"] == []
    assert data["stats"]["total_scraped"] == 0


def test_news_partial_scraper_failure(client, mocker):
    call_count = {"n": 0}

    def flaky(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise requests.ConnectionError("fail")
        return MockResponse(text=ARTICLE_HTML_STRUCTURED)

    mocker.patch("main.requests.get", side_effect=flaky)
    data = client.get("/news").json()
    assert data["stats"]["total_scraped"] >= 0  # did not crash


def test_news_stats_values_are_integers(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    stats = client.get("/news").json()["stats"]
    for v in stats.values():
        assert isinstance(v, int)


def test_news_fetched_at_parseable(client, mocker):
    mocker.patch("main.requests.get", side_effect=scraper_side_effect(ARTICLE_HTML_STRUCTURED))
    fetched_at = client.get("/news").json()["fetched_at"]
    datetime.fromisoformat(fetched_at.rstrip("Z"))


# ===========================================================================
# POST /categorise
# ===========================================================================

def test_categorise_empty_articles(client):
    resp = client.post("/categorise", json={"articles": []})
    data = resp.json()
    assert data["results"] == []
    assert data["method"] == "none"


def test_categorise_missing_articles_key(client):
    resp = client.post("/categorise", json={})
    assert resp.json()["results"] == []
    assert resp.json()["method"] == "none"


def test_categorise_keyword_fallback_no_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/categorise", json={
        "articles": [{"id": 0, "title": "Ransomware Cyber Attack Claims", "summary": ""}]
    })
    data = resp.json()
    assert data["method"] == "keywords"
    assert "Cyber" in data["results"][0]["categories"]


def test_categorise_keyword_fallback_default_markets(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/categorise", json={
        "articles": [{"id": 0, "title": "Insurance Industry Update", "summary": ""}]
    })
    assert resp.json()["results"][0]["categories"] == ["Markets"]


def test_categorise_keyword_preserves_all_ids(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = client.post("/categorise", json={
        "articles": [
            {"id": 5,  "title": "Hurricane Catastrophe CAT Losses", "summary": ""},
            {"id": 10, "title": "Flood Insurance Reform Regulation", "summary": ""},
            {"id": 99, "title": "Motor Fleet Telematics Auto", "summary": ""},
        ]
    })
    result_ids = {r["id"] for r in resp.json()["results"]}
    assert result_ids == {5, 10, 99}


def test_categorise_ai_path_used_with_api_key(client, mocker, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")

    mock_content = MagicMock()
    mock_content.text = '[{"id": 0, "categories": ["Cyber"]}]'
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    mock_ai_client = MagicMock()
    mock_ai_client.messages.create.return_value = mock_message
    mock_class = mocker.patch("main.anthropic.Anthropic")
    mock_class.return_value = mock_ai_client

    resp = client.post("/categorise", json={
        "articles": [{"id": 0, "title": "Cyber Ransomware Attack", "summary": ""}]
    })
    data = resp.json()
    assert data["method"] == "ai"
    assert data["results"][0]["categories"] == ["Cyber"]


def test_categorise_ai_strips_markdown_fences(client, mocker, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")

    mock_content = MagicMock()
    mock_content.text = '```json\n[{"id": 0, "categories": ["Markets"]}]\n```'
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    mock_ai_client = MagicMock()
    mock_ai_client.messages.create.return_value = mock_message
    mocker.patch("main.anthropic.Anthropic").return_value = mock_ai_client

    resp = client.post("/categorise", json={
        "articles": [{"id": 0, "title": "Market Update", "summary": ""}]
    })
    assert resp.json()["method"] == "ai"
    assert resp.json()["results"][0]["categories"] == ["Markets"]


def test_categorise_ai_fallback_on_api_exception(client, mocker, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")

    mock_ai_client = MagicMock()
    mock_ai_client.messages.create.side_effect = Exception("API error")
    mocker.patch("main.anthropic.Anthropic").return_value = mock_ai_client

    resp = client.post("/categorise", json={
        "articles": [{"id": 0, "title": "Cyber Ransomware Hack Data Breach", "summary": ""}]
    })
    data = resp.json()
    assert data["method"] == "keywords"
    assert "Cyber" in data["results"][0]["categories"]


def test_categorise_ai_fallback_on_invalid_json(client, mocker, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")

    mock_content = MagicMock()
    mock_content.text = "not valid json at all"
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    mock_ai_client = MagicMock()
    mock_ai_client.messages.create.return_value = mock_message
    mocker.patch("main.anthropic.Anthropic").return_value = mock_ai_client

    resp = client.post("/categorise", json={
        "articles": [{"id": 0, "title": "Hurricane Catastrophe Losses", "summary": ""}]
    })
    assert resp.json()["method"] == "keywords"
