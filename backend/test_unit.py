"""
Unit tests for pure functions in main.py.
No network calls, no mocking required.
"""
import pytest
from main import normalize_title, titles_similar, keyword_categorise, score_and_rank
from conftest import make_article


# ===========================================================================
# normalize_title
# ===========================================================================

def test_normalize_title_lowercases():
    assert normalize_title("HURRICANE Season LOSSES") == "hurricane season losses"


def test_normalize_title_strips_punctuation():
    result = normalize_title("Fire & Flood: Record Losses!")
    assert "&" not in result
    assert ":" not in result
    assert "!" not in result


def test_normalize_title_preserves_numbers():
    assert normalize_title("Top 10 Insurers in 2024") == "top 10 insurers in 2024"


def test_normalize_title_strips_whitespace():
    assert normalize_title("  Hurricane Losses  ") == "hurricane losses"


def test_normalize_title_empty_string():
    assert normalize_title("") == ""


def test_normalize_title_only_punctuation():
    assert normalize_title("!!! ???") == ""


def test_normalize_title_ampersand_word_set_splits_correctly():
    # "Fire & Flood" -> "fire  flood" but .split() handles double space
    n = normalize_title("Fire & Flood Losses")
    words = set(n.split())
    assert "fire" in words
    assert "flood" in words


# ===========================================================================
# titles_similar
# ===========================================================================

def test_titles_similar_identical():
    t = "Hurricane Season Drives Record Catastrophe Losses"
    assert titles_similar(t, t) is True


def test_titles_similar_paraphrased_same_story():
    t1 = "Hurricane Season Drives Record Catastrophe Losses"
    t2 = "Hurricane Season Causes Record Catastrophe Losses"
    assert titles_similar(t1, t2) is True


def test_titles_similar_completely_different():
    t1 = "Cyber Insurance Premiums Rise After Ransomware Surge"
    t2 = "Hurricane Season Drives Record Catastrophe Losses"
    assert titles_similar(t1, t2) is False


def test_titles_similar_stopwords_only_returns_false():
    # After stopword removal both sets are empty
    assert titles_similar("the in to and of", "the in to and of") is False


def test_titles_similar_partial_overlap_below_threshold():
    t1 = "Cyber Market Hardens After Data Breach"
    t2 = "Motor Fleet Telematics Market Growth"
    assert titles_similar(t1, t2) is False


def test_titles_similar_relaxed_threshold():
    t1 = "Cyber Market Hardens After Data Breach"
    t2 = "Motor Fleet Telematics Market Growth"
    assert titles_similar(t1, t2, threshold=0.1) is True


def test_titles_similar_impossible_threshold():
    t = "Hurricane Season Drives Record Catastrophe Losses"
    assert titles_similar(t, t, threshold=1.1) is False


def test_titles_similar_single_word_match():
    assert titles_similar("Reinsurance", "Reinsurance") is True


def test_titles_similar_stopwords_stripped():
    t1 = "The Flood is on the Rise"
    t2 = "A Flood on the Rise"
    assert titles_similar(t1, t2) is True


# ===========================================================================
# keyword_categorise
# ===========================================================================

def test_keyword_categorise_cyber():
    result = keyword_categorise([{"id": 0, "title": "Ransomware Attack Drives Cyber Claims", "summary": "Hackers targeted data"}])
    assert "Cyber" in result[0]["categories"]


def test_keyword_categorise_climate_cat():
    result = keyword_categorise([{"id": 0, "title": "Hurricane Catastrophe Season Record", "summary": "Climate losses surge"}])
    assert "Climate & CAT" in result[0]["categories"]


def test_keyword_categorise_default_markets_fallback():
    result = keyword_categorise([{"id": 0, "title": "Insurance Industry News", "summary": "General update"}])
    assert result[0]["categories"] == ["Markets"]


def test_keyword_categorise_multiple_categories():
    result = keyword_categorise([{"id": 0, "title": "Cyber Regulatory Compliance Update", "summary": "NAIC data breach mandate"}])
    cats = result[0]["categories"]
    assert "Cyber" in cats
    assert "Regulatory" in cats


def test_keyword_categorise_preserves_id():
    result = keyword_categorise([{"id": 99, "title": "Flood claims rise", "summary": ""}])
    assert result[0]["id"] == 99


def test_keyword_categorise_empty_list():
    assert keyword_categorise([]) == []


def test_keyword_categorise_case_insensitive():
    result = keyword_categorise([{"id": 0, "title": "FLOOD WILDFIRE HURRICANE", "summary": ""}])
    assert "Climate & CAT" in result[0]["categories"]


def test_keyword_categorise_reinsurance_keywords():
    result = keyword_categorise([{"id": 0, "title": "Swiss Re Treaty Cedent Agreement", "summary": "Retrocession deal signed"}])
    assert "Reinsurance" in result[0]["categories"]


def test_keyword_categorise_motor_keywords():
    result = keyword_categorise([{"id": 0, "title": "EV Fleet Telematics Expansion", "summary": "autonomous road vehicles"}])
    assert "Motor" in result[0]["categories"]


def test_keyword_categorise_ai_keyword_with_spaces():
    # " ai " keyword requires space padding — title starts with AI
    result = keyword_categorise([{"id": 0, "title": "AI Transforms Insurance Underwriting", "summary": ""}])
    assert "Cyber" in result[0]["categories"]


