from __future__ import annotations

from long_game_sdk.sdk.discovery import InstrumentIdentity
from long_game_sdk.sdk.observers.auto_onboarder import AutoOnboarder


class FakeInstrument:
    def __init__(self, idn: str):
        self.idn = idn
        self.closed = False

    def query(self, command: str) -> str:
        assert command == "*IDN?"
        return self.idn

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self, resources: tuple[str, ...], idn: str):
        self.resources = resources
        self.instrument = FakeInstrument(idn)

    def list_resources(self) -> tuple[str, ...]:
        return self.resources

    def open_resource(self, resource: str) -> FakeInstrument:
        assert resource in self.resources
        return self.instrument


def test_auto_onboarder_uses_in_process_manual_enrichment(monkeypatch):
    calls: list[InstrumentIdentity] = []

    def fake_enrich_identity(identity: InstrumentIdentity):
        calls.append(identity)
        return object()

    monkeypatch.setattr("long_game_sdk.sdk.observers.auto_onboarder.enrich_identity", fake_enrich_identity)
    rm = FakeResourceManager(
        ("TCPIP::192.168.0.54::INSTR",),
        "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA252502163,00.04.05.SP2",
    )
    onboarder = AutoOnboarder(resource_manager=rm, poll_interval=0)

    onboarder.scan()

    assert calls == [
        InstrumentIdentity(
            transport="visa",
            resource="TCPIP::192.168.0.54::INSTR",
            manufacturer="RIGOL TECHNOLOGIES",
            model="DS1054Z",
            serial="DS1ZA252502163",
            firmware="00.04.05.SP2",
            idn="RIGOL TECHNOLOGIES,DS1054Z,DS1ZA252502163,00.04.05.SP2",
        )
    ]
    assert "TCPIP::192.168.0.54::INSTR" in onboarder.known_instruments


def test_auto_onboarder_continues_when_manual_enrichment_fails(monkeypatch):
    def fake_enrich_identity(identity: InstrumentIdentity):
        raise RuntimeError("search offline")

    monkeypatch.setattr("long_game_sdk.sdk.observers.auto_onboarder.enrich_identity", fake_enrich_identity)
    rm = FakeResourceManager(
        ("TCPIP::192.168.0.2::INSTR",),
        "Keithley Instruments Inc.,Model 2611B,MYFP001305,4.0.8",
    )
    onboarder = AutoOnboarder(resource_manager=rm, poll_interval=0)

    onboarder.scan()

    assert "TCPIP::192.168.0.2::INSTR" in onboarder.known_instruments
