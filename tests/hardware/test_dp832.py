from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pyvisa
import pytest

from long_game_sdk.sdk.safety import apply_safe_state
from long_game_sdk.sdk.universal_driver import UniversalDriver

pytestmark = [pytest.mark.hardware, pytest.mark.rigol_dp832]

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "rigol_dp832.yaml"


def _find_dp832_resource() -> tuple[str, tuple[str, str, str]]:
    manager = pyvisa.ResourceManager("@py")
    try:
        for resource in manager.list_resources():
            instrument = None
            identity = ""
            try:
                instrument = cast(Any, manager.open_resource(resource))
                instrument.timeout = 3000
                identity = instrument.query("*IDN?").strip().replace("\x00", "")
            except Exception:
                continue
            finally:
                if instrument is not None:
                    try:
                        instrument.close()
                    except Exception:
                        pass
            fields = tuple(part.strip() for part in identity.split(","))
            if (
                len(fields) >= 3
                and all(fields[:3])
                and fields[0].casefold() in {"rigol", "rigol technologies"}
                and fields[1].casefold() == "dp832"
            ):
                return resource, (fields[0], fields[1], fields[2])
    finally:
        manager.close()
    pytest.skip("No Rigol DP832 detected via PyVISA")


def _query_dp832_with_safe_state(resource: str, identity: tuple[str, str, str]) -> None:
    manufacturer, model, serial = identity
    expected = {resource: {
        "expected_manufacturer": manufacturer,
        "expected_model": model,
        "expected_serial": serial,
        "energy_source": True,
    }}
    try:
        before = apply_safe_state([resource], expected_devices=expected)
        assert len(before) == 1 and before[0].safe, before
        driver = None
        try:
            driver = UniversalDriver(
                resource,
                SCHEMA_PATH,
                expected_manufacturer=manufacturer,
                expected_model=model,
                expected_serial=serial,
            )
            response = getattr(driver, "get_voltage")(channel=1)
            assert float(response.strip()) >= 0.0
        finally:
            if driver is not None:
                driver.close()
    finally:
        after = apply_safe_state([resource], expected_devices=expected)
        assert len(after) == 1 and after[0].safe, after


def test_dp832_query_against_live_hardware() -> None:
    _query_dp832_with_safe_state(*_find_dp832_resource())
