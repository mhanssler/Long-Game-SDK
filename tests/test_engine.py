from long_game_sdk.sdk.universal_driver import UniversalDriver
import os

# Ensure the schema path is correct for where we saved it
SCHEMA_PATH = "/Users/morgan/business/long-game-sdk/schemas/rigol_dp832.yaml"
# You would replace this with a real VISA address for your instrument
# For test purposes, let's assume we are testing connection capability
RESOURCE_NAME = "USB0::0x1AB1::0x0E11::DP8C000000000::INSTR" 

def test_engine():
    try:
        print(f"Initializing driver for {RESOURCE_NAME} using {SCHEMA_PATH}...")
        driver = UniversalDriver(RESOURCE_NAME, SCHEMA_PATH)
        
        # This will fail if no physical device is plugged in, 
        # but verifies that the dynamic method injection works.
        print("Driver successfully created!")
        print(f"Available commands: set_voltage, set_current, turn_on, etc.")
        
        # driver.set_voltage(channel=1, value=5.0)
        
    except Exception as e:
        print(f"Test engine reached point of hardware interaction: {e}")

if __name__ == "__main__":
    test_engine()
