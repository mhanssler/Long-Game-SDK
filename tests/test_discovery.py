from __future__ import annotations

from long_game_sdk.sdk import discovery
from long_game_sdk.sdk.discovery import InstrumentIdentity


def _raw_u3(resource: str = "USB::0cd5::0003::bus3-addr10") -> InstrumentIdentity:
    return InstrumentIdentity(
        transport="usb",
        resource=resource,
        manufacturer="LabJack",
        model="LabJack U3",
        serial="UNKNOWN",
        idn="LabJack,LabJack U3,UNKNOWN",
        vendor_id="0cd5",
        product_id="0003",
    )


def test_single_labjack_u3_is_enriched_with_vendor_identity_and_stable_resource(monkeypatch) -> None:
    events: list[object] = []

    class Driver:
        def __init__(self) -> None:
            events.append("open")

        def read_identity(self) -> tuple[str, str, str]:
            events.append("identity")
            return "LabJack", "U3-HV", "320104933"

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(discovery, "LabJackU3Driver", Driver)

    result = discovery._enrich_single_labjack_u3([_raw_u3()])

    assert events == ["open", "identity", "close"]
    assert result == [
        InstrumentIdentity(
            transport="usb",
            resource="USB::0cd5::0003::serial320104933",
            manufacturer="LabJack",
            model="U3-HV",
            serial="320104933",
            idn="LabJack,U3-HV,320104933",
            vendor_id="0cd5",
            product_id="0003",
        )
    ]


def test_multiple_labjack_u3_devices_remain_unenriched_when_assignment_is_ambiguous(monkeypatch) -> None:
    raw = [_raw_u3("USB::u3-a"), _raw_u3("USB::u3-b")]
    monkeypatch.setattr(
        discovery,
        "LabJackU3Driver",
        lambda: (_ for _ in ()).throw(AssertionError("must not open an ambiguous device")),
    )

    assert discovery._enrich_single_labjack_u3(raw) == raw


def test_labjack_enrichment_failure_preserves_read_only_usb_identity(monkeypatch) -> None:
    raw = [_raw_u3()]

    class Driver:
        def __init__(self) -> None:
            self.closed = False

        def read_identity(self) -> tuple[str, str, str]:
            raise OSError("device unavailable")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(discovery, "LabJackU3Driver", Driver)

    assert discovery._enrich_single_labjack_u3(raw) == raw
