# lmp_visualizer/settings_manager.py

import json
from typing import Dict, Any

class SettingsManager:
    """Handles saving and loading of the application state to JSON."""

    @staticmethod
    def save_state(filepath: str, configuration: Dict[str, Any]):
        """Saves the given configuration dictionary to a JSON file."""
        try:
            with open(filepath, 'w') as f:
                json.dump(configuration, f, indent=4)
            return True
        except Exception as e:
            print(f"Error saving state: {e}")
            return False

    @staticmethod
    def load_state(filepath: str) -> Dict[str, Any]:
        """Loads a configuration dictionary from a JSON file."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading state: {e}")
            return {}