def test_keyword_categorise_property_casualty():
    result = keyword_categorise([{"id": 0, "title": "P&C Market Rates Harden", "summary": "homeowner dwelling damage"}])
    assert "Property & Casualty" in result[0]["categories"]


def test_keyword_categorise_life_health():
    result = keyword_categorise([{"id": 0, "title": "Longevity Risk in Pension Annuity Markets", "summary": "mortality benefit wellness"}])
    assert "Life & Health" in result[0]["categories"]


def test_keyword_categorise_regulatory():
    result = keyword_categorise([{"id": 0, "title": "NAIC Issues Solvency Ruling on Compliance", "summary": "mandate legislation"}])
    assert "Regulatory" in result[0]["categories"]


def test_keyword_categorise_commercial():
    result = keyword_categorise([{"id": 0, "title": "Workers Compensation Commercial SME Market", "summary": "employer corporate enterprise"}])
    assert "Commercial" in result[0]["categories"]


# ===========================================================================
# score_and_rank
# ===========================================================================

def test_score_and_rank_empty():
    assert score_and_rank([]) == []


def test_score_and_rank_single_article_score():
    articles = [make_article()]
    result = score_and_rank(articles)
    assert len(result) == 1
    # base 1.0 + position bonus max 2.0 = 3.0
    assert result[0]["relevance_score"] == 3.0


def test_score_and_rank_multi_source_bonus():
    articles = [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal"),
        make_article(title="Hurricane Season Causes Record Catastrophe Losses", source="Business Insurance"),
    ]
    result = score_and_rank(articles)
    assert len(result) == 1
    assert result[0]["source_count"] == 2
    assert result[0]["relevance_score"] > 10.0  # base + 2*5 + position


def test_score_and_rank_trending_bonus():
    articles = [make_article(is_trending=True)]
    result = score_and_rank(articles)
    # base 1.0 + trending 3.0 + position 2.0 = 6.0
    assert result[0]["relevance_score"] == 6.0
    assert result[0]["is_trending"] is True


def test_score_and_rank_comment_bonus():
    articles = [make_article(comment_count=10)]
    result = score_and_rank(articles)
    # base 1.0 + comment (10/10)*4.0 + position 2.0 = 7.0
    assert result[0]["relevance_score"] == 7.0


def test_score_and_rank_capped_at_10():
    articles = [make_article(title=f"Article {i}", url=f"https://example.com/{i}") for i in range(15)]
    assert len(score_and_rank(articles)) == 10


def test_score_and_rank_sorted_descending():
    articles = [
        make_article(title="Cyber Ransomware Hack Attack Data Breach", source="Insurance Journal", is_trending=True),
        make_article(title="Motor Fleet Telematics Update", source="Carrier Management"),
        make_article(title="Life Health Pension Annuity Benefit Wellness Mortality", source="Business Insurance", comment_count=5),
    ]
    result = score_and_rank(articles)
    scores = [a["relevance_score"] for a in result]
    assert scores == sorted(scores, reverse=True)


def test_score_and_rank_deduplication():
    articles = [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal"),
        make_article(title="Hurricane Season Causes Record Catastrophe Losses", source="Business Insurance"),
        make_article(title="Cyber Insurance Market Hardens After Data Breach Wave", source="Carrier Management"),
    ]
    result = score_and_rank(articles)
    assert len(result) == 2


def test_score_and_rank_best_summary_selected():
    articles = [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal", summary=""),
        make_article(title="Hurricane Season Causes Record Catastrophe Losses", source="Business Insurance", summary="Long meaningful summary about hurricane losses"),
    ]
    result = score_and_rank(articles)
    assert result[0]["summary"] == "Long meaningful summary about hurricane losses"


def test_score_and_rank_single_source_format():
    articles = [make_article(source="Insurance Journal")]
    result = score_and_rank(articles)
    assert result[0]["source"] == "Insurance Journal"


def test_score_and_rank_multi_source_format():
    articles = [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal"),
        make_article(title="Hurricane Season Causes Record Catastrophe Losses", source="Business Insurance"),
    ]
    result = score_and_rank(articles)
    assert " & " in result[0]["source"]
    assert set(result[0]["sources"]) == {"Insurance Journal", "Business Insurance"}


def test_score_and_rank_latest_news_fallback_reason():
    articles = [make_article()]
    result = score_and_rank(articles)
    assert "Latest news" in result[0]["trending_reason"]


def test_score_and_rank_is_trending_set_for_multi_source():
    # Neither article is individually trending
    articles = [
        make_article(title="Hurricane Season Drives Record Catastrophe Losses", source="Insurance Journal", is_trending=False),
        make_article(title="Hurricane Season Causes Record Catastrophe Losses", source="Business Insurance", is_trending=False),
    ]
    result = score_and_rank(articles)
    assert result[0]["is_trending"] is True


def test_score_and_rank_position_bonus_decreases():
    # Build 20 unique articles; first should score higher than last
    articles = [make_article(title=f"Unique Unrelated Story Number {i} About Nothing", url=f"https://x.com/{i}") for i in range(20)]
    result = score_and_rank(articles)
    assert result[0]["relevance_score"] >= result[-1]["relevance_score"]
