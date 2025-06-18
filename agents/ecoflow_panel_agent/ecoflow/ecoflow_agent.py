"""
EcoFlow Battery Agent for VOLTTRON
Interfaces with EcoFlow smart home panels and batteries via API
"""

import logging
import requests
import json
import sys
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from volttron import utils
from volttron.client.messaging import topics, headers as headers_mod
from volttron.utils import (setup_logging, format_timestamp, 
                                          get_aware_utc_now, parse_timestamp_string)
from volttron.client import Agent, Core, RPC
from volttron.utils import (
    setup_logging, format_timestamp, get_aware_utc_now
)
from volttron.utils.jsonrpc import RemoteError

setup_logging()
_log = logging.getLogger(__name__)


class EcoFlowAgent(Agent):
    """
    EcoFlow Battery Agent
    Handles communication with EcoFlow smart home panels and batteries
    """

    def __init__(self, config_path, **kwargs):
        super(EcoFlowAgent, self).__init__(**kwargs)
        
        self.default_config = {
            "api_base_url": "https://api.ecoflow.com",
            "access_key": "your_access_key",
            "secret_key": "your_secret_key",
            "devices": [],  # List of device serial numbers
            "poll_interval": 30,  # seconds
            "auto_discover": True,
            "battery_management": {
                "min_soc": 20,
                "max_soc": 95,
                "target_soc": 80,
                "discharge_limit": 80
            },
            "grid_management": {
                "enable_grid_tie": True,
                "max_feed_in": 3000,  # watts
                "grid_frequency": 60  # Hz
            }
        }
        
        # Initialize variables
        self.access_key = ""
        self.secret_key = ""
        self.devices = {}
        self.device_states = {}
        self.last_update = {}
        
        # Configuration setup
        self.vip.config.set_default("config", self.default_config)
        self.vip.config.subscribe(
            self.configure_main,
            actions=["NEW", "UPDATE"],
            pattern="config"
        )

    def configure_main(self, config_name, action, contents):
        """Handle configuration updates"""
        config = self.default_config.copy()
        config.update(contents)
        
        self.api_base_url = config.get("api_base_url")
        self.access_key = config.get("access_key")
        self.secret_key = config.get("secret_key")
        self.poll_interval = config.get("poll_interval", 30)
        self.auto_discover = config.get("auto_discover", True)
        self.battery_config = config.get("battery_management", {})
        self.grid_config = config.get("grid_management", {})
        
        # Configure specific devices if provided
        device_list = config.get("devices", [])
        for device_sn in device_list:
            self.devices[device_sn] = {"serial_number": device_sn, "type": "unknown"}
        
        _log.info(f"EcoFlow Agent configured with {len(self.devices)} devices")

    @Core.receiver("onstart")
    def startup(self, sender, **kwargs):
        """Agent startup"""
        _log.info("EcoFlow Agent starting...")
        
        # Validate API credentials
        if not self.access_key or not self.secret_key:
            _log.error("API credentials not provided. Cannot start agent.")
            return
        
        # Subscribe to command topic
        self.vip.pubsub.subscribe(
            peer="pubsub",
            prefix="devices/ecoflow",
            callback=self.handle_commands
        )
        
        # Discover devices if enabled
        if self.auto_discover:
            self.discover_devices()
        
        # Start periodic polling
        self.core.periodic(self.poll_interval)(self.poll_devices)
        
        _log.info("EcoFlow Agent started successfully")

    def generate_signature(self, method, url, params=None, data=None):
        """Generate API signature for authentication"""
        try:
            timestamp = str(int(time.time() * 1000))
            nonce = str(int(time.time()))
            
            # Create canonical string
            canonical_string = f"{method}\n{url}\n"
            
            if params:
                sorted_params = sorted(params.items())
                param_string = "&".join([f"{k}={v}" for k, v in sorted_params])
                canonical_string += param_string + "\n"
            else:
                canonical_string += "\n"
            
            if data:
                canonical_string += json.dumps(data, separators=(',', ':'))
            
            canonical_string += f"\n{self.access_key}\n{timestamp}\n{nonce}"
            
            # Generate signature
            signature = hmac.new(
                self.secret_key.encode('utf-8'),
                canonical_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return {
                "timestamp": timestamp,
                "nonce": nonce,
                "signature": signature
            }
            
        except Exception as e:
            _log.error(f"Error generating signature: {e}")
            return None

    def make_api_request(self, method, endpoint, params=None, data=None):
        """Make authenticated API request to EcoFlow"""
        try:
            url = f"{self.api_base_url}{endpoint}"
            auth_data = self.generate_signature(method, endpoint, params, data)
            
            if not auth_data:
                return None
            
            headers = {
                "Content-Type": "application/json",
                "X-Access-Key": self.access_key,
                "X-Timestamp": auth_data["timestamp"],
                "X-Nonce": auth_data["nonce"],
                "X-Signature": auth_data["signature"]
            }
            
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=30)
            elif method.upper() == "PUT":
                response = requests.put(url, headers=headers, json=data, timeout=30)
            else:
                _log.error(f"Unsupported HTTP method: {method}")
                return None
            
            if response.status_code == 200:
                return response.json()
            else:
                _log.error(f"API request failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            _log.error(f"Error making API request: {e}")
            return None

    def discover_devices(self):
        """Discover EcoFlow devices associated with account"""
        try:
            _log.info("Discovering EcoFlow devices...")
            
            # Get device list from API
            response = self.make_api_request("GET", "/devices")
            
            if response and response.get("code") == 0:
                device_list = response.get("data", [])
                
                for device in device_list:
                    device_sn = device.get("sn")
                    device_type = device.get("deviceType")
                    device_name = device.get("deviceName", f"EcoFlow_{device_type}")
                    
                    self.devices[device_sn] = {
                        "serial_number": device_sn,
                        "type": device_type,
                        "name": device_name,
                        "online": device.get("online", False),
                        "model": device.get("productType", ""),
                        "firmware_version": device.get("version", "")
                    }
                    
                    _log.info(f"Discovered EcoFlow device: {device_name} ({device_sn})")
                
                _log.info(f"Discovery complete. Found {len(self.devices)} EcoFlow devices")
                
            else:
                _log.error("Failed to discover devices")
                
        except Exception as e:
            _log.error(f"Error discovering devices: {e}")

    def poll_devices(self):
        """Poll all devices for current status"""
        if not self.devices:
            if self.auto_discover:
                self.discover_devices()
            return
        
        for device_sn, device_info in self.devices.items():
            self.poll_single_device(device_sn, device_info)

    def poll_single_device(self, device_sn, device_info):
        """Poll a single device for status"""
        try:
            # Get real-time device data
            device_data = self.get_device_status(device_sn)
            
            if device_data:
                # Process and normalize the data
                normalized_data = self.normalize_device_data(device_sn, device_data, device_info)
                
                # Store device state
                self.device_states[device_sn] = normalized_data
                self.last_update[device_sn] = datetime.now()
                
                # Publish device data
                self.publish_device_data(device_sn, normalized_data)
                
        except Exception as e:
            _log.error(f"Error polling device {device_sn}: {e}")

    def get_device_status(self, device_sn):
        """Get current status for a specific device"""
        try:
            # Get device quotas (real-time data)
            quota_response = self.make_api_request("GET", f"/devices/{device_sn}/quota")
            
            if quota_response and quota_response.get("code") == 0:
                return quota_response.get("data", {})
            else:
                _log.error(f"Failed to get device status for {device_sn}")
                return None
                
        except Exception as e:
            _log.error(f"Error getting device status for {device_sn}: {e}")
            return None

    def normalize_device_data(self, device_sn, raw_data, device_info):
        """Normalize raw device data into standard format"""
        try:
            device_type = device_info.get("type", "unknown")
            
            # Base device information
            normalized = {
                "device_id": device_sn,
                "name": device_info.get("name", f"EcoFlow_{device_sn}"),
                "type": device_type,
                "model": device_info.get("model", ""),
                "firmware_version": device_info.get("firmware_version", ""),
                "online": device_info.get("online", False),
                "timestamp": format_timestamp(get_aware_utc_now())
            }
            
            # Extract battery information
            if "bms" in raw_data:
                bms_data = raw_data["bms"]
                normalized.update({
                    "battery_soc": bms_data.get("soc", 0),
                    "battery_voltage": bms_data.get("vol", 0) / 1000.0,  # Convert mV to V
                    "battery_current": bms_data.get("amp", 0) / 1000.0,  # Convert mA to A
                    "battery_temp": bms_data.get("temp", 0),
                    "battery_cycles": bms_data.get("cycles", 0),
                    "battery_capacity": bms_data.get("designCap", 0),
                    "battery_remain": bms_data.get("remain", 0),
                    "battery_health": bms_data.get("soh", 100)
                })
            
            # Extract inverter information
            if "inv" in raw_data:
                inv_data = raw_data["inv"]
                normalized.update({
                    "inverter_input_watts": inv_data.get("inputWatts", 0),
                    "inverter_output_watts": inv_data.get("outputWatts", 0),
                    "inverter_temp": inv_data.get("temp", 0),
                    "ac_output_voltage": inv_data.get("acOutVol", 0) / 1000.0,
                    "ac_output_freq": inv_data.get("acOutFreq", 0) / 100.0
                })
            
            # Extract AC input information (grid)
            if "acIn" in raw_data:
                ac_in_data = raw_data["acIn"]
                normalized.update({
                    "grid_input_watts": ac_in_data.get("watts", 0),
                    "grid_voltage": ac_in_data.get("vol", 0) / 1000.0,
                    "grid_frequency": ac_in_data.get("freq", 0) / 100.0
                })
            
            # Extract solar input information
            if "pv" in raw_data:
                pv_data = raw_data["pv"]
                normalized.update({
                    "solar_input_watts": pv_data.get("watts", 0),
                    "solar_voltage": pv_data.get("vol", 0) / 1000.0,
                    "solar_current": pv_data.get("amp", 0) / 1000.0
                })
            
            # Calculate derived values
            normalized["power_input"] = (
                normalized.get("grid_input_watts", 0) + 
                normalized.get("solar_input_watts", 0)
            )
            normalized["power_output"] = normalized.get("inverter_output_watts", 0)
            normalized["net_power"] = normalized["power_input"] - normalized["power_output"]
            
            # Calculate estimated remaining time
            if normalized.get("battery_remain", 0) > 0 and normalized["power_output"] > 0:
                normalized["estimated_runtime"] = (
                    normalized["battery_remain"] / normalized["power_output"] * 60  # minutes
                )
            else:
                normalized["estimated_runtime"] = 0
            
            return normalized
            
        except Exception as e:
            _log.error(f"Error normalizing device data for {device_sn}: {e}")
            return {}

    def publish_device_data(self, device_sn, data):
        """Publish device data to message bus"""
        try:
            topic = f"devices/ecoflow/{device_sn}"
            headers = {
                headers_mod.TIMESTAMP: format_timestamp(get_aware_utc_now()),
                "device_type": "ecoflow_battery"
            }
            
            # Add metadata for external systems
            metadata = {
                "units": {
                    "battery_soc": "percent",
                    "voltage": "volts",
                    "current": "amps",
                    "power": "watts",
                    "energy": "watt_hours",
                    "temperature": "celsius",
                    "frequency": "hertz",
                    "time": "minutes"
                },
                "device_class": "battery_system",
                "manufacturer": "EcoFlow"
            }
            
            self.vip.pubsub.publish(
                "pubsub",
                topic,
                headers=headers,
                message=[data, metadata]
            )
            
            _log.debug(f"Published data for device {device_sn}")
            
        except Exception as e:
            _log.error(f"Error publishing device data: {e}")

    def handle_commands(self, peer, sender, bus, topic, headers, message):
        """Handle commands sent to EcoFlow devices"""
        try:
            # Extract device ID from topic
            topic_parts = topic.split('/')
            if len(topic_parts) >= 3 and topic_parts[-1] == "command":
                device_sn = topic_parts[-2]
                
                if isinstance(message, list) and len(message) > 0:
                    command_data = message[0]
                else:
                    command_data = message
                
                command = command_data.get("command")
                value = command_data.get("value")
                
                _log.info(f"Received command for {device_sn}: {command} = {value}")
                
                # Execute command
                success = self.execute_command(device_sn, command, value)
                
                # Publish command result
                self.publish_command_result(device_sn, command, value, success)
                
        except Exception as e:
            _log.error(f"Error handling command: {e}")

    def execute_command(self, device_sn, command, value):
        """Execute a command on a specific EcoFlow device"""
        try:
            if device_sn not in self.devices:
                _log.error(f"Device {device_sn} not found")
                return False
            
            # Map commands to API calls
            if command == "enable_discharge":
                return self.set_discharge_enabled(device_sn, bool(value))
            elif command == "set_discharge_limit":
                return self.set_discharge_limit(device_sn, int(value))
            elif command == "start_charging":
                return self.start_charging(device_sn)
            elif command == "stop_charging":
                return self.stop_charging(device_sn)
            elif command == "set_charge_limit":
                return self.set_charge_limit(device_sn, int(value))
            elif command == "enable_grid_tie":
                return self.set_grid_tie_enabled(device_sn, bool(value))
            elif command == "set_output_enabled":
                return self.set_output_enabled(device_sn, bool(value))
            elif command == "reboot":
                return self.reboot_device(device_sn)
            else:
                _log.warning(f"Unknown command: {command}")
                return False
                
        except Exception as e:
            _log.error(f"Error executing command {command}: {e}")
            return False

    def set_discharge_enabled(self, device_sn, enabled):
        """Enable or disable battery discharge"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 81,
                "params": {
                    "enabled": int(enabled)
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"Discharge {'enabled' if enabled else 'disabled'} for {device_sn}")
                return True
            else:
                _log.error(f"Failed to set discharge for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error setting discharge for {device_sn}: {e}")
            return False

    def set_discharge_limit(self, device_sn, limit_percent):
        """Set battery discharge limit"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 82,
                "params": {
                    "minSoc": max(0, min(100, limit_percent))
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"Discharge limit set to {limit_percent}% for {device_sn}")
                return True
            else:
                _log.error(f"Failed to set discharge limit for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error setting discharge limit for {device_sn}: {e}")
            return False

    def set_charge_limit(self, device_sn, limit_percent):
        """Set battery charge limit"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 83,
                "params": {
                    "maxSoc": max(0, min(100, limit_percent))
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"Charge limit set to {limit_percent}% for {device_sn}")
                return True
            else:
                _log.error(f"Failed to set charge limit for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error setting charge limit for {device_sn}: {e}")
            return False

    def start_charging(self, device_sn):
        """Start battery charging"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 84,
                "params": {
                    "chgPause": 0  # 0 = start charging, 1 = pause charging
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"Started charging for {device_sn}")
                return True
            else:
                _log.error(f"Failed to start charging for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error starting charging for {device_sn}: {e}")
            return False

    def stop_charging(self, device_sn):
        """Stop battery charging"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 84,
                "params": {
                    "chgPause": 1  # 0 = start charging, 1 = pause charging
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"Stopped charging for {device_sn}")
                return True
            else:
                _log.error(f"Failed to stop charging for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error stopping charging for {device_sn}: {e}")
            return False

    def set_output_enabled(self, device_sn, enabled):
        """Enable or disable AC output"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 85,
                "params": {
                    "enabled": int(enabled)
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"AC output {'enabled' if enabled else 'disabled'} for {device_sn}")
                return True
            else:
                _log.error(f"Failed to set AC output for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error setting AC output for {device_sn}: {e}")
            return False

    def set_grid_tie_enabled(self, device_sn, enabled):
        """Enable or disable grid tie functionality"""
        try:
            data = {
                "sn": device_sn,
                "cmdSet": 32,
                "cmdId": 86,
                "params": {
                    "enabled": int(enabled)
                }
            }
            
            response = self.make_api_request("POST", "/devices/control", data=data)
            
            if response and response.get("code") == 0:
                _log.info(f"Grid tie {'enabled' if enabled else 'disabled'} for {device_sn}")
                return True
            else:
                _log.error(f"Failed to set grid tie for {device_sn}")
                return False
                
        except Exception as e:
            _log.error(f"Error setting grid tie for {device_sn}: {e}")
            return False

    def publish_command_result(self, device_sn, command, value, success):
        """Publish command execution result"""
        try:
            result_topic = f"devices/ecoflow/{device_sn}/command_result"
            result_message = {
                "command": command,
                "value": value,
                "success": success,
                "timestamp": format_timestamp(get_aware_utc_now())
            }
            
            self.vip.pubsub.publish(
                "pubsub",
                result_topic,
                message=result_message
            )
            
        except Exception as e:
            _log.error(f"Error publishing command result: {e}")

    @RPC.export
    def get_device_status_rpc(self, device_sn=None):
        """RPC method to get device status"""
        if device_sn:
            return self.device_states.get(device_sn, {})
        else:
            return self.device_states

    @RPC.export
    def control_device_rpc(self, device_sn, command, value=None):
        """RPC method to control device"""
        return self.execute_command(device_sn, command, value)

    @RPC.export
    def get_battery_info(self, device_sn):
        """RPC method to get detailed battery information"""
        device_data = self.device_states.get(device_sn, {})
        return {
            "soc": device_data.get("battery_soc", 0),
            "voltage": device_data.get("battery_voltage", 0),
            "current": device_data.get("battery_current", 0),
            "temperature": device_data.get("battery_temp", 0),
            "cycles": device_data.get("battery_cycles", 0),
            "health": device_data.get("battery_health", 100),
            "capacity": device_data.get("battery_capacity", 0),
            "remaining": device_data.get("battery_remain", 0),
            "estimated_runtime": device_data.get("estimated_runtime", 0)
        }


def main():
    """Main method called by VOLTTRON"""
    try:
        utils.vip_main(EcoFlowAgent, version="1.0")
    except Exception as e:
        _log.exception("Unhandled exception in main")
        _log.error(repr(e))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass