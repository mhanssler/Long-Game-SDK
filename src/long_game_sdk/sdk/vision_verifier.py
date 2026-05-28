from abc import ABC, abstractmethod
from typing import Dict, Any

class VisionVerifier(ABC):
    @abstractmethod
    def analyze(self, image_path: str) -> Dict[str, Any]:
        """
        Analyze an image from the oscilloscope.
        Returns a dict like:
        {
            "is_valid": bool,
            "error": str | None, # e.g., 'clipped', 'noise', 'no_signal'
            "suggestion": Dict[str, Any] | None # e.g., {'vertical_scale': 'increase'}
        }
        """
        pass

class ScopeVisionVerifier(VisionVerifier):
    def analyze(self, image_path: str) -> Dict[str, Any]:
        # Implementation placeholder
        return {"is_valid": True, "error": None, "suggestion": None}
