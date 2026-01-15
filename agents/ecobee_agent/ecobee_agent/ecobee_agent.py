"""
Ecobee Smart Thermostat Agent for PEZZRR Controller
Handles OAuth2 authentication and thermostat control via Ecobee API
"""

import os
import sys
import logging
import json
import time
from datetime import datetime, timedelta
from threading import Timer
from typing import Dict

# Import gevent first to ensure proper monkey-patching before requests/urllib3
import gevent
from gevent import monkey
monkey.patch_all()

# Now import requests after monkey-patching
import requests

from volttron import utils
from volttron.client.messaging import topics, headers as headers_mod
from volttron.utils import format_timestamp, get_aware_utc_now
from volttron.client import Agent, Core, RPC

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)

__version__ = "1.0.0"

class EcobeeAgent(Agent):
    """
    Ecobee Smart Thermostat Agent for PEZZRR Controller.
    This agent integrates with Ecobee's API to manage smart thermostats.
    It provides functionality for authentication, data collection, and HVAC control.
    
    This agent handles:
    1. OAuth2 authentication with Ecobee API
    2. Thermostat data collection
    3. HVAC control commands
    4. Temperature setpoint management
    5. VOLTTRON platform integration
    """

    def __init__(self, config_path, **kwargs):
        super(EcobeeAgent, self).__init__(**kwargs)
        
        # Default configuration
        self.default_config = {
            "api_key": "",
            "app_id": "",
            "scope": "smartWrite",
            "poll_interval": 300,  # 5 minutes
            "thermostat_name": "Home",
            "campus": "CAMPUS",
            "building": "BUILDING",
            "device_id": "ecobee_thermostat",
            "publish_temperature": True,
            "publish_setpoints": True,
            "publish_hvac_mode": True,
            "api_base_url": "https://api.ecobee.com/1",
            "oauth_base_url": "https://api.ecobee.com/authorize"
        }
        
        # Authentication tokens
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.authorization_code = None
        
        # Thermostat data
        self.thermostat_data = {}
        self.thermostat_id = None
        
        # Control state
        self.poll_timer = None
        self.token_refresh_timer = None
        
        # Topic configuration
        self.publish_base_topic = None
        self.device_topic = None
        
        # # Load configuration from file or use defaults
        # self.config = self.load_config_file()
        # _log.info("Loaded configuration: {self.config}")
        # if not self.validate_config(self.config):
        #     _log.error("Invalid configuration. Please check your config.json file.")
        #     raise ValueError("Invalid configuration. Please check your config.json file.")
        
        # Configuration setup
        self.vip.config.set_default("config", self.default_config)
        self.vip.config.subscribe(self.configure_main, actions=["NEW", "UPDATE"])

    # def load_config_file(self) -> Dict:
    #     """Load configuration from the specified config file"""
    #     config = self.default_config.copy()
    #     config_loaded = False

    #     config_path = os.path.join('')

    #     try:
    #         if os.path.exists(config_path):
    #             _log.info(f"Loading configuration from: {config_path}")
                
    #             with open(config_path, 'r') as f:
    #                 file_config = json.load(f)
                
    #             # Validate required fields
    #             if self.validate_config(file_config):
    #                 config.update(file_config)
    #                 config_loaded = True
    #                 _log.info(f"Successfully loaded configuration from {config_path}")
    #             else:
    #                 _log.warning(f"Invalid configuration in {config_path}, trying next location")
                    
    #     except Exception as e:
    #         _log.warning(f"Failed to load config from {config_path}: {e}")
        
    #     if not config_loaded:
    #         _log.warning("No valid configuration file found, using defaults with simulation mode")
        
    #     return config

    # def validate_config(self, config: Dict) -> bool:
    #     """Validate the configuration file"""
    #     try:
    #         # For real API mode, require credentials
    #         required_fields = ["api_key"]
    #         for field in required_fields:
    #             if not config.get(field):
    #                 _log.error(f"Missing required configuration field: {field}")
    #                 return False
            
    #         # Validate numeric values
    #         if "poll_interval" in config:
    #             if not isinstance(config["poll_interval"], (int, float)) or config["poll_interval"] <= 0:
    #                 _log.error("poll_interval must be a positive number")
    #                 return False
            
    #         return True
            
    #     except Exception as e:
    #         _log.error(f"Error validating configuration: {e}")
    #         return False
               
    def configure_main(self, config_name, action, contents):
        """Configure the agent from config store"""
        _log.info(f"Configuring Ecobee Agent: {action}, config_name={config_name}, contents keys={list(contents.keys()) if contents else 'None'}")

        # Only process the main config
        if config_name != "config":
            _log.info(f"Ignoring non-main config: {config_name}")
            return

        config = self.default_config.copy()
        config.update(contents)

        # Store configuration - only update api_key if provided
        new_api_key = config.get("api_key")
        if new_api_key:
            self.api_key = new_api_key
        elif not hasattr(self, 'api_key') or not self.api_key:
            self.api_key = ""
        self.app_id = config.get("app_id") 
        self.scope = config.get("scope", "smartWrite")
        self.poll_interval = config.get("poll_interval", 300)
        self.thermostat_name = config.get("thermostat_name", "Home")
        
        # Build topic paths
        campus = config.get("campus", "CAMPUS")
        building = config.get("building", "BUILDING") 
        device_id = config.get("device_id", "ecobee_thermostat")
        
        self.device_topic = f"{campus}/{building}/{device_id}"
        self.publish_base_topic = f"devices/{self.device_topic}"
        
        # API endpoints
        self.api_base_url = config.get("api_base_url", "https://api.ecobee.com/1")
        self.oauth_base_url = config.get("oauth_base_url", "https://api.ecobee.com/authorize")
        
        # Load stored tokens if available
        self._load_stored_tokens()
        
        if not self.api_key:
            _log.error("API key not configured! Please set api_key in configuration.")
            return
            
        # Start authentication flow if needed
        if not self.access_token:
            _log.info("No access token found. Starting OAuth2 flow...")
            self._start_oauth_flow()
        else:
            _log.info("Access token available. Starting thermostat polling...")
            self._start_polling()

    @Core.receiver("onstart")
    def onstart(self, sender, **kwargs):
        """Called when the agent starts"""
        _log.info("Ecobee Agent starting...")

    @Core.receiver("onstop") 
    def onstop(self, sender, **kwargs):
        """Called when the agent stops"""
        _log.info("Ecobee Agent stopping...")
        if self.poll_timer:
            self.poll_timer.cancel()
        if self.token_refresh_timer:
            self.token_refresh_timer.cancel()
        self._save_tokens()

    def _start_oauth_flow(self):
        """Start the OAuth2 authentication flow"""
        try:
            # Step 1: Get authorization code
            auth_url = f"{self.oauth_base_url}?response_type=ecobeePin&client_id={self.api_key}&scope={self.scope}"
            
            auth_response = requests.get(auth_url)
            auth_response.raise_for_status()
            
            auth_data = auth_response.json()
            
            if 'ecobeePin' in auth_data:
                self.authorization_code = auth_data['code']
                ecobee_pin = auth_data['ecobeePin']
                interval = auth_data['interval']
                expires_in = auth_data['expires_in']
                
                _log.info(f"Ecobee PIN: {ecobee_pin}")
                _log.info(f"Please go to https://www.ecobee.com/consumerportal and enter PIN: {ecobee_pin}")
                _log.info(f"You have {expires_in} seconds to complete authorization")
                
                # Wait for user to authorize, then get tokens
                Timer(interval, self._get_access_token).start()
                
            else:
                _log.error(f"Failed to get authorization code: {auth_data}")
                
        except Exception as e:
            _log.error(f"Error starting OAuth flow: {e}")

    def _get_access_token(self, retry_count=0):
        """Exchange authorization code for access token"""
        max_retries = 60  # Retry for up to 10 minutes (60 * 10 seconds)

        try:
            # Token endpoint is at root, not under /1
            token_url = "https://api.ecobee.com/token"

            token_data = {
                'grant_type': 'ecobeePin',
                'code': self.authorization_code,
                'client_id': self.api_key
            }

            _log.info(f"Token request: url={token_url}, client_id={self.api_key}, code={self.authorization_code}")

            # Ecobee API expects query parameters in URL for token exchange
            response = requests.post(
                f"{token_url}?grant_type=ecobeePin&code={self.authorization_code}&client_id={self.api_key}"
            )
            response_data = response.json()
            _log.info(f"Token response (status {response.status_code}): {response_data}")

            if response.status_code == 200 and 'access_token' in response_data:
                self.access_token = response_data['access_token']
                self.refresh_token = response_data['refresh_token']
                self.token_expires_at = datetime.now() + timedelta(seconds=response_data['expires_in'])

                _log.info("Successfully obtained access token!")

                # Save tokens and start polling
                self._save_tokens()
                self._start_polling()
                self._schedule_token_refresh()

            else:
                # Check for authorization_pending error (Ecobee uses status.code format)
                error_code = response_data.get('status', {}).get('code', 0)
                error_msg = response_data.get('status', {}).get('message', '')

                # Error code 2 = "authorization_pending" - user hasn't authorized yet
                # Error code 1 = "authentication token required" - may need retry
                if error_code == 2 or (retry_count < max_retries and 'authorization' in error_msg.lower()):
                    _log.info(f"Authorization pending. Checking again in 10 seconds... (attempt {retry_count + 1}/{max_retries})")
                    Timer(10, lambda: self._get_access_token(retry_count + 1)).start()
                elif retry_count < max_retries:
                    _log.info(f"Waiting for authorization. Retrying in 10 seconds... (attempt {retry_count + 1}/{max_retries})")
                    Timer(10, lambda: self._get_access_token(retry_count + 1)).start()
                else:
                    _log.error(f"Failed to get access token after {max_retries} attempts: {response_data}")

        except Exception as e:
            _log.error(f"Error getting access token: {e}")
            if retry_count < max_retries:
                Timer(10, lambda: self._get_access_token(retry_count + 1)).start()

    def _refresh_access_token(self):
        """Refresh the access token using refresh token"""
        try:
            # Token endpoint is at root, not under /1
            token_url = "https://api.ecobee.com/token"
            
            refresh_data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'client_id': self.api_key
            }
            
            headers = {
                'Content-Type': 'application/json'
            }
            
            response = requests.post(token_url, json=refresh_data, headers=headers)
            response.raise_for_status()
            
            token_info = response.json()
            
            self.access_token = token_info['access_token']
            self.refresh_token = token_info['refresh_token']
            self.token_expires_at = datetime.now() + timedelta(seconds=token_info['expires_in'])
            
            _log.info("Successfully refreshed access token")
            self._save_tokens()
            self._schedule_token_refresh()
            
        except Exception as e:
            _log.error(f"Error refreshing access token: {e}")
            # Start new OAuth flow if refresh fails
            self._start_oauth_flow()

    def _schedule_token_refresh(self):
        """Schedule token refresh before expiration"""
        if self.token_expires_at:
            # Refresh 5 minutes before expiration
            refresh_time = self.token_expires_at - timedelta(minutes=5)
            delay = (refresh_time - datetime.now()).total_seconds()
            
            if delay > 0:
                if self.token_refresh_timer:
                    self.token_refresh_timer.cancel()
                self.token_refresh_timer = Timer(delay, self._refresh_access_token)
                self.token_refresh_timer.start()

    def _save_tokens(self):
        """Save tokens to agent data directory"""
        try:
            token_data = {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None
            }
            
            # Use VOLTTRON's config store to save tokens securely
            self.vip.config.set("tokens", token_data)
            _log.debug("Tokens saved successfully")
            
        except Exception as e:
            _log.error(f"Error saving tokens: {e}")

    def _load_stored_tokens(self):
        """Load previously saved tokens"""
        try:
            token_data = self.vip.config.get("tokens")
            if token_data:
                self.access_token = token_data.get('access_token')
                self.refresh_token = token_data.get('refresh_token')
                
                expires_str = token_data.get('expires_at')
                if expires_str:
                    self.token_expires_at = datetime.fromisoformat(expires_str)
                    
                    # Check if token is still valid
                    if self.token_expires_at < datetime.now():
                        _log.info("Stored token expired. Will refresh...")
                        self._refresh_access_token()
                    else:
                        _log.info("Loaded valid stored tokens")
                        self._schedule_token_refresh()
                        
        except Exception as e:
            _log.error(f"Error loading stored tokens: {e}")

    def _start_polling(self):
        """Start polling thermostat data"""
        self._poll_thermostat()
        
        # Schedule next poll
        if self.poll_timer:
            self.poll_timer.cancel()
        self.poll_timer = Timer(self.poll_interval, self._start_polling)
        self.poll_timer.start()

    def _poll_thermostat(self):
        """Poll thermostat for current data"""
        try:
            if not self.access_token:
                _log.warning("No access token available for polling")
                return
                
            # Get thermostat data
            url = f"{self.api_base_url}/thermostat"
            
            params = {
                'json': json.dumps({
                    'selection': {
                        'selectionType': 'registered',
                        'selectionMatch': '',
                        'includeRuntime': True,
                        'includeSettings': True,
                        'includeWeather': True,
                        'includeEvents': True
                    }
                })
            }
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            
            if 'thermostatList' in data and data['thermostatList']:
                thermostat = data['thermostatList'][0]  # Use first thermostat
                self.thermostat_id = thermostat['identifier']
                self.thermostat_data = thermostat
                
                # Process and publish data
                self._process_thermostat_data(thermostat)
                
            else:
                _log.warning("No thermostats found in response")
                
        except Exception as e:
            _log.error(f"Error polling thermostat: {e}")

    def _process_thermostat_data(self, thermostat):
        """Process thermostat data and publish to VOLTTRON"""
        try:
            runtime = thermostat.get('runtime', {})
            settings = thermostat.get('settings', {})
            
            # Extract key data points
            current_temp = runtime.get('actualTemperature', 0) / 10.0  # Convert from 10ths
            heat_setpoint = runtime.get('desiredHeat', 0) / 10.0
            cool_setpoint = runtime.get('desiredCool', 0) / 10.0
            hvac_mode = settings.get('hvacMode', 'off')
            
            # Current HVAC state
            hvac_state = self._determine_hvac_state(runtime)
            
            # Prepare data for publishing
            device_data = {
                'temperature': current_temp,
                'heat_setpoint': heat_setpoint,
                'cool_setpoint': cool_setpoint,
                'hvac_mode': hvac_mode,
                'hvac_state': hvac_state,
                'humidity': runtime.get('actualHumidity', 0),
                'timestamp': format_timestamp(get_aware_utc_now())
            }
            
            # Publish to VOLTTRON
            self._publish_device_data(device_data)
            
            _log.debug(f"Thermostat data: {device_data}")
            
        except Exception as e:
            _log.error(f"Error processing thermostat data: {e}")

    def _determine_hvac_state(self, runtime):
        """Determine current HVAC operating state"""
        equipment_status = runtime.get('equipmentStatus', '')
        
        if 'heatPump' in equipment_status or 'auxHeat' in equipment_status:
            return 'heating'
        elif 'compCool' in equipment_status:
            return 'cooling'
        elif 'fan' in equipment_status:
            return 'fan_only'
        else:
            return 'idle'

    def _publish_device_data(self, data):
        """Publish device data to VOLTTRON message bus"""
        try:
            # Create separate topics for each data point
            timestamp = get_aware_utc_now()
            headers = {
                headers_mod.DATE: format_timestamp(timestamp),
                headers_mod.TIMESTAMP: format_timestamp(timestamp)
            }
            
            # Publish all data points
            topic = f"{self.publish_base_topic}/all"
            message = [data, self._get_metadata()]
            
            self.vip.pubsub.publish('pubsub', topic, headers, message).get(timeout=5)
            
            # Also publish individual points for easy access
            for point, value in data.items():
                point_topic = f"{self.publish_base_topic}/{point}"
                point_message = [{point: value}, {point: self._get_point_metadata(point, value)}]
                self.vip.pubsub.publish('pubsub', point_topic, headers, point_message).get(timeout=5)
                
        except Exception as e:
            _log.error(f"Error publishing device data: {e}")

    def _get_metadata(self):
        """Get metadata for published data"""
        return {
            'temperature': {'units': 'fahrenheit', 'tz': 'UTC', 'type': 'float'},
            'heat_setpoint': {'units': 'fahrenheit', 'tz': 'UTC', 'type': 'float'},
            'cool_setpoint': {'units': 'fahrenheit', 'tz': 'UTC', 'type': 'float'},
            'hvac_mode': {'units': 'mode', 'tz': 'UTC', 'type': 'string'},
            'hvac_state': {'units': 'state', 'tz': 'UTC', 'type': 'string'},
            'humidity': {'units': 'percent', 'tz': 'UTC', 'type': 'integer'},
            'timestamp': {'units': 'timestamp', 'tz': 'UTC', 'type': 'string'}
        }

    def _get_point_metadata(self, point, value):
        """Get metadata for a specific point"""
        metadata_map = self._get_metadata()
        return metadata_map.get(point, {'units': 'unknown', 'tz': 'UTC', 'type': type(value).__name__})

    @RPC.export
    def set_temperature(self, heat_setpoint=None, cool_setpoint=None, hold_type='nextTransition'):
        """
        Set thermostat temperature setpoints
        
        Args:
            heat_setpoint (float): Heating setpoint in Fahrenheit
            cool_setpoint (float): Cooling setpoint in Fahrenheit  
            hold_type (str): Hold type - 'nextTransition', 'indefinite', or 'holdHours'
        
        Returns:
            dict: Response from API call
        """
        try:
            if not self.access_token or not self.thermostat_id:
                return {'success': False, 'error': 'Not authenticated or no thermostat found'}
                
            # Build the thermostat update request
            functions = []
            
            if heat_setpoint is not None or cool_setpoint is not None:
                function_data = {
                    'type': 'setHold',
                    'params': {
                        'holdType': hold_type
                    }
                }
                
                if heat_setpoint is not None:
                    function_data['params']['heatHoldTemp'] = int(heat_setpoint * 10)  # Convert to 10ths
                    
                if cool_setpoint is not None:
                    function_data['params']['coolHoldTemp'] = int(cool_setpoint * 10)  # Convert to 10ths
                    
                functions.append(function_data)
            
            if not functions:
                return {'success': False, 'error': 'No setpoints provided'}
                
            # Make API call
            url = f"{self.api_base_url}/thermostat"
            
            payload = {
                'selection': {
                    'selectionType': 'thermostats',
                    'selectionMatch': self.thermostat_id
                },
                'functions': functions
            }
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            
            _log.info(f"Temperature setpoint changed: heat={heat_setpoint}, cool={cool_setpoint}")
            
            # Trigger immediate poll to get updated data
            Timer(2, self._poll_thermostat).start()
            
            return {'success': True, 'response': result}
            
        except Exception as e:
            _log.error(f"Error setting temperature: {e}")
            return {'success': False, 'error': str(e)}

    @RPC.export
    def set_hvac_mode(self, mode):
        """
        Set HVAC operating mode
        
        Args:
            mode (str): HVAC mode - 'auto', 'heat', 'cool', 'off'
            
        Returns:
            dict: Response from API call
        """
        try:
            if not self.access_token or not self.thermostat_id:
                return {'success': False, 'error': 'Not authenticated or no thermostat found'}
                
            valid_modes = ['auto', 'heat', 'cool', 'off']
            if mode not in valid_modes:
                return {'success': False, 'error': f'Invalid mode. Must be one of: {valid_modes}'}
                
            url = f"{self.api_base_url}/thermostat"
            
            payload = {
                'selection': {
                    'selectionType': 'thermostats', 
                    'selectionMatch': self.thermostat_id
                },
                'thermostat': {
                    'settings': {
                        'hvacMode': mode
                    }
                }
            }
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            
            _log.info(f"HVAC mode changed to: {mode}")
            
            # Trigger immediate poll to get updated data
            Timer(2, self._poll_thermostat).start()
            
            return {'success': True, 'response': result}
            
        except Exception as e:
            _log.error(f"Error setting HVAC mode: {e}")
            return {'success': False, 'error': str(e)}

    @RPC.export
    def resume_schedule(self):
        """Resume normal thermostat schedule"""
        try:
            if not self.access_token or not self.thermostat_id:
                return {'success': False, 'error': 'Not authenticated or no thermostat found'}
                
            url = f"{self.api_base_url}/thermostat"
            
            payload = {
                'selection': {
                    'selectionType': 'thermostats',
                    'selectionMatch': self.thermostat_id
                },
                'functions': [{
                    'type': 'resumeProgram'
                }]
            }
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            
            _log.info("Resumed normal thermostat schedule")
            
            # Trigger immediate poll to get updated data
            Timer(2, self._poll_thermostat).start()
            
            return {'success': True, 'response': result}
            
        except Exception as e:
            _log.error(f"Error resuming schedule: {e}")
            return {'success': False, 'error': str(e)}

    @RPC.export
    def get_current_data(self):
        """Get current thermostat data"""
        return {
            'success': True,
            'data': self.thermostat_data.get('runtime', {}),
            'settings': self.thermostat_data.get('settings', {})
        }

    @RPC.export
    def get_status(self):
        """Get agent status"""
        return {
            'authenticated': self.access_token is not None,
            'thermostat_connected': self.thermostat_id is not None,
            'last_poll': self.thermostat_data.get('runtime', {}).get('lastModified', 'Never'),
            'device_topic': self.device_topic
        }


def main():
    """Main method called to start the agent."""
    utils.vip_main(EcobeeAgent, 
                   description='Ecobee Smart Thermostat Agent',
                   argv=sys.argv)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass