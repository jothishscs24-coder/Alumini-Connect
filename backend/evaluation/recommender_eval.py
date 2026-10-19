"""Evaluate recommendation rankings on a small, synthetic labeled corpus.

Run with ``python -m backend.evaluation.recommender_eval --mode lexical`` for
the deterministic CI baseline, or ``--mode semantic`` to require the configured
SentenceTransformer model and measure real semantic rankings.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from statistics import mean
from typing import Any

CORPUS_PATH = Path(__file__).with_name("recommender_corpus.json")
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def validate_corpus(corpus: dict[str, Any]) -> None:
    queries = corpus.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("Corpus must contain a non-empty queries list")
    query_ids: set[str] = set()
    for query in queries:
        query_id = query.get("id")
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            raise ValueError("Each query must have a unique, non-empty id")
        query_ids.add(query_id)
        candidates = query.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"Query {query_id} must have candidates")
        candidate_ids = [candidate.get("id") for candidate in candidates]
        if any(not isinstance(candidate_id, str) or not candidate_id for candidate_id in candidate_ids):
            raise ValueError(f"Query {query_id} has a candidate without an id")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"Query {query_id} has duplicate candidate ids")
        relevance = query.get("relevance")
        if not isinstance(relevance, dict) or set(relevance) != set(candidate_ids):
            raise ValueError(f"Query {query_id} must label every candidate exactly once")
        if any(not isinstance(value, int) or value < 0 for value in relevance.values()):
            raise ValueError(f"Query {query_id} relevance values must be non-negative integers")
        if not any(relevance.values()):
            raise ValueError(f"Query {query_id} must have at least one relevant candidate")
        if not isinstance(query.get("profile"), str) or not query["profile"].strip():
            raise ValueError(f"Query {query_id} has no profile text")
        if any(not isinstance(candidate.get("profile"), str) or not candidate["profile"].strip() for candidate in candidates):
            raise ValueError(f"Query {query_id} has a candidate with no profile text")


def ranking_metrics(ranked_ids: list[str], relevance: dict[str, int], k: int) -> dict[str, float]:
    """Return Recall@K, MRR@K, and graded NDCG@K for one ranking."""
    if k < 1:
        raise ValueError("k must be at least 1")
    relevant_count = sum(value > 0 for value in relevance.values())
    if relevant_count == 0:
        raise ValueError("At least one candidate must be relevant")
    top = ranked_ids[:k]
    hits = [position for position, candidate_id in enumerate(top, start=1) if relevance.get(candidate_id, 0) > 0]
    recall = len(hits) / relevant_count
    reciprocal_rank = 1 / hits[0] if hits else 0.0
    dcg = sum((2 ** relevance.get(candidate_id, 0) - 1) / math.log2(position + 1)
              for position, candidate_id in enumerate(top, start=1))
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]
    ideal_dcg = sum((2 ** grade - 1) / math.log2(position + 1)
                    for position, grade in enumerate(ideal_grades, start=1))
    return {"recall_at_k": recall, "mrr_at_k": reciprocal_rank, "ndcg_at_k": dcg / ideal_dcg if ideal_dcg else 0.0}


def evaluate_rankings(cases: list[dict[str, Any]], ranked_cases: dict[str, list[str]], k: int) -> dict[str, Any]:
    rows = []
    for case in cases:
        query_id = case["id"]
        metrics = ranking_metrics(ranked_cases[query_id], case["relevance"], k)
        rows.append({"query_id": query_id, **metrics, "ranking": ranked_cases[query_id]})
    return {
        "k": k,
        "query_count": len(rows),
        "macro_average": {metric: mean(row[metric] for row in rows)
                          for metric in ("recall_at_k", "mrr_at_k", "ndcg_at_k")},
        "queries": rows,
    }


def _score_corpus(corpus: dict[str, Any], mode: str) -> tuple[dict[str, list[str]], str]:
    # Import app functions only when scoring, so metric-only tests stay lightweight.
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("JWT_SECRET", "recommender-evaluation-only-secret-2026")
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from backend.app.main import _cosine, _embed, _fallback_similarity

    if mode == "lexical":
        semantic_vectors: dict[str, list[float] | None] = {}
        method = "lexical_fallback"
    else:
        texts = {query["profile"] for query in corpus["queries"]}
        texts.update(candidate["profile"] for query in corpus["queries"] for candidate in query["candidates"])
        semantic_vectors = {text: _embed(text) for text in sorted(texts)}
        semantic_active = all(vector is not None for vector in semantic_vectors.values())
        if mode == "semantic" and not semantic_active:
            raise RuntimeError("Semantic mode requested, but the configured model did not produce every embedding")
        method = "semantic_embeddings" if semantic_active else "lexical_fallback"

    ranked_cases: dict[str, list[str]] = {}
    for query in corpus["queries"]:
        query_vector = semantic_vectors.get(query["profile"])
        scored = []
        for candidate in query["candidates"]:
            candidate_vector = semantic_vectors.get(candidate["profile"])
            if method == "semantic_embeddings":
                score = max(0.0, min(1.0, _cosine(query_vector or [], candidate_vector or [])))
            else:
                score = _fallback_similarity(query["profile"], candidate["profile"])
            scored.append((candidate["id"], score))
        ranked_cases[query["id"]] = [candidate_id for candidate_id, _ in sorted(scored, key=lambda item: (-item[1], item[0]))]
    return ranked_cases, method


def run_evaluation(corpus: dict[str, Any], mode: str, k: int) -> dict[str, Any]:
    if mode not in {"lexical", "auto", "semantic"}:
        raise ValueError("mode must be lexical, auto, or semantic")
    validate_corpus(corpus)
    ranked_cases, method = _score_corpus(corpus, mode)
    return {
        "schema_version": 1,
        "corpus_description": corpus.get("description", ""),
        "mode_requested": mode,
        "ranking_method": method,
        "embedding_model": os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL) if method == "semantic_embeddings" else None,
        **evaluate_rankings(corpus["queries"], ranked_cases, k),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("lexical", "auto", "semantic"), default="lexical")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-ndcg", type=float, help="Fail if macro-average NDCG@K is below this value")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path instead of stdout")
    args = parser.parse_args(argv)
    if args.min_ndcg is not None and not 0 <= args.min_ndcg <= 1:
        parser.error("--min-ndcg must be between 0 and 1")
    try:
        corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        report = run_evaluation(corpus, args.mode, args.k)
    except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(f"Recommender evaluation failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    score = report["macro_average"]["ndcg_at_k"]
    print(f"method={report['ranking_method']} ndcg@{args.k}={score:.3f}", file=sys.stderr)
    if args.min_ndcg is not None and score < args.min_ndcg:
        print(f"NDCG@{args.k} {score:.3f} is below required minimum {args.min_ndcg:.3f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
