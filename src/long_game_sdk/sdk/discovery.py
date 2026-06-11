"""Hardware discovery for Long Game SDK.

Discovery is intentionally broad:
- VISA/SCPI devices are queried with *IDN? and become UniversalDriver candidates.
- Raw USB devices are inventoried so vendor-specific drivers such as LabJack can
  be matched even when they are not VISA resources.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, cast

import pyvisa

try:  # PyUSB is required for raw USB inventory.
    import usb.core
    import usb.util
except Exception:  # pragma: no cover - optional runtime dependency guard
    usb = None  # type: ignore[assignment]


@dataclass(frozen=True)
class InstrumentIdentity:
    transport: str
    resource: str
    manufacturer: str = "UNKNOWN"
    model: str = "UNKNOWN"
    serial: str = "UNKNOWN"
    firmware: str = "UNKNOWN"
    idn: str = "UNKNOWN"
    vendor_id: str | None = None
    product_id: str | None = None
    driver: str = "unknown"
    schema: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(text: str | None) -> str:
    return (text or "UNKNOWN").strip().replace("\x00", "") or "UNKNOWN"


def _parse_idn(idn: str) -> tuple[str, str, str, str]:
    parts = [_clean(part) for part in idn.split(",")]
    while len(parts) < 4:
        parts.append("UNKNOWN")
    return parts[0], parts[1], parts[2], parts[3]


def discover_visa() -> list[InstrumentIdentity]:
    """Discover VISA instruments and identify them with *IDN?."""

    rm = pyvisa.ResourceManager("@py")
    identities: list[InstrumentIdentity] = []
    for resource in rm.list_resources():
        instrument = None
        try:
            instrument = cast(Any, rm.open_resource(resource))
            instrument.timeout = 3000
            idn = _clean(instrument.query("*IDN?"))
            manufacturer, model, serial, firmware = _parse_idn(idn)
            identities.append(
                InstrumentIdentity(
                    transport="visa",
                    resource=resource,
                    manufacturer=manufacturer,
                    model=model,
                    serial=serial,
                    firmware=firmware,
                    idn=idn,
                )
            )
        except Exception as exc:  # noqa: BLE001 - discovery should continue
            identities.append(
                InstrumentIdentity(
                    transport="visa",
                    resource=resource,
                    idn=f"IDENTIFICATION_FAILED: {exc}",
                )
            )
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass
    return identities


def discover_usb() -> list[InstrumentIdentity]:
    """Discover raw USB devices for non-VISA equipment such as LabJack U3."""

    if usb is None:  # type: ignore[name-defined]
        return []

    identities: list[InstrumentIdentity] = []
    try:
        devices = usb.core.find(find_all=True)  # type: ignore[name-defined]
    except Exception:
        return []

    for device in devices:
        vendor_id = f"{device.idVendor:04x}"
        product_id = f"{device.idProduct:04x}"
        resource = f"USB::{vendor_id}::{product_id}::bus{device.bus}-addr{device.address}"
        manufacturer = "UNKNOWN"
        model = "UNKNOWN"
        serial = "UNKNOWN"
        try:
            manufacturer = _clean(usb.util.get_string(device, device.iManufacturer)) if device.iManufacturer else "UNKNOWN"  # type: ignore[name-defined]
            model = _clean(usb.util.get_string(device, device.iProduct)) if device.iProduct else "UNKNOWN"  # type: ignore[name-defined]
            serial = _clean(usb.util.get_string(device, device.iSerialNumber)) if device.iSerialNumber else "UNKNOWN"  # type: ignore[name-defined]
        except Exception:
            pass
        identities.append(
            InstrumentIdentity(
                transport="usb",
                resource=resource,
                manufacturer=manufacturer,
                model=model,
                serial=serial,
                idn=f"{manufacturer},{model},{serial}",
                vendor_id=vendor_id,
                product_id=product_id,
            )
        )
    return identities


def discover_all() -> list[InstrumentIdentity]:
    """Discover VISA instruments plus non-VISA USB instruments."""

    visa_devices = discover_visa()
    visa_usb_pairs = {
        (identity.vendor_id, identity.product_id)
        for identity in visa_devices
        if identity.vendor_id and identity.product_id
    }
    # PyVISA resource strings do not expose vendor/product as dataclass fields yet,
    # so de-duplicate raw USB primarily by known VISA USBTMC pairs below.
    raw_usb = []
    for identity in discover_usb():
        # Rigol USBTMC devices are already represented through PyVISA resources;
        # keep raw USB entries for non-VISA devices such as LabJack. Ignore root
        # hubs and commodity USB hubs because they are not controllable test gear.
        if identity.vendor_id and identity.vendor_id.lower() in {"1ab1", "1d6b", "0bda"}:
            continue
        pair = (identity.vendor_id, identity.product_id)
        if pair in visa_usb_pairs:
            continue
        raw_usb.append(identity)
    return visa_devices + raw_usb


def print_inventory() -> None:
    print("--- Long Game SDK Discovery ---")
    for identity in discover_all():
        print(
            f"{identity.transport:4} | {identity.manufacturer} | {identity.model} | "
            f"serial={identity.serial} | resource={identity.resource}"
        )


if __name__ == "__main__":
    print_inventory()
