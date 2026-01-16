#!/usr/bin/env python3
"""
Simple EcoFlow Agent Test Script for VOLTTRON 2.0
==================================================

Quick and easy test script for EcoFlow agent functionality.
Compatible with VOLTTRON 2.0 (no vctl shell dependency).

Usage:
    python simple_test_ecoflow.py
"""

import subprocess
import json
import sys
import os
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

def generate_signature(access_key, secret_key, method, url, params=None, data=None):
    """Generate API signature for EcoFlow authentication"""
    try:
        import random
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
