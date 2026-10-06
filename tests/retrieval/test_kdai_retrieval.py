#!/usr/bin/env python3
"""Run a semantic evidence search against the KDAI Qdrant collection."""

import argparse
import os

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--collection", default="kdai_evidence_v1")
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--source", choices=["koha", "dspace"])
    args = parser.parse_args()
    api_key = os.environ.get("QDRANT_API_KEY", "")
    if not api_key:
        raise SystemExit("QDRANT_API_KEY is not set")
    embedder = TextEmbedding(model_name=args.model)
    vector = list(embedder.embed([args.query]))[0].tolist()
    query_filter = None
    if args.source:
        query_filter = models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchValue(value=args.source))]
        )
    client = QdrantClient(url=args.qdrant_url, api_key=api_key, timeout=120)
    hits = client.query_points(
        collection_name=args.collection,
        query=vector,
        query_filter=query_filter,
        limit=args.limit,
        with_payload=True,
    ).points
    for position, hit in enumerate(hits, start=1):
        payload = hit.payload or {}
        print(f"[{position}] score={hit.score:.4f} source={payload.get('source')} {payload.get('locator', '')}")
        print(payload.get("title", "Untitled"))
        print(payload.get("record_url", ""))
        excerpt = str(payload.get("text", "")).replace("\n", " ")[:420]
        print(excerpt + ("..." if len(excerpt) == 420 else ""))
        print()


if __name__ == "__main__":
    main()
