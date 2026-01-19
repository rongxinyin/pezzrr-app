#!/usr/bin/env python3
"""
Simple EcoFlow Agent Test Script for VOLTTRON 2.0
==================================================

Quick and easy test script for EcoFlow agent functionality.
Compatible with VOLTTRON 2.0 (no vctl shell dependency).
Includes Smart Home Panel 2 circuit data querying.

Usage:
    python simple_test_ecoflow.py
    python simple_test_ecoflow.py --panel    # Query Smart Home Panel 2 data only
"""

import subprocess
import json
import sys
import os
import random
import requests
import hashlib
import hmac
import time
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
    print("Testing VOLTTRON Environment")
    print("-" * 35)

    # Check VOLTTRON_HOME
    volttron_home = os.environ.get('VOLTTRON_HOME')
    if volttron_home:
        print(f"[OK] VOLTTRON_HOME: {volttron_home}")
    else:
        print("[FAIL] VOLTTRON_HOME not set")
        return False

    # Check if VOLTTRON is running
    success, stdout, stderr = run_vctl_command("vctl status")
    if success and ("RUNNING" in stdout or "running" in stdout):
        print("[OK] VOLTTRON platform is running")
        return True
    else:
        print("[FAIL] VOLTTRON platform not running")
        print(f"   Error: {stderr or stdout}")
        return False

def test_agent_installation():
    """Test if EcoFlow agent is installed and running"""
    print("\nTesting Agent Installation")
    print("-" * 30)

    success, stdout, stderr = run_vctl_command("vctl list")
    if success:
        if "ecoflow" in stdout.lower():
            print("[OK] EcoFlow agent found in agent list")

            # Check if running
            if "RUNNING" in stdout or "running" in stdout:
                print("[OK] EcoFlow agent is running")
                return True
            else:
                print("[WARN] EcoFlow agent installed but not running")
                print("Tip: Try: vctl start <agent-uuid>")
                return False
        else:
            print("[FAIL] EcoFlow agent not found")
            print("Tip: Install with: vctl install .")
            return False
    else:
        print(f"[FAIL] Error checking agents: {stderr}")
        return False

def test_agent_connectivity():
    """Test if agent is connected to VOLTTRON message bus"""
    print("\nTesting Agent Connectivity")
    print("-" * 30)

    success, stdout, stderr = run_vctl_command("vctl peerlist")
    if success:
        if "ecoflow" in stdout.lower():
            print("[OK] EcoFlow agent connected to message bus")
            return True
        else:
            print("[FAIL] EcoFlow agent not in peerlist")
            return False
    else:
        print(f"[FAIL] Error checking peerlist: {stderr}")
        return False

def get_agent_identity():
    """Get the ecoflow agent identity from vctl list"""
    success, stdout, stderr = run_vctl_command("vctl list")
    if success:
        for line in stdout.split('\n'):
            if 'ecoflow' in line.lower() and ('running' in line.lower() or line.strip().startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9'))):
                parts = line.split()
                for part in parts:
                    if 'ecoflow' in part.lower() and '_' in part:
                        return part
    return "ecoflow-agent-1.0.0_1"  # Default fallback

def test_agent_config():
    """Test agent configuration via vctl config"""
    print("\nTesting Agent Configuration")
    print("-" * 31)

    agent_identity = get_agent_identity()

    # Check if config exists
    success, stdout, stderr = run_vctl_command(f"vctl config list {agent_identity}")
    if not success:
        print(f"[FAIL] Cannot access agent config: {stderr}")
        return False, None

    if "config" not in stdout:
        print("[FAIL] No config found for agent")
        return False, None

    print("[OK] Agent config store accessible")

    # Get the config
    success, stdout, stderr = run_vctl_command(f"vctl config get {agent_identity} config")
    if success:
        try:
            config = json.loads(stdout)
            access_key = config.get('access_key', '')
            secret_key = config.get('secret_key', '')

            if access_key and access_key != "your_access_key":
                print("[OK] Access key configured")
                print(f"   Device SN: {config.get('device_sn', 'N/A')}")
                print(f"   Poll Interval: {config.get('poll_interval', 'N/A')}s")
                print(f"   Auto Discover: {config.get('auto_discover', 'N/A')}")
                return True, config
            else:
                print("[FAIL] Access key not configured")
                return False, None
        except json.JSONDecodeError:
            print("[FAIL] Invalid config format")
            return False, None
    else:
        print(f"[FAIL] Cannot read config: {stderr}")
        return False, None

def get_qstring(params):
    """Convert params dict to sorted query string"""
    if not params:
        return ""
    sorted_params = sorted(params.items())
    return "&".join([f"{k}={v}" for k, v in sorted_params])

def hmac_sha256(data, key):
    """Generate HMAC-SHA256 signature"""
    hashed = hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).digest()
    return ''.join(format(byte, '02x') for byte in hashed)

def generate_signature(access_key, secret_key, method=None, url=None, params=None, data=None):
    """Generate API signature for EcoFlow authentication"""
    try:
        timestamp = str(int(time.time() * 1000))
        nonce = str(random.randint(100000, 999999))

        # Build headers dict for signing
        headers_dict = {
            'accessKey': access_key,
            'nonce': nonce,
            'timestamp': timestamp
        }

        # Build sign string: params (if any) + headers
        sign_parts = []
        if params:
            sign_parts.append(get_qstring(params))
        sign_parts.append(get_qstring(headers_dict))
        sign_str = '&'.join(sign_parts)

        # Generate signature
        signature = hmac_sha256(sign_str, secret_key)

        return {
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature
        }

    except Exception as e:
        print(f"Error generating signature: {e}")
        return None

def test_ecoflow_api(config):
    """Test direct EcoFlow API connection"""
    print("\nTesting EcoFlow API Connection")
    print("-" * 33)

    if not config:
        print("[FAIL] Missing config")
        return False

    access_key = config.get('access_key')
    secret_key = config.get('secret_key')
    api_base_url = config.get('api_base_url', 'https://api.ecoflow.com')

    if not access_key or not secret_key:
        print("[FAIL] Missing API credentials")
        return False

    try:
        print("   Connecting to EcoFlow API...")

        endpoint = "/iot-open/sign/device/list"
        method = "GET"

        auth_data = generate_signature(access_key, secret_key, method, endpoint)

        if not auth_data:
            print("[FAIL] Could not generate signature")
            return False

        headers = {
            "Content-Type": "application/json",
            "accessKey": access_key,
            "timestamp": auth_data["timestamp"],
            "nonce": auth_data["nonce"],
            "sign": auth_data["signature"]
        }

        url = f"{api_base_url}{endpoint}"
        response = requests.get(url, headers=headers, timeout=30)

        print(f"   Response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if data.get("code") == "0" or data.get("code") == 0:
                print("[OK] EcoFlow API connection successful!")

                devices = data.get("data", [])
                if devices:
                    print(f"\n   Found {len(devices)} device(s):")
                    for device in devices:
                        print(f"      Device SN: {device.get('sn', 'N/A')}")
                        print(f"      Device Name: {device.get('deviceName', 'N/A')}")
                        print(f"      Online: {device.get('online', 'N/A')}")
                        print()
                else:
                    print("   No devices found (auto_discover may find them)")

                return True
            else:
                print(f"[FAIL] API error: {data.get('message', 'Unknown error')}")
                return False
        else:
            print(f"[FAIL] API request failed: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print("[FAIL] API request timed out")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] API request error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        return False


def make_api_request(access_key, secret_key, api_base_url, endpoint, method="GET"):
    """Make an authenticated API request to EcoFlow"""
    auth_data = generate_signature(access_key, secret_key)
    if not auth_data:
        return None

    headers = {
        "Content-Type": "application/json",
        "accessKey": access_key,
        "timestamp": auth_data["timestamp"],
        "nonce": auth_data["nonce"],
        "sign": auth_data["signature"]
    }

    url = f"{api_base_url}{endpoint}"
    response = requests.get(url, headers=headers, timeout=30)
    return response.json()


def test_smart_home_panel_data(config):
    """Query and display Smart Home Panel 2 data including circuit information"""
    print("\n" + "=" * 70)
    print("SMART HOME PANEL 2 - CIRCUIT OPERATING DATA")
    print("=" * 70)

    if not config:
        print("[FAIL] Missing config")
        return False

    access_key = config.get('access_key')
    secret_key = config.get('secret_key')
    # Smart Home Panel 2 quota endpoint requires api-a.ecoflow.com
    api_base_url = config.get('api_base_url', 'https://api-a.ecoflow.com')
    # Force api-a for quota endpoint (api.ecoflow.com doesn't support it)
    if 'api.ecoflow.com' in api_base_url and 'api-a' not in api_base_url:
        api_base_url = 'https://api-a.ecoflow.com'
    device_sn = config.get('device_sn')

    if not access_key or not secret_key:
        print("[FAIL] Missing API credentials")
        return False

    if not device_sn:
        print("[FAIL] Missing device_sn in config")
        return False

    try:
        print(f"Device SN: {device_sn}")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 70)

        # Query device quota data
        endpoint = f"/iot-open/sign/device/quota/all?sn={device_sn}"
        result = make_api_request(access_key, secret_key, api_base_url, endpoint)

        if not result:
            print("[FAIL] No response from API")
            return False

        if result.get("code") != "0" and result.get("code") != 0:
            print(f"[FAIL] API error: {result.get('message', 'Unknown error')}")
            return False

        data = result.get("data", {})
        if not data:
            print("[FAIL] No data returned from API")
            return False

        print("[OK] Successfully retrieved Smart Home Panel 2 data\n")

        # Get circuit power readings
        hall1_watt = data.get("loadInfo.hall1Watt", [])

        print(f"{'CIRCUIT POWER READINGS':^70}")
        print("-" * 70)
        print(f"{'Circuit':<15} {'Name':<25} {'Power (W)':<12} {'Status':<15}")
        print("-" * 70)

        total_load = 0
        for i in range(1, 13):
            ch_name = data.get(f"pd303_mc.loadIncreInfo.hall1IncreInfo.ch{i}Info.chName", f"Circuit {i}")
            ch_status = data.get(f"pd303_mc.loadIncreInfo.hall1IncreInfo.ch{i}Sta.loadSta", "UNKNOWN")
            set_amp = data.get(f"pd303_mc.loadIncreInfo.hall1IncreInfo.ch{i}Info.setAmp", 0)

            # Get power from hall1Watt array (0-indexed)
            power = hall1_watt[i-1] if i-1 < len(hall1_watt) else 0.0
            total_load += power

            # Determine status label
            if ch_status == "LOAD_CH_POWER_ON":
                status = "ON"
            elif ch_status == "LOAD_CH_POWER_OFF":
                status = "OFF"
            else:
                status = ch_status.replace("LOAD_CH_", "") if ch_status != "UNKNOWN" else "UNKNOWN"

            power_str = f"{power:>8.1f}" if power != 0 else f"{'0.0':>8}"
            print(f"Circuit {i:<6} {ch_name:<25} {power_str:<12} {status:<15}")

        print("-" * 70)
        print(f"{'TOTAL LOAD:':<41} {total_load:>8.1f} W")

        # Backup channels (battery ports)
        print(f"\n{'BACKUP BATTERY PORTS':^70}")
        print("-" * 70)

        backup_watt = data.get("backupInfo.chWatt", data.get("wattInfo.chWatt", [0, 0, 0]))
        for i, port_power in enumerate(backup_watt, 1):
            ch_status = data.get(f"pd303_mc.backupIncreInfo.ch{i}Info.ctrlSta", "UNKNOWN")
            enabled = data.get(f"pd303_mc.ch{i}EnableSet", 0)

            if "DISCHARGE" in str(ch_status):
                status = "DISCHARGING"
            elif "CHARGE" in str(ch_status):
                status = "CHARGING"
            elif "OFF" in str(ch_status):
                status = "STANDBY"
            elif enabled:
                status = "ENABLED"
            else:
                status = "DISABLED"

            print(f"Battery Port {i}:  {port_power:>8.1f} W  [{status}]")

        # System summary
        print(f"\n{'SYSTEM SUMMARY':^70}")
        print("-" * 70)

        battery_soc = data.get("pd303_mc.backupIncreInfo.curDischargeSoc",
                               data.get("backupIncreInfo.curDischargeSoc", 0))
        battery_cap = data.get("pd303_mc.backupIncreInfo.backupDischargeRmainBatCap",
                               data.get("backupIncreInfo.backupDischargeRmainBatCap", 0))
        energy2_output = data.get("pd303_mc.backupIncreInfo.Energy2Info.outputPower", 0)
        discharge_time = data.get("backupInfo.backupDischargeTime", 0)
        charge_power = data.get("pd303_mc.chargeWattPower", data.get("chargeWattPower", 0))
        force_charge_high = data.get("pd303_mc.foceChargeHight", data.get("foceChargeHight", 0))

        print(f"Battery SOC:              {battery_soc:.1f}%")
        print(f"Remaining Capacity:       {battery_cap:.1f} Wh")
        print(f"Battery Output Power:     {energy2_output:.1f} W")
        if discharge_time > 0:
            print(f"Discharge Time:           {discharge_time} min ({discharge_time/60:.1f} hrs)")
        print(f"Charge Power Setting:     {charge_power} W")
        print(f"Force Charge High:        {force_charge_high}%")

        # Connected batteries info
        print(f"\n{'CONNECTED BATTERIES':^70}")
        print("-" * 70)

        for i in range(1, 4):
            energy_info = f"pd303_mc.backupIncreInfo.Energy{i}Info"
            rate_power = data.get(f"{energy_info}.devInfo.ratePower", 0)
            is_output = data.get(f"{energy_info}.isPowerOutput", 0)
            output_power = data.get(f"{energy_info}.outputPower", 0)

            if rate_power > 0:
                status = "ACTIVE" if is_output else "STANDBY"
                print(f"Energy Unit {i}: {rate_power:.0f}W rated, {output_power:.1f}W output [{status}]")

        print("=" * 70)
        return True

    except requests.exceptions.Timeout:
        print("[FAIL] API request timed out")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] API request error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_file():
    """Test if the config file exists and is valid"""
    print("\nTesting Config File")
    print("-" * 30)

    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "config", "ecoflow_config.json")

    if os.path.exists(config_path):
        print(f"[OK] Config file found: {config_path}")
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            print("[OK] Config file is valid JSON")
            print(f"   Device Type: {config.get('device_type', 'N/A')}")
            print(f"   Device SN: {config.get('device_sn', 'N/A')}")
            print(f"   Poll Interval: {config.get('poll_interval', 'N/A')}s")
            return True, config
        except json.JSONDecodeError as e:
            print(f"[FAIL] Invalid JSON: {e}")
            return False, None
    else:
        print(f"[FAIL] Config file not found at: {config_path}")
        return False, None

