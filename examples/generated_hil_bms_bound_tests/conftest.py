"""Generated bench fixtures for requirements-derived tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


BENCH_CONFIG_PATH = Path(__file__).with_name("bench_config.yaml")
EXPECTED_INSTRUMENTS = ('main_psu', 'electronic_load')
SAFE_STATE_CONTROLS = ('All PSU outputs OFF before connect/disconnect', 'Electronic load input OFF before wiring changes', 'DUT contactors open before removing covers', 'Run lg-safe before and after live hardware tests')


@pytest.fixture(scope="session")
def bench_config():
    return yaml.safe_load(BENCH_CONFIG_PATH.read_text())


@pytest.fixture(scope="session")
def instruments(bench_config):
    rig = bench_config.get("rig", {})
    return {item["name"]: item for item in rig.get("instruments", []) if item.get("name")}


@pytest.fixture
def safe_state(bench_config, instruments):
    # Generated safe-state placeholder. Replace comments with real fixture actions
    # before removing generated pytest.skip(...) calls in test modules.
    # - All PSU outputs OFF before connect/disconnect
    # - Electronic load input OFF before wiring changes
    # - DUT contactors open before removing covers
    # - Run lg-safe before and after live hardware tests
    # TODO: call lg-safe or bench-specific driver safe-state commands here.
    yield
    # TODO: repeat safe-state commands after the test body exits.
