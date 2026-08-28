"""Deterministic local YAML memory for resolved diagnostic cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Protocol, runtime_checkable

import yaml

from .diagnostic_session import DiagnosticSession

_CASE_FIELDS = (
    "schema_version",
    "case_id",
    "instrument_class",
    "instrument_identity",
    "symptom",
    "symptom_tags",
    "findings_summary",
    "confirmed_root_cause",
    "fix_applied",
    "recommended_fix",
    "outcome",
)
_WORD = re.compile(r"[a-z0-9]+")


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate mapping key: {key}", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


@runtime_checkable
class DiagnosticCaseRetriever(Protocol):
    """Structured retrieval boundary, replaceable by a future implementation."""

    def retrieve(
        self,
        instrument_identity: Mapping[str, str] | str,
        symptom: str,
        *,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...


@runtime_checkable
class DiagnosticCaseStore(DiagnosticCaseRetriever, Protocol):
    """Retrieval plus persistence used by the diagnostic engine."""

    def save(self, session: DiagnosticSession) -> Path: ...


class DiagnosticCaseLibrary:
    """Filesystem-backed deterministic YAML case store.

    Filenames are derived solely from a SHA-256 digest of canonical case
    content. Callers cannot provide paths or filenames.
    """

    def __init__(self, root: str | Path) -> None:
        requested = Path(root).expanduser()
        requested.mkdir(parents=True, exist_ok=True)
        metadata = requested.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("diagnostic case library root must be a real directory, not a symlink")
        self.root = requested.resolve()

    def save(self, session: DiagnosticSession) -> Path:
        """Atomically save a resolved session, returning its stable path."""

        if not isinstance(session, DiagnosticSession):
            raise TypeError("session must be a DiagnosticSession")
        if session.status != "resolved":
            raise ValueError("only resolved diagnostic sessions can be saved")
        if session.outcome != "resolved":
            raise ValueError("only operator-confirmed outcomes exactly equal to resolved can be saved")
        required_text = {
            "symptom": session.symptom,
            "confirmed root cause": session.confirmed_root_cause,
            "fix applied": session.fix_applied,
        }
        missing = [name for name, value in required_text.items() if not isinstance(value, str) or not value.strip()]
        if missing:
            raise ValueError(f"resolved diagnostic case is missing {', '.join(missing)}")
        body = self._case_body(session)
        canonical = yaml.safe_dump(body, allow_unicode=True, sort_keys=True).encode("utf-8")
        case_id = hashlib.sha256(canonical).hexdigest()
        case: dict[str, Any] = {"schema_version": 1, "case_id": case_id, **body}
        payload = yaml.safe_dump(case, allow_unicode=True, sort_keys=False)
        destination = self._case_path(case_id)
        if not _valid_case(case, destination):
            raise ValueError("resolved diagnostic case contains invalid values")
        if destination.exists() or destination.is_symlink():
            if self._safe_read(destination) == case:
                return destination
            raise FileExistsError("diagnostic case destination does not contain the canonical case")

        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".case-", suffix=".tmp", dir=self.root)
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Hard-link publication is atomic and never replaces a raced
                # destination (including a symlink or non-regular file).
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                if self._safe_read(destination) != case:
                    raise FileExistsError("diagnostic case destination does not contain the canonical case") from None
            directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination

    def retrieve(
        self,
        instrument_identity: Mapping[str, str] | str,
        symptom: str,
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return stable top-N cases matched by class and symptom keywords."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit <= 0:
            return []
        instrument_class = _instrument_class(instrument_identity)
        query_terms = set(_normalize_keywords(symptom))
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for path in sorted(self.root.glob("case-*.yaml"), key=lambda item: item.name):
            case = self._safe_read(path)
            if case is None or _normalize_class(case["instrument_class"]) != instrument_class:
                continue
            terms = set(_normalize_keywords(case["symptom"]))
            terms.update(case["symptom_tags"])
            overlap = len(query_terms & terms)
            if overlap == 0:
                continue
            ranked.append((-overlap, case["case_id"], case))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [deepcopy(case) for _, _, case in ranked[:limit]]

    def _case_path(self, case_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", case_id):
            raise ValueError("invalid diagnostic case identifier")
        path = self.root / f"case-{case_id}.yaml"
        if path.parent.resolve() != self.root:
            raise ValueError("diagnostic case path escapes library root")
        return path

    def _safe_read(self, path: Path) -> dict[str, Any] | None:
        descriptor: int | None = None
        try:
            if path.parent != self.root or path.is_symlink():
                return None
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return None
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = None
                loaded = yaml.load(stream.read(), Loader=_UniqueSafeLoader)
        except (OSError, UnicodeError, yaml.YAMLError):
            return None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not isinstance(loaded, dict) or set(loaded) != set(_CASE_FIELDS):
            return None
        if not _valid_case(loaded, path):
            return None
        return loaded

    @staticmethod
    def _case_body(session: DiagnosticSession) -> dict[str, Any]:
        identity = {str(key): str(value) for key, value in sorted(session.instrument_identity.items())}
        instrument_class = _instrument_class(identity)
        symptom = (session.symptom or "").strip()
        tags = set(_normalize_keywords(symptom))
        for tag in session.symptom_tags:
            tags.update(_normalize_keywords(tag))
        findings = [
            {
                "probe_name": finding.probe_name,
                "args": _canonical(finding.args),
                "result": _canonical(finding.result),
            }
            for finding in session.findings
        ]
        return {
            "instrument_class": instrument_class,
            "instrument_identity": identity,
            "symptom": symptom,
            "symptom_tags": sorted(tags),
            "findings_summary": findings,
            "confirmed_root_cause": session.confirmed_root_cause,
            "fix_applied": session.fix_applied,
            "recommended_fix": session.recommended_fix,
            "outcome": session.outcome,
        }


def _instrument_class(identity: Mapping[str, str] | str) -> str:
    if isinstance(identity, str):
        value = identity
    elif isinstance(identity, Mapping):
        value = identity.get("instrument_class") or identity.get("identity") or identity.get("model") or ""
    else:
        raise TypeError("instrument identity must be a string or mapping")
    normalized = _normalize_class(str(value))
    if not normalized:
        raise ValueError("instrument identity must include an instrument class")
    return normalized


def _normalize_class(value: str) -> str:
    return "_".join(_WORD.findall(value.casefold()))


def _normalize_keywords(value: str) -> list[str]:
    if not isinstance(value, str):
        return []
    return sorted({_stem(word) for word in _WORD.findall(value.casefold()) if word})


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _canonical(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("diagnostic case values must be finite")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"unsupported diagnostic case value: {type(value).__name__}")


def _plain_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_plain_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _plain_value(item) for key, item in value.items())
    return False


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_case(case: dict[str, Any], path: Path) -> bool:
    identifier = case.get("case_id")
    body = {key: case[key] for key in _CASE_FIELDS[2:]}
    canonical = yaml.safe_dump(body, allow_unicode=True, sort_keys=True).encode("utf-8")
    content_identifier = hashlib.sha256(canonical).hexdigest()
    identity = case.get("instrument_identity")
    tags = case.get("symptom_tags")
    findings = case.get("findings_summary")
    fix_applied = case.get("fix_applied")
    recommended_fix = case.get("recommended_fix")
    finding_shape = isinstance(findings, list) and all(
        isinstance(finding, dict)
        and set(finding) == {"probe_name", "args", "result"}
        and _nonempty_text(finding["probe_name"])
        and isinstance(finding["args"], dict)
        and _plain_value(finding["args"])
        and _plain_value(finding["result"])
        for finding in (findings if isinstance(findings, list) else [])
    )
    # Coordinate semantics: v1 stores resolved sessions only. A non-empty
    # confirmed_root_cause plus an actually applied fix are the explicit
    # operator-verification markers. Recommendations alone are never memory.
    return bool(
        case.get("schema_version") == 1
        and isinstance(identifier, str)
        and re.fullmatch(r"[a-f0-9]{64}", identifier)
        and identifier == content_identifier
        and path.name == f"case-{identifier}.yaml"
        and _nonempty_text(case.get("instrument_class"))
        and case["instrument_class"] == _normalize_class(case["instrument_class"])
        and isinstance(identity, dict)
        and bool(identity)
        and all(_nonempty_text(key) and _nonempty_text(value) for key, value in identity.items())
        and _nonempty_text(case.get("symptom"))
        and isinstance(tags, list)
        and bool(tags)
        and all(_nonempty_text(tag) and re.fullmatch(r"[a-z0-9]+", tag) for tag in tags)
        and tags == sorted(set(tags))
        and finding_shape
        and _nonempty_text(case.get("confirmed_root_cause"))
        and _nonempty_text(fix_applied)
        and (recommended_fix is None or _nonempty_text(recommended_fix))
        and case.get("outcome") == "resolved"
    )


__all__ = ["DiagnosticCaseLibrary", "DiagnosticCaseRetriever", "DiagnosticCaseStore"]
