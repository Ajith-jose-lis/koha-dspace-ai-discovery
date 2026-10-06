#!/usr/bin/env python3
"""Build a reviewed DSpace Simple Archive Format set from Zenodo OA records.

The script searches public Zenodo records, accepts only English PDF research
outputs carrying a redistribution-friendly Creative Commons licence, validates
the downloaded bytes, and writes both a DSpace SAF tree and audit manifests.
It never imports content into DSpace; review the manifest before importing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree


API = "https://zenodo.org/api/records"
USER_AGENT = "Koha-DSpace-AI-prototype/1.0 (research metadata acquisition)"
ALLOWED_LICENSE_PREFIXES = ("cc-by-", "cc0-", "cc-zero")
ALLOWED_TYPES = {
    "publication-article",
    "publication-conferencepaper",
    "publication-report",
    "publication-thesis",
    "journal article",
    "conference paper",
    "report",
    "thesis",
    "article",
}
TOPICS = [
    "digital libraries",
    "information retrieval",
    "metadata quality",
    "semantic search",
    "retrieval augmented generation",
    "library discovery systems",
    "recommender systems",
    "natural language processing",
    "open access repositories",
    "research data management",
]


def request(url: str, *, attempts: int = 5, timeout: int = 60) -> bytes:
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            if attempt + 1 == attempts:
                break
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"request failed after {attempts} attempts: {url}: {last}")


def strip_html(value: str) -> str:
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value or "", flags=re.I)
    value = re.sub(r"</\s*p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def ascii_filename(value: str, fallback: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return (value[:100] or fallback) + ("" if value.lower().endswith(".pdf") else ".pdf")


def get_hits(payload: dict) -> list[dict]:
    hits = payload.get("hits", {})
    return hits.get("hits", []) if isinstance(hits, dict) else []


def record_access_open(record: dict) -> bool:
    access = record.get("access", {})
    metadata = record.get("metadata", {})
    if access:
        return access.get("record") == "public" and access.get("files", "public") == "public"
    return metadata.get("access_right", "open") == "open"


def licence(record: dict) -> tuple[str, str, str]:
    metadata = record.get("metadata", {})
    candidates = metadata.get("rights") or []
    if not candidates and metadata.get("license"):
        candidates = [metadata["license"]]
    for item in candidates:
        if isinstance(item, str):
            ident, title, url = item, item, ""
        else:
            ident = str(item.get("id") or "")
            title = str(item.get("title") or ident)
            url = str(item.get("link") or item.get("url") or "")
        ident_l = ident.lower()
        if ident_l.startswith(ALLOWED_LICENSE_PREFIXES):
            return ident, title, url
    return "", "", ""


def resource_type(record: dict) -> str:
    value = record.get("metadata", {}).get("resource_type", {})
    if isinstance(value, str):
        return value.lower()
    return str(value.get("id") or value.get("title") or value.get("type") or "").lower()


def creators(record: dict) -> list[str]:
    output = []
    for creator in record.get("metadata", {}).get("creators", []):
        person = creator.get("person_or_org", {}) if isinstance(creator, dict) else {}
        name = person.get("name") or (creator.get("name") if isinstance(creator, dict) else "")
        if name:
            output.append(str(name).strip())
    return output


def keywords(record: dict) -> list[str]:
    metadata = record.get("metadata", {})
    output = []
    for item in metadata.get("subjects", []):
        if isinstance(item, dict):
            value = item.get("subject") or item.get("term") or item.get("id")
        else:
            value = item
        if value:
            output.append(str(value).strip())
    for value in metadata.get("keywords", []):
        if value:
            output.append(str(value).strip())
    return list(dict.fromkeys(output))


def pdf_entries(record: dict) -> list[dict]:
    raw = record.get("files") or []
    if isinstance(raw, dict):
        raw = list((raw.get("entries") or {}).values())
    output = []
    for entry in raw:
        key = str(entry.get("key") or entry.get("filename") or "")
        mimetype = str(entry.get("mimetype") or entry.get("type") or "")
        links = entry.get("links") or {}
        url = links.get("content") or links.get("download") or links.get("self")
        size = int(entry.get("size") or 0)
        if url and (key.lower().endswith(".pdf") or "pdf" in mimetype.lower()):
            output.append({"key": key, "url": url, "size": size})
    return sorted(output, key=lambda x: (x["size"] == 0, x["size"]))


def record_url(record: dict) -> str:
    links = record.get("links") or {}
    return str(links.get("self_html") or links.get("html") or f"https://zenodo.org/records/{record.get('id')}")


def normalise(record: dict, topic: str, max_pdf_bytes: int) -> dict | None:
    metadata = record.get("metadata", {})
    title = strip_html(str(metadata.get("title") or ""))
    description = strip_html(str(metadata.get("description") or metadata.get("abstract") or ""))
    author_names = creators(record)
    lic_id, lic_title, lic_url = licence(record)
    rtype = resource_type(record)
    language = str(metadata.get("language") or "en").lower()
    pdfs = pdf_entries(record)
    if not record_access_open(record) or not lic_id or not pdfs:
        return None
    if rtype and rtype not in ALLOWED_TYPES and not any(x in rtype for x in ("article", "conference", "report", "thesis")):
        return None
    if language not in ("en", "eng", "english", ""):
        return None
    if len(title) < 12 or not author_names or len(description) < 80:
        return None
    pdf = next((p for p in pdfs if not p["size"] or p["size"] <= max_pdf_bytes), None)
    if not pdf:
        return None
    haystack = " ".join([title, description, " ".join(keywords(record))]).lower()
    topic_tokens = [x for x in re.findall(r"[a-z]+", topic.lower()) if len(x) > 3]
    if topic_tokens and not any(token in haystack for token in topic_tokens):
        return None
    doi = str(metadata.get("doi") or record.get("doi") or "")
    if not doi:
        for identifier in metadata.get("identifiers", []):
            if isinstance(identifier, dict) and identifier.get("scheme") == "doi":
                doi = str(identifier.get("identifier") or "")
                break
    return {
        "record_id": str(record.get("id") or ""),
        "topic": topic,
        "title": title,
        "creators": author_names,
        "date_issued": str(metadata.get("publication_date") or metadata.get("date") or ""),
        "abstract": description,
        "subjects": keywords(record) or [topic],
        "doi": doi,
        "record_url": record_url(record),
        "license_id": lic_id,
        "license_title": lic_title,
        "license_url": lic_url,
        "resource_type": rtype or "Research article",
        "publisher": str(metadata.get("publisher") or "Zenodo"),
        "pdf_source_name": pdf["key"],
        "pdf_url": pdf["url"],
        "reported_size": pdf["size"],
    }


def dc(parent: Element, element: str, qualifier: str, value: str, language: str | None = None) -> None:
    if not value:
        return
    attrs = {"element": element, "qualifier": qualifier}
    if language:
        attrs["language"] = language
    child = SubElement(parent, "dcvalue", attrs)
    child.text = value


def write_xml(path: Path, item: dict) -> None:
    root = Element("dublin_core")
    dc(root, "title", "none", item["title"], "en")
    for name in item["creators"]:
        dc(root, "contributor", "author", name)
    dc(root, "date", "issued", item["date_issued"])
    dc(root, "description", "abstract", item["abstract"], "en")
    for subject in item["subjects"]:
        dc(root, "subject", "none", subject, "en")
    dc(root, "identifier", "doi", item["doi"])
    dc(root, "identifier", "uri", item["record_url"])
    dc(root, "rights", "none", item["license_title"])
    dc(root, "rights", "uri", item["license_url"])
    dc(root, "type", "none", item["resource_type"])
    dc(root, "publisher", "none", item["publisher"])
    dc(root, "language", "iso", "en")
    dc(root, "description", "provenance", "Open-access source acquired from Zenodo; source and licence verified during prototype ingest.")
    ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def search(topic: str, size: int) -> list[dict]:
    params = urllib.parse.urlencode({"q": f'"{topic}"', "size": size, "sort": "bestmatch"})
    payload = json.loads(request(f"{API}?{params}").decode("utf-8"))
    return get_hits(payload)


def download_pdf(item: dict, destination: Path, max_pdf_bytes: int) -> tuple[int, str]:
    data = request(item["pdf_url"], attempts=5, timeout=120)
    if len(data) > max_pdf_bytes:
        raise RuntimeError(f"PDF exceeds size limit ({len(data)} bytes)")
    if not data.startswith(b"%PDF-"):
        raise RuntimeError("downloaded content is not a PDF")
    destination.write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/opt/koha-dspace-ai/data/dspace-oa-20")
    parser.add_argument("--target", type=int, default=20)
    parser.add_argument("--per-topic", type=int, default=2)
    parser.add_argument("--search-size", type=int, default=60)
    parser.add_argument("--max-pdf-mb", type=int, default=25)
    args = parser.parse_args()
    output = Path(args.output_dir)
    staging = output.with_name(output.name + ".building")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    max_pdf_bytes = args.max_pdf_mb * 1024 * 1024
    accepted: list[dict] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    failures: list[dict] = []

    for topic in TOPICS:
        if len(accepted) >= args.target:
            break
        print(f"Searching: {topic}", flush=True)
        topic_count = 0
        try:
            records = search(topic, args.search_size)
        except Exception as exc:
            failures.append({"topic": topic, "record_id": "", "reason": f"search: {exc}"})
            continue
        for record in records:
            if len(accepted) >= args.target or topic_count >= args.per_topic:
                break
            item = normalise(record, topic, max_pdf_bytes)
            if not item:
                continue
            title_key = re.sub(r"\W+", "", item["title"].lower())
            if item["record_id"] in seen_ids or title_key in seen_titles:
                continue
            number = len(accepted) + 1
            item_dir = staging / f"item_{number:03d}"
            item_dir.mkdir()
            filename = ascii_filename(item["pdf_source_name"], f"document-{number:03d}")
            try:
                size, sha256 = download_pdf(item, item_dir / filename, max_pdf_bytes)
            except Exception as exc:
                shutil.rmtree(item_dir)
                failures.append({"topic": topic, "record_id": item["record_id"], "reason": str(exc)})
                continue
            item.update({"item": number, "pdf_filename": filename, "size_bytes": size, "sha256": sha256})
            write_xml(item_dir / "dublin_core.xml", item)
            licence_text = (
                f"Title: {item['title']}\nSource: {item['record_url']}\n"
                f"Licence: {item['license_title']} ({item['license_id']})\n"
                f"Licence URL: {item['license_url']}\n"
            )
            (item_dir / "LICENSE.txt").write_text(licence_text, encoding="utf-8")
            (item_dir / "contents").write_text(
                f"{filename}\tbundle:ORIGINAL\tdescription:Open-access full text\tprimary:true\n"
                "LICENSE.txt\tbundle:LICENSE\tdescription:Source and licence record\n",
                encoding="utf-8",
            )
            accepted.append(item)
            seen_ids.add(item["record_id"])
            seen_titles.add(title_key)
            topic_count += 1
            print(f"[{number:02d}/{args.target}] {item['title']} [{item['license_id']}]", flush=True)
            time.sleep(0.5)

    manifest_fields = [
        "item", "topic", "title", "creators", "date_issued", "doi", "record_url",
        "license_id", "license_title", "license_url", "resource_type", "publisher",
        "pdf_filename", "size_bytes", "sha256", "pdf_url",
    ]
    with (staging / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=manifest_fields, extrasaction="ignore")
        writer.writeheader()
        for item in accepted:
            row = dict(item)
            row["creators"] = "; ".join(item["creators"])
            writer.writerow(row)
    (staging / "manifest.json").write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
    (staging / "rejections.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(accepted) != args.target:
        print(f"VALIDATION FAILED: obtained {len(accepted)} of {args.target}; nothing replaced", file=sys.stderr)
        return 2
    if output.exists():
        shutil.rmtree(output)
    staging.rename(output)
    print(f"VALIDATION PASSED: {len(accepted)} licensed PDFs prepared in {output}")
    print(f"Review: {output / 'manifest.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
