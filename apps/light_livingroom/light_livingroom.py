from appdaemon.plugins.hass import Hass
from common.decorators import handle_errors

class LivingroomLight(Hass):

    def extract_config(self):
        # Extract configs from apps.yaml
        config = self.args               
        self.location    = config["location"]
        self.event_type  = config["switch"]["event_type"]
        self.switch_ieee = config["switch"]["device_ieee"]
        self.bulbs       = [b["entity"]["state"] for b in config["bulbs"]]

    def initialize(self):
        self.extract_config()    
        self.log(f"----- Initializing LivingroomLight for {self.location.upper()} -----") 
        self.listen_event(
            self.on_button_press,
            self.event_type,
            device_ieee = self.switch_ieee
        )
        
    @handle_errors
    def on_button_press(self, event_name, data, **kwargs):
        self.log(f"Button press event: {data}")
        command = data.get("command")
        self.log(f"Turning livingroom lights {command}")
        if command == "on":           
            for bulb in self.bulbs:
                self.turn_on(bulb)
        elif command == "off":
            for bulb in self.bulbs:
                self.turn_off(bulb)
