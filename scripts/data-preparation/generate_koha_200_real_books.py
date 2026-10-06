#!/usr/bin/env python3
"""Build a validated 200-record MARC21 batch for the Koha prototype.

Source metadata: Library of Congress Online Catalog SRU/MARCXML.
Cover gate: Open Library Covers API with ``default=false``.

The script never imports records into Koha.  It creates a staged ISO-2709 MARC
file, MARCXML copy, audit CSV, rejection log and JSON manifest for review.

VM setup and example:

    python3 -m venv /opt/koha-dspace-ai/tools/recordgen-venv
    /opt/koha-dspace-ai/tools/recordgen-venv/bin/pip install pymarc==5.2.2
    /opt/koha-dspace-ai/tools/recordgen-venv/bin/python \
      generate_koha_200_real_books.py \
      --output-dir /opt/koha-dspace-ai/data/koha-import-200

The generated holdings use branch MAIN, item type BK, barcodes KDAI0001 to
KDAI0200, and are intended for Koha's BKS bibliographic framework.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pymarc import Field, Leader, Record, Subfield, parse_xml_to_array, record_to_xml


SRU_BASE = "http://lx2.loc.gov:210/LCDB"
MARCXML_NS = "http://www.loc.gov/MARC21/slim"
USER_AGENT = "Koha-DSpace-AI-Discovery/1.0 (bibliographic prototype)"

# Twenty records per subject gives a balanced 200-book prototype collection.
SUBJECT_PLAN: list[tuple[str, int]] = [
    ("artificial intelligence", 20),
    ("computer science", 20),
    ("library science", 20),
    ("education", 20),
    ("psychology", 20),
    ("economics", 20),
    ("environmental science", 20),
    ("medicine", 20),
    ("world history", 20),
    ("English literature", 20),
]

# Used only if one planned subject cannot supply enough records satisfying all
# quality gates (book format, ISBN, LCSH and a verified cover).
FILLER_SUBJECTS = [
    "data science",
    "information technology",
    "sociology",
    "business",
    "public health",
    "climate change",
    "political science",
    "law",
    "mathematics",
    "physics",
    "biology",
    "fiction",
]


def http_get(url: str, timeout: int = 45, attempts: int = 3) -> bytes:
    """Download a URL with conservative retry handling."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"Download failed after {attempts} attempts: {url}: {last_error}")


def fetch_sru_page(subject: str, start_record: int, maximum: int) -> list[Record]:
    """Retrieve one page of English-language MARCXML records from LC SRU."""
    # LCDB's SRU gateway supports the Dublin Core subject index with the
    # standard equality relation.  Its older CQL implementation rejects the
    # newer ``all`` relation.
    query = f'dc.subject="{subject}"'
    params = {
        "operation": "searchRetrieve",
        "version": "1.1",
        "query": query,
        "startRecord": str(start_record),
        "maximumRecords": str(maximum),
        "recordSchema": "marcxml",
    }
    url = f"{SRU_BASE}?{urllib.parse.urlencode(params)}"
    payload = http_get(url)
    if b"diagnostic" in payload.lower() and b"<record" not in payload.lower():
        message = re.sub(rb"\s+", b" ", payload)[:500].decode("utf-8", "replace")
        print(f"WARNING: LC SRU diagnostic for {subject!r} at startRecord={start_record}; moving to the next subject.", file=sys.stderr, flush=True)
        return []
    try:
        # The SRU envelope also contains elements named ``record``.  Pymarc
        # yields None placeholders for those wrapper elements, so retain only
        # actual MARC Record instances.
        parsed = parse_xml_to_array(io.BytesIO(payload), strict=False)
        return [record for record in parsed if isinstance(record, Record)]
    except Exception as error:
        raise RuntimeError(f"Could not parse LC MARCXML for {subject!r}: {error}") from error


def first_subfield(record: Record, tag: str, code: str) -> str:
    fields = record.get_fields(tag)
    field = fields[0] if fields else None
    if field is None or field.is_control_field():
        return ""
    values = field.get_subfields(code)
    return values[0].strip() if values else ""


def control_value(record: Record, tag: str) -> str:
    fields = record.get_fields(tag)
    field = fields[0] if fields else None
    if field is not None and field.is_control_field():
        return field.data.strip()
    return ""


def joined_subfields(record: Record, tag: str, codes: Iterable[str]) -> str:
    fields = record.get_fields(tag)
    field = fields[0] if fields else None
    if field is None or field.is_control_field():
        return ""
    values: list[str] = []
    for code in codes:
        values.extend(value.strip() for value in field.get_subfields(code) if value.strip())
    return " ".join(values)


