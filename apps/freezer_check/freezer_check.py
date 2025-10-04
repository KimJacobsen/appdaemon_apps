from datetime import datetime, timedelta
from appdaemon.adapi import ADAPI
from common.decorators import log_call
from collections import deque
from typing import Deque


class FreezerCheck(ADAPI):
    """Monitor freezer power consumption and alert on anomalies."""

    def extract_config(self):
        """Extract and validate configuration from apps.yaml."""
        # Extract configs from apps.yaml
        config = self.args
        self.sample_rate_minutes    = config.get("sample_rate", 5)
        self.check_interval_minutes = config.get("check_interval", 61)
        self.alert_power_min        = config.get("power_alert_min", 25) 
        self.alert_power_max        = config.get("power_alert_max", 100) 
        self.notifier               = config.get("notifier", "notify/notify") # Default notifier

        # Extract entities from apps.yaml
        self.smartplug_power = config["smartplug_freezer"]["entity"]["power"]

    def initialize(self):
        """Initialize the app, state, and schedulers."""
        self.log("----- Initializing FreezerCheck App -----")
        self.extract_config()

        self.num_data_points = int(self.check_interval_minutes / self.sample_rate_minutes)
        self.power_samples: Deque[float] = deque(maxlen=self.num_data_points)

        # Start sampling and checking right away for faster feedback after restarts.
        self.log(f"Sampling power every {self.sample_rate_minutes} minutes.")
        self.run_every(self.on_power_usage_sample, "now", self.sample_rate_minutes * 60)

        # Schedule the first check after one full interval has passed.
        self.log(f"Checking average power every {self.check_interval_minutes} minutes.")
        self.run_in(self.schedule_recurring_check, self.check_interval_minutes * 60)

    @log_call
    def on_power_usage_sample(self, **kwargs):
        """Callback to sample the current power usage from the smartplug."""
        try:
            power_usage = float(self.get_state(self.smartplug_power))
            self.power_samples.append(power_usage)
            self.log(f"[Sample] Current: {power_usage}W")

        except (ValueError, TypeError) as e:
            self.log(f"[ERROR] Could not parse power usage: {e}", level="ERROR")

    @log_call
    def schedule_recurring_check(self, kwargs):
        """Helper to start the recurring check after the first interval."""
        self.run_every(self.on_check_freezer_sample, "now", self.check_interval_minutes * 60)

    @log_call
    def on_check_freezer_sample(self, **kwargs):
        """Callback to check the average power and alert if outside bounds."""
        if len(self.power_samples) != self.num_data_points:
            self.log(f"[WARNING] Not enough data to check. "
                     f"Expected {self.num_data_points} samples, got {len(self.power_samples)}.")
            return

        avg_power = sum(self.power_samples) / self.num_data_points
        self.log(f"[Check] Average power over last {self.check_interval_minutes} mins: {avg_power:.2f}W. "
                 f"Thresholds: min={self.alert_power_min}W, max={self.alert_power_max}W.")

        if avg_power < self.alert_power_min or avg_power > self.alert_power_max:
            self.log(f"[ALERT] Power usage out of bounds: {avg_power:.2f}W")
            self.send_alert(avg_power)
        else:
            self.log("[OK] Freezer power consumption is normal.")

    @log_call
    def send_alert(self, power_avg, **kwargs):
        """Send a notification via the configured notifier service."""
        title = "Freezer Power Alert"
        message = (f"⚠️ Freezer power consumption is abnormal! "
                   f"Average over the last {self.check_interval_minutes} minutes was {power_avg:.2f}W.")
        self.log(f"Sending notification: '{title} - {message}' to {self.notifier}")
        self.call_service(self.notifier, title=title, message=message)