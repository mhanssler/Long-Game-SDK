"""Generate verification test plans from structured requirements YAML.

This module turns customer/product requirements into a client-facing Markdown
verification plan with traceability from requirement IDs to generated test cases
and evidence artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

SUPPORTED_VERIFICATION_METHODS = {"inspection", "analysis", "demonstration", "test"}
REQUIRED_REQUIREMENT_FIELDS = ("id", "text", "verification_method", "acceptance_criteria")


class RequirementValidationError(ValueError):
    """Raised when requirements YAML cannot produce a credible test plan."""


@dataclass(frozen=True)
class Requirement:
    """Normalized requirement record used for test-plan generation."""

    id: str
    title: str
    text: str
    requirement_type: str
    verification_method: str
    acceptance_criteria: Mapping[str, Any]
    source: str | None = None
    priority: str | None = None
    operating_conditions: Mapping[str, Any] | None = None
    risks: tuple[str, ...] = ()
    instrumentation: tuple[str, ...] = ()
    safety_controls: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    raw: Mapping[str, Any] | None = None

    @property
    def test_case_id(self) -> str:
        return f"TC-{self.id}"

    @property
    def acceptance_summary(self) -> str:
        pass_condition = self.acceptance_criteria.get("pass_condition")
        if pass_condition:
            return str(pass_condition)
        return "; ".join(f"{key}: {value}" for key, value in self.acceptance_criteria.items())

    @property
    def is_safety_related(self) -> bool:
        values = {self.requirement_type.lower(), (self.priority or "").lower()}
        return "safety" in values or "safety-critical" in values or bool(self.safety_controls)


@dataclass(frozen=True)
class RequirementsDocument:
    """A parsed requirements document plus project metadata."""

    project: Mapping[str, Any]
    requirements: tuple[Requirement, ...]
    source_path: Path

    @property
    def title(self) -> str:
        product = self.project.get("product") or self.project.get("subsystem") or "Requirements"
        return str(product)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _as_mapping(value: Any, *, field: str, requirement_id: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RequirementValidationError(f"{requirement_id} field {field} must be a mapping")
    return value


def _require_text(item: Mapping[str, Any], field: str, *, requirement_id: str) -> str:
    value = item.get(field)
    if value is None or not str(value).strip():
        raise RequirementValidationError(f"{requirement_id} missing required field: {field}")
    return str(value).strip()


def _parse_requirement(item: Any, index: int) -> Requirement:
    if not isinstance(item, Mapping):
        raise RequirementValidationError(f"requirement[{index}] must be a mapping")

    missing_id = item.get("id") is None or not str(item.get("id")).strip()
    if missing_id:
        raise RequirementValidationError(f"requirement[{index}] missing required field: id")
    requirement_id = str(item["id"]).strip()

    for field in REQUIRED_REQUIREMENT_FIELDS:
        if field == "id":
            continue
        if item.get(field) is None or not str(item.get(field)).strip():
            raise RequirementValidationError(f"{requirement_id} missing required field: {field}")

    verification_method = str(item["verification_method"]).strip().lower()
    if verification_method not in SUPPORTED_VERIFICATION_METHODS:
        raise RequirementValidationError(f"{requirement_id} has unsupported verification_method: {verification_method}")

    acceptance_criteria = _as_mapping(
        item.get("acceptance_criteria"), field="acceptance_criteria", requirement_id=requirement_id
    )
    if not acceptance_criteria:
        raise RequirementValidationError(f"{requirement_id} missing required field: acceptance_criteria")

    requirement_type = str(item.get("type") or item.get("requirement_type") or "functional").strip().lower()
    return Requirement(
        id=requirement_id,
        title=str(item.get("title") or requirement_id),
        text=_require_text(item, "text", requirement_id=requirement_id),
        requirement_type=requirement_type,
        verification_method=verification_method,
        acceptance_criteria=acceptance_criteria,
        source=str(item.get("source")) if item.get("source") else None,
        priority=str(item.get("priority")) if item.get("priority") else None,
        operating_conditions=_as_mapping(
            item.get("operating_conditions"), field="operating_conditions", requirement_id=requirement_id
        ),
        risks=_as_tuple(item.get("risks")),
        instrumentation=_as_tuple(item.get("instrumentation")),
        safety_controls=_as_tuple(item.get("safety_controls")),
        evidence=_as_tuple(item.get("evidence")),
        raw=item,
    )


def load_requirements(path: str | Path) -> RequirementsDocument:
    """Load and validate requirements from YAML."""

    source_path = Path(path)
    data = yaml.safe_load(source_path.read_text()) or {}
    if not isinstance(data, Mapping):
        raise RequirementValidationError(f"Requirements document must be a mapping: {source_path}")

    project = data.get("project") or {}
    if not isinstance(project, Mapping):
        raise RequirementValidationError("project must be a mapping when provided")

    raw_requirements = data.get("requirements")
    if not isinstance(raw_requirements, Sequence) or isinstance(raw_requirements, (str, bytes, bytearray)):
        raise RequirementValidationError("requirements must be a list")
    if not raw_requirements:
        raise RequirementValidationError("requirements must include at least one requirement")

    requirements = tuple(_parse_requirement(item, index) for index, item in enumerate(raw_requirements))
    return RequirementsDocument(project=project, requirements=requirements, source_path=source_path)


def _bullet_lines(items: Sequence[str], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [f"- {item}" for item in items]


def _format_mapping(mapping: Mapping[str, Any] | None, *, empty: str) -> list[str]:
    if not mapping:
        return [f"- {empty}"]
    return [f"- {key}: {value}" for key, value in mapping.items()]


def _traceability_lines(requirements: Sequence[Requirement]) -> list[str]:
    lines: list[str] = []
    for req in requirements:
        evidence = ", ".join(req.evidence) if req.evidence else "TBD"
        lines.extend(
            [
                f"- **{req.id}** — {req.title}",
                f"  - Type: {req.requirement_type}",
                f"  - Verification method: {req.verification_method}",
                f"  - Test case ID: {req.test_case_id}",
                f"  - Acceptance criteria: {req.acceptance_summary}",
                f"  - Evidence artifact: {evidence}",
            ]
        )
    return lines


def _test_case_lines(req: Requirement) -> list[str]:
    lines = [
        f"### Test Case: `{req.test_case_id}`",
        "",
        f"- Related requirement: `{req.id}`",
        f"- Objective: Verify {req.title}.",
        f"- Method: {req.verification_method}",
        f"- Requirement text: {req.text}",
        "- Preconditions:",
        *_format_mapping(req.operating_conditions, empty="Define operating conditions before execution."),
        "- Instrumentation:",
        *_bullet_lines(req.instrumentation, empty="Instrumentation TBD."),
        "- Procedure:",
        f"  1. Put the DUT and bench into the required starting state for `{req.id}`.",
        "  2. Verify preflight checks and instrument readiness.",
        f"  3. Execute the {req.verification_method} activity for {req.title}.",
        "  4. Capture required data and metadata.",
        "  5. Return the DUT and bench to the defined safe state.",
        f"- Acceptance criteria: {req.acceptance_summary}",
        "- Expected evidence:",
        *_bullet_lines(req.evidence, empty="Evidence artifact TBD."),
        "- Failure triage:",
        "  - DUT failure indicators: measured behavior violates acceptance criteria with valid bench setup.",
        "  - Bench/setup failure indicators: preflight, instrument, fixture, or calibration check fails.",
        "  - Automation failure indicators: script/runtime/data-path failure prevents valid evidence capture.",
    ]
    if req.safety_controls:
        lines.extend(["- Safety notes:", *[f"  - {item}" for item in req.safety_controls]])
    elif req.is_safety_related:
        lines.extend(["- Safety notes:", "  - Define explicit safe-state and stop-work controls before execution."])
    lines.append("")
    return lines


def generate_test_plan(document: RequirementsDocument, title: str | None = None) -> str:
    """Generate a client-facing Markdown verification test plan."""

    plan_title = title or document.title
    project = document.project
    safety_requirements = [req for req in document.requirements if req.is_safety_related]
    lines = [
        f"# Verification Test Plan: {plan_title}",
        "",
        "## Executive Summary",
        "",
        f"This test plan translates {len(document.requirements)} requirement(s) into verification activities, traceability records, and evidence expectations.",
        "",
        "## Project Metadata",
        "",
        f"- Client: {project.get('client', 'Not specified')}",
        f"- Product / subsystem: {project.get('product') or project.get('subsystem') or 'Not specified'}",
        f"- Program phase: {project.get('phase', 'Not specified')}",
        f"- Requirements source: {document.source_path}",
        "",
        "## Scope and Assumptions",
        "",
        "- This plan covers requirements included in the supplied YAML document.",
        "- Test execution requires calibrated instrumentation and bench preflight before collecting evidence.",
        "- Each generated test case should be reviewed by engineering and safety owners before live hardware use.",
        "",
        "## Requirement Traceability Matrix",
        "",
        *_traceability_lines(document.requirements),
        "",
        "## Safety / Preflight Controls",
        "",
    ]
    if safety_requirements:
        for req in safety_requirements:
            lines.append(f"- **{req.id}** requires explicit safe-state/preflight review.")
            if req.safety_controls:
                lines.extend(f"  - {item}" for item in req.safety_controls)
            else:
                lines.append("  - Define safe-state, interlock, and stop-work criteria before execution.")
    else:
        lines.append("- No safety-critical requirements were marked; still run standard bench preflight before execution.")

    lines.extend(
        [
            "",
            "## Test Cases",
            "",
        ]
    )
    for req in document.requirements:
        lines.extend(_test_case_lines(req))

    lines.extend(
        [
            "## Data and Evidence Requirements",
            "",
            "- Capture DUT serial, hardware revision, firmware version, operator, fixture ID, instrument calibration status, test script version, and git commit where applicable.",
            "- Archive raw data beside the generated report and link each evidence artifact back to requirement ID and test case ID.",
            "",
            "## Exit Criteria",
            "",
            "- Every requirement has an approved verification method and test case.",
            "- Every executed test has raw data and reviewable evidence.",
            "- Any failure is triaged as DUT, bench/setup, automation, or requirement ambiguity.",
            "- Safety-critical tests include documented safe-state behavior before and after execution.",
            "",
            "## Approval",
            "",
            "- Prepared by: ____________________ Date: __________",
            "- Engineering review: _____________ Date: __________",
            "- Safety review: __________________ Date: __________",
            "- Client approval: ________________ Date: __________",
            "",
        ]
    )
    return "\n".join(lines)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "requirement"


def _python_string(value: str) -> str:
    return json.dumps(str(value))


def _pytest_markers(req: Requirement) -> list[str]:
    markers = [f"@pytest.mark.requirement({_python_string(req.id)})"]
    if req.requirement_type:
        markers.append(f"@pytest.mark.{_slug(req.requirement_type)}")
    if req.priority and _slug(req.priority) not in {_slug(req.requirement_type)}:
        markers.append(f"@pytest.mark.{_slug(req.priority)}")
    if req.is_safety_related and "@pytest.mark.safety_critical" not in markers:
        markers.append("@pytest.mark.safety_critical")
    return markers


def _comment_lines(title: str, items: Sequence[str]) -> list[str]:
    if not items:
        return [f"    # {title}: TBD"]
    lines = [f"    # {title}:"]
    lines.extend(f"    # - {item}" for item in items)
    return lines


def generate_pytest_skeleton(req: Requirement, *, bind_bench: bool = False) -> str:
    """Generate an executable pytest skeleton for one requirement.

    The generated test intentionally skips by default so it is safe to commit
    before bench-specific fixtures and assertions are implemented.
    """

    function_name = f"test_{_slug(req.id)}_{_slug(req.title)}"
    markers = _pytest_markers(req)
    lines = [
        '"""Generated verification skeleton for requirement ' + req.id + '."""',
        "",
        "from __future__ import annotations",
        "",
        "from pathlib import Path",
        "",
        "import pytest",
        "",
        "",
        f"REQUIREMENT_ID = {_python_string(req.id)}",
        f"TEST_CASE_ID = {_python_string(req.test_case_id)}",
        f"REQUIREMENT_TITLE = {_python_string(req.title)}",
        f"ACCEPTANCE_CRITERIA = {_python_string(req.acceptance_summary)}",
        f"EVIDENCE_ARTIFACTS = {tuple(req.evidence)!r}",
        "",
        "",
        "@pytest.fixture",
        "def evidence_dir(tmp_path: Path) -> Path:",
        "    path = tmp_path / REQUIREMENT_ID",
        "    path.mkdir(parents=True, exist_ok=True)",
        "    return path",
        "",
    ]
    if not bind_bench:
        lines.extend(
            [
                "",
                "@pytest.fixture",
                "def safe_state():",
                "    # TODO: replace with bench-specific safe-state fixture.",
                "    # This fixture must force the rig safe before and after live hardware tests.",
                "    yield",
                "",
            ]
        )
    fixture_args = "safe_state, evidence_dir: Path"
    if bind_bench:
        fixture_args += ", bench_config, instruments"
    lines.extend(
        [
            "",
            *markers,
            f"def {function_name}({fixture_args}):",
        f"    \"\"\"{req.test_case_id}: {req.text}\"\"\"",
        f"    # Requirement: {req.id} — {req.title}",
        f"    # Method: {req.verification_method}",
        f"    # Acceptance criteria: {req.acceptance_summary}",
        *_comment_lines("Instrumentation", req.instrumentation),
        *_comment_lines("Safety controls", req.safety_controls),
        *_comment_lines("Evidence artifacts", req.evidence),
        "    # TODO: implement bench setup, stimulus, measurements, assertions, and evidence writes.",
        "    # Suggested evidence path pattern:",
        "    for artifact in EVIDENCE_ARTIFACTS:",
        "        _ = evidence_dir / artifact",
        "    pytest.skip(\"Generated skeleton requires bench-specific implementation.\")",
        "",
        ]
    )
    return "\n".join(lines)


def pytest_skeleton_filename(req: Requirement) -> str:
    """Return the generated pytest filename for a requirement."""

    return f"test_{_slug(req.id)}_{_slug(req.title)}.py"


def _load_bench_config(path: str | Path) -> Mapping[str, Any]:
    bench_path = Path(path)
    data = yaml.safe_load(bench_path.read_text()) or {}
    if not isinstance(data, Mapping):
        raise RequirementValidationError(f"Bench config must be a mapping: {bench_path}")
    return data


def _bench_instrument_names(bench_config: Mapping[str, Any]) -> tuple[str, ...]:
    rig = bench_config.get("rig") or {}
    if not isinstance(rig, Mapping):
        return ()
    instruments = rig.get("instruments") or []
    if not isinstance(instruments, Sequence) or isinstance(instruments, (str, bytes, bytearray)):
        return ()
    names: list[str] = []
    for item in instruments:
        if isinstance(item, Mapping) and item.get("name"):
            names.append(str(item["name"]))
    return tuple(names)


def _bench_safe_state_controls(bench_config: Mapping[str, Any]) -> tuple[str, ...]:
    safety_plan = bench_config.get("safety_plan") or {}
    if isinstance(safety_plan, Mapping) and safety_plan.get("safe_state"):
        return _as_tuple(safety_plan.get("safe_state"))
    rig = bench_config.get("rig") or {}
    if isinstance(rig, Mapping) and rig.get("safe_state"):
        return _as_tuple(rig.get("safe_state"))
    return ("Run lg-safe before and after live hardware tests",)


def generate_bench_conftest(bench_config_path: str | Path) -> str:
    """Generate shared pytest fixtures for a YAML-defined bench."""

    bench_config = _load_bench_config(bench_config_path)
    instrument_names = _bench_instrument_names(bench_config)
    safe_state_controls = _bench_safe_state_controls(bench_config)
    lines = [
        '"""Generated bench fixtures for requirements-derived tests."""',
        "",
        "from __future__ import annotations",
        "",
        "from pathlib import Path",
        "",
        "import pytest",
        "import yaml",
        "",
        "",
        'BENCH_CONFIG_PATH = Path(__file__).with_name("bench_config.yaml")',
        f"EXPECTED_INSTRUMENTS = {instrument_names!r}",
        f"SAFE_STATE_CONTROLS = {safe_state_controls!r}",
        "",
        "",
        '@pytest.fixture(scope="session")',
        "def bench_config():",
        "    return yaml.safe_load(BENCH_CONFIG_PATH.read_text())",
        "",
        "",
        '@pytest.fixture(scope="session")',
        "def instruments(bench_config):",
        '    rig = bench_config.get("rig", {})',
        '    return {item["name"]: item for item in rig.get("instruments", []) if item.get("name")}',
        "",
        "",
        "@pytest.fixture",
        "def safe_state(bench_config, instruments):",
        "    # Generated safe-state placeholder. Replace comments with real fixture actions",
        "    # before removing generated pytest.skip(...) calls in test modules.",
    ]
    lines.extend(f"    # - {control}" for control in safe_state_controls)
    lines.extend(
        [
            "    # TODO: call lg-safe or bench-specific driver safe-state commands here.",
            "    yield",
            "    # TODO: repeat safe-state commands after the test body exits.",
            "",
        ]
    )
    return "\n".join(lines)


def write_pytest_skeletons(document: RequirementsDocument, output_dir: str | Path, *, bench_config_path: str | Path | None = None) -> tuple[Path, ...]:
    """Write one pytest skeleton file per requirement."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if bench_config_path is not None:
        source_bench = Path(bench_config_path)
        shutil.copyfile(source_bench, destination / "bench_config.yaml")
        conftest_path = destination / "conftest.py"
        conftest_path.write_text(generate_bench_conftest(source_bench))
        written.append(conftest_path)
        written.append(destination / "bench_config.yaml")
    for req in document.requirements:
        path = destination / pytest_skeleton_filename(req)
        path.write_text(generate_pytest_skeleton(req, bind_bench=bench_config_path is not None))
        written.append(path)
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a verification test plan from requirements YAML.")
    parser.add_argument("requirements", help="Path to requirements YAML")
    parser.add_argument("--output", "-o", help="Optional Markdown output path")
    parser.add_argument("--title", help="Optional report title override")
    parser.add_argument("--pytest-dir", help="Optional directory for generated pytest skeleton files")
    parser.add_argument("--bench-config", help="Optional bench YAML to bind generated pytest skeletons to shared fixtures")
    args = parser.parse_args(argv)

    document = load_requirements(args.requirements)
    markdown = generate_test_plan(document, title=args.title)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        print(f"Wrote verification test plan: {output}")
    else:
        print(markdown)

    if args.pytest_dir:
        written = write_pytest_skeletons(document, args.pytest_dir, bench_config_path=args.bench_config)
        test_count = len([path for path in written if path.name.startswith("test_")])
        print(f"Wrote {test_count} pytest skeleton(s): {Path(args.pytest_dir)}")
        if args.bench_config:
            print(f"Bound skeletons to bench config: {args.bench_config}")


if __name__ == "__main__":
    main()
