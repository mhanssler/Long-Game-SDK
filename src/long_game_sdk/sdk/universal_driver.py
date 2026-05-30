import yaml
import pyvisa
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

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
                setattr(self, cmd_name, self._create_cmd_method(cmd_template))

    def _create_cmd_method(self, template: str):
        def method(*args, **kwargs):
            command = template.format(*args, **kwargs)
            try:
                if '?' in command:
                    return self.instrument.query(command)
                else:
                    return self.instrument.write(command)
            except Exception as e:
                return self._handle_instrument_error(e, command)
        return method

    def _handle_instrument_error(self, e: Exception, command: str) -> str:
        """Intelligent error resolution."""
        # 1. Attempt to query system error if supported
        try:
            err = self.instrument.query(":SYSTem:ERRor?")
            logger.error(f"Hardware Error: {err} | Command: {command}")
        except:
            err = str(e)
            
        # 2. Search local troubleshooting DB (YAML)
        troubleshooting = self.schema.get('troubleshooting', {})
        if err in troubleshooting:
            return f"Known Issue: {troubleshooting[err]}"
            
        # 3. LLM-based online resolution
        return self._search_error_online(err, command)

    def _search_error_online(self, error_msg: str, command: str) -> str:
        """Uses Brave Search MCP tool to resolve hardware errors."""
        query = f"Rigol instrument error {error_msg} for command {command} fix"
        logger.info(f"Searching web for: {query}")
        
        # This calls the MCP search tool we just registered
        # The agent's framework will inject the tool available in the environment
        try:
            # We assume the tool is registered as mcp_search_brave_search
            # If the tool isn't available yet, this will fail gracefully
            if hasattr(self, 'agent'):
                result = self.agent.call_tool("mcp_search_brave_search", query=query)
                return f"Found online solution: {result}"
        except Exception as e:
            return f"Search failed: {e}. Please check your Brave API Key."
        
        return "Search tool not configured. Please check mcp configuration."

    def close(self):
        self.instrument.close()
