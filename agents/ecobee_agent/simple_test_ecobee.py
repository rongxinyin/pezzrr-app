#!/usr/bin/env python3
"""
Simple Ecobee Agent Test Script
==============================

Quick and easy test script for Ecobee agent functionality.
Run this without needing to create a full VOLTTRON test agent.

Usage:
    python simple_test_ecobee.py
"""

import subprocess
import json
import sys
import os
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
        print(f"   Error: {stderr}")
        return False

def test_agent_installation():
    """Test if Ecobee agent is installed"""
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
                print("💡 Try: vctl start --tag ecobee")
                return False
        else:
            print("❌ Ecobee agent not found")
            print("💡 Install with: vctl install . --vip-identity ecobee_agent --tag ecobee")
            return False
    else:
        print(f"❌ Error checking agents: {stderr}")
        return False

def test_agent_rpc():
    """Test RPC calls to Ecobee agent"""
    print("\n📡 Testing RPC Communication")
    print("-" * 30)
    
    # Create a simple test script for vctl shell
    test_script = '''
import sys
try:
    result = vip.rpc.call('ecobee-agent-1.0.0_1', 'get_status').get(timeout=10)
    print("RPC_SUCCESS:", result)
except Exception as e:
    print("RPC_ERROR:", str(e))
    sys.exit(1)
'''
    
    # Write test script to temp file
    with open('/tmp/ecobee_test.py', 'w') as f:
        f.write(test_script)
    
    # Run the test script
    success, stdout, stderr = run_vctl_command("vctl shell < /tmp/ecobee_test.py")
    
    if "RPC_SUCCESS:" in stdout:
        print("✅ RPC communication successful")
        
        # Parse the result
        for line in stdout.split('\n'):
            if line.startswith("RPC_SUCCESS:"):
                try:
                    result_str = line.replace("RPC_SUCCESS: ", "")
                    result = eval(result_str)  # Simple eval for test output
                    
                    print(f"   Status: {result.get('status', 'unknown')}")
                    print(f"   Version: {result.get('version', 'unknown')}")
                    print(f"   API Key Configured: {result.get('api_key_configured', False)}")
                    print(f"   Authenticated: {result.get('authenticated', False)}")
                    print(f"   Thermostat Count: {result.get('thermostat_count', 0)}")
                    
                    return result.get('api_key_configured', False)
                except:
                    print("   ⚠️ Could not parse agent status")
                    return False
        return True
    else:
        print("❌ RPC communication failed")
        if "RPC_ERROR:" in stdout:
            for line in stdout.split('\n'):
                if line.startswith("RPC_ERROR:"):
                    error = line.replace("RPC_ERROR: ", "")
                    print(f"   Error: {error}")
        return False

def test_authentication():
    """Test authentication flow"""
    print("\n🔐 Testing Authentication")
    print("-" * 26)
    
    # Test authentication start
    auth_script = '''
try:
    result = vip.rpc.call('ecobee_agent', 'start_authentication').get(timeout=15)
    print("AUTH_RESULT:", result)
except Exception as e:
    print("AUTH_ERROR:", str(e))
'''
    
    with open('/tmp/ecobee_auth_test.py', 'w') as f:
        f.write(auth_script)
    
    success, stdout, stderr = run_vctl_command("vctl shell < /tmp/ecobee_auth_test.py")
    
    if "AUTH_RESULT:" in stdout:
        for line in stdout.split('\n'):
            if line.startswith("AUTH_RESULT:"):
                try:
                    result_str = line.replace("AUTH_RESULT: ", "")
                    result = eval(result_str)
                    
                    if result.get('success'):
                        print("✅ Authentication start successful")
                        print(f"   PIN: {result.get('pin', 'N/A')}")
                        print("   📋 Complete these steps:")
                        print("   1. Go to: https://www.ecobee.com/consumerportal")
                        print("   2. Login to your Ecobee account")
                        print("   3. Go to 'My Apps' in profile menu")
                        print(f"   4. Enter PIN: {result.get('pin', 'N/A')}")
                        print("   5. Click 'Authorize'")
                        
                        return True, result.get('code')
                    else:
                        print(f"❌ Authentication failed: {result.get('error', 'Unknown error')}")
                        return False, None
                except:
                    print("   ⚠️ Could not parse authentication result")
                    return False, None
    else:
        print("❌ Authentication test failed")
        if "AUTH_ERROR:" in stdout:
            for line in stdout.split('\n'):
                if line.startswith("AUTH_ERROR:"):
                    error = line.replace("AUTH_ERROR: ", "")
                    print(f"   Error: {error}")
        return False, None

def interactive_auth_completion(auth_code):
    """Interactive authentication completion"""
    if not auth_code:
        return False
        
    print("\n🔄 Authentication Completion")
    print("-" * 28)
    
    response = input("Have you completed the PIN authorization in Ecobee portal? (y/n): ")
    if response.lower() != 'y':
        print("⏭️ Skipping authentication completion")
        return False
    
    complete_script = f'''
try:
    result = vip.rpc.call('ecobee_agent', 'complete_authentication', '{auth_code}').get(timeout=15)
    print("COMPLETE_RESULT:", result)
except Exception as e:
    print("COMPLETE_ERROR:", str(e))
'''
    
    with open('/tmp/ecobee_complete_test.py', 'w') as f:
        f.write(complete_script)
    
    success, stdout, stderr = run_vctl_command("vctl shell < /tmp/ecobee_complete_test.py")
    
    if "COMPLETE_RESULT:" in stdout:
        for line in stdout.split('\n'):
            if line.startswith("COMPLETE_RESULT:"):
                try:
                    result_str = line.replace("COMPLETE_RESULT: ", "")
                    result = eval(result_str)
                    
                    if result.get('success'):
                        print("✅ Authentication completed successfully!")
                        return True
                    else:
                        print(f"❌ Authentication completion failed: {result.get('error', 'Unknown')}")
                        return False
                except:
                    print("   ⚠️ Could not parse completion result")
                    return False
    else:
        print("❌ Authentication completion test failed")
        return False