def clean_display_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(" /:;,.")


def normalized_isbns(record: Record) -> list[str]:
    values: list[str] = []
    for field in record.get_fields("020"):
        for raw in field.get_subfields("a"):
            candidate = re.split(r"[ (:]", raw.strip(), maxsplit=1)[0]
            candidate = re.sub(r"[^0-9Xx]", "", candidate).upper()
            if len(candidate) in (10, 13) and candidate not in values:
                values.append(candidate)
    return values


def is_english_monograph(record: Record) -> bool:
    leader = str(record.leader)
    if len(leader) < 8 or leader[6] != "a" or leader[7] not in {"m", "a"}:
        return False
    fields_008 = record.get_fields("008")
    field_008 = fields_008[0] if fields_008 else None
    if field_008 and field_008.is_control_field() and len(field_008.data) >= 38:
        language = field_008.data[35:38]
        if language not in {"eng", "   ", "|||"}:
            return False
    return True


def has_lc_subject(record: Record) -> bool:
    return any(field.get_subfields("a") for field in record.get_fields("650"))


def openlibrary_cover(isbn: str, cache: dict[str, str | None]) -> str | None:
    """Return a verified cover URL, or None when Open Library has no image."""
    if isbn in cache:
        return cache[isbn]
    url = f"https://covers.openlibrary.org/b/isbn/{urllib.parse.quote(isbn)}-M.jpg?default=false"
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Range": "bytes=0-32"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status in {200, 206} and content_type.startswith("image/"):
                    cache[isbn] = url
                    return url
        except urllib.error.HTTPError as error:
            if error.code == 404:
                cache[isbn] = None
                return None
            if error.code == 429 and attempt < 3:
                time.sleep(attempt * 3)
                continue
        except (urllib.error.URLError, TimeoutError):
            if attempt < 3:
                time.sleep(attempt * 2)
                continue
        break
    cache[isbn] = None
    return None


def find_verified_cover(record: Record, cache: dict[str, str | None]) -> tuple[str, str] | None:
    for isbn in normalized_isbns(record):
        cover_url = openlibrary_cover(isbn, cache)
        if cover_url:
            return isbn, cover_url
    return None


def title_key(record: Record) -> str:
    title = clean_display_text(joined_subfields(record, "245", ("a", "b", "n", "p"))).casefold()
    author = clean_display_text(joined_subfields(record, "100", ("a", "d"))).casefold()
    return re.sub(r"[^a-z0-9]+", "", f"{title}|{author}")


def publication_year(record: Record) -> str:
    raw = first_subfield(record, "264", "c") or first_subfield(record, "260", "c")
    match = re.search(r"(?:17|18|19|20)\d{2}", raw)
    return match.group(0) if match else ""


def call_number(record: Record) -> tuple[str, str]:
    ddc = clean_display_text(joined_subfields(record, "082", ("a", "b")))
    if ddc:
        return ddc, "ddc"
    lcc = clean_display_text(joined_subfields(record, "050", ("a", "b")))
    if lcc:
        return lcc, "lcc"
    return "000", "ddc"


def add_koha_fields(
    record: Record,
    barcode: str,
    branch: str,
    item_type: str,
    verified_isbn: str,
    cover_url: str,
) -> tuple[str, str]:
    """Remove source holdings and add one clean Koha item plus local provenance."""
    record.remove_fields("942", "952")
    number, source = call_number(record)
    record.add_field(
        Field(
            tag="942",
            indicators=[" ", " "],
            subfields=[Subfield("2", source), Subfield("c", item_type)],
        )
    )
    record.add_field(
        Field(
            tag="952",
            indicators=[" ", " "],
            subfields=[
                Subfield("2", source),
                Subfield("a", branch),
                Subfield("b", branch),
                Subfield("o", number),
                Subfield("p", barcode),
                Subfield("y", item_type),
            ],
        )
    )
    record.add_field(
        Field(
            tag="590",
            indicators=[" ", " "],
            subfields=[
                Subfield(
                    "a",
                    f"Prototype record sourced from the Library of Congress; "
                    f"Open Library cover verified for ISBN {verified_isbn}.",
                )
            ],
        )
    )
    # MARCXML is Unicode.  Force UTF-8 when serializing to ISO 2709.
    leader = str(record.leader)
    if len(leader) >= 10 and leader[9] != "a":
        record.leader = Leader(leader[:9] + "a" + leader[10:])
    record.force_utf8 = True
    return number, source


