"""
Ecobee Smart Thermostat Agent Package
"""
__version__ = "1.0.0"

# Import the main function so it can be found by VOLTTRON
from .ecobee_agent import main

__all__ = ['main']