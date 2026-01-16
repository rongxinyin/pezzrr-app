#!/usr/bin/env python3
"""
Simple Ecobee Agent Test Script for VOLTTRON 2.0
================================================

Quick and easy test script for Ecobee agent functionality.
Compatible with VOLTTRON 2.0 (no vctl shell dependency).

Usage:
    python simple_test_ecobee.py
"""

import subprocess
import json
import sys
import os
import requests
from datetime import datetime

def run_vctl_command(cmd):
    """Run a vctl command and return the result"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)

def test_volttron_environment():
    """Test VOLTTRON environment setup"""
    print("🔧 Testing VOLTTRON Environment")
    print("-" * 35)

    # Check VOLTTRON_HOME
    volttron_home = os.environ.get('VOLTTRON_HOME')
    if volttron_home:
        print(f"✅ VOLTTRON_HOME: {volttron_home}")
    else:
        print("❌ VOLTTRON_HOME not set")
        return False

    # Check if VOLTTRON is running
    success, stdout, stderr = run_vctl_command("vctl status")
    if success and ("RUNNING" in stdout or "running" in stdout):
        print("✅ VOLTTRON platform is running")
        return True
    else:
        print("❌ VOLTTRON platform not running")
        print(f"   Error: {stderr or stdout}")
        return False

def test_agent_installation():
    """Test if Ecobee agent is installed and running"""
    print("\n📦 Testing Agent Installation")
    print("-" * 30)

    success, stdout, stderr = run_vctl_command("vctl list")
    if success:
        if "ecobee" in stdout.lower():
            print("✅ Ecobee agent found in agent list")

            # Check if running
            if "RUNNING" in stdout or "running" in stdout:
                print("✅ Ecobee agent is running")
                return True
            else:
                print("⚠️ Ecobee agent installed but not running")
                print("💡 Try: vctl start <agent-uuid>")
                return False
        else:
            print("❌ Ecobee agent not found")
            print("💡 Install with: vctl install .")
            return False
    else:
        print(f"❌ Error checking agents: {stderr}")
        return False

def test_agent_connectivity():
    """Test if agent is connected to VOLTTRON message bus"""
    print("\n🔌 Testing Agent Connectivity")
    print("-" * 30)

    success, stdout, stderr = run_vctl_command("vctl peerlist")
    if success:
        if "ecobee" in stdout.lower():
            print("✅ Ecobee agent connected to message bus")
            return True
        else:
            print("❌ Ecobee agent not in peerlist")
            return False
    else:
        print(f"❌ Error checking peerlist: {stderr}")
        return False

def get_agent_identity():
    """Get the ecobee agent identity from vctl list"""
    success, stdout, stderr = run_vctl_command("vctl list")
    if success:
        for line in stdout.split('\n'):
            # Look for lines with ecobee that have the running status format
            if 'ecobee' in line.lower() and ('running' in line.lower() or line.strip().startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9'))):
                parts = line.split()
                # Format: UUID AGENT IDENTITY TAG PRIORITY STATUS HEALTH
                # Find the identity which contains 'ecobee' and ends with '_1' or similar
                for part in parts:
                    if 'ecobee' in part.lower() and '_' in part:
                        return part
    return "ecobee-agent-1.0.0_1"  # Default fallback

def test_agent_config():
    """Test agent configuration via vctl config"""
    print("\n⚙️ Testing Agent Configuration")
    print("-" * 31)

    agent_identity = get_agent_identity()

    # Check if config exists
    success, stdout, stderr = run_vctl_command(f"vctl config list {agent_identity}")
    if not success:
        print(f"❌ Cannot access agent config: {stderr}")
        return False, None

    if "config" not in stdout:
        print("❌ No config found for agent")
        return False, None

    print("✅ Agent config store accessible")

    # Get the config
    success, stdout, stderr = run_vctl_command(f"vctl config get {agent_identity} config")
    if success:
        try:
            config = json.loads(stdout)
            api_key = config.get('api_key', '')

            if api_key and api_key != "YOUR_ECOBEE_API_KEY_HERE":
                print("✅ API key configured")
                print(f"   App ID: {config.get('app_id', 'N/A')}")
                print(f"   Poll Interval: {config.get('poll_interval', 'N/A')}s")
                print(f"   Thermostat Name: {config.get('thermostat_name', 'N/A')}")
                return True, config
            else:
                print("❌ API key not configured")
                return False, None
        except json.JSONDecodeError:
            print("❌ Invalid config format")
            return False, None
    else:
        print(f"❌ Cannot read config: {stderr}")
        return False, None

def test_agent_tokens():
    """Test if agent has valid authentication tokens"""
    print("\n🔐 Testing Authentication Tokens")
    print("-" * 33)

    agent_identity = get_agent_identity()

    success, stdout, stderr = run_vctl_command(f"vctl config get {agent_identity} tokens")
    if success:
        try:
            tokens = json.loads(stdout)
            access_token = tokens.get('access_token', '')
            refresh_token = tokens.get('refresh_token', '')
            expires_at = tokens.get('expires_at', '')

            if access_token and refresh_token:
                print("✅ Authentication tokens found")
                print(f"   Access Token: {access_token[:50]}...")
                print(f"   Refresh Token: {refresh_token[:20]}...")
                print(f"   Expires At: {expires_at}")

                # Check if expired
                if expires_at:
                    try:
                        exp_time = datetime.fromisoformat(expires_at)
                        if exp_time < datetime.now():
                            print("⚠️ Access token expired (will auto-refresh)")
                        else:
                            print("✅ Access token is valid")
                    except:
                        pass

                return True, tokens
            else:
                print("❌ No tokens found - authentication required")
                return False, None
        except json.JSONDecodeError:
            print("❌ No tokens stored")
            return False, None
    else:
        print("❌ No tokens stored - authentication required")
        return False, None

def test_ecobee_api(config, tokens):
    """Test direct Ecobee API connection"""
    print("\n🌡️ Testing Ecobee API Connection")
    print("-" * 33)

    if not config or not tokens:
        print("❌ Missing config or tokens")
        return False

    api_key = config.get('api_key')
    refresh_token = tokens.get('refresh_token')

    if not api_key or not refresh_token:
        print("❌ Missing API key or refresh token")
        return False

    try:
        # Refresh token first
        print("   Refreshing access token...")
        token_response = requests.post(
            f"https://api.ecobee.com/token?grant_type=refresh_token&refresh_token={refresh_token}&client_id={api_key}",
            timeout=15
        )

        if token_response.status_code != 200:
            print(f"❌ Token refresh failed: {token_response.status_code}")
            return False

        token_data = token_response.json()
        access_token = token_data.get('access_token')

        if not access_token:
            print("❌ No access token in response")
            return False

        print("✅ Token refreshed successfully")

        # Get thermostat data
        print("   Fetching thermostat data...")
        url = "https://api.ecobee.com/1/thermostat"
        params = {
            'json': json.dumps({
                'selection': {
                    'selectionType': 'registered',
                    'selectionMatch': '',
                    'includeRuntime': True,
                    'includeSettings': True,
                    'includeWeather': True
                }
            })
        }
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        response = requests.get(url, params=params, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()

            if 'thermostatList' in data and data['thermostatList']:
                print("✅ Thermostat data retrieved successfully!\n")

                for thermostat in data['thermostatList']:
                    runtime = thermostat.get('runtime', {})
                    settings = thermostat.get('settings', {})
                    weather = thermostat.get('weather', {})

                    current_temp = runtime.get('actualTemperature', 0) / 10.0
                    heat_setpoint = runtime.get('desiredHeat', 0) / 10.0
                    cool_setpoint = runtime.get('desiredCool', 0) / 10.0
                    humidity = runtime.get('actualHumidity', 0)
                    hvac_mode = settings.get('hvacMode', 'unknown')
                    equipment_status = runtime.get('equipmentStatus', 'idle') or 'idle'

                    print(f"   📍 Thermostat: {thermostat.get('name', 'Unknown')}")
                    print(f"      ID: {thermostat.get('identifier')}")
                    print(f"      Model: {thermostat.get('modelNumber')}")
                    print(f"      🌡️  Current Temp: {current_temp}°F")
                    print(f"      💧 Humidity: {humidity}%")
                    print(f"      🔥 Heat Setpoint: {heat_setpoint}°F")
                    print(f"      ❄️  Cool Setpoint: {cool_setpoint}°F")
                    print(f"      ⚙️  HVAC Mode: {hvac_mode}")
                    print(f"      📊 Status: {equipment_status}")

                    # Weather info
                    if weather and 'forecasts' in weather and weather['forecasts']:
                        current_weather = weather['forecasts'][0]
                        outside_temp = current_weather.get('temperature', 0) / 10.0
                        print(f"      🌤️  Outside Temp: {outside_temp}°F")

                return True
            else:
                print("❌ No thermostats found")
                return False
        else:
            print(f"❌ API request failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print("❌ API request timed out")
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ API request error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def print_help():
    """Print helpful commands"""
    print("\n📚 Helpful Commands")
    print("-" * 30)
    print("Agent management:")
    print("   vctl status              - Check agent status")
    print("   vctl start <uuid>        - Start agent")
    print("   vctl stop <uuid>         - Stop agent")
    print("   vctl restart <uuid>      - Restart agent")
    print()
    print("Configuration:")
    print("   vctl config list <identity>        - List configs")
    print("   vctl config get <identity> config  - Get agent config")
    print("   vctl config get <identity> tokens  - Get auth tokens")
    print()
    print("RPC methods available:")
    print("   get_status()             - Get agent status")
    print("   get_current_data()       - Get thermostat data")
    print("   set_temperature(heat, cool) - Set temperature")
    print("   set_hvac_mode(mode)      - Set HVAC mode")
    print("   resume_schedule()        - Resume normal schedule")

def main():
    """Main test function"""
    print("🏠 Ecobee Agent Test Suite (VOLTTRON 2.0)")
    print("=" * 45)
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    tests_passed = 0
    total_tests = 0
    config = None
    tokens = None

    # Test 1: VOLTTRON Environment
    total_tests += 1
    if test_volttron_environment():
        tests_passed += 1
    else:
        print("\n❌ VOLTTRON environment test failed. Fix before continuing.")
        return

    # Test 2: Agent Installation
    total_tests += 1
    if test_agent_installation():
        tests_passed += 1
    else:
        print("\n❌ Agent installation test failed. Install agent before continuing.")
        return

    # Test 3: Agent Connectivity
    total_tests += 1
    if test_agent_connectivity():
        tests_passed += 1
    else:
        print("\n⚠️ Agent not connected. Continuing with other tests...")

    # Test 4: Agent Config
    total_tests += 1
    config_ok, config = test_agent_config()
    if config_ok:
        tests_passed += 1
    else:
        print("\n❌ Config test failed. Configure agent before continuing.")
        print("💡 Command: vctl config store <identity> config /path/to/config.json")
        print_help()
        return

    # Test 5: Authentication Tokens
    total_tests += 1
    tokens_ok, tokens = test_agent_tokens()
    if tokens_ok:
        tests_passed += 1
    else:
        print("\n⚠️ No tokens found. Agent will start OAuth flow on restart.")

    # Test 6: Ecobee API Connection
    if config and tokens:
        total_tests += 1
        if test_ecobee_api(config, tokens):
            tests_passed += 1

    # Summary
    print("\n" + "=" * 45)
    print("📊 TEST SUMMARY")
    print("=" * 45)
    print(f"✅ Passed: {tests_passed}/{total_tests} tests")

    if tests_passed == total_tests:
        print("🎉 All tests passed! Ecobee agent is fully functional.")
    elif tests_passed >= total_tests * 0.8:
        print("✅ Most tests passed. Agent is working well.")
    elif tests_passed >= total_tests * 0.5:
        print("⚠️ Some tests failed. Check the output above for issues.")
    else:
        print("❌ Multiple tests failed. Check configuration and authentication.")

    print_help()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️ Tests interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