def record_summary(
    record: Record,
    barcode: str,
    subject_group: str,
    isbn: str,
    cover_url: str,
    call_no: str,
) -> dict[str, str | int | bool]:
    title = clean_display_text(joined_subfields(record, "245", ("a", "b", "n", "p")))
    author = clean_display_text(
        joined_subfields(record, "100", ("a", "b", "c", "d", "q"))
        or joined_subfields(record, "110", ("a", "b"))
    )
    subjects = [
        clean_display_text(" -- ".join(field.get_subfields("a", "x", "y", "z")))
        for field in record.get_fields("650")
    ]
    subjects = [subject for subject in subjects if subject]
    return {
        "barcode": barcode,
        "subject_group": subject_group,
        "title": title,
        "author": author,
        "isbn": isbn,
        "publication_year": publication_year(record),
        "call_number": call_no,
        "lc_subject_count": len(subjects),
        "subjects": " | ".join(subjects),
        "summary_520": bool(record.get_fields("520")),
        "contents_505": bool(record.get_fields("505")),
        "cover_url": cover_url,
        "lc_control_number": first_subfield(record, "010", "a") or control_value(record, "001"),
    }


def write_outputs(
    output_dir: Path,
    selected: list[tuple[Record, dict[str, str | int | bool]]],
    rejected: Counter[str],
    args: argparse.Namespace,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    marc_path = output_dir / "koha_200_real_books.mrc"
    xml_path = output_dir / "koha_200_real_books.xml"
    audit_path = output_dir / "koha_200_real_books_audit.csv"
    manifest_path = output_dir / "koha_200_real_books_manifest.json"

    with marc_path.open("wb") as marc_file:
        for record, _ in selected:
            marc_file.write(record.as_marc())

    with xml_path.open("wb") as xml_file:
        xml_file.write(f'<collection xmlns="{MARCXML_NS}">\n'.encode())
        for record, _ in selected:
            xml_file.write(record_to_xml(record, namespace=True))
            xml_file.write(b"\n")
        xml_file.write(b"</collection>\n")

    fieldnames = list(selected[0][1].keys())
    with audit_path.open("w", encoding="utf-8", newline="") as audit_file:
        writer = csv.DictWriter(audit_file, fieldnames=fieldnames)
        writer.writeheader()
        for _, summary in selected:
            writer.writerow(summary)

    subject_counts = Counter(str(summary["subject_group"]) for _, summary in selected)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "record_count": len(selected),
        "target": args.target,
        "source": "Library of Congress Online Catalog SRU/MARCXML",
        "source_url": SRU_BASE,
        "cover_gate": "Open Library Covers API, default=false",
        "koha_framework": "BKS",
        "branch": args.branch,
        "item_type": args.item_type,
        "barcode_first": selected[0][1]["barcode"],
        "barcode_last": selected[-1][1]["barcode"],
        "subject_counts": dict(sorted(subject_counts.items())),
        "records_with_505_contents": sum(bool(summary["contents_505"]) for _, summary in selected),
        "records_with_520_summary": sum(bool(summary["summary_520"]) for _, summary in selected),
        "rejection_counts": dict(sorted(rejected.items())),
        "outputs": {
            "marc_iso2709": marc_path.name,
            "marcxml": xml_path.name,
            "audit_csv": audit_path.name,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def collect_records(args: argparse.Namespace) -> tuple[list[tuple[Record, dict]], Counter[str]]:
    selected: list[tuple[Record, dict]] = []
    rejected: Counter[str] = Counter()
    seen_isbns: set[str] = set()
    seen_titles: set[str] = set()
    cover_cache: dict[str, str | None] = {}

    def precheck(record: Record) -> bool:
        """Apply cheap gates before making a cover-service request."""
        if not is_english_monograph(record):
            rejected["not_english_book_monograph"] += 1
            return False
        if not has_lc_subject(record):
            rejected["no_650_lc_subject"] += 1
            return False
        key = title_key(record)
        if not key or key in seen_titles:
            rejected["duplicate_or_missing_title"] += 1
            return False
        isbns = normalized_isbns(record)
        if not isbns:
            rejected["no_isbn"] += 1
            return False
        if all(isbn in seen_isbns for isbn in isbns):
            rejected["duplicate_isbn"] += 1
            return False
        return True

    def consider(
        record: Record,
        group: str,
        quota_end: int,
        cover: tuple[str, str] | None,
    ) -> None:
        if len(selected) >= quota_end or len(selected) >= args.target:
            return
        # Recheck duplicate gates because earlier records in this same page may
        # have been accepted while cover lookups ran concurrently.
        if not is_english_monograph(record):
            return
        if not has_lc_subject(record):
            return
        key = title_key(record)
        if not key or key in seen_titles:
            rejected["duplicate_or_missing_title"] += 1
            return
        isbns = normalized_isbns(record)
        if not isbns:
            rejected["no_isbn"] += 1
            return
        if all(isbn in seen_isbns for isbn in isbns):
            rejected["duplicate_isbn"] += 1
            return
        if cover is None:
            rejected["no_verified_openlibrary_cover"] += 1
            return
        isbn, cover_url = cover
        if isbn in seen_isbns:
            rejected["duplicate_isbn"] += 1
            return
        barcode = f"{args.barcode_prefix}{len(selected) + 1:04d}"
        call_no, _ = add_koha_fields(
            record,
            barcode=barcode,
            branch=args.branch,
            item_type=args.item_type,
            verified_isbn=isbn,
            cover_url=cover_url,
        )
        summary = record_summary(record, barcode, group, isbn, cover_url, call_no)
        selected.append((record, summary))
        seen_isbns.update(isbns)
        seen_titles.add(key)
        print(
            f"[{len(selected):03d}/{args.target}] {group}: "
            f"{summary['title']} — {summary['author']} ({isbn})",
            flush=True,
        )

    def process_page(records: list[Record], group: str, quota_end: int) -> None:
        eligible = [record for record in records if precheck(record)]
        if not eligible:
            return
        with ThreadPoolExecutor(max_workers=args.cover_workers) as pool:
            covers = list(pool.map(lambda record: find_verified_cover(record, cover_cache), eligible))
        for record, cover in zip(eligible, covers):
            consider(record, group, quota_end, cover)
            if len(selected) >= quota_end or len(selected) >= args.target:
                break

    for subject, planned_quota in SUBJECT_PLAN:
        if len(selected) >= args.target:
            break
        group_start = len(selected)
        quota_end = min(args.target, group_start + planned_quota)
        for start in range(1, args.max_records_per_subject + 1, args.page_size):
            if len(selected) >= quota_end:
                break
            print(f"Fetching LC subject {subject!r}, startRecord={start}", flush=True)
            records = fetch_sru_page(subject, start, args.page_size)
            if not records:
                break
            process_page(records, subject, quota_end)
            time.sleep(args.sru_delay)
        if len(selected) < quota_end:
            print(
                f"Subject {subject!r} supplied {len(selected) - group_start}/{planned_quota}; "
                "the final filler pass will cover the shortfall.",
                file=sys.stderr,
            )

    if len(selected) < args.target:
        for subject in FILLER_SUBJECTS:
            if len(selected) >= args.target:
                break
            for start in range(1, args.max_records_per_subject + 1, args.page_size):
                if len(selected) >= args.target:
                    break
                print(f"Filler fetch {subject!r}, startRecord={start}", flush=True)
                records = fetch_sru_page(subject, start, args.page_size)
                if not records:
                    break
                process_page(records, f"filler: {subject}", args.target)
                time.sleep(args.sru_delay)

    return selected, rejected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target", type=int, default=200)
    parser.add_argument("--branch", default="MAIN")
    parser.add_argument("--item-type", default="BK")
    parser.add_argument("--barcode-prefix", default="KDAI")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-records-per-subject", type=int, default=300)
    parser.add_argument("--sru-delay", type=float, default=0.35)
    parser.add_argument("--cover-workers", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.target < 1:
        raise SystemExit("--target must be positive")
    selected, rejected = collect_records(args)
    if not selected:
        raise SystemExit("No records passed validation; no output files were written.")
    write_outputs(args.output_dir, selected, rejected, args)
    print(f"\nCreated {len(selected)} validated records in {args.output_dir}")
    print(json.dumps(dict(sorted(rejected.items())), indent=2))
    if len(selected) != args.target:
        print(
            f"ERROR: strict quality gates produced {len(selected)}/{args.target} records. "
            "Do not import this partial batch; expand the subject plan and rerun.",
            file=sys.stderr,
        )
        return 2
    print("VALIDATION PASSED: exact target reached; every record has ISBN, 650 subject and verified cover.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