def test_thermostat_data():
    """Test thermostat data retrieval"""
    print("\n🌡️ Testing Thermostat Data")
    print("-" * 26)
    
    data_script = '''
try:
    result = vip.rpc.call('ecobee_agent', 'get_thermostats').get(timeout=15)
    print("DATA_RESULT:", result)
except Exception as e:
    print("DATA_ERROR:", str(e))
'''
    
    with open('/tmp/ecobee_data_test.py', 'w') as f:
        f.write(data_script)
    
    success, stdout, stderr = run_vctl_command("vctl shell < /tmp/ecobee_data_test.py")
    
    if "DATA_RESULT:" in stdout:
        for line in stdout.split('\n'):
            if line.startswith("DATA_RESULT:"):
                try:
                    result_str = line.replace("DATA_RESULT: ", "")
                    result = eval(result_str)
                    
                    if result.get('success'):
                        thermostats = result.get('thermostats', {})
                        print(f"✅ Successfully retrieved {len(thermostats)} thermostat(s)")
                        
                        for tid, data in thermostats.items():
                            print(f"   📍 {data.get('name', 'Unnamed')} ({tid[:8]}...)")
                            print(f"      Temperature: {data.get('current_temperature', 'N/A')}°F")
                            print(f"      Humidity: {data.get('humidity', 'N/A')}%")
                            print(f"      Mode: {data.get('hvac_mode', 'N/A')}")
                        
                        return True, list(thermostats.keys())
                    else:
                        print(f"❌ Data retrieval failed: {result.get('error', 'Unknown')}")
                        return False, []
                except:
                    print("   ⚠️ Could not parse data result")
                    return False, []
    else:
        print("❌ Data retrieval test failed")
        return False, []

def print_manual_commands():
    """Print manual test commands"""
    print("\n📚 Manual Testing Commands")
    print("-" * 30)
    print("For additional testing, use these commands:")
    print()
    print("1. VOLTTRON Shell:")
    print("   vctl shell")
    print()
    print("2. In the shell, test these:")
    print("   # Check agent status")
    print("   vip.rpc.call('ecobee_agent', 'get_agent_status').get()")
    print()
    print("   # Start authentication")
    print("   vip.rpc.call('ecobee_agent', 'start_authentication').get()")
    print()
    print("   # Complete authentication (use code from above)")
    print("   vip.rpc.call('ecobee_agent', 'complete_authentication', 'YOUR_CODE').get()")
    print()
    print("   # Get thermostat data")
    print("   vip.rpc.call('ecobee_agent', 'get_thermostats').get()")
    print()
    print("   # Set temperature (CAREFUL - test values only!)")
    print("   vip.rpc.call('ecobee_agent', 'set_temperature', 'THERMOSTAT_ID', 74.0, 70.0).get()")

def main():
    """Main test function"""
    print("🏠 Simple Ecobee Agent Test Suite")
    print("=" * 40)
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Clean up any previous test files
    for temp_file in ['/tmp/ecobee_test.py', '/tmp/ecobee_auth_test.py', '/tmp/ecobee_complete_test.py', '/tmp/ecobee_data_test.py']:
        try:
            os.remove(temp_file)
        except:
            pass
    
    tests_passed = 0
    total_tests = 0
    
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
    
    # Test 3: RPC Communication
    total_tests += 1
    api_configured = False
    if test_agent_rpc():
        tests_passed += 1
        api_configured = True
    
    if not api_configured:
        print("\n⚠️ API key not configured. Update config.json and restart agent.")
        print("💡 Command: vctl config store ecobee_agent config config.json")
        print_manual_commands()
        return
    
    # Test 4: Authentication
    total_tests += 1
    auth_success, auth_code = test_authentication()
    if auth_success:
        tests_passed += 1
        
        # Test 5: Interactive auth completion
        total_tests += 1
        if interactive_auth_completion(auth_code):
            tests_passed += 1
            
            # Test 6: Thermostat data
            total_tests += 1
            data_success, thermostats = test_thermostat_data()
            if data_success:
                tests_passed += 1
    
    # Summary
    print("\n" + "=" * 40)
    print("📊 TEST SUMMARY")
    print("=" * 40)
    print(f"✅ Passed: {tests_passed}/{total_tests} tests")
    
    if tests_passed == total_tests:
        print("🎉 All tests passed! Ecobee agent is fully functional.")
    elif tests_passed >= total_tests * 0.8:
        print("✅ Most tests passed. Agent is working well.")
    else:
        print("⚠️ Some tests failed. Check the output above for issues.")
    
    print_manual_commands()
    
    # Clean up test files
    for temp_file in ['/tmp/ecobee_test.py', '/tmp/ecobee_auth_test.py', '/tmp/ecobee_complete_test.py', '/tmp/ecobee_data_test.py']:
        try:
            os.remove(temp_file)
        except:
            pass

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️ Tests interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()