from __future__ import annotations

import sys
from types import SimpleNamespace

from long_game_sdk.sdk.drivers.labjack_u3 import LabJackU3Driver


class _FakeU3Device:
    opened_serials: list[object] = []

    def __init__(self, *, autoOpen: bool) -> None:  # noqa: N803 - vendor API spelling
        self.auto_open = autoOpen
        self.closed = False
        self.writes: list[tuple[int, float]] = []

    def open(self, *, serial: object) -> None:
        self.opened_serials.append(serial)

    def configU3(self) -> dict[str, object]:  # noqa: N802 - vendor API spelling
        return {
            "DeviceName": "U3-HV",
            "SerialNumber": 320104933,
        }

    def getFeedback(self, command):  # noqa: N802 - vendor API spelling
        assert command == "port-direction-read"
        return [{"FIO": 0, "EIO": 0, "CIO": 0}]

    def configIO(self) -> dict[str, object]:  # noqa: N802 - vendor API spelling
        return {
            "NumberOfTimersEnabled": 0,
            "EnableCounter0": False,
            "EnableCounter1": False,
        }

    def readRegister(self, register: int) -> float:  # noqa: N802 - vendor API spelling
        return {5000: 0.0001, 5002: 0.0002}[register]

    def writeRegister(self, register: int, value: float) -> None:  # noqa: N802
        self.writes.append((register, value))

    def close(self) -> None:
        self.closed = True


def test_numeric_string_serial_opens_exact_labjack_and_reads_live_output_state(
    monkeypatch, tmp_path
) -> None:
    _FakeU3Device.opened_serials = []
    fake_u3 = SimpleNamespace(U3=_FakeU3Device, PortDirRead=lambda: "port-direction-read")
    monkeypatch.setitem(sys.modules, "u3", fake_u3)
    monkeypatch.setenv("LABJACK_EXODRIVER", str(tmp_path / "missing.so"))

    driver = LabJackU3Driver(serial="320104933")
    try:
        assert _FakeU3Device.opened_serials == [320104933]
        assert driver.read_identity() == ("LabJack", "U3-HV", "320104933")
        assert driver.read_dac_volts() == (0.0001, 0.0002)
        assert driver.read_io_config() == (0, 0, 0, 0, False, False)
    finally:
        driver.close()


def test_identity_requires_hardware_model_and_serial(monkeypatch, tmp_path) -> None:
    class MissingIdentityDevice(_FakeU3Device):
        def configU3(self) -> dict[str, object]:  # noqa: N802
            return {
                "FIODirection": 0,
                "EIODirection": 0,
                "CIODirection": 0,
                "TimerCounterMask": 64,
            }

    monkeypatch.setitem(sys.modules, "u3", SimpleNamespace(U3=MissingIdentityDevice))
    monkeypatch.setenv("LABJACK_EXODRIVER", str(tmp_path / "missing.so"))
    driver = LabJackU3Driver(serial=320104933)
    try:
        try:
            driver.read_identity()
        except RuntimeError as error:
            assert "identity is incomplete" in str(error)
        else:
            raise AssertionError("missing hardware identity must fail closed")
    finally:
        driver.close()
