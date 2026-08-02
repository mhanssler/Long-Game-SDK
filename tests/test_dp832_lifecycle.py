from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _hardware_module() -> ModuleType:
    path = Path(__file__).with_name("hardware") / "test_dp832.py"
    spec = importlib.util.spec_from_file_location("dp832_hardware_lifecycle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_final_safe_state_runs_even_when_driver_close_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _hardware_module()
    events: list[str] = []

    def safe_state(*args, **kwargs):
        events.append("safe")
        return [SimpleNamespace(safe=True)]

    class Driver:
        def __init__(self, *args, **kwargs) -> None:
            events.append("open")

        def get_voltage(self, *, channel: int) -> str:
            events.append(f"query-{channel}")
            return "1.0"

        def close(self) -> None:
            events.append("close")
            raise RuntimeError("close failed")

    monkeypatch.setattr(module, "apply_safe_state", safe_state)
    monkeypatch.setattr(module, "UniversalDriver", Driver)

    with pytest.raises(RuntimeError, match="close failed"):
        module._query_dp832_with_safe_state("USB::DP832", ("RIGOL", "DP832", "SN1"))

    assert events == ["safe", "open", "query-1", "close", "safe"]


def test_discovery_closes_resource_manager_when_instrument_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _hardware_module()
    events: list[str] = []

    class Instrument:
        timeout = 0

        def query(self, command: str) -> str:
            assert command == "*IDN?"
            return "RIGOL TECHNOLOGIES,DP832,serial,version"

        def close(self) -> None:
            events.append("instrument-close")
            raise RuntimeError("close failed")

    class Manager:
        def list_resources(self) -> tuple[str, ...]:
            return ("USB::DP832",)

        def open_resource(self, resource: str) -> Instrument:
            assert resource == "USB::DP832"
            return Instrument()

        def close(self) -> None:
            events.append("manager-close")

    monkeypatch.setattr(module.pyvisa, "ResourceManager", lambda backend: Manager())

    assert module._find_dp832_resource() == (
        "USB::DP832",
        ("RIGOL TECHNOLOGIES", "DP832", "serial"),
    )
    assert events == ["instrument-close", "manager-close"]
