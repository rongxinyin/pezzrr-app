"""
Smart Home Intelligent Load Control Agent
Coordinates Ecobee AC, Kasa Smart Plugs, and EcoFlow Battery operations
Based on PNNL ILC architecture with smart home adaptations
"""

import logging
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import gevent

from volttron import utils
from volttron.client.messaging import topics, headers as headers_mod
from volttron.utils import (setup_logging, format_timestamp, 
                                          get_aware_utc_now, parse_timestamp_string)
from volttron.client import Agent, Core, RPC
from volttron.utils.jsonrpc import RemoteError

# Smart home device control modules
from transitions import Machine

setup_logging()
_log = logging.getLogger(__name__)


class SmartHomeILCAgent(Agent):
    """
    Intelligent Load Control Agent for Smart Home
    Manages Ecobee thermostats, Kasa smart plugs, and EcoFlow batteries
    """
    
    states = [
        'idle', 'monitoring', 'demand_response', 'emergency_backup',
        'load_shifting', 'peak_shaving', 'battery_charging'
    ]
    
    transitions = [
        {'trigger': 'start_monitoring', 'source': 'idle', 'dest': 'monitoring'},
        {'trigger': 'demand_response_signal', 'source': '*', 'dest': 'demand_response'},
        {'trigger': 'emergency_signal', 'source': '*', 'dest': 'emergency_backup'},
        {'trigger': 'peak_detected', 'source': 'monitoring', 'dest': 'peak_shaving'},
        {'trigger': 'off_peak_time', 'source': 'monitoring', 'dest': 'battery_charging'},
        {'trigger': 'return_to_normal', 'source': '*', 'dest': 'monitoring'},
        {'trigger': 'stop', 'source': '*', 'dest': 'idle'}
    ]

    def __init__(self, config_path, **kwargs):
        super(SmartHomeILCAgent, self).__init__(**kwargs)
        
        # State machine setup
        self.machine = Machine(
            model=self, states=SmartHomeILCAgent.states,
            transitions=SmartHomeILCAgent.transitions, initial='idle'
        )
        
        # Default configuration
        self.default_config = {
            "home_id": "smart_home_001",
            "location": {"lat": 40.7128, "lon": -74.0060},  # NYC example
            "devices": {
                "ecobee": {"thermostats": []},
                "kasa": {"smart_plugs": []},
                "ecoflow": {"batteries": []}
            },
            "demand_targets": {
                "normal": 5000,  # 5kW normal operation
                "peak_shaving": 3000,  # 3kW during peak hours
                "emergency": 1000  # 1kW emergency mode
            },
            "comfort_settings": {
                "temp_tolerance": 2.0,  # ±2°F temperature tolerance
                "priority_loads": ["refrigerator", "medical_devices"],
                "deferrable_loads": ["water_heater", "washer", "dryer"]
            },
            "time_of_use": {
                "peak_hours": {"start": "16:00", "end": "20:00"},
                "off_peak_hours": {"start": "23:00", "end": "06:00"}
            },
            "battery_management": {
                "min_soc": 20,  # Minimum state of charge (%)
                "target_soc": 80,  # Target state of charge (%)
                "emergency_reserve": 10  # Emergency reserve (%)
            }
        }
        
        # Initialize variables
        self.current_power = 0.0
        self.battery_soc = 0.0
        self.outdoor_temp = 70.0
        self.indoor_temp = 72.0
        self.demand_response_active = False
        self.device_states = {}
        self.load_forecast = {}
        self.weather_forecast = {}
        
        # Device control agents
        self.ecobee_agent = None
        self.kasa_agent = None
        self.ecoflow_agent = None
        
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
        
        _log.info(f"Configuration {action}: {config_name}")
        self.setup_device_connections(config)
        self.start_monitoring()

    def setup_device_connections(self, config):
        """Setup connections to device control agents"""
        try:
            # Subscribe to device data topics
            self.vip.pubsub.subscribe(
                peer="pubsub",
                prefix="devices/ecobee",
                callback=self.handle_ecobee_data
            )
            
            self.vip.pubsub.subscribe(
                peer="pubsub",
                prefix="devices/kasa",
                callback=self.handle_kasa_data
            )
            
            self.vip.pubsub.subscribe(
                peer="pubsub",
                prefix="devices/ecoflow",
                callback=self.handle_ecoflow_data
            )
            
            # Subscribe to OpenADR signals
            self.vip.pubsub.subscribe(
                peer="pubsub",
                prefix="openadr/events",
                callback=self.handle_demand_response
            )
            
            _log.info("Device connections established")
            
        except Exception as e:
            _log.error(f"Error setting up device connections: {e}")

    @Core.receiver("onstart")
    def startup(self, sender, **kwargs):
        """Agent startup"""
        _log.info("Smart Home ILC Agent starting...")
        self.start_monitoring()
        
        # Schedule periodic tasks
        self.core.periodic(60)(self.monitor_system)  # Every minute
        self.core.periodic(300)(self.optimize_loads)  # Every 5 minutes
        self.core.periodic(3600)(self.update_forecasts)  # Every hour

    def handle_ecobee_data(self, peer, sender, bus, topic, headers, message):
        """Handle Ecobee thermostat data"""
        try:
            data = message[0]
            device_id = topic.split('/')[-1]
            
            self.device_states[f"ecobee_{device_id}"] = {
                "indoor_temp": data.get("indoor_temp", 72.0),
                "outdoor_temp": data.get("outdoor_temp", 70.0),
                "cooling_setpoint": data.get("cooling_setpoint", 75.0),
                "heating_setpoint": data.get("heating_setpoint", 68.0),
                "hvac_mode": data.get("hvac_mode", "auto"),
                "fan_status": data.get("fan_status", "auto"),
                "power_consumption": data.get("power_consumption", 0.0),
                "timestamp": headers.get(headers_mod.TIMESTAMP)
            }
            
            self.indoor_temp = data.get("indoor_temp", 72.0)
            self.outdoor_temp = data.get("outdoor_temp", 70.0)
            
            _log.debug(f"Updated Ecobee data for {device_id}")
            
        except Exception as e:
            _log.error(f"Error handling Ecobee data: {e}")

    def handle_kasa_data(self, peer, sender, bus, topic, headers, message):
        """Handle Kasa smart plug data"""
        try:
            data = message[0]
            device_id = topic.split('/')[-1]
            
            self.device_states[f"kasa_{device_id}"] = {
                "power_consumption": data.get("power", 0.0),
                "voltage": data.get("voltage", 120.0),
                "current": data.get("current", 0.0),
                "switch_state": data.get("state", False),
                "device_info": data.get("device_info", {}),
                "timestamp": headers.get(headers_mod.TIMESTAMP)
            }
            
            _log.debug(f"Updated Kasa data for {device_id}")
            
        except Exception as e:
            _log.error(f"Error handling Kasa data: {e}")

    def handle_ecoflow_data(self, peer, sender, bus, topic, headers, message):
        """Handle EcoFlow battery data"""
        try:
            data = message[0]
            device_id = topic.split('/')[-1]
            
            self.device_states[f"ecoflow_{device_id}"] = {
                "battery_soc": data.get("soc", 0.0),
                "battery_voltage": data.get("voltage", 0.0),
                "power_input": data.get("power_input", 0.0),
                "power_output": data.get("power_output", 0.0),
                "remaining_time": data.get("remaining_time", 0),
                "temperature": data.get("temperature", 25.0),
                "timestamp": headers.get(headers_mod.TIMESTAMP)
            }
            
            self.battery_soc = data.get("soc", 0.0)
            
            _log.debug(f"Updated EcoFlow data for {device_id}")
            
        except Exception as e:
            _log.error(f"Error handling EcoFlow data: {e}")

    def handle_demand_response(self, peer, sender, bus, topic, headers, message):
        """Handle OpenADR demand response signals"""
        try:
            dr_event = message[0]
            event_id = dr_event.get("event_id")
            event_type = dr_event.get("event_type", "load_reduction")
            target_reduction = dr_event.get("target_kw", 0.0)
            start_time = dr_event.get("start_time")
            end_time = dr_event.get("end_time")
            
            _log.info(f"Demand Response Event: {event_id}, Type: {event_type}, "
                     f"Reduction: {target_reduction}kW")
            
            if event_type == "load_reduction":
                self.demand_response_signal()
                self.execute_demand_response(target_reduction, start_time, end_time)
            elif event_type == "emergency":
                self.emergency_signal()
                self.execute_emergency_response()
                
        except Exception as e:
            _log.error(f"Error handling demand response: {e}")

    def monitor_system(self):
        """Monitor overall system status"""
        try:
            # Calculate total power consumption
            total_power = sum([
                device.get("power_consumption", 0.0) 
                for device in self.device_states.values()
            ])
            self.current_power = total_power
            
            # Check for peak conditions
            current_hour = datetime.now().hour
            if 16 <= current_hour <= 20:  # Peak hours
                if total_power > self.default_config["demand_targets"]["peak_shaving"]:
                    self.peak_detected()
            
            # Check battery status
            battery_devices = [
                device for key, device in self.device_states.items() 
                if "ecoflow" in key
            ]
            
            if battery_devices:
                avg_soc = sum([d.get("battery_soc", 0) for d in battery_devices]) / len(battery_devices)
                self.battery_soc = avg_soc
            
            # Publish system status
            self.publish_system_status()
            
        except Exception as e:
            _log.error(f"Error monitoring system: {e}")

    def optimize_loads(self):
        """Optimize load distribution based on current conditions"""
        try:
            if self.state == "demand_response":
                self.optimize_for_demand_response()
            elif self.state == "peak_shaving":
                self.optimize_for_peak_shaving()
            elif self.state == "battery_charging":
                self.optimize_for_battery_charging()
            else:
                self.optimize_for_comfort()
                
        except Exception as e:
            _log.error(f"Error optimizing loads: {e}")

    def optimize_for_demand_response(self):
        """Optimize loads during demand response events"""
        _log.info("Optimizing for demand response")
        
        # Prioritize load reduction strategies
        strategies = [
            self.adjust_hvac_setpoints,
            self.curtail_non_essential_loads,
            self.use_battery_power,
            self.defer_flexible_loads
        ]
        
        for strategy in strategies:
            try:
                strategy()
            except Exception as e:
                _log.error(f"Error executing strategy {strategy.__name__}: {e}")

    def optimize_for_peak_shaving(self):
        """Optimize loads during peak hours"""
        _log.info("Optimizing for peak shaving")
        
        # Moderate load reduction
        self.adjust_hvac_setpoints(moderate=True)
        self.use_battery_power()
        self.schedule_flexible_loads()

    def optimize_for_battery_charging(self):
        """Optimize for off-peak battery charging"""
        _log.info("Optimizing for battery charging")
        
        # Use excess grid capacity for battery charging
        self.charge_batteries()
        self.pre_cool_or_heat()

    def optimize_for_comfort(self):
        """Normal optimization for comfort and efficiency"""
        # Standard comfort-based optimization
        self.maintain_comfort_settings()
        self.balance_battery_usage()

    def adjust_hvac_setpoints(self, moderate=False):
        """Adjust HVAC setpoints for load reduction"""
        adjustment = 1.0 if moderate else 2.0
        
        for device_key, device_data in self.device_states.items():
            if "ecobee" in device_key:
                device_id = device_key.replace("ecobee_", "")
                
                # Adjust setpoints based on season and current conditions
                if self.outdoor_temp > 75:  # Cooling season
                    new_setpoint = device_data["cooling_setpoint"] + adjustment
                    self.send_ecobee_command(device_id, "set_cooling_setpoint", new_setpoint)
                elif self.outdoor_temp < 65:  # Heating season
                    new_setpoint = device_data["heating_setpoint"] - adjustment
                    self.send_ecobee_command(device_id, "set_heating_setpoint", new_setpoint)

    def curtail_non_essential_loads(self):
        """Turn off non-essential loads"""
        non_essential = ["water_heater", "pool_pump", "entertainment_system"]
        
        for device_key, device_data in self.device_states.items():
            if "kasa" in device_key:
                device_id = device_key.replace("kasa_", "")
                device_info = device_data.get("device_info", {})
                device_type = device_info.get("type", "")
                
                if device_type in non_essential and device_data["switch_state"]:
                    self.send_kasa_command(device_id, "turn_off")

    def use_battery_power(self):
        """Switch to battery power to reduce grid consumption"""
        for device_key, device_data in self.device_states.items():
            if "ecoflow" in device_key:
                device_id = device_key.replace("ecoflow_", "")
                soc = device_data["battery_soc"]
                
                if soc > self.default_config["battery_management"]["min_soc"]:
                    # Enable battery discharge
                    self.send_ecoflow_command(device_id, "enable_discharge", True)

    def charge_batteries(self):
        """Charge batteries during off-peak hours"""
        for device_key, device_data in self.device_states.items():
            if "ecoflow" in device_key:
                device_id = device_key.replace("ecoflow_", "")
                soc = device_data["battery_soc"]
                target_soc = self.default_config["battery_management"]["target_soc"]
                
                if soc < target_soc:
                    self.send_ecoflow_command(device_id, "start_charging")

    def send_ecobee_command(self, device_id, command, value=None):
        """Send command to Ecobee agent"""
        try:
            topic = f"devices/ecobee/{device_id}/command"
            message = {"command": command, "value": value}
            self.vip.pubsub.publish("pubsub", topic, message=message)
            _log.debug(f"Sent Ecobee command: {command} to {device_id}")
        except Exception as e:
            _log.error(f"Error sending Ecobee command: {e}")

    def send_kasa_command(self, device_id, command, value=None):
        """Send command to Kasa agent"""
        try:
            topic = f"devices/kasa/{device_id}/command"
            message = {"command": command, "value": value}
            self.vip.pubsub.publish("pubsub", topic, message=message)
            _log.debug(f"Sent Kasa command: {command} to {device_id}")
        except Exception as e:
            _log.error(f"Error sending Kasa command: {e}")

    def send_ecoflow_command(self, device_id, command, value=None):
        """Send command to EcoFlow agent"""
        try:
            topic = f"devices/ecoflow/{device_id}/command"
            message = {"command": command, "value": value}
            self.vip.pubsub.publish("pubsub", topic, message=message)
            _log.debug(f"Sent EcoFlow command: {command} to {device_id}")
        except Exception as e:
            _log.error(f"Error sending EcoFlow command: {e}")

    def publish_system_status(self):
        """Publish current system status"""
        try:
            status = {
                "timestamp": format_timestamp(get_aware_utc_now()),
                "state": self.state,
                "total_power": self.current_power,
                "battery_soc": self.battery_soc,
                "indoor_temp": self.indoor_temp,
                "outdoor_temp": self.outdoor_temp,
                "demand_response_active": self.demand_response_active,
                "device_count": len(self.device_states)
            }
            
            self.vip.pubsub.publish(
                "pubsub",
                "smart_home/status",
                message=status
            )
            
        except Exception as e:
            _log.error(f"Error publishing system status: {e}")

    def execute_demand_response(self, target_reduction, start_time, end_time):
        """Execute demand response strategy"""
        _log.info(f"Executing demand response: {target_reduction}kW reduction")
        self.demand_response_active = True
        
        # Schedule return to normal
        duration = (
            parse_timestamp_string(end_time) - 
            parse_timestamp_string(start_time)
        ).total_seconds()
        
        self.core.schedule(
            get_aware_utc_now() + timedelta(seconds=duration),
            self.end_demand_response
        )

    def end_demand_response(self):
        """End demand response and return to normal operation"""
        _log.info("Ending demand response, returning to normal operation")
        self.demand_response_active = False
        self.return_to_normal()

    def execute_emergency_response(self):
        """Execute emergency response strategy"""
        _log.warning("Executing emergency response")
        
        # Turn off all non-critical loads
        self.curtail_non_essential_loads()
        
        # Switch to battery power immediately
        self.use_battery_power()
        
        # Set HVAC to minimum operation
        self.adjust_hvac_setpoints(moderate=False)

    def update_forecasts(self):
        """Update load and weather forecasts"""
        try:
            # This would integrate with weather APIs and load forecasting models
            _log.info("Updating forecasts...")
            
            # Placeholder for forecast updates
            self.weather_forecast = {
                "next_24h": {"avg_temp": 75, "peak_temp": 85},
                "peak_hours": {"temp": 85, "load_factor": 1.2}
            }
            
            self.load_forecast = {
                "next_hour": self.current_power * 1.1,
                "peak_hour": self.current_power * 1.3
            }
            
        except Exception as e:
            _log.error(f"Error updating forecasts: {e}")

    # Additional helper methods for specific device types and strategies...
    def maintain_comfort_settings(self):
        """Maintain comfortable indoor conditions"""
        pass

    def balance_battery_usage(self):
        """Balance battery charging/discharging for optimal lifespan"""
        pass

    def schedule_flexible_loads(self):
        """Schedule flexible loads for optimal times"""
        pass

    def defer_flexible_loads(self):
        """Defer flexible loads during high demand periods"""
        pass

    def pre_cool_or_heat(self):
        """Pre-condition the home during off-peak hours"""
        pass


def main():
    """Main method called by VOLTTRON"""
    try:
        utils.vip_main(SmartHomeILCAgent, version="1.0")
    except Exception as e:
        _log.exception("Unhandled exception in main")
        _log.error(repr(e))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass