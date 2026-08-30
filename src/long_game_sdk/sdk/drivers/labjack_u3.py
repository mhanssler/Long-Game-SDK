"""LabJack U3 driver wrapper.

This uses LabJackPython's ``u3`` module when available. The class is written so
install/discovery can still succeed without the vendor library; attempting to
control the U3 then produces an actionable dependency error.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LabJackDependencyError(RuntimeError):
    pass


@dataclass
class LabJackU3Driver:
    auto_open: bool = True
    serial: str | int | None = None

    def __post_init__(self) -> None:
        local_exodriver = Path(os.environ.get("LABJACK_EXODRIVER", "~/.local/lib/liblabjackusb.so")).expanduser()
        if local_exodriver.exists():
            ctypes.CDLL(str(local_exodriver), mode=ctypes.RTLD_GLOBAL)
        try:
            import u3  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on hardware env
            raise LabJackDependencyError(
                "LabJack U3 control requires LabJackPython. Install with: "
                "uv add LabJackPython, and ensure OS USB permissions allow access."
            ) from exc
        self._u3 = u3
        if self.serial is not None:
            # Never let a safety operation attach to whichever U3 happens to be first.
            self.device = u3.U3(autoOpen=False)
            serial_locator = self.serial
            if isinstance(serial_locator, str) and serial_locator.isdecimal():
                serial_locator = int(serial_locator)
            self.device.open(serial=serial_locator)
        else:
            self.device = u3.U3(autoOpen=self.auto_open)
        try:
            self.device.configU3()
        except Exception:
            # Some firmware/OS combinations still allow basic IO without this.
            pass

    def read_ain(self, channel: int) -> float:
        return float(self.device.getAIN(channel))

    def _read_config(self) -> dict[str, Any]:
        config = self.device.configU3()
        if not isinstance(config, dict):
            raise RuntimeError("LabJack U3 returned an invalid configuration snapshot")
        return config

    def read_identity(self) -> tuple[str, str, str]:
        """Read immutable physical identity from the currently opened U3."""

        config = self._read_config()
        model_value = config.get("DeviceName")
        serial_value = config.get("SerialNumber")
        if model_value is None or serial_value is None:
            raise RuntimeError("LabJack U3 identity is incomplete")
        model = str(model_value)
        serial = str(serial_value)
        if not model or not serial:
            raise RuntimeError("LabJack U3 identity is incomplete")
        return "LabJack", model, serial

    def read_dac_volts(self) -> tuple[float, float]:
        """Read the live calibrated DAC output registers in volts."""

        try:
            return float(self.device.readRegister(5000)), float(self.device.readRegister(5002))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("LabJack U3 DAC readback is unavailable") from exc

    def read_io_config(self) -> tuple[int, int, int, int, bool, bool]:
        """Read live port directions plus current timer/counter enables."""

        try:
            feedback = self.device.getFeedback(self._u3.PortDirRead())
            io_config = self.device.configIO()
            if (
                not isinstance(feedback, list)
                or len(feedback) != 1
                or not isinstance(feedback[0], dict)
                or not isinstance(io_config, dict)
            ):
                raise TypeError("invalid LabJack U3 live IO response")
            directions = feedback[0]
            fio = directions["FIO"]
            eio = directions["EIO"]
            cio = directions["CIO"]
            timers = io_config["NumberOfTimersEnabled"]
            counter0 = io_config["EnableCounter0"]
            counter1 = io_config["EnableCounter1"]
            if any(type(value) is not int for value in (fio, eio, cio, timers)):
                raise TypeError("invalid LabJack U3 direction or timer value")
            if not isinstance(counter0, bool) or not isinstance(counter1, bool):
                raise TypeError("invalid LabJack U3 counter state")
            if any(value < 0 for value in (fio, eio, cio, timers)):
                raise ValueError("negative LabJack U3 IO state")
            return fio, eio, cio, timers, counter0, counter1
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("LabJack U3 live digital-output state is unavailable") from exc

    def set_dac(self, channel: int, volts: float) -> None:
        if channel not in (0, 1):
            raise ValueError("LabJack U3 DAC channel must be 0 or 1")
        if not 0.0 <= volts <= 5.0:
            raise ValueError("LabJack U3 DAC voltage must be between 0 and 5 V")
        register = 5000 if channel == 0 else 5002
        self.device.writeRegister(register, volts)

    def set_fio(self, channel: int, state: int) -> None:
        if state not in (0, 1):
            raise ValueError("Digital output state must be 0 or 1")
        self.device.setFIOState(channel, state)

    def get_fio(self, channel: int) -> int:
        return int(self.device.getFIOState(channel))

    def safe_state(self) -> None:
        # Conservative DAQ state: analog outputs to zero. Do not change FIO
        # directions/states because connected fixtures may rely on them.
        self.set_dac(0, 0.0)
        self.set_dac(1, 0.0)

    def close(self) -> None:
        self.device.close()
