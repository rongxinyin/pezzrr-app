import volttron.platform.agent
import kasa
import requests
import yaml

class LoadManagementAgent(volttron.platform.agent.BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Load configuration from YAML file
        with open("volttron_config.yaml", "r") as file:
            config = yaml.safe_load(file)
        
        self.kasa_plug_ip = config["volttron"]["kasa"]["ip"]
        self.ecobee_api_key = config["volttron"]["ecobee"]["api_key"]
        self.ecobee_thermostat_id = config["volttron"]["ecobee"]["thermostat_id"]
    
    async def control_kasa_plug(self, state: bool):
        """Controls Kasa smart plug on/off."""
        plug = kasa.SmartPlug(self.kasa_plug_ip)
        await plug.update()
        if state:
            await plug.turn_on()
        else:
            await plug.turn_off()
    
    def control_ecobee_thermostat(self, temperature: float):
        """Controls Ecobee thermostat to set temperature."""
        url = f"https://api.ecobee.com/1/thermostat?format=json"
        headers = {"Authorization": f"Bearer {self.ecobee_api_key}", "Content-Type": "application/json"}
        payload = {
            "selection": {"selectionType": "thermostats", "selectionMatch": self.ecobee_thermostat_id},
            "thermostat": {"settings": {"holdTemp": True, "coolHoldTemp": temperature * 10}}
        }
        response = requests.post(url, json=payload, headers=headers)
        return response.json()

    def on_message(self, topic, headers, message):
        """Handles incoming VOLTTRON messages."""
        if topic == "control/kasa":
            state = message.get("state", False)
            self.vip.loop.call_soon_threadsafe(lambda: self.control_kasa_plug(state))
        elif topic == "control/ecobee":
            temperature = message.get("temperature", 72.0)
            self.control_ecobee_thermostat(temperature)
    
    def start_agent(self):
        """Subscribes to control topics."""
        self.vip.pubsub.subscribe(peer='pubsub', prefix='control/kasa', callback=self.on_message)
        self.vip.pubsub.subscribe(peer='pubsub', prefix='control/ecobee', callback=self.on_message)

if __name__ == "__main__":
    volttron.platform.agent.utils.vip_main(LoadManagementAgent)
