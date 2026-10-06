#!/usr/bin/env python3
"""Enrich a validated Koha MARC batch with source-backed 505/520 notes.

The input file is never modified. Missing contents notes are retrieved from
Open Library edition metadata. Missing summaries are retrieved first from Open
Library and then, when available, from Google Books. Every added 520 carries
the assigning source and source URL; every added 505 carries its source URL.

No text is generated or inferred. Records without authoritative enrichment
remain unchanged and are identified in the audit CSV and JSON manifest.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pymarc import Field, MARCReader, Record, Subfield, record_to_xml


USER_AGENT = "Koha-DSpace-AI-Discovery/1.1 (metadata enrichment prototype)"
MARCXML_NS = "http://www.loc.gov/MARC21/slim"


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(value: Any, limit: int = 5000) -> str:
    if isinstance(value, dict):
        value = value.get("value", "")
    if not isinstance(value, str):
        return ""
    parser = _HTMLText()
    try:
        parser.feed(html.unescape(value))
        value = " ".join(parser.parts)
    except Exception:
        value = re.sub(r"<[^>]+>", " ", html.unescape(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip()


def normalized_isbns(record: Record) -> list[str]:
    result: list[str] = []
    for field in record.get_fields("020"):
        for raw in field.get_subfields("a"):
            candidate = re.split(r"[ (:]", raw.strip(), maxsplit=1)[0]
            candidate = re.sub(r"[^0-9Xx]", "", candidate).upper()
            if len(candidate) in (10, 13) and candidate not in result:
                result.append(candidate)
    return result


def first_subfield(record: Record, tag: str, code: str) -> str:
    fields = record.get_fields(tag)
    if not fields or fields[0].is_control_field():
        return ""
    values = fields[0].get_subfields(code)
    return clean_text(values[0], 1000) if values else ""


def http_json(url: str, attempts: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=35) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    print(f"WARNING: metadata request failed: {last_error}", flush=True)
    return {}


def openlibrary_metadata(isbn: str) -> dict[str, str]:
    key = f"ISBN:{isbn}"
    params = urllib.parse.urlencode({
        "bibkeys": key,
        "jscmd": "details",
        "format": "json",
    })
    data = http_json(f"https://openlibrary.org/api/books?{params}")
    book = data.get(key, {}) if isinstance(data, dict) else {}
    details = book.get("details", {}) if isinstance(book, dict) else {}
    if not isinstance(details, dict):
        details = {}

    description = clean_text(details.get("description", ""))
    toc_raw = details.get("table_of_contents", [])
    toc_titles: list[str] = []
    if isinstance(toc_raw, list):
        for entry in toc_raw:
            if isinstance(entry, dict):
                title = clean_text(entry.get("title", ""), 500)
            else:
                title = clean_text(entry, 500)
            if title and title not in toc_titles:
                toc_titles.append(title)
    toc = " -- ".join(toc_titles)[:7000].rstrip(" -")
    source_url = clean_text(book.get("info_url", ""), 1000) if isinstance(book, dict) else ""
    if not source_url:
        source_url = f"https://openlibrary.org/isbn/{isbn}"

    # Some edition records point to a Work where the description is stored.
    if not description:
        works = details.get("works", [])
        if isinstance(works, list) and works:
            work = works[0]
            work_key = work.get("key", "") if isinstance(work, dict) else ""
            if isinstance(work_key, str) and work_key.startswith("/works/"):
                work_data = http_json(f"https://openlibrary.org{work_key}.json")
                description = clean_text(work_data.get("description", ""))

    return {
        "summary": description,
        "contents": toc,
        "source": "Open Library",
        "url": source_url,
    }


def google_books_summary(isbn: str) -> dict[str, str]:
    params = urllib.parse.urlencode({"q": f"isbn:{isbn}", "maxResults": "5", "printType": "books"})
    data = http_json(f"https://www.googleapis.com/books/v1/volumes?{params}")
    items = data.get("items", [])
    if not isinstance(items, list):
        return {}
    for item in items:
        info = item.get("volumeInfo", {}) if isinstance(item, dict) else {}
        if not isinstance(info, dict):
            continue
        identifiers = info.get("industryIdentifiers", [])
        matches = False
        if isinstance(identifiers, list):
            for identifier in identifiers:
                raw = identifier.get("identifier", "") if isinstance(identifier, dict) else ""
                normalized = re.sub(r"[^0-9Xx]", "", str(raw)).upper()
                if normalized == isbn:
                    matches = True
                    break
        if not matches:
            continue
        description = clean_text(info.get("description", ""))
        if description:
            return {
                "summary": description,
                "source": "Google Books",
                "url": clean_text(info.get("infoLink") or info.get("canonicalVolumeLink") or "", 1000),
            }
    return {}


def add_summary(record: Record, text: str, source: str, url: str) -> None:
    subfields = [Subfield(code="a", value=text), Subfield(code="c", value=source)]
    if url:
        subfields.append(Subfield(code="u", value=url))
    record.add_ordered_field(Field(tag="520", indicators=[" ", " "], subfields=subfields))


def add_contents(record: Record, text: str, url: str) -> None:
    subfields = [Subfield(code="a", value=text)]
    if url:
        subfields.append(Subfield(code="u", value=url))
    record.add_ordered_field(Field(tag="505", indicators=["0", " "], subfields=subfields))


def validate_record(record: Record) -> list[str]:
    errors: list[str] = []
    if not normalized_isbns(record):
        errors.append("missing_isbn")
    if not record.get_fields("650"):
        errors.append("missing_650")
    if not record.get_fields("942"):
        errors.append("missing_942")
    fields_952 = record.get_fields("952")
    if not fields_952 or not fields_952[0].get_subfields("p"):
        errors.append("missing_952_barcode")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--delay", type=float, default=0.35)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with args.input.open("rb") as source:
        records = [record for record in MARCReader(source, to_unicode=True, force_utf8=True) if record]
    if not records:
        raise SystemExit("No readable MARC records found; no output written.")

    counters: Counter[str] = Counter()
    audit: list[dict[str, str]] = []
    validation_errors: list[str] = []

    for number, record in enumerate(records, start=1):
        title = first_subfield(record, "245", "a")
        isbns = normalized_isbns(record)
        isbn = isbns[0] if isbns else ""
        had_505 = bool(record.get_fields("505"))
        had_520 = bool(record.get_fields("520"))
        added_505 = False
        added_520 = False
        source_505 = ""
        source_520 = ""

        metadata: dict[str, str] = {}
        if isbn and (not had_505 or not had_520):
            metadata = openlibrary_metadata(isbn)
            time.sleep(args.delay)

        if not had_505 and metadata.get("contents"):
            add_contents(record, metadata["contents"], metadata.get("url", ""))
            added_505 = True
            source_505 = metadata.get("source", "")
            counters["added_505"] += 1

        if not had_520:
            if metadata.get("summary"):
                add_summary(record, metadata["summary"], metadata.get("source", ""), metadata.get("url", ""))
                added_520 = True
                source_520 = metadata.get("source", "")
            elif isbn:
                google = google_books_summary(isbn)
                time.sleep(args.delay)
                if google.get("summary"):
                    add_summary(record, google["summary"], google.get("source", ""), google.get("url", ""))
                    added_520 = True
                    source_520 = google.get("source", "")
            if added_520:
                counters["added_520"] += 1

        if record.get_fields("505"):
            counters["final_with_505"] += 1
        if record.get_fields("520"):
            counters["final_with_520"] += 1

        errors = validate_record(record)
        if errors:
            validation_errors.append(f"record {number}: {','.join(errors)}")

        audit.append({
            "record_number": str(number),
            "isbn": isbn,
            "title": title,
            "had_505": str(had_505).lower(),
            "added_505": str(added_505).lower(),
            "source_505": source_505,
            "had_520": str(had_520).lower(),
            "added_520": str(added_520).lower(),
            "source_520": source_520,
            "final_has_505": str(bool(record.get_fields("505"))).lower(),
            "final_has_520": str(bool(record.get_fields("520"))).lower(),
        })
        print(
            f"[{number:03d}/{len(records)}] 505={'yes' if record.get_fields('505') else 'no'} "
            f"520={'yes' if record.get_fields('520') else 'no'} {title}",
            flush=True,
        )

    if validation_errors:
        raise SystemExit("Validation failed: " + "; ".join(validation_errors[:10]))

    marc_path = args.output_dir / "koha_200_real_books_enriched.mrc"
    xml_path = args.output_dir / "koha_200_real_books_enriched.xml"
    audit_path = args.output_dir / "koha_200_real_books_enrichment_audit.csv"
    manifest_path = args.output_dir / "koha_200_real_books_enrichment_manifest.json"

    with marc_path.open("wb") as output:
        for record in records:
            output.write(record.as_marc())
    with xml_path.open("wb") as output:
        output.write(f'<collection xmlns="{MARCXML_NS}">\n'.encode())
        for record in records:
            output.write(record_to_xml(record, namespace=True))
            output.write(b"\n")
        output.write(b"</collection>\n")
    with audit_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(audit[0]))
        writer.writeheader()
        writer.writerows(audit)

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "record_count": len(records),
        "added_505": counters["added_505"],
        "added_520": counters["added_520"],
        "final_with_505": counters["final_with_505"],
        "final_without_505": len(records) - counters["final_with_505"],
        "final_with_520": counters["final_with_520"],
        "final_without_520": len(records) - counters["final_with_520"],
        "sources": ["Open Library", "Google Books"],
        "policy": "Only source-supplied metadata; no generated or inferred summaries/contents.",
        "outputs": {
            "marc_iso2709": marc_path.name,
            "marcxml": xml_path.name,
            "audit_csv": audit_path.name,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("\nENRICHMENT COMPLETE")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
