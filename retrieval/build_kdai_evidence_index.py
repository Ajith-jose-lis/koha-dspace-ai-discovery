#!/usr/bin/env python3
"""Build a citation-ready Koha + DSpace evidence index in Qdrant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from pymarc import MARCReader
from pypdf import PdfReader


DEFAULT_MARC = Path("/opt/koha-dspace-ai/data/koha-import-200/koha_vufind_200.mrc")
DEFAULT_DSPACE = Path("/opt/koha-dspace-ai/data/dspace-oa-20")
DEFAULT_AUDIT = Path("/opt/koha-dspace-ai/logs/kdai-evidence-index.json")
DEFAULT_COLLECTION = "kdai_evidence_v1"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
NAMESPACE = uuid.UUID("dd185111-444e-44e7-b8bd-f87f56acb655")


@dataclass
class EvidenceChunk:
    point_id: str
    source: str
    document_id: str
    chunk_index: int
    title: str
    text: str
    authors: list[str]
    subjects: list[str]
    resource_type: str
    vufind_id: str
    record_url: str
    source_url: str
    locator: str
    page_start: int | None = None
    page_end: int | None = None
    isbn: list[str] | None = None
    call_number: str = ""
    barcode: str = ""
    licence: str = ""

    def payload(self) -> dict:
        data = asdict(self)
        data.pop("point_id")
        return {key: value for key, value in data.items() if value not in (None, "", [])}


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" /;:,\t\r\n")


def unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = clean(value)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def marc_values(record, tags: list[str], subfields: list[str]) -> list[str]:
    values: list[str] = []
    for tag in tags:
        for field in record.get_fields(tag):
            for code in subfields:
                values.extend(field.get_subfields(code))
    return unique(values)


def stable_id(source: str, document_id: str, chunk_index: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return str(uuid.uuid5(NAMESPACE, f"{source}|{document_id}|{chunk_index}|{digest}"))


def build_koha_chunks(marc_path: Path) -> tuple[list[EvidenceChunk], int]:
    chunks: list[EvidenceChunk] = []
    records = 0
    with marc_path.open("rb") as stream:
        reader = MARCReader(stream, to_unicode=True, force_utf8=True, utf8_handling="replace")
        for record in reader:
            if record is None:
                continue
            records += 1
            title = clean(record.title) or f"Koha record {records}"
            authors = marc_values(record, ["100", "110", "111", "700", "710", "711"], ["a", "b", "c", "d"])
            subjects = marc_values(record, ["600", "610", "611", "630", "648", "650", "651", "653"], ["a", "x", "y", "z", "v"])
            summaries = marc_values(record, ["520"], ["a", "b"])
            contents = marc_values(record, ["505"], ["a", "t", "r"])
            publishers = marc_values(record, ["260", "264"], ["a", "b", "c"])
            isbns = marc_values(record, ["020"], ["a"])
            call_numbers = marc_values(record, ["050", "082", "090", "952"], ["a", "b", "o"])
            barcodes = marc_values(record, ["952"], ["p"])
            biblio_numbers = marc_values(record, ["999"], ["c"])
            document_id = biblio_numbers[0] if biblio_numbers else str(records)
            lines = [f"Title: {title}"]
            if authors:
                lines.append("Authors: " + "; ".join(authors))
            if subjects:
                lines.append("Subjects: " + "; ".join(subjects))
            if summaries:
                lines.append("Summary: " + " ".join(summaries))
            if contents:
                lines.append("Contents: " + " ".join(contents))
            if publishers:
                lines.append("Publication: " + "; ".join(publishers))
            if isbns:
                lines.append("ISBN: " + "; ".join(isbns))
            if call_numbers:
                lines.append("Call number: " + "; ".join(call_numbers))
            if barcodes:
                lines.append("Barcode: " + "; ".join(barcodes))
            text = "\n".join(lines)
            chunks.append(
                EvidenceChunk(
                    point_id=stable_id("koha", document_id, 0, text),
                    source="koha",
                    document_id=document_id,
                    chunk_index=0,
                    title=title,
                    text=text,
                    authors=authors,
                    subjects=subjects,
                    resource_type="catalogue record",
                    vufind_id=document_id,
                    record_url=f"/vufind/Record/{document_id}",
                    source_url="",
                    locator="Koha catalogue record",
                    isbn=isbns,
                    call_number=call_numbers[0] if call_numbers else "",
                    barcode=barcodes[0] if barcodes else "",
                )
            )
    return chunks, records


def parse_dc(path: Path) -> dict[tuple[str, str], list[str]]:
    metadata: dict[tuple[str, str], list[str]] = {}
    root = ET.parse(path).getroot()
    for node in root.iter():
        if not node.tag.endswith("dcvalue"):
            continue
        element = node.attrib.get("element", "")
        qualifier = node.attrib.get("qualifier", "")
        value = clean(node.text)
        if value:
            metadata.setdefault((element, qualifier), []).append(value)
    return metadata


def dc_values(metadata: dict, element: str, *qualifiers: str) -> list[str]:
    values: list[str] = []
    for (current_element, qualifier), current_values in metadata.items():
        if current_element == element and (not qualifiers or qualifier in qualifiers):
            values.extend(current_values)
    return unique(values)


def word_chunks(text: str, size: int, overlap: int) -> list[str]:
    words = clean(text).split()
    if not words:
        return []
    output: list[str] = []
    step = max(1, size - overlap)
    for start in range(0, len(words), step):
        piece = " ".join(words[start:start + size])
        if len(piece) >= 80:
            output.append(piece)
        if start + size >= len(words):
            break
    return output


def build_dspace_chunks(
    source_dir: Path,
    handle_prefix: str,
    first_handle: int,
    chunk_words: int,
    overlap_words: int,
) -> tuple[list[EvidenceChunk], int, list[dict]]:
    chunks: list[EvidenceChunk] = []
    documents = 0
    document_audit: list[dict] = []
    item_dirs = sorted(path for path in source_dir.glob("item_*") if path.is_dir())
    for position, item_dir in enumerate(item_dirs):
        dc_path = item_dir / "dublin_core.xml"
        pdfs = sorted(item_dir.glob("*.pdf"))
        if not dc_path.is_file() or not pdfs:
            continue
        documents += 1
        metadata = parse_dc(dc_path)
        title_values = dc_values(metadata, "title")
        title = title_values[0] if title_values else item_dir.name
        authors = dc_values(metadata, "contributor", "author")
        subjects = dc_values(metadata, "subject")
        abstracts = dc_values(metadata, "description", "abstract")
        licences = dc_values(metadata, "rights")
        types = dc_values(metadata, "type")
        source_urls = [value for value in dc_values(metadata, "identifier", "uri") if value.startswith("http")]
        handle_number = first_handle + position
        handle = f"{handle_prefix}/{handle_number}"
        vufind_id = "dspace-" + handle.replace("/", "-")
        prefix_lines = [f"Title: {title}"]
        if authors:
            prefix_lines.append("Authors: " + "; ".join(authors))
        if subjects:
            prefix_lines.append("Subjects: " + "; ".join(subjects))
        if abstracts:
            prefix_lines.append("Abstract: " + " ".join(abstracts))
        prefix = "\n".join(prefix_lines)
        doc_chunks_before = len(chunks)
        pages_with_text = 0
        for pdf_path in pdfs:
            reader = PdfReader(str(pdf_path))
            for page_number, page in enumerate(reader.pages, start=1):
                page_text = clean(page.extract_text() or "")
                if not page_text:
                    continue
                pages_with_text += 1
                for page_piece in word_chunks(page_text, chunk_words, overlap_words):
                    chunk_index = len(chunks) - doc_chunks_before
                    evidence_text = prefix + f"\nPage {page_number}: " + page_piece
                    chunks.append(
                        EvidenceChunk(
                            point_id=stable_id("dspace", handle, chunk_index, evidence_text),
                            source="dspace",
                            document_id=handle,
                            chunk_index=chunk_index,
                            title=title,
                            text=evidence_text,
                            authors=authors,
                            subjects=subjects,
                            resource_type=types[0] if types else "open-access document",
                            vufind_id=vufind_id,
                            record_url=f"/vufind/Record/{vufind_id}",
                            source_url=source_urls[0] if source_urls else "",
                            locator=f"Page {page_number}",
                            page_start=page_number,
                            page_end=page_number,
                            licence=licences[0] if licences else "",
                        )
                    )
        document_audit.append(
            {
                "item": item_dir.name,
                "handle": handle,
                "title": title,
                "pdf_count": len(pdfs),
                "pages_with_text": pages_with_text,
                "chunks": len(chunks) - doc_chunks_before,
            }
        )
    return chunks, documents, document_audit


def batches(values: list, size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marc", type=Path, default=DEFAULT_MARC)
    parser.add_argument("--dspace", type=Path, default=DEFAULT_DSPACE)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--expected-koha", type=int, default=200)
    parser.add_argument("--expected-dspace", type=int, default=20)
    parser.add_argument("--handle-prefix", default="123456789")
    parser.add_argument("--first-handle", type=int, default=3)
    parser.add_argument("--chunk-words", type=int, default=220)
    parser.add_argument("--overlap-words", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--recreate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.marc.is_file():
        raise SystemExit(f"MARC file not found: {args.marc}")
    if not args.dspace.is_dir():
        raise SystemExit(f"DSpace SAF directory not found: {args.dspace}")

    print("Reading Koha MARC evidence...", flush=True)
    koha_chunks, koha_records = build_koha_chunks(args.marc)
    print("Reading DSpace PDFs page by page...", flush=True)
    dspace_chunks, dspace_documents, document_audit = build_dspace_chunks(
        args.dspace,
        args.handle_prefix,
        args.first_handle,
        args.chunk_words,
        args.overlap_words,
    )
    if koha_records != args.expected_koha:
        raise SystemExit(f"VALIDATION FAILED: expected {args.expected_koha} Koha records, found {koha_records}")
    if dspace_documents != args.expected_dspace:
        raise SystemExit(f"VALIDATION FAILED: expected {args.expected_dspace} DSpace documents, found {dspace_documents}")
    empty_docs = [item for item in document_audit if item["chunks"] == 0]
    if empty_docs:
        raise SystemExit("VALIDATION FAILED: PDFs without extractable evidence: " + ", ".join(item["item"] for item in empty_docs))

    all_chunks = koha_chunks + dspace_chunks
    audit = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "collection": args.collection,
        "model": args.model,
        "koha_records": koha_records,
        "koha_chunks": len(koha_chunks),
        "dspace_documents": dspace_documents,
        "dspace_chunks": len(dspace_chunks),
        "total_chunks": len(all_chunks),
        "dspace_document_audit": document_audit,
        "policy": "Source-supplied metadata and extracted PDF text only; no generated evidence.",
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: audit[key] for key in ["koha_records", "koha_chunks", "dspace_documents", "dspace_chunks", "total_chunks"]}, indent=2))
    if args.validate_only:
        print(f"VALIDATION PASSED; audit written to {args.audit}")
        return 0

    api_key = os.environ.get("QDRANT_API_KEY", "")
    if not api_key:
        raise SystemExit("QDRANT_API_KEY is not set")

    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient, models

    print(f"Loading embedding model {args.model}...", flush=True)
    embedder = TextEmbedding(model_name=args.model)
    client = QdrantClient(url=args.qdrant_url, api_key=api_key, timeout=120)
    exists = client.collection_exists(args.collection)
    if exists and not args.recreate:
        raise SystemExit(f"Collection {args.collection!r} already exists; use --recreate to replace it")

    sample_vector = list(embedder.embed([all_chunks[0].text]))[0]
    vector_size = len(sample_vector)
    if exists:
        print(f"Deleting existing collection {args.collection}...", flush=True)
        client.delete_collection(args.collection)
    client.create_collection(
        collection_name=args.collection,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
    )

    indexed = 0
    for chunk_batch in batches(all_chunks, args.batch_size):
        vectors = list(embedder.embed([chunk.text for chunk in chunk_batch], batch_size=args.batch_size))
        points = [
            models.PointStruct(id=chunk.point_id, vector=vector.tolist(), payload=chunk.payload())
            for chunk, vector in zip(chunk_batch, vectors, strict=True)
        ]
        client.upsert(collection_name=args.collection, points=points, wait=True)
        indexed += len(points)
        print(f"Indexed {indexed}/{len(all_chunks)} evidence chunks", flush=True)

    for field_name in ["source", "document_id", "vufind_id", "resource_type"]:
        client.create_payload_index(
            collection_name=args.collection,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    info = client.get_collection(args.collection)
    print(f"INDEXING PASSED: collection={args.collection} points={info.points_count} vector_size={vector_size}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
