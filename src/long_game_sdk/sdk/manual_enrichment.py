"""Manual-driven schema enrichment for plug-and-play instruments.

This module turns a discovered identity into a stronger YAML driver schema by:
1. finding likely programming/reference manuals on the public web,
2. downloading/caching the best candidate documents,
3. extracting SCPI-like commands from the manual text, and
4. merging those commands into the instrument schema without changing safety
   policy beyond already-known safe-state commands.

The first implementation is intentionally deterministic and offline-cacheable.
It does not require an LLM service. An LLM/manual QA layer can be added on top
later, but this gives the SDK a real "ID hardware -> pull manual -> write driver"
path today.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import yaml

from long_game_sdk.sdk.discovery import InstrumentIdentity, discover_all
from long_game_sdk.sdk.registry import PROJECT_ROOT, ensure_schema, infer_capability_profile, match_driver

MANUALS_DIR = PROJECT_ROOT / "manuals"
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
USER_AGENT = "LongGameSDK/0.1 (+https://github.com/mhanssler/Long-Game-SDK)"


@dataclass(frozen=True)
class ManualCandidate:
    url: str
    title: str
    score: int


@dataclass(frozen=True)
class EnrichmentResult:
    identity: InstrumentIdentity
    schema_path: Path | None
    manual_path: Path | None
    manual_url: str | None
    commands_added: int
    commands_total: int
    errors: tuple[str, ...]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = dict(attrs)
        href = attr.get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None
            self._text = []


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_") or "unknown"


def _urlopen(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-visible manual URLs only
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Manual candidate too large: {length} bytes")
        return response.read(MAX_DOWNLOAD_BYTES + 1)


def _normalize_search_url(href: str) -> str | None:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/l/?") or href.startswith("/l/?kh="):
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        uddg = query.get("uddg", [None])[0]
        if uddg:
            return urllib.parse.unquote(uddg)
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def _score_candidate(identity: InstrumentIdentity, url: str, title: str) -> int:
    text = f"{url} {title}".upper()
    score = 0
    for token in (identity.manufacturer, identity.model):
        token = token.strip().upper()
        if token and token != "UNKNOWN" and token in text:
            score += 20
    if "PROGRAM" in text or "PROGRAMMING" in text:
        score += 30
    if "PROGRAMMER" in text:
        score += 25
    if "REMOTE" in text or "SCPI" in text or "COMMAND" in text:
        score += 20
    if "MANUAL" in text or "GUIDE" in text:
        score += 10
    if url.lower().endswith(".pdf") or ".pdf" in url.lower():
        score += 10
    # Penalize generic marketplaces and SEO mirrors.
    if any(domain in text for domain in ("EBAY", "AMAZON", "ALIBABA", "MANUALSLIB")):
        score -= 15
    return score


def manual_search_queries(identity: InstrumentIdentity) -> list[str]:
    manufacturer = "" if identity.manufacturer == "UNKNOWN" else identity.manufacturer
    model = "" if identity.model == "UNKNOWN" else identity.model
    base = " ".join(part for part in (manufacturer, model) if part).strip() or identity.idn
    return [
        f'{base} programming manual PDF SCPI',
        f'{base} programmer manual remote commands',
        f'{base} user manual SCPI commands PDF',
    ]


def find_manual_candidates(identity: InstrumentIdentity, *, limit: int = 8) -> list[ManualCandidate]:
    """Find likely programming/manual documents with DuckDuckGo HTML search."""

    candidates: dict[str, ManualCandidate] = {}
    for query in manual_search_queries(identity):
        search_url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        try:
            page = _urlopen(search_url).decode("utf-8", errors="ignore")
        except Exception:
            continue
        parser = _LinkParser()
        parser.feed(page)
        for href, title in parser.links:
            url = _normalize_search_url(href)
            if not url:
                continue
            title = html.unescape(title) or url
            score = _score_candidate(identity, url, title)
            if score <= 0:
                continue
            existing = candidates.get(url)
            candidate = ManualCandidate(url=url, title=title, score=score)
            if existing is None or candidate.score > existing.score:
                candidates[url] = candidate
    return sorted(candidates.values(), key=lambda item: item.score, reverse=True)[:limit]


def _manual_cache_path(identity: InstrumentIdentity, url: str) -> Path:
    suffix = ".pdf" if ".pdf" in urllib.parse.urlparse(url).path.lower() else ".html"
    return MANUALS_DIR / f"{_slug(identity.manufacturer)}_{_slug(identity.model)}_{abs(hash(url)) & 0xFFFFFFFF:x}{suffix}"


def download_manual(identity: InstrumentIdentity, candidates: Iterable[ManualCandidate]) -> tuple[Path | None, str | None, tuple[str, ...]]:
    MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for candidate in candidates:
        path = _manual_cache_path(identity, candidate.url)
        if path.exists() and path.stat().st_size > 0:
            return path, candidate.url, tuple(errors)
        try:
            data = _urlopen(candidate.url)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Manual candidate exceeded size cap")
            path.write_bytes(data)
            return path, candidate.url, tuple(errors)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            errors.append(f"{candidate.url}: {exc}")
    return None, None, tuple(errors)


def extract_manual_text(path: Path, *, max_chars: int = 500_000) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages: list[str] = []
            for page in reader.pages[:80]:
                pages.append(page.extract_text() or "")
                if sum(len(item) for item in pages) >= max_chars:
                    break
            return "\n".join(pages)[:max_chars]
        except Exception:
            return ""
    raw = path.read_bytes()[: max_chars * 2]
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text)[:max_chars]


_SCPI_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<cmd>\*IDN\?|\*RST|\*CLS|\*ESR\?|\*OPC\?|:?[A-Z][A-Z0-9]{1,12}(?::[A-Z][A-Z0-9]{1,18}){0,6}\??(?:\s+[A-Z0-9_., +\-{}#@\[\]/]+)?)",
    flags=re.I,
)


def _canonical_command(raw: str) -> str | None:
    command = re.sub(r"\s+", " ", raw.strip().upper())
    command = command.strip(" .,;()[]")
    # If a regex match grabbed prose after a query, cut at the query marker.
    query_index = command.find("?")
    if query_index >= 0:
        command = command[: query_index + 1]
    # For non-query commands with parameters, keep only a short SCPI-safe tail and
    # drop obvious prose words that appear after examples in manuals.
    command = re.split(r"\s+(?:RETURNS|READS|SETS|DISABLES|ENABLES|SELECTS|CONFIGURES|MEASURES)\b", command, maxsplit=1)[0]
    if not command:
        return None
    if command.startswith(("HTTP", "HTTPS", "USB", "VXI", "GPIB")):
        return None
    if len(command) < 4 or len(command) > 120:
        return None
    # Require SCPI-ish shape: common star command or colon tree or query.
    if not (command.startswith("*") or command.startswith(":") or ":" in command or command.endswith("?")):
        return None
    return command


def extract_scpi_commands(text: str, *, limit: int = 250) -> list[str]:
    counts: dict[str, int] = {}
    for match in _SCPI_RE.finditer(text):
        command = _canonical_command(match.group("cmd"))
        if command:
            counts[command] = counts.get(command, 0) + 1
    commands = sorted(counts, key=lambda cmd: (-counts[cmd], cmd))
    return commands[:limit]


def _command_name(command: str, existing: set[str]) -> str:
    base = command.replace("?", "_query").replace("*", "star_")
    base = re.sub(r"[^A-Z0-9]+", "_", base.upper()).strip("_").lower()
    base = re.sub(r"_+", "_", base)[:70] or "command"
    name = base
    index = 2
    while name in existing:
        name = f"{base}_{index}"
        index += 1
    existing.add(name)
    return name


def merge_commands_into_schema(schema: dict[str, Any], commands: list[str], identity: InstrumentIdentity, manual_url: str | None) -> tuple[dict[str, Any], int, int]:
    profile = infer_capability_profile(identity)
    capabilities = schema.setdefault("capabilities", {})
    capability = capabilities.setdefault(profile.instrument_class, {"commands": {}})
    command_map = capability.setdefault("commands", {})
    existing_values = {str(value).upper() for value in command_map.values()}
    existing_names = set(command_map.keys())
    added = 0
    for command in commands:
        if command.upper() in existing_values:
            continue
        name = _command_name(command, existing_names)
        command_map[name] = command
        existing_values.add(command.upper())
        added += 1

    generated = schema.setdefault("generated", {})
    generated["manual_enriched"] = True
    generated["manual_url"] = manual_url
    generated["manual_command_count"] = len(commands)
    generated["manual_commands_added"] = added
    generated["confidence"] = "manual-derived" if commands else generated.get("confidence", "generic")

    safety = schema.setdefault("safety", {})
    safety.setdefault("safe_state", [])
    safety.setdefault("verification", [])
    safety["manual_enrichment_note"] = (
        "Manual-derived commands are added as schema capabilities, but safe-state writes are not expanded automatically. "
        "Potentially hazardous commands require explicit curated safety policy."
    )
    return schema, added, len(commands)


def enrich_identity(identity: InstrumentIdentity, *, force: bool = False, search_limit: int = 8) -> EnrichmentResult:
    schema_path = ensure_schema(identity)
    errors: list[str] = []
    if schema_path is None:
        return EnrichmentResult(identity, None, None, None, 0, 0, ("No schema strategy for identity",))
    try:
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return EnrichmentResult(identity, schema_path, None, None, 0, 0, (f"Schema read failed: {exc}",))

    if schema.get("generated", {}).get("manual_enriched") and not force:
        return EnrichmentResult(identity, schema_path, None, schema.get("generated", {}).get("manual_url"), 0, int(schema.get("generated", {}).get("manual_command_count") or 0), ())

    candidates = find_manual_candidates(identity, limit=search_limit)
    if not candidates:
        return EnrichmentResult(identity, schema_path, None, None, 0, 0, ("No likely manual candidates found",))
    manual_path, manual_url, download_errors = download_manual(identity, candidates)
    errors.extend(download_errors)
    if manual_path is None:
        return EnrichmentResult(identity, schema_path, None, None, 0, 0, tuple(errors) or ("Manual download failed",))
    text = extract_manual_text(manual_path)
    if not text:
        return EnrichmentResult(identity, schema_path, manual_path, manual_url, 0, 0, tuple(errors) + ("Manual text extraction failed",))
    commands = extract_scpi_commands(text)
    if not commands:
        return EnrichmentResult(identity, schema_path, manual_path, manual_url, 0, 0, tuple(errors) + ("No SCPI-like commands found in manual",))
    schema, added, total = merge_commands_into_schema(schema, commands, identity, manual_url)
    schema_path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    return EnrichmentResult(identity, schema_path, manual_path, manual_url, added, total, tuple(errors))


def enrich_all(*, force: bool = False, search_limit: int = 8) -> list[EnrichmentResult]:
    return [enrich_identity(identity, force=force, search_limit=search_limit) for identity in discover_all() if identity.transport == "visa"]


def _print_result(result: EnrichmentResult) -> None:
    identity = result.identity
    print(f"\n{identity.manufacturer} {identity.model} ({identity.serial})")
    print(f"  resource: {identity.resource}")
    print(f"  schema:   {result.schema_path or ''}")
    print(f"  manual:   {result.manual_url or ''}")
    if result.manual_path:
        print(f"  cache:    {result.manual_path}")
    print(f"  commands: added={result.commands_added} total_found={result.commands_total}")
    if result.errors:
        print("  errors:")
        for error in result.errors:
            print(f"    {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find manuals and enrich generated Long Game SDK schemas.")
    parser.add_argument("--force", action="store_true", help="Re-run enrichment even if a schema is already manual_enriched")
    parser.add_argument("--limit", type=int, default=8, help="Manual search candidates per instrument")
    args = parser.parse_args(argv)

    print("--- Long Game SDK Manual Driver Enrichment ---")
    results = enrich_all(force=args.force, search_limit=args.limit)
    for result in results:
        _print_result(result)
    if not results:
        print("No VISA/SCPI instruments discovered for manual enrichment.")
    # Manual lookup is best-effort: an unavailable search engine or missing manual
    # should not make install/onboarding pipelines fail.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
