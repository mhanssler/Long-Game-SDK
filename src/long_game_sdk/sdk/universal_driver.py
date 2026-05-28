import yaml
import pyvisa
from typing import Any, Dict

class UniversalDriver:
    """Dynamically generated driver based on YAML capability schemas."""
    
    def __init__(self, resource_name: str, schema_path: str):
        self.resource_name = resource_name
        self.rm = pyvisa.ResourceManager()
        self.instrument = self.rm.open_resource(resource_name)
        
        with open(schema_path, 'r') as f:
            self.schema = yaml.safe_load(f)
            
        self._setup_capabilities()

    def _setup_capabilities(self):
        """Dynamically create methods based on schema capabilities."""
        for cap_name, cap_data in self.schema.get('capabilities', {}).items():
            for cmd_name, cmd_template in cap_data.get('commands', {}).items():
                # Define a method dynamically
                setattr(self, cmd_name, self._create_cmd_method(cmd_template))

    def _create_cmd_method(self, template: str):
        def method(*args, **kwargs):
            # Basic SCPI string formatting
            command = template.format(*args, **kwargs)
            # Check if it's a query (ends with '?') or write
            if '?' in command:
                return self.instrument.query(command)
            else:
                return self.instrument.write(command)
        return method

    def close(self):
        self.instrument.close()

# Usage Example:
# driver = UniversalDriver("USB0::...", "schemas/rigol_dp832.yaml")
# driver.set_voltage(channel=1, value=5.0)
