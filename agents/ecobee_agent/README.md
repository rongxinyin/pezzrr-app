# Ecobee Smart Thermostat Agent

VOLTTRON agent for controlling Ecobee smart thermostats via the Ecobee API.

## Features

- OAuth2 authentication with Ecobee API
- Real-time thermostat data collection
- Temperature setpoint control
- HVAC mode control
- Schedule management
- VOLTTRON platform integration

## Prerequisites

1. Ecobee Developer Account
2. API Key from Ecobee Developer Portal
3. VOLTTRON 10 platform running

## Getting Your Ecobee API Key

1. Go to https://www.ecobee.com/developers/
2. Sign in with your Ecobee account
3. Create a new app:
   - App Name: "Smart Home Controller" (or your choice)
   - Summary: "VOLTTRON integration for smart home"
   - Authorization Method: "PIN"
   - Application Scope: "smartWrite"
4. Save your API Key

## Installation

1. **Create directory structure:**
   ```bash
   mkdir -p ~/volttron-smart-home/agents/ecobee_agent/ecobee
   cd ~/volttron-smart-home/agents/ecobee_agent
   ```