def print_help():
    """Print helpful commands"""
    print("\nHelpful Commands")
    print("-" * 30)
    print("This script options:")
    print("   python simple_test_ecoflow.py          - Run full test suite")
    print("   python simple_test_ecoflow.py --panel  - Query SHP2 data only")
    print("   python simple_test_ecoflow.py -p       - Query SHP2 data only (short)")
    print()
    print("Agent management:")
    print("   vctl status              - Check agent status")
    print("   vctl start <uuid>        - Start agent")
    print("   vctl stop <uuid>         - Stop agent")
    print("   vctl restart <uuid>      - Restart agent")
    print()
    print("Install agent:")
    print("   cd agents/ecoflow_agent")
    print("   vctl install . --agent-config ../../config/ecoflow_config.json")
    print()
    print("Configuration:")
    print("   vctl config list <identity>        - List configs")
    print("   vctl config get <identity> config  - Get agent config")
    print()
    print("RPC methods available:")
    print("   get_device_status_rpc()     - Get all device statuses")
    print("   get_battery_info(sn)        - Get battery info for device")
    print("   control_device_rpc(sn, cmd) - Control device")

def main():
    """Main test function"""
    # Check for command line arguments
    panel_only = "--panel" in sys.argv or "-p" in sys.argv

    if panel_only:
        # Just query Smart Home Panel 2 data
        print("EcoFlow Smart Home Panel 2 Data Query")
        print("=" * 45)
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Load config file
        config_file_ok, config = test_config_file()
        if config_file_ok and config:
            test_smart_home_panel_data(config)
        else:
            print("[FAIL] Could not load config file")
        return

    # Full test suite
    print("EcoFlow Agent Test Suite (VOLTTRON 2.0)")
    print("=" * 45)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    tests_passed = 0
    total_tests = 0
    config = None

    # Test 0: Config file
    total_tests += 1
    config_file_ok, file_config = test_config_file()
    if config_file_ok:
        tests_passed += 1
        config = file_config

    # Test 1: VOLTTRON Environment
    total_tests += 1
    if test_volttron_environment():
        tests_passed += 1

        # Test 2: Agent Installation
        total_tests += 1
        if test_agent_installation():
            tests_passed += 1

            # Test 3: Agent Connectivity
            total_tests += 1
            if test_agent_connectivity():
                tests_passed += 1

            # Test 4: Agent Config
            total_tests += 1
            config_ok, agent_config = test_agent_config()
            if config_ok:
                tests_passed += 1
                config = agent_config
        else:
            print("\n[INFO] Agent not installed. Skipping connectivity tests.")
    else:
        print("\n[INFO] VOLTTRON not running. Skipping platform tests.")

    # Test 5: EcoFlow API Connection (can run without VOLTTRON)
    if config:
        total_tests += 1
        if test_ecoflow_api(config):
            tests_passed += 1

    # Test 6: Smart Home Panel 2 Data (if device_type is ecoflow_panel)
    if config and config.get('device_type') == 'ecoflow_panel':
        total_tests += 1
        if test_smart_home_panel_data(config):
            tests_passed += 1

    # Summary
    print("\n" + "=" * 45)
    print("TEST SUMMARY")
    print("=" * 45)
    print(f"Passed: {tests_passed}/{total_tests} tests")

    if tests_passed == total_tests:
        print("[SUCCESS] All tests passed! EcoFlow agent is fully functional.")
    elif tests_passed >= total_tests * 0.8:
        print("[OK] Most tests passed. Agent is working well.")
    elif tests_passed >= total_tests * 0.5:
        print("[WARN] Some tests failed. Check the output above for issues.")
    else:
        print("[FAIL] Multiple tests failed. Check configuration and authentication.")

    print_help()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
