from __future__ import annotations

import argparse
import logging
import time
from typing import Any, Sequence, cast

import pyvisa

from long_game_sdk.sdk.discovery import InstrumentIdentity, _parse_idn
from long_game_sdk.sdk.manual_enrichment import enrich_identity

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AutoOnboarder:
    def __init__(self, poll_interval: int = 60, resource_manager: Any | None = None):
        # Force the pure-Python backend for cross-platform parity with lg-discover.
        # This avoids depending on NI-VISA being installed on Windows/macOS/Linux.
        self.rm = resource_manager if resource_manager is not None else pyvisa.ResourceManager("@py")
        self.poll_interval = poll_interval
        self.known_instruments: set[str] = set()

    def scan(self) -> None:
        try:
            current_instruments = set(self.rm.list_resources())
            new_instruments = current_instruments - self.known_instruments

            for instrument_id in new_instruments:
                logger.info("New hardware detected: %s", instrument_id)
                self.onboard(instrument_id)
                # Mark as seen even if optional manual enrichment fails so a missing
                # web result or offline network does not spam the operator forever.
                self.known_instruments.add(instrument_id)

        except Exception as exc:  # noqa: BLE001 - service should keep running
            logger.error("Scan error: %s", exc)

    def identify(self, instrument_id: str) -> InstrumentIdentity:
        instrument: Any | None = None
        try:
            instrument = cast(Any, self.rm.open_resource(instrument_id))
            try:
                instrument.timeout = 3000
            except Exception:
                pass
            idn = str(instrument.query("*IDN?")).strip().replace("\x00", "")
            manufacturer, model, serial, firmware = _parse_idn(idn)
            return InstrumentIdentity(
                transport="visa",
                resource=instrument_id,
                manufacturer=manufacturer,
                model=model,
                serial=serial,
                firmware=firmware,
                idn=idn,
            )
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass

    def onboard(self, instrument_id: str) -> None:
        try:
            identity = self.identify(instrument_id)
            logger.info("Device identified: %s", identity.idn)
            logger.info("Searching for manuals/schema enrichment for: %s", identity.model)
            result = enrich_identity(identity)
            if result.schema_path:
                logger.info("Schema ready: %s", result.schema_path)
            if result.manual_url:
                logger.info("Manual candidate: %s", result.manual_url)
            if result.errors:
                for error in result.errors:
                    logger.warning("Manual enrichment warning for %s: %s", instrument_id, error)
        except Exception as exc:  # noqa: BLE001 - one device should not kill observer
            logger.error("Onboarding failed for %s: %s", instrument_id, exc)

    def run(self) -> None:
        logger.info("Auto-Onboarder service started.")
        while True:
            self.scan()
            time.sleep(self.poll_interval)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Watch VISA resources and auto-onboard newly detected instruments.")
    parser.add_argument("--poll-interval", type=int, default=60, help="Seconds between discovery scans")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    args = parser.parse_args(argv)

    onboarder = AutoOnboarder(poll_interval=args.poll_interval)
    if args.once:
        onboarder.scan()
        return
    onboarder.run()


if __name__ == "__main__":
    main()
