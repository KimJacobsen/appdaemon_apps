from appdaemon.plugins.hass import Hass
from common.decorators import log_call, handle_errors, debugpy_init

#@debugpy_init(port=6789)
class BatteryMonitor(Hass):
    """Monitor battery levels of various Home Assistant devices and send notifications when batteries are low."""

    @handle_errors(level="ERROR")
    def extract_config(self) -> None:
        """Extract and validate configuration from apps.yaml."""
        config = self.args

        # Extract device configurations
        self.sensors = config.get("sensors", [])
        self.binary_sensors = config.get("binary_sensors", [])
        
        # Extract settings with defaults
        self.battery_threshold = float(config.get("battery_threshold", 20))
        self.check_interval = int(config.get("check_interval", 24 * 60 * 60))
        self.notify_service = config.get("notifier", "notify/notify")
        
        # Get custom binary sensor low battery states
        self.binary_low_states = [state.lower() for state in config.get("binary_low_states", 
            ["on", "low", "replace", "critical", "false"])]

        # Validate configuration
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate the configuration values."""
        if not 0 <= self.battery_threshold <= 100:
            self.log("Battery threshold must be between 0 and 100. Using default of 20.", level="WARNING")
            self.battery_threshold = 20

        if self.check_interval < 300:  # Minimum 5 minutes
            self.log("Check interval too short. Using minimum of 300 seconds.", level="WARNING")
            self.check_interval = 300

    def initialize(self) -> None:
        """Initialize the battery monitor app."""
        self.extract_config()
        self.log("Battery Monitor App Initialized", level="INFO")
        
        # Start monitoring schedule
        self.run_every(self.check_battery_levels, 
                      "now", 
                      self.check_interval)

    @log_call
    @handle_errors(level="WARNING")
    def check_battery_levels(self, kwargs: dict[str, any]) -> None:
        """Check battery levels of all configured devices."""
        self.log("Checking battery levels for all devices", level="INFO")
        
        low_battery_devices = []
        low_battery_devices.extend(self._check_regular_sensors())
        low_battery_devices.extend(self._check_binary_sensors())

        # Send notifications if any devices have low battery
        if low_battery_devices:
            self.notify_low_batteries(low_battery_devices)

    @log_call
    @handle_errors(level="WARNING")
    def _check_regular_sensors(self) -> list[dict[str, str]]:
        """Check battery levels of sensors that report percentage."""
        low_battery_devices = []
        
        for sensor in self.sensors:
            bat_entity = sensor.get("entity", {}).get("battery", None)
            if not bat_entity:
                continue

            battery_level = self.get_state(bat_entity)
            if battery_level is None:
                self.log(f"No battery state available for {bat_entity}", level="DEBUG")
                continue

            try:
                battery_level = float(battery_level)
                if battery_level < self.battery_threshold:
                    device_name = sensor.get("device", bat_entity)
                    low_battery_devices.append({
                        "name": device_name,
                        "level": f"{battery_level:.1f}%",
                        "type": "sensor"
                    })
            except (ValueError, TypeError):
                self.log(f"Invalid battery level '{battery_level}' for {bat_entity}", level="WARNING")

        return low_battery_devices
    

    @log_call
    @handle_errors(level="WARNING")
    def _check_binary_sensors(self) -> list[dict[str, str]]:
        """Check battery state of binary sensors (like TRVs)."""
        low_battery_devices = []
        
        for sensor in self.binary_sensors:
            bat_entity = sensor.get("entity", {}).get("battery", None)
            if not bat_entity:
                continue

            battery_state = self.get_state(bat_entity)
            if battery_state is None:
                self.log(f"No battery state available for {bat_entity}", level="DEBUG")
                continue

            # Check if current state indicates low battery
            if str(battery_state).lower() in self.binary_low_states:
                device_name = sensor.get("device", bat_entity)
                low_battery_devices.append({
                    "name": device_name,
                    "level": "LOW",
                    "type": "binary_sensor"
                })

        return low_battery_devices

    @handle_errors(level="ERROR")
    def notify_low_batteries(self, low_battery_devices: list[dict[str, str]]) -> None:
        """Send notification about devices with low batteries."""
        if not low_battery_devices:
            return

        # Group devices by type for better readability
        sensors = [d for d in low_battery_devices if d['type'] == 'sensor']
        binary_sensors = [d for d in low_battery_devices if d['type'] == 'binary_sensor']
        
        # Build notification message
        message_parts = ["Devices needing battery replacement:"]
        
        if sensors:
            message_parts.append("\nDevices with low battery level:")
            for device in sorted(sensors, key=lambda x: x['name']):
                message_parts.append(f"- {device['name']}: {device['level']}")
                
        if binary_sensors:
            message_parts.append("\nTRVs reporting battery warning:")
            for device in sorted(binary_sensors, key=lambda x: x['name']):
                message_parts.append(f"- {device['name']}")
        
        message = '\n'.join(message_parts)
        self.log(message, level="WARNING")
        
        # Send notification through Home Assistant
        try:
            self.call_service(self.notify_service,
                            title="Battery Replacement Needed",
                            message=message)
        except Exception as e:
            self.log(f"Failed to send notification: {str(e)}", level="ERROR")
            # Try fallback notification service if configured one fails
            try:
                self.call_service("notify/notify",
                                title="Battery Replacement Needed",
                                message=message)
            except Exception as e:
                self.log(f"Failed to send fallback notification: {str(e)}", level="ERROR")
        