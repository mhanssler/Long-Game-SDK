import subprocess
import time
import logging
import pyvisa
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AutoOnboarder:
    def __init__(self, poll_interval=60):
        self.rm = pyvisa.ResourceManager()
        self.poll_interval = poll_interval
        self.known_instruments = set()
        self.schemas_dir = Path("/Users/morgan/business/long-game-sdk/schemas")

    def scan(self):
        try:
            current_instruments = set(self.rm.list_resources())
            new_instruments = current_instruments - self.known_instruments
            
            for instrument_id in new_instruments:
                logger.info(f"New hardware detected: {instrument_id}")
                self.onboard(instrument_id)
                self.known_instruments.add(instrument_id)
                
        except Exception as e:
            logger.error(f"Scan error: {e}")

    def onboard(self, instrument_id):
        try:
            # 1. Get Device Identity
            instr = self.rm.open_resource(instrument_id)
            idn = instr.query("*IDN?").strip()
            instr.close()
            logger.info(f"Device identified: {idn}")
            
            # 2. Trigger Scraper
            # We assume the model is in the IDN string
            # In production, we'd use a more robust regex to extract the model
            model = idn.split(',')[1]
            logger.info(f"Searching for datasheet for: {model}")
            
            # This calls the scraper script we previously created
            # We'll pass the model name as an argument
            subprocess.run(["uv", "run", "/Users/morgan/datasheet_scraper.py", "--model", model], check=True)
            
        except Exception as e:
            logger.error(f"Onboarding failed for {instrument_id}: {e}")

    def run(self):
        logger.info("Auto-Onboarder service started.")
        while True:
            self.scan()
            time.sleep(self.poll_interval)

if __name__ == "__main__":
    onboarder = AutoOnboarder()
    onboarder.run()
