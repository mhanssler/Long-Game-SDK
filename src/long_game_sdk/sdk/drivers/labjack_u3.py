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
            self.device.open(serial=self.serial)
        else:
            self.device = u3.U3(autoOpen=self.auto_open)
        try:
            self.device.configU3()
        except Exception:
            # Some firmware/OS combinations still allow basic IO without this.
            pass

    def read_ain(self, channel: int) -> float:
        return float(self.device.getAIN(channel))

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
