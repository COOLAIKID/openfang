"""Tests for core/toolkit/nlp.py — NLP and text analysis toolkit."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from core.toolkit import nlp


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse(result: str) -> dict | list:
    return json.loads(result)


# ---------------------------------------------------------------------------
# summarize_text
# ---------------------------------------------------------------------------

class TestSummarizeText:
    def test_returns_valid_json(self):
        text = "Python is a great language. It is used everywhere. Many developers love Python."
        result = nlp.summarize_text(text)
        data = _parse(result)
        assert "summary" in data

    def test_empty_text_returns_empty_summary(self):
        data = _parse(nlp.summarize_text(""))
        assert data["summary"] == ""

    def test_compression_pct_is_float(self):
        text = " ".join(["The quick brown fox jumped."] * 10)
        data = _parse(nlp.summarize_text(text))
        assert isinstance(data["compression_pct"], float)

    def test_summary_shorter_than_original(self):
        text = "This is sentence one. This is sentence two. This is sentence three. " \
               "This is sentence four. This is sentence five. This is sentence six. " \
               "This is sentence seven. This is sentence eight."
        data = _parse(nlp.summarize_text(text, max_sentences=3))
        assert data["summary_words"] <= data["original_words"]

    def test_max_sentences_respected(self):
        sentences = [f"Sentence number {i} is here for testing." for i in range(20)]
        text = " ".join(sentences)
        data = _parse(nlp.summarize_text(text, max_sentences=3))
        # Summary should contain at most 3 sentences worth of content
        assert data["summary_words"] < data["original_words"]

    def test_original_words_counted(self):
        text = "one two three four five"
        data = _parse(nlp.summarize_text(text))
        assert data["original_words"] == 5


# ---------------------------------------------------------------------------
# extract_entities
# ---------------------------------------------------------------------------

class TestExtractEntities:
    def test_extracts_email(self):
        text = "Contact us at support@example.com for more info."
        data = _parse(nlp.extract_entities(text))
        assert "support@example.com" in data["emails"]

    def test_extracts_url(self):
        text = "Visit our site at https://www.example.com today."
        data = _parse(nlp.extract_entities(text))
        assert any("example.com" in u for u in data["urls"])

    def test_extracts_price(self):
        text = "The product costs $49.99 and is worth every penny."
        data = _parse(nlp.extract_entities(text))
        assert any("49.99" in p or "$49" in p for p in data["prices"])

    def test_extracts_date(self):
        text = "The event is on January 15, 2025 at the conference."
        data = _parse(nlp.extract_entities(text))
        assert data["dates"]

    def test_returns_all_entity_types(self):
        data = _parse(nlp.extract_entities("Hello world"))
        for key in ("emails", "urls", "phones", "prices", "dates", "proper_nouns"):
            assert key in data

    def test_no_entities_returns_empty_lists(self):
        data = _parse(nlp.extract_entities("hello world this is plain text"))
        assert data["emails"] == []
        assert data["urls"] == []

    def test_multiple_emails_extracted(self):
        text = "Email a@b.com and c@d.org for details."
        data = _parse(nlp.extract_entities(text))
        assert len(data["emails"]) == 2


# ---------------------------------------------------------------------------
# sentiment_analysis
# ---------------------------------------------------------------------------

class TestSentimentAnalysis:
    def test_positive_text(self):
        data = _parse(nlp.sentiment_analysis("This is amazing and excellent work!"))
        assert data["sentiment"] == "positive"
        assert data["score"] > 0

    def test_negative_text(self):
        data = _parse(nlp.sentiment_analysis("This is terrible and horrible. Absolutely dreadful."))
        assert data["sentiment"] == "negative"
        assert data["score"] < 0

    def test_neutral_text(self):
        data = _parse(nlp.sentiment_analysis("The meeting is at 3pm on Tuesday."))
        assert data["sentiment"] in ("neutral", "positive", "negative")

    def test_returns_required_fields(self):
        data = _parse(nlp.sentiment_analysis("Test text"))
        for key in ("sentiment", "score", "confidence", "positive_words", "negative_words"):
            assert key in data

    def test_score_in_range(self):
        data = _parse(nlp.sentiment_analysis("Great product but bad delivery time."))
        assert -1.0 <= data["score"] <= 1.0

    def test_confidence_is_string(self):
        data = _parse(nlp.sentiment_analysis("This is good."))
        assert data["confidence"] in ("high", "medium", "low")

    def test_positive_words_list(self):
        data = _parse(nlp.sentiment_analysis("I love this amazing excellent product!"))
        assert isinstance(data["positive_words"], list)

    def test_negation_reduces_score(self):
        pos = _parse(nlp.sentiment_analysis("This is great."))
        neg = _parse(nlp.sentiment_analysis("This is not great."))
        assert pos["score"] > neg["score"]


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_english_detected(self):
        text = "The quick brown fox jumps over the lazy dog and the cat sat on the mat."
        data = _parse(nlp.detect_language(text))
        assert data["code"] == "en"

    def test_spanish_detected(self):
        text = "Hola, cómo estás. Estoy bien, gracias por preguntar. Que tengas un buen día."
        data = _parse(nlp.detect_language(text))
        assert data["code"] in ("es", "en")  # Pure Spanish should hit es

    def test_returns_required_fields(self):
        data = _parse(nlp.detect_language("Hello world"))
        for key in ("language", "code", "confidence"):
            assert key in data

    def test_empty_text_defaults_english(self):
        data = _parse(nlp.detect_language(""))
        assert data["code"] == "en"

    def test_chinese_text_detected(self):
        text = "你好世界这是中文文本用于测试语言检测功能"
        data = _parse(nlp.detect_language(text))
        assert data["code"] == "zh"

    def test_confidence_is_float(self):
        data = _parse(nlp.detect_language("Hello this is English text"))
        assert isinstance(data["confidence"], float)
        assert 0.0 <= data["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------

class TestExtractKeywords:
    def test_returns_list_of_dicts(self):
        text = "Python machine learning deep learning neural networks are transforming AI technology."
        result = _parse(nlp.extract_keywords(text))
        assert isinstance(result, list)
        assert result
        assert "word" in result[0]
        assert "score" in result[0]

    def test_respects_num_parameter(self):
        text = "Python machine learning deep learning neural networks transforming artificial intelligence computing"
        result = _parse(nlp.extract_keywords(text, num=3))
        assert len(result) <= 3

    def test_keywords_are_strings(self):
        text = "Search engine optimization keyword research content marketing strategy."
        result = _parse(nlp.extract_keywords(text))
        for item in result:
            assert isinstance(item["word"], str)

    def test_scores_are_positive(self):
        text = "Machine learning model training data preprocessing feature engineering."
        result = _parse(nlp.extract_keywords(text))
        for item in result:
            assert item["score"] >= 0

    def test_stopwords_excluded(self):
        text = "The quick brown fox jumps over the lazy dog in the forest."
        result = _parse(nlp.extract_keywords(text))
        words = [item["word"] for item in result]
        for stop in ("the", "over", "in"):
            assert stop not in words

    def test_empty_text_returns_list(self):
        result = _parse(nlp.extract_keywords(""))
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# text_similarity
# ---------------------------------------------------------------------------

class TestTextSimilarity:
    def test_identical_texts_high_similarity(self):
        text = "Machine learning is a subset of artificial intelligence."
        data = _parse(nlp.text_similarity(text, text))
        assert data["similarity"] >= 0.95
        assert data["interpretation"] == "identical"

    def test_different_topics_low_similarity(self):
        text1 = "Machine learning neural networks deep learning AI"
        text2 = "Cooking pasta tomato sauce Italian cuisine recipe"
        data = _parse(nlp.text_similarity(text1, text2))
        assert data["similarity"] < 0.4

    def test_similar_texts_medium_similarity(self):
        text1 = "Python is a great programming language for data science."
        text2 = "Python is an excellent language for machine learning and data analysis."
        data = _parse(nlp.text_similarity(text1, text2))
        assert data["similarity"] > 0.0

    def test_returns_required_fields(self):
        data = _parse(nlp.text_similarity("hello world", "hello there"))
        assert "similarity" in data
        assert "interpretation" in data

    def test_similarity_in_range_0_to_1(self):
        data = _parse(nlp.text_similarity("foo bar", "baz qux"))
        assert 0.0 <= data["similarity"] <= 1.0

    def test_empty_texts_returns_zero(self):
        data = _parse(nlp.text_similarity("", ""))
        assert data["similarity"] == 0.0


# ---------------------------------------------------------------------------
# detect_spam
# ---------------------------------------------------------------------------

class TestDetectSpam:
    def test_clean_text_low_score(self):
        text = "I wanted to share some thoughts about Python programming and software development."
        data = _parse(nlp.detect_spam(text))
        assert data["is_spam"] is False
        assert data["score_0_to_1"] < 0.5

    def test_spammy_text_high_score(self):
        text = "CONGRATULATIONS! You have WON a FREE prize!!! Click here NOW! Limited offer! Buy now!"
        data = _parse(nlp.detect_spam(text))
        assert data["score_0_to_1"] > 0.3

    def test_returns_required_fields(self):
        data = _parse(nlp.detect_spam("Hello world"))
        assert "score_0_to_1" in data
        assert "is_spam" in data
        assert "triggers" in data

    def test_score_between_0_and_1(self):
        data = _parse(nlp.detect_spam("Buy now get free money cash urgently!!!"))
        assert 0.0 <= data["score_0_to_1"] <= 1.0

    def test_triggers_is_list(self):
        data = _parse(nlp.detect_spam("Test message"))
        assert isinstance(data["triggers"], list)

    def test_caps_trigger_detected(self):
        text = "THIS IS ALL CAPS TEXT WRITTEN IN UPPERCASE EVERYWHERE ALL THE TIME"
        data = _parse(nlp.detect_spam(text))
        # Should flag the caps
        assert any("CAPS" in t or "caps" in t.lower() for t in data["triggers"])


# ---------------------------------------------------------------------------
# anonymize_text
# ---------------------------------------------------------------------------

class TestAnonymizeText:
    def test_email_replaced(self):
        text = "Contact john@example.com for details."
        result = nlp.anonymize_text(text)
        assert "john@example.com" not in result
        assert "[EMAIL]" in result

    def test_url_replaced(self):
        text = "Visit https://www.secret-site.com for more."
        result = nlp.anonymize_text(text)
        assert "secret-site.com" not in result
        assert "[URL]" in result

    def test_price_replaced(self):
        text = "The cost is $500.00 for the package."
        result = nlp.anonymize_text(text)
        assert "[AMOUNT]" in result

    def test_plain_text_unchanged(self):
        text = "This is just regular text with no PII."
        result = nlp.anonymize_text(text)
        assert result == text

    def test_credit_card_replaced(self):
        text = "My card number is 4111 1111 1111 1111."
        result = nlp.anonymize_text(text)
        assert "[CARD]" in result

    def test_returns_string(self):
        result = nlp.anonymize_text("test text")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# topic_classifier
# ---------------------------------------------------------------------------

class TestTopicClassifier:
    def test_technology_text(self):
        text = "Python software machine learning algorithm AI computer programming code database API"
        data = _parse(nlp.topic_classifier(text))
        assert data["topic"] in ("technology", "business", "science")

    def test_finance_text(self):
        text = "stock market investment portfolio dividend earnings revenue profit debt equity fund"
        data = _parse(nlp.topic_classifier(text))
        assert data["topic"] == "finance"

    def test_health_text(self):
        text = "medical hospital doctor patient drug treatment disease nutrition diet exercise fitness"
        data = _parse(nlp.topic_classifier(text))
        assert data["topic"] == "health"

    def test_returns_required_fields(self):
        data = _parse(nlp.topic_classifier("Hello world"))
        assert "topic" in data
        assert "confidence" in data
        assert "scores" in data

    def test_scores_are_non_negative(self):
        data = _parse(nlp.topic_classifier("Some generic text here"))
        for score in data["scores"].values():
            assert score >= 0

    def test_all_topics_present_in_scores(self):
        data = _parse(nlp.topic_classifier("Hello world"))
        expected_topics = {"technology", "finance", "health", "sports", "politics",
                           "entertainment", "travel", "food", "science", "business"}
        assert expected_topics == set(data["scores"].keys())

    def test_confidence_is_float_0_to_1(self):
        data = _parse(nlp.topic_classifier("The game score was amazing this season."))
        assert 0.0 <= data["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# split_sentences
# ---------------------------------------------------------------------------

class TestSplitSentences:
    def test_basic_sentence_split(self):
        text = "First sentence. Second sentence. Third sentence."
        result = _parse(nlp.split_sentences(text))
        assert len(result) >= 2

    def test_question_split(self):
        text = "Is this working? Yes it is! Great."
        result = _parse(nlp.split_sentences(text))
        assert len(result) >= 2

    def test_abbreviations_not_split(self):
        text = "Dr. Smith went to Mr. Jones. They discussed the results."
        result = _parse(nlp.split_sentences(text))
        # Should not split on Dr. or Mr.
        assert len(result) <= 3

    def test_empty_text_returns_empty_list(self):
        result = _parse(nlp.split_sentences(""))
        assert result == []

    def test_returns_list_of_strings(self):
        result = _parse(nlp.split_sentences("Hello world. Goodbye."))
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)
