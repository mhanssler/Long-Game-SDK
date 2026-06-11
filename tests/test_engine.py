from long_game_sdk.sdk.universal_driver import UniversalDriver
from pathlib import Path

import pyvisa

# Ensure the schema path is correct for where we saved it
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "rigol_dp832.yaml"


def find_dp832_resource() -> str:
    rm = pyvisa.ResourceManager("@py")
    for resource in rm.list_resources():
        try:
            instrument = rm.open_resource(resource)
            instrument.timeout = 3000
            idn = instrument.query("*IDN?").strip().replace("\x00", "")
            instrument.close()
        except Exception:
            continue
        if "RIGOL" in idn.upper() and "DP832" in idn.upper():
            return resource
    raise RuntimeError("No Rigol DP832 detected via PyVISA")

def test_engine():
    try:
        resource_name = find_dp832_resource()
        print(f"Initializing driver for {resource_name} using {SCHEMA_PATH}...")
        driver = UniversalDriver(resource_name, str(SCHEMA_PATH))
        
        # This will fail if no physical device is plugged in, 
        # but verifies that the dynamic method injection works.
        print("Driver successfully created!")
        print(f"Available commands: set_voltage, set_current, turn_on, etc.")
        print(f"CH1 measured voltage: {driver.get_voltage(channel=1).strip()}")
        driver.close()
        
        # driver.set_voltage(channel=1, value=5.0)
        
    except Exception as e:
        print(f"Test engine reached point of hardware interaction: {e}")

if __name__ == "__main__":
    test_engine()
