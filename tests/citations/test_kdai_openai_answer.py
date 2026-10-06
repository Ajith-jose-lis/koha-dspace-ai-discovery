#!/usr/bin/env python3
"""Generate one evidence-grounded KDAI answer with OpenAI."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from fastembed import TextEmbedding
from openai import OpenAI
from qdrant_client import QdrantClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--collection", default="kdai_evidence_v1")
    parser.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--openai-model", default=os.environ.get("OPENAI_MODEL", "gpt-6-luna"))
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--evidence-limit", type=int, default=6)
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--max-per-document", type=int, default=2)
    parser.add_argument("--max-output-tokens", type=int, default=700)
    return parser.parse_args()


def select_diverse(points: list, limit: int, max_per_document: int) -> list:
    """Select high-scoring evidence while ensuring both collections are represented."""
    chosen: list = []
    selected_ids: set = set()
    document_counts: Counter = Counter()

    def document_key(point) -> str:
        payload = point.payload or {}
        return str(payload.get("document_id") or payload.get("vufind_id") or point.id)

    def add(point) -> bool:
        if point.id in selected_ids:
            return False
        key = document_key(point)
        if document_counts[key] >= max_per_document:
            return False
        chosen.append(point)
        selected_ids.add(point.id)
        document_counts[key] += 1
        return True

    # Reserve two places for each source when relevant evidence exists.
    for source in ("koha", "dspace"):
        count = 0
        for point in points:
            if (point.payload or {}).get("source") == source and add(point):
                count += 1
                if count == 2 or len(chosen) == limit:
                    break

    for point in points:
        if len(chosen) == limit:
            break
        add(point)
    return sorted(chosen, key=lambda point: point.score, reverse=True)


def build_evidence(points: list) -> tuple[str, list[dict]]:
    blocks: list[str] = []
    sources: list[dict] = []
    for number, point in enumerate(points, start=1):
        payload = point.payload or {}
        title = str(payload.get("title") or "Untitled")
        locator = str(payload.get("locator") or "Record")
        source = str(payload.get("source") or "unknown").upper()
        text = str(payload.get("text") or "").strip()
        record_url = str(payload.get("record_url") or "")
        blocks.append(
            f"[SOURCE {number}]\n"
            f"Collection: {source}\n"
            f"Title: {title}\n"
            f"Locator: {locator}\n"
            f"Record URL: {record_url}\n"
            f"Evidence:\n{text}"
        )
        sources.append(
            {
                "number": number,
                "source": source,
                "title": title,
                "locator": locator,
                "record_url": record_url,
                "score": float(point.score),
            }
        )
    return "\n\n".join(blocks), sources


def main() -> int:
    args = parse_args()
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    qdrant_key = os.environ.get("QDRANT_API_KEY", "")
    if not openai_key:
        raise SystemExit("OPENAI_API_KEY is not set")
    if not qdrant_key:
        raise SystemExit("QDRANT_API_KEY is not set")

    print("Retrieving library evidence...", flush=True)
    embedding = TextEmbedding(model_name=args.embedding_model)
    vector = list(embedding.embed([args.query]))[0].tolist()
    qdrant = QdrantClient(url=args.qdrant_url, api_key=qdrant_key, timeout=120)
    candidates = qdrant.query_points(
        collection_name=args.collection,
        query=vector,
        limit=args.candidate_limit,
        with_payload=True,
    ).points
    evidence_points = select_diverse(candidates, args.evidence_limit, args.max_per_document)
    if not evidence_points:
        raise SystemExit("No library evidence was retrieved")
    evidence_text, sources = build_evidence(evidence_points)

    instructions = (
        "You are the evidence-grounded AI assistant for the Koha-DSpace AI Library. "
        "Answer only from the supplied library evidence. Do not use unsupported outside facts. "
        "Cite every substantive claim with bracketed source numbers such as [1] or [2]. "
        "Never invent a citation, title, author, page, quotation, URL, or availability statement. "
        "If the evidence is insufficient, state that clearly and suggest a narrower library search. "
        "Write a concise, accessible answer with a short direct answer first."
    )
    user_input = f"USER QUESTION:\n{args.query}\n\nLIBRARY EVIDENCE:\n{evidence_text}"

    print(f"Generating answer with {args.openai_model}...", flush=True)
    response = OpenAI(api_key=openai_key).responses.create(
        model=args.openai_model,
        instructions=instructions,
        input=user_input,
        max_output_tokens=args.max_output_tokens,
    )
    answer = (response.output_text or "").strip()
    if not answer:
        raise SystemExit("The model returned no answer text")

    print("\n=== AI ANSWER ===\n")
    print(answer)
    print("\n=== SUPPORTING LIBRARY SOURCES ===\n")
    for source in sources:
        print(
            f"[{source['number']}] {source['source']} | {source['title']} | "
            f"{source['locator']} | score={source['score']:.4f}"
        )
        print(source["record_url"])
    usage = getattr(response, "usage", None)
    if usage:
        input_tokens = getattr(usage, "input_tokens", "unknown")
        output_tokens = getattr(usage, "output_tokens", "unknown")
        total_tokens = getattr(usage, "total_tokens", "unknown")
        print(
            "\n=== TOKEN USAGE ===\n"
            f"input={input_tokens} output={output_tokens} total={total_tokens}"
        )
    print("\nTEST PASSED: answer generated from retrieved Koha/DSpace evidence.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
