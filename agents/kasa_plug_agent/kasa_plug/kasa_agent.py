"""
Kasa Smart Plug Agent for VOLTTRON
Interfaces with Kasa KP125M smart plugs using Matter protocol
"""

import logging
import asyncio
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import socket
import struct
import time

from volttron import utils
from volttron.client.messaging import topics, headers as headers_mod
from volttron.utils import (setup_logging, format_timestamp, 
                                          get_aware_utc_now, parse_timestamp_string)
from volttron.client import Agent, Core, RPC
from volttron.utils.jsonrpc import RemoteError

# Import Kasa library for TP-Link devices
try:
    from kasa import SmartPlug, Discover
    from kasa.exceptions import SmartDeviceException
    KASA_AVAILABLE = True
except ImportError:
    KASA_AVAILABLE = False
    _log.warning("Kasa library not available. Install with: pip install python-kasa")

setup_logging()
_log = logging.getLogger(__name__)


class KasaAgent(Agent):
    """
    Kasa Smart Plug Agent
    Handles communication with Kasa KP125M smart plugs
    """

    def __init__(self, config_path, **kwargs):
        super(KasaAgent, self).__init__(**kwargs)
        
        self.default_config = {
            "devices": [],  # List of device IPs or auto-discovery
            "auto_discover": True,
            "poll_interval": 30,  # seconds
            "network_scan_range": "192.168.1.0/24",
            "device_timeout": 10,
            "retry_attempts": 3,
            "energy_monitoring": True,
            "schedule_enabled": True
        }
        
        # Initialize variables
        self.smart_plugs = {}
        self.device_states = {}
        self.last_update = {}
        self.discovery_running = False
        
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
        
        self.auto_discover = config.get("auto_discover", True)
        self.poll_interval = config.get("poll_interval", 30)
        self.device_timeout = config.get("device_timeout", 10)
        self.retry_attempts = config.get("retry_attempts", 3)
        self.energy_monitoring = config.get("energy_monitoring", True)
        
        # Configure specific devices if provided
        if config.get("devices"):
            self.configure_devices(config["devices"])
        
        _log.info(f"Kasa Agent configured")

    def configure_devices(self, device_configs):
        """Configure specific devices from config"""
        for device_config in device_configs:
            ip_address = device_config.get("ip")
            device_name = device_config.get("name", ip_address)
            
            if ip_address:
                asyncio.run(self.add_device(ip_address, device_name))

    @Core.receiver("onstart")
    def startup(self, sender, **kwargs):
        """Agent startup"""
        _log.info("Kasa Agent starting...")
        
        if not KASA_AVAILABLE:
            _log.error("Kasa library not available. Cannot start agent.")
            return
        
        # Subscribe to command topic
        self.vip.pubsub.subscribe(
            peer="pubsub",
            prefix="devices/kasa",
            callback=self.handle_commands
        )
        
        # Start device discovery if enabled
        if self.auto_discover:
            self.core.spawn(self.discover_devices)
        
        # Start periodic polling
        self.core.periodic(self.poll_interval)(self.poll_devices)
        
        _log.info("Kasa Agent started successfully")

    async def discover_devices(self):
        """Discover Kasa devices on the network"""
        if self.discovery_running:
            return
        
        self.discovery_running = True
        _log.info("Starting Kasa device discovery...")
        
        try:
            # Discover devices using Kasa library
            devices = await Discover.discover(timeout=self.device_timeout)
            
            for ip, device in devices.items():
                await device.update()
                
                if device.is_plug:
                    device_id = self.get_device_id(device)
                    device_name = device.alias or f"Kasa_Plug_{ip.replace('.', '_')}"
                    
                    self.smart_plugs[device_id] = {
                        "device": device,
                        "ip": ip,
                        "name": device_name,
                        "model": device.model,
                        "last_seen": datetime.now()
                    }
                    
                    _log.info(f"Discovered Kasa plug: {device_name} at {ip}")
            
            _log.info(f"Discovery complete. Found {len(self.smart_plugs)} Kasa devices")
            
        except Exception as e:
            _log.error(f"Error during device discovery: {e}")
        
        finally:
            self.discovery_running = False

    async def add_device(self, ip_address, device_name=None):
        """Add a specific device by IP address"""
        try:
            device = SmartPlug(ip_address)
            await device.update()
            
            if device.is_plug:
                device_id = self.get_device_id(device)
                name = device_name or device.alias or f"Kasa_Plug_{ip_address.replace('.', '_')}"
                
                self.smart_plugs[device_id] = {
                    "device": device,
                    "ip": ip_address,
                    "name": name,
                    "model": device.model,
                    "last_seen": datetime.now()
                }
                
                _log.info(f"Added Kasa plug: {name} at {ip_address}")
                return True
            else:
                _log.warning(f"Device at {ip_address} is not a smart plug")
                return False
                
        except Exception as e:
            _log.error(f"Error adding device at {ip_address}: {e}")
            return False

    def get_device_id(self, device):
        """Generate consistent device ID"""
        return device.mac.replace(":", "").lower() if device.mac else device.host.replace(".", "_")

    def poll_devices(self):
        """Poll all devices for current status"""
        if not self.smart_plugs:
            # Trigger discovery if no devices found
            if self.auto_discover and not self.discovery_running:
                self.core.spawn(self.discover_devices)
            return
        
        for device_id, device_info in self.smart_plugs.items():
            self.core.spawn(self.poll_single_device, device_id, device_info)

    async def poll_single_device(self, device_id, device_info):
        """Poll a single device for status"""
        try:
            device = device_info["device"]
            await device.update()
            
            # Basic device information
            device_data = {
                "device_id": device_id,
                "name": device_info["name"],
                "ip": device_info["ip"],
                "model": device_info["model"],
                "mac": device.mac,
                "alias": device.alias,
                "state": device.is_on,
                "rssi": getattr(device, 'rssi', None),
                "timestamp": format_timestamp(get_aware_utc_now())
            }
            
            # Energy monitoring data (if available)
            if self.energy_monitoring and hasattr(device, 'current_consumption'):
                try:
                    energy_data = await self.get_energy_data(device)
                    device_data.update(energy_data)
                except Exception as e:
                    _log.debug(f"Energy monitoring not available for {device_id}: {e}")
            
            # Hardware information
            device_data["hardware_info"] = {
                "hardware_version": getattr(device, 'hw_version', ''),
                "software_version": getattr(device, 'sw_version', ''),
                "type": getattr(device, 'device_type', ''),
                "features": list(device.features) if hasattr(device, 'features') else []
            }
            
            # Store device state
            self.device_states[device_id] = device_data
            device_info["last_seen"] = datetime.now()
            
            # Publish device data
            self.publish_device_data(device_id, device_data)
            
        except SmartDeviceException as e:
            _log.warning(f"Smart device error for {device_id}: {e}")
            # Mark device as offline
            self.mark_device_offline(device_id)
        except Exception as e:
            _log.error(f"Error polling device {device_id}: {e}")

    async def get_energy_data(self, device):
        """Get energy monitoring data from device"""
        energy_data = {}
        
        try:
            # Current power consumption
            if hasattr(device, 'current_consumption'):
                energy_data["power"] = await device.current_consumption()
            
            # Voltage and current (if available)
            if hasattr(device, 'voltage'):
                energy_data["voltage"] = await device.voltage()
            
            if hasattr(device, 'current'):
                energy_data["current"] = await device.current()
            
            # Energy usage statistics (if available)
            if hasattr(device, 'get_emeter_realtime'):
                emeter_data = await device.get_emeter_realtime()
                energy_data.update({
                    "voltage_mv": emeter_data.get("voltage_mv", 0) / 1000.0,
                    "current_ma": emeter_data.get("current_ma", 0) / 1000.0,
                    "power_mw": emeter_data.get("power_mw", 0) / 1000.0,
                    "total_wh": emeter_data.get("total_wh", 0)
                })
            
            # Daily and monthly statistics
            if hasattr(device, 'get_emeter_daily'):
                daily_stats = await device.get_emeter_daily(year=datetime.now().year, month=datetime.now().month)
                energy_data["daily_usage"] = daily_stats
            
        except Exception as e:
            _log.debug(f"Error getting energy data: {e}")
        
        return energy_data

    def mark_device_offline(self, device_id):
        """Mark a device as offline"""
        if device_id in self.device_states:
            self.device_states[device_id]["state"] = "offline"
            self.device_states[device_id]["timestamp"] = format_timestamp(get_aware_utc_now())
            
            # Publish offline status
            self.publish_device_data(device_id, self.device_states[device_id])

    def publish_device_data(self, device_id, data):
        """Publish device data to message bus"""
        try:
            topic = f"devices/kasa/{device_id}"
            headers = {
                headers_mod.TIMESTAMP: format_timestamp(get_aware_utc_now()),
                "device_type": "kasa_smart_plug"
            }
            
            # Add device info for external systems
            device_info = {
                "units": {
                    "power": "watts",
                    "voltage": "volts",
                    "current": "amps",
                    "energy": "watt_hours"
                },
                "device_class": "smart_plug",
                "manufacturer": "TP-Link Kasa"
            }
            
            self.vip.pubsub.publish(
                "pubsub",
                topic,
                headers=headers,
                message=[data, device_info]
            )
            
            _log.debug(f"Published data for device {device_id}")
            
        except Exception as e:
            _log.error(f"Error publishing device data: {e}")

    def handle_commands(self, peer, sender, bus, topic, headers, message):
        """Handle commands sent to smart plugs"""
        try:
            # Extract device ID from topic
            topic_parts = topic.split('/')
            if len(topic_parts) >= 3 and topic_parts[-1] == "command":
                device_id = topic_parts[-2]
                
                if isinstance(message, list) and len(message) > 0:
                    command_data = message[0]
                else:
                    command_data = message
                
                command = command_data.get("command")
                value = command_data.get("value")
                
                _log.info(f"Received command for {device_id}: {command} = {value}")
                
                # Execute command asynchronously
                self.core.spawn(self.execute_command, device_id, command, value)
                
        except Exception as e:
            _log.error(f"Error handling command: {e}")

    async def execute_command(self, device_id, command, value=None):
        """Execute a command on a specific smart plug"""
        try:
            if device_id not in self.smart_plugs:
                _log.error(f"Device {device_id} not found")
                self.publish_command_result(device_id, command, value, False, "Device not found")
                return
            
            device = self.smart_plugs[device_id]["device"]
            success = False
            error_msg = None
            
            try:
                if command == "turn_on":
                    await device.turn_on()
                    success = True
                elif command == "turn_off":
                    await device.turn_off()
                    success = True
                elif command == "toggle":
                    if device.is_on:
                        await device.turn_off()
                    else:
                        await device.turn_on()
                    success = True
                elif command == "set_alias":
                    await device.set_alias(str(value))
                    self.smart_plugs[device_id]["name"] = str(value)
                    success = True
                elif command == "set_led":
                    if hasattr(device, 'set_led'):
                        await device.set_led(bool(value))
                        success = True
                    else:
                        error_msg = "LED control not supported"
                elif command == "reboot":
                    if hasattr(device, 'reboot'):
                        await device.reboot()
                        success = True
                    else:
                        error_msg = "Reboot not supported"
                else:
                    error_msg = f"Unknown command: {command}"
                
                if success:
                    # Update device state immediately
                    await device.update()
                    _log.info(f"Successfully executed {command} on {device_id}")
                
            except SmartDeviceException as e:
                error_msg = f"Device error: {str(e)}"
                _log.error(f"Device error executing {command} on {device_id}: {e}")
            
            # Publish command result
            self.publish_command_result(device_id, command, value, success, error_msg)
            
        except Exception as e:
            _log.error(f"Error executing command {command} on {device_id}: {e}")
            self.publish_command_result(device_id, command, value, False, str(e))

    def publish_command_result(self, device_id, command, value, success, error_msg=None):
        """Publish command execution result"""
        try:
            result_topic = f"devices/kasa/{device_id}/command_result"
            result_message = {
                "command": command,
                "value": value,
                "success": success,
                "error": error_msg,
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
    def get_device_status(self, device_id=None):
        """RPC method to get device status"""
        if device_id:
            return self.device_states.get(device_id, {})
        else:
            return self.device_states

    @RPC.export
    def control_device(self, device_id, command, value=None):
        """RPC method to control device"""
        self.core.spawn(self.execute_command, device_id, command, value)
        return True

    @RPC.export
    def discover_new_devices(self):
        """RPC method to trigger device discovery"""
        if not self.discovery_running:
            self.core.spawn(self.discover_devices)
            return True
        return False

    @RPC.export
    def get_energy_usage(self, device_id, period="day"):
        """RPC method to get energy usage statistics"""
        try:
            if device_id not in self.smart_plugs:
                return None
            
            device = self.smart_plugs[device_id]["device"]
            
            # This would need to be implemented based on specific energy data needs
            # For now, return current consumption data
            current_data = self.device_states.get(device_id, {})
            return {
                "current_power": current_data.get("power", 0),
                "voltage": current_data.get("voltage", 0),
                "current": current_data.get("current", 0),
                "daily_usage": current_data.get("daily_usage", {}),
                "period": period
            }
            
        except Exception as e:
            _log.error(f"Error getting energy usage for {device_id}: {e}")
            return None

    @RPC.export
    def set_schedule(self, device_id, schedule_data):
        """RPC method to set device schedule"""
        # This would implement scheduling functionality
        # For now, return success status
        _log.info(f"Schedule set for {device_id}: {schedule_data}")
        return True

    @Core.receiver("onstop")
    def shutdown(self, sender, **kwargs):
        """Clean shutdown"""
        _log.info("Kasa Agent shutting down...")
        
        # Clean up any running tasks
        self.discovery_running = False


def main():
    """Main method called by VOLTTRON"""
    try:
        utils.vip_main(KasaAgent, version="1.0")
    except Exception as e:
        _log.exception("Unhandled exception in main")
        _log.error(repr(e))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass