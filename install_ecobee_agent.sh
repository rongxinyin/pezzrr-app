#!/bin/bash

# Installation script for Ecobee Agent
# Run this from the volttron-smart-home directory

set -e

echo "Installing Ecobee Agent for VOLTTRON..."

# Set VOLTTRON_HOME if not set
if [ -z "$VOLTTRON_HOME" ]; then
    export VOLTTRON_HOME=~/github/pezzrr-app/.volttron
    echo "VOLTTRON_HOME set to: $VOLTTRON_HOME"
fi

# Check if VOLTTRON is running
if ! vctl status > /dev/null 2>&1; then
    echo "VOLTTRON platform is not running. Please start VOLTTRON first:"
    echo "volttron -vv"
    exit 1
fi

# Install dependencies
echo "Installing Python dependencies..."
pip install requests pytz

# Package the agent
echo "Packaging Ecobee Agent..."
cd agents/ecobee_agent
python setup.py bdist_wheel
if [ $? -ne 0 ]; then
    echo "Failed to package the Ecobee Agent. Please check the setup.py file."
    exit 1
fi
echo "Packaging complete. Wheel file created in dist/ directory."

# Install the agent
echo "Installing agent to VOLTTRON..."
AGENT_WHEEL=$(find dist -name "*.whl" | head -1)
# AGENT_WHEEL=$VOLTTRON_HOME/wheelhouse/ecobee_agent-1.0.0-py3-none-any.whl
if [ ! -f "$AGENT_WHEEL" ]; then
    echo "Error: Agent wheel not found at $AGENT_WHEEL"
    exit 1
fi
echo "Found agent wheel: $AGENT_WHEEL"

# Install the agent using vctl
vctl install $AGENT_WHEEL --tag ecobee --force

echo "Agent installed with name: ecobee"

# Store configuration
echo "Storing agent configuration..."
vctl config store ecobee config config/config.json

echo ""
echo "============================================"
echo "Ecobee Agent Installation Complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "1. Edit agents/ecobee_agent/config.json with your API key"
echo "2. Update the configuration: vctl config store config config.json"
echo "3. Start the agent: vctl start"
echo ""
echo "Agent UUID: "
echo "Configuration: vctl config list "
echo "Start agent: vctl start "
echo "Check status: vctl status"
echo "View logs: tail -f $VOLTTRON_HOME/volttron.log"