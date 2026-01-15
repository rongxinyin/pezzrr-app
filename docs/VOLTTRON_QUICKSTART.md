# VOLTTRON Quick Start Guide

This guide covers starting the VOLTTRON platform and the Ecobee thermostat agent.

## Prerequisites

- Python 3.10+
- Virtual environment set up at `./venv`

## 1. Start the VOLTTRON Platform

```bash
# Activate the virtual environment
source venv/bin/activate

# Start VOLTTRON (use absolute path for VOLTTRON_HOME)
VOLTTRON_HOME=$(pwd)/.volttron volttron -vv &

# Wait a few seconds for platform to initialize
sleep 5
```

## 2. Check Platform Status

```bash
source venv/bin/activate
VOLTTRON_HOME=$(pwd)/.volttron vctl status
```

Expected output shows platform agents:
```
UUID   AGENT                      IDENTITY                  TAG      PRIORITY STATUS          HEALTH
7      volttron-listener-2.0.0rc2 volttron-listener-agent-1 listener          running [PID]   GOOD
```

## 3. Install and Start the Ecobee Agent

### First-time Installation

```bash
# Build the agent wheel
cd agents/ecobee_agent
python setup.py bdist_wheel
cd ../..

# Install the agent
source venv/bin/activate
VOLTTRON_HOME=$(pwd)/.volttron vctl install agents/ecobee_agent/dist/ecobee_agent-1.0.0-py3-none-any.whl

# Store configuration (edit config/ecobee_config.json with your API key first)
VOLTTRON_HOME=$(pwd)/.volttron vctl config store ecobee-agent-1.0.0_1 config config/ecobee_config.json

# Start the agent
VOLTTRON_HOME=$(pwd)/.volttron vctl start ecobee-agent-1.0.0_1
```

### Starting an Already-Installed Agent

```bash
source venv/bin/activate
VOLTTRON_HOME=$(pwd)/.volttron vctl start ecobee-agent-1.0.0_1
```

## 4. Ecobee OAuth Authorization

On first start, the agent will request OAuth authorization:

1. Check the logs for the PIN:
   ```bash
   tail -f .volttron/volttron.log | grep -i "ecobee PIN"
   ```

2. Go to https://www.ecobee.com/consumerportal
3. Log in and navigate to "My Apps"
4. Click "Add Application"
5. Enter the PIN displayed in the logs
6. The agent will automatically obtain tokens and start polling

## 5. Verify Agent is Working

```bash
# Check agent status
VOLTTRON_HOME=$(pwd)/.volttron vctl status

# Watch for thermostat data in logs
tail -f .volttron/volttron.log | grep -i "temperature\|humidity"
```

## 6. Stop the Platform

```bash
source venv/bin/activate
VOLTTRON_HOME=$(pwd)/.volttron vctl shutdown --platform
```

## Common Commands

| Command | Description |
|---------|-------------|
| `vctl status` | List all agents and their status |
| `vctl start <agent>` | Start an agent |
| `vctl stop <agent>` | Stop an agent |
| `vctl restart <agent>` | Restart an agent |
| `vctl remove <agent>` | Uninstall an agent |
| `vctl config list <agent>` | List agent configurations |
| `vctl shutdown --platform` | Stop the VOLTTRON platform |

## Ecobee Agent Configuration

Edit `config/ecobee_config.json`:

```json
{
  "api_key": "YOUR_ECOBEE_API_KEY",
  "poll_interval": 300,
  "device_id": "YOUR_THERMOSTAT_ID",
  "campus": "CAMPUS",
  "building": "BUILDING"
}
```

Get your API key from the Ecobee Developer Portal: https://www.ecobee.com/developers/

## Published Topics

The Ecobee agent publishes data to:

- `devices/CAMPUS/BUILDING/<device_id>/temperature`
- `devices/CAMPUS/BUILDING/<device_id>/humidity`
- `devices/CAMPUS/BUILDING/<device_id>/hvac_state`
- `devices/CAMPUS/BUILDING/<device_id>/hvac_mode`
- `devices/CAMPUS/BUILDING/<device_id>/heat_setpoint`
- `devices/CAMPUS/BUILDING/<device_id>/cool_setpoint`
- `devices/CAMPUS/BUILDING/<device_id>/all` (all data points)

## Troubleshooting

### Platform won't start
- Check if another instance is running: `ps aux | grep volttron`
- Remove stale PID file: `rm .volttron/VOLTTRON_PID`

### Agent keeps crashing
- Check logs: `tail -100 .volttron/volttron.log`
- Verify configuration: `vctl config get <agent> config`

### OAuth errors
- Ensure API key is correct in configuration
- PINs expire after 15 minutes - restart agent for new PIN
- Don't restart agent while waiting for PIN authorization
