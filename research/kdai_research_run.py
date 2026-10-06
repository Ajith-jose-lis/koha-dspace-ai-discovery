#!/usr/bin/env python3

import argparse
import json
import re
import time

import kdai_ai_service as kdai


def main():
    parser = argparse.ArgumentParser(description="KDAI controlled research-model runner")
    parser.add_argument("--provider", required=True, choices=["openai", "ollama"])
    parser.add_argument("--query", required=True)
    args = parser.parse_args()

    query = kdai.normalized_query(args.query)
    if len(query) < 3:
        raise SystemExit("Query is too short.")

    started = time.perf_counter()

    evidence, sources = kdai.retrieve(query)
    answer, usage = kdai.generate(query, evidence, args.provider)
    kdai.validate_citations(answer, len(sources))

    cited_numbers = sorted(
        {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
    )

    for source in sources:
        source["cited"] = source["number"] in cited_numbers

    insufficient = (
        "I could not find sufficient supporting evidence "
        "in the connected library collections."
    )

    latency_ms = int((time.perf_counter() - started) * 1000)
    refused = answer.strip() == insufficient

    kdai.log_research_run(
        query=query,
        provider=args.provider,
        model=kdai.provider_model(args.provider),
        answer=answer,
        evidence_text=evidence,
        sources=sources,
        cited_numbers=cited_numbers,
        usage=usage,
        refused=refused,
        latency_ms=latency_ms,
    )

    result = {
        "query": query,
        "provider": args.provider,
        "model": kdai.provider_model(args.provider),
        "answer": answer,
        "sources": sources,
        "usage": usage,
        "refused": refused,
        "latency_ms": latency_ms,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
