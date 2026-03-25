import pytest
from fastapi.testclient import TestClient
from main import app


# ---------------------------------------------------------------------------
# TestClient
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Article factory
# ---------------------------------------------------------------------------

def make_article(
    title="Hurricane Season Drives Record Catastrophe Losses",
    source="Insurance Journal",
    summary="",
    url="https://example.com/article/1",
    date="2024-01-15",
    is_trending=False,
    comment_count=0,
):
    return dict(
        title=title, source=source, summary=summary, url=url,
        date=date, is_trending=is_trending, comment_count=comment_count,
    )


@pytest.fixture
def single_article():
    return make_article()


@pytest.fixture
def two_articles_same_story():
    return [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal"),
        make_article(title="Hurricane Season Causes Record Catastrophe Losses", source="Business Insurance"),
    ]


@pytest.fixture
def two_articles_different_story():
    return [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal"),
        make_article(title="Cyber Insurance Market Hardens After Data Breach Wave", source="Carrier Management"),
    ]


@pytest.fixture
def article_with_comments():
    return make_article(
        title="Flood Insurance Reform Bill Passes Senate Committee",
        source="Insurance Journal",
        comment_count=42,
        is_trending=True,
    )


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

ARTICLE_HTML_STRUCTURED = """
<html><body>
  <article>
    <h2><a href="/news/2024/hurricane-season">Hurricane Season Drives Record Losses</a></h2>
    <a href="/news/2024/hurricane-season">Read more</a>
    <time>January 15, 2024</time>
    <p>Insurers faced record catastrophe losses this hurricane season.</p>
  </article>
  <article>
    <h2><a href="/news/2024/cyber-claims">Cyber Claims Surge in Q4</a></h2>
    <a href="/news/2024/cyber-claims">Read more</a>
    <time>January 14, 2024</time>
    <p>Ransomware attacks drove a surge in cyber insurance claims.</p>
  </article>
</body></html>
"""

ARTICLE_HTML_FALLBACK = """
<html><body>
  <a href="/news/2024/story-one">Flood Insurance Reform Bill Passes Senate After Long Debate</a>
  <a href="/news/2024/story-two">Motor Insurance Telematics Adoption Grows Across Fleet Market</a>
  <a href="/about">About us</a>
</body></html>
"""

META_DESCRIPTION_HTML = """
<html><head>
  <meta name="description" content="Enriched summary fetched from article page.">
</head><body></body></html>
"""


@pytest.fixture
def structured_html():
    return ARTICLE_HTML_STRUCTURED


@pytest.fixture
def fallback_html():
    return ARTICLE_HTML_FALLBACK


# ---------------------------------------------------------------------------
# Mock HTTP response helper
# ---------------------------------------------------------------------------

import requests as _requests


class MockResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _requests.HTTPError(response=self)


def scraper_side_effect(scraper_html, enrichment_html=""):
    """Return a side_effect callable that routes scraper vs enrichment URLs."""
    scraper_domains = (
        "insurancejournal.com",
        "businessinsurance.com",
        "carriermanagement.com",
        "claimsjournal.com",
        "insurancebusinessmag.com",
    )

    def _side_effect(url, **kwargs):
        if any(d in url for d in scraper_domains):
            return MockResponse(text=scraper_html)
        return MockResponse(text=enrichment_html or META_DESCRIPTION_HTML)

    return _side_effect
