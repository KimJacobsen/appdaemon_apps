# log_bridge.py
from appdaemon.adapi import ADAPI
from common.decorators import handle_errors

class LogBridge(ADAPI):
    """
    Bridges AppDaemon logs to a Home Assistant sensor and logbook.
    """

    def initialize(self):
        """Initializes the app, gets config, and sets up the log listener."""
        self.log("----- Initializing LogBridge App -----")
        
        # --- Configuration ---
        config = self.args
        self.entity_id          = config.get("entity_id", "sensor.appdaemon_log")
        self.min_level          = config.get("level", "INFO").upper()
        self.logbook_enabled    = config.get("logbook_enabled", True)
        self.logbook_min_level  = config.get("logbook_min_level", "WARNING").upper()
        
        # --- State ---
        self._is_forwarding = False # Re-entrancy guard to prevent infinite loops

        self.log(f"Forwarding logs of level '{self.min_level}' or higher to '{self.entity_id}'.")
        self.listen_log(self.forward_log_cb, level=self.min_level)

    @handle_errors
    def forward_log_cb(self, name, ts, level, message, kwargs):
        """Callback that forwards a log entry to Home Assistant."""
        # Prevent infinite loops if this method itself causes a log entry.
        if self._is_forwarding:
            return
        
        try:
            self._is_forwarding = True
            # Set state with a concise value and detailed attributes.
            self.set_state(self.entity_id, state=level.upper(), attributes={
                "friendly_name": "AppDaemon Log",
                "message": message,
                "app_name": name,
                "timestamp": ts
            })

            # Optionally send to logbook, typically for more severe levels.
            log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if self.logbook_enabled and log_levels.index(level.upper()) >= log_levels.index(self.logbook_min_level):
                self.call_service("logbook/log", name=f"AppDaemon: {name}", message=message, entity_id=self.entity_id)
        finally:
            self._is_forwarding = False