from __future__ import annotations

from pathlib import Path

from long_game_sdk.sdk.universal_driver import UniversalDriver

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "rigol_dp832.yaml"


class FakeInstrument:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command == ":MEASure:VOLTage? CH1":
            return "4.998"
        raise AssertionError(f"unexpected query: {command}")

    def write(self, command: str) -> None:
        raise AssertionError(f"unit test must not write hardware: {command}")

    def close(self) -> None:
        self.closed = True


def test_dynamic_query_method_uses_injected_transport() -> None:
    instrument = FakeInstrument()
    driver = UniversalDriver(
        "FAKE::DP832",
        SCHEMA_PATH,
        instrument=instrument,
    )

    assert getattr(driver, "get_voltage")(channel=1).strip() == "4.998"
    assert instrument.queries == [":MEASure:VOLTage? CH1"]

    driver.close()
    assert instrument.closed
