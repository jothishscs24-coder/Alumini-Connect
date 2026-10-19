"""Tests for the labeled ranking evaluation and its offline baseline."""
import json

import pytest

from backend.evaluation.recommender_eval import (
    CORPUS_PATH,
    evaluate_rankings,
    ranking_metrics,
    run_evaluation,
    validate_corpus,
)


def test_ranking_metrics_rewards_relevant_candidates_near_the_top():
    grades = {"best": 3, "also-good": 1, "irrelevant": 0}
    best = ranking_metrics(["best", "also-good", "irrelevant"], grades, 3)
    worse = ranking_metrics(["irrelevant", "also-good", "best"], grades, 3)

    assert best == {"recall_at_k": 1.0, "mrr_at_k": 1.0, "ndcg_at_k": 1.0}
    assert worse["recall_at_k"] == 1.0
    assert worse["mrr_at_k"] == 0.5
    assert 0 < worse["ndcg_at_k"] < best["ndcg_at_k"]


def test_metric_input_validation():
    with pytest.raises(ValueError, match="at least 1"):
        ranking_metrics(["candidate"], {"candidate": 1}, 0)
    with pytest.raises(ValueError, match="At least one candidate"):
        ranking_metrics(["candidate"], {"candidate": 0}, 1)


def test_corpus_is_valid_and_contains_only_labeled_candidates():
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    validate_corpus(corpus)
    assert corpus["schema_version"] == 1
    assert all(set(query["relevance"]) == {candidate["id"] for candidate in query["candidates"]}
               for query in corpus["queries"])


def test_corpus_rejects_missing_candidate_labels():
    bad = {"queries": [{"id": "q", "profile": "student", "candidates": [{"id": "a", "profile": "alumnus"}], "relevance": {}}]}
    with pytest.raises(ValueError, match="label every candidate"):
        validate_corpus(bad)


def test_lexical_baseline_runs_on_synthetic_corpus():
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    report = run_evaluation(corpus, mode="lexical", k=3)

    assert report["ranking_method"] == "lexical_fallback"
    assert report["query_count"] == len(corpus["queries"])
    assert set(report["macro_average"]) == {"recall_at_k", "mrr_at_k", "ndcg_at_k"}
    assert all(len(row["ranking"]) == len(query["candidates"])
               for row, query in zip(report["queries"], corpus["queries"]))


def test_semantic_mode_scores_with_embeddings(monkeypatch):
    import backend.app.main as app_main

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    vectors = {}
    query_vectors = {
        "backend-career": [1.0, 0.0, 0.0, 0.0],
        "data-career": [0.0, 1.0, 0.0, 0.0],
        "product-career": [0.0, 0.0, 1.0, 0.0],
        "career-mentoring": [0.5, 0.1, 0.35, 0.0],
    }
    candidate_vectors = {
        "backend-platform": [1.0, 0.0, 0.0, 0.0],
        "cloud-engineering": [0.8, 0.2, 0.0, 0.0],
        "product-marketing": [0.0, 0.0, 1.0, 0.0],
        "visual-design": [0.0, 0.0, 0.0, 1.0],
        "applied-data-science": [0.0, 1.0, 0.0, 0.0],
        "business-intelligence": [0.0, 0.8, 0.2, 0.0],
        "product-strategy": [0.0, 0.0, 1.0, 0.0],
        "user-research": [0.0, 0.0, 0.9, 0.1],
        "career-mentor": [0.5, 0.1, 0.35, 0.0],
    }
    for query in corpus["queries"]:
        vectors[query["profile"]] = query_vectors[query["id"]]
        for candidate in query["candidates"]:
            vectors[candidate["profile"]] = candidate_vectors[candidate["id"]]
    monkeypatch.setattr(app_main, "_embed", lambda text: vectors[text])

    report = run_evaluation(corpus, mode="semantic", k=3)

    assert report["ranking_method"] == "semantic_embeddings"
    assert report["embedding_model"]
    assert report["macro_average"]["ndcg_at_k"] > 0
    for row, query in zip(report["queries"], corpus["queries"]):
        best_relevance = max(query["relevance"].values())
        assert query["relevance"][row["ranking"][0]] == best_relevance


def test_semantic_mode_fails_instead_of_silently_using_fallback(monkeypatch):
    import backend.app.main as app_main

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(app_main, "_embed", lambda text: None)
    with pytest.raises(RuntimeError, match="did not produce every embedding"):
        run_evaluation(corpus, mode="semantic", k=3)


def test_evaluation_rejects_unknown_mode():
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="mode must be"):
        run_evaluation(corpus, mode="unknown", k=3)
