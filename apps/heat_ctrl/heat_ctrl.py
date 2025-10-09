from appdaemon.adapi import ADAPI
from functools import wraps
from common.decorators import log_call, requires_active_listener
import debugpy


class HeatControl(ADAPI):

    def extract_config(self):
        # Extract configs from apps.yaml
        config = self.args       
        self.periods      = config.get("periods",{})
        self.location     = config.get("location", {})
        self.retry_count  = config.get("retry_count", 3)
        self.max_retries  = config.get("max_retries", 3)
        self.setpoint_on  = float(config.get("TRV_setpoint_on", 24.0))
        self.setpoint_off = float(config.get("TRV_setpoint_off", 6.0))
        self.temp_tol_min = float(config.get("temperature_tollerance_min", 0.5))
        self.temp_tol_max = float(config.get("temperature_tollerance_min", 0.5))        
        self.notifier     = config.get("notifier", "notify/notify")

        # Extract entities from apps.yaml
        self.temp_sensor   = config["meter_temperature"]["entity"]["temperature"]
        self.window_sensor = config.get("window_sensor", {}).get("entity", {}).get("window_sensor")
        
        # Handle multiple TRVs
        self.trv_configs = []        
        for trv in config["trvs"]:
            self.trv_configs.append({
                "entity_id": trv["entities"]["trv"]["entity_id"],
                "attr": trv["entities"]["trv"]["attr"]["temperature"]
            })
       
    
    def initialize(self):  
        
        self.extract_config() 
        if self.location == 'kitchen':
            debugpy.listen(("0.0.0.0", 5678))
            
        self.log(f"----- Initializing HeatControl for {self.location.upper()} -----") 

        # Subscribe to events
        self.log(f"Subscribing to temp sensor: {self.temp_sensor}")
        self.listen_state(self.on_temperature_change, entity_id=self.temp_sensor)

        if self.window_sensor:
            self.log(f"Subscribing to window sensor: {self.window_sensor}")
            self.listen_state(self.on_window_change, entity_id=self.window_sensor)

        # Subscribe to all TRVs
        for trv in self.trv_configs:
            self.log(f"Subscribing to TRV: {trv['entity_id']}")
            self.listen_state(self.on_trv_setpoint_change,
                            entity_id=trv['entity_id'],
                            attribute=trv['attr'])

    # State control
        self.active = True


    def activate_listener(self):
        self.active = True
        self.log(f"LISTENER ACTIVATED")

    def suspend_listener(self):
        self.active = False
        self.log(f"LISTENER SUSPENDED")


    @requires_active_listener
    @log_call
    def on_trv_setpoint_change(self, entity, attribute, old, new, **kwargs):
        if float(new) not in [self.setpoint_on, self.setpoint_off]:
            self.log(f"[OK] Manual override detected (new setpoint: {new}). Reverting...")
            self.suspend_listener()

            if float(old) in [self.setpoint_on, self.setpoint_off]:
                self.set_trv_setpoint(old)
            else:
                self.log(f"[ERROR] Unrecognized previous value: {old}. Turning TRV OFF.")
                self.set_trv_setpoint(self.setpoint_off)
    
    @requires_active_listener
    @log_call
    def on_temperature_change(self, entity, attribute, old, new, **kwargs):
        if new != "unavailable":
            
            today = self.get_now().strftime("%a").lower() 
            
            for period_name, entries in self.periods.items():
                if self.is_day_in_range(period_name, today):
                    for entry in entries:
                        start, end, setpoint = entry.split(",")
                        if self.now_is_between(start, end):
                            self.log(f"[OK] Matched period: {period_name},{start},{end}, setpoint: {setpoint}")
                            self.suspend_listener()
                            self.control_trv(setpoint, new)
                            return
                else:
                    self.log(f"Day is not in range: period_name: {period_name} | today: {today}")
                    self.log(f"Check apps.yaml if period_temperature is definded for this moment")
        else:
            self.log(f"The {entity} is {new}")

    def is_day_in_range(self, day_range: str, current_day: str) -> bool:
        """
        Checks if a given day falls within a specified day range (e.g., "mon" or "tue_fri").
        Handles weekday ranges that wrap around the end of the week (e.g., "sat_mon").
        """
        weekdays = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        
        try:
            current_day_index = weekdays.index(current_day)
        except ValueError:
            self.log(f"Invalid current_day '{current_day}' provided.", level="WARNING")
            return False

        parts = day_range.split('_')
        start_day, end_day = parts[0], parts[-1]

        try:
            start_index, end_index = weekdays.index(start_day), weekdays.index(end_day)
        except ValueError:
            self.log(f"Invalid day range '{day_range}' in config.", level="WARNING")
            return False

        if start_index <= end_index:
            # Standard range, e.g., mon_fri
            return start_index <= current_day_index <= end_index
        else:
            # Wraparound range, e.g., sat_tue
            return current_day_index >= start_index or current_day_index <= end_index

    @requires_active_listener
    @log_call
    def on_window_change(self, entity, attribute, old, new, **kwargs):
        """Callback for when the window sensor state changes."""
        if new == "on":  # Window is open
            self.log(f"[INFO] Window opened in {self.location}. Turning TRV off.")
            self.suspend_listener()
            self.set_trv_setpoint(self.setpoint_off)
        elif new == "off":  # Window is closed
            self.log(f"[INFO] Window closed in {self.location}. Resuming normal control.")
        else:
            self.log(f"[WARNING] Unknown window state: {new}")


    @log_call
    def control_trv(self, target_temp: str, current_temp: str, **kwargs):
        current_setpoint = float(self.get_trv_setpoint())       
        target_temp = float(target_temp)        
        current_temp = float(current_temp)        

        lower_bound = target_temp - self.temp_tol_min
        upper_bound = target_temp + self.temp_tol_max

        self.log(f"{current_setpoint=} | {target_temp=} | {current_temp=} | {lower_bound=} | {upper_bound=}")


        # Check window state before turning on the heat
        if self.window_sensor and self.get_state(self.window_sensor) == "on":
            self.log("[INFO] Window is open. TRV will remain off.")            
            if current_setpoint != self.setpoint_off:
                self.set_trv_setpoint(self.setpoint_off)
            else:
                self.activate_listener()
                return

        if current_setpoint in [self.setpoint_on, self.setpoint_off]:
            if current_temp < lower_bound and current_setpoint != self.setpoint_on:
                self.set_trv_setpoint(self.setpoint_on)
            elif current_temp > upper_bound and current_setpoint != self.setpoint_off:
                self.set_trv_setpoint(self.setpoint_off)
            else:
                self.log(f"[OK] No action: Current TRV setpoint {current_setpoint}C is fine.")
                self.activate_listener()
        else:
            self.log(f"[ERROR] TRV is at unknown state {current_setpoint}C. No action taken.")
            self.activate_listener()

   
    @log_call
    def set_trv_setpoint(self, setpoint: float, **kwargs):
        if not self.active:
            self.log(f"Setting TRV setpoints to {setpoint}C...")
            self.retry_count = 0
            self.expected_setpoint = float(setpoint)
            
            # Set temperature for all TRVs
            for i, trv in enumerate(self.trv_entities):
                self.call_service("climate/set_temperature",
                                entity_id=trv,
                                temperature=self.expected_setpoint)
                if i < len(self.trv_entities) - 1:  # Don't sleep after the last TRV
                    self.run_in(lambda x: None, 1)  # Sleep for 1 second        
            self.run_in(self.verify_trv_setpoint, 6)

    @log_call
    def verify_trv_setpoint(self, **kwargs):
        all_setpoints_correct = True
        error_messages = []

        for trv in self.trv_configs:
            current_setpoint = self.get_state(trv['entity_id'], trv['attr'])
            try:
                current_setpoint = float(current_setpoint)
            except (ValueError, TypeError):
                self.log(f"[ERROR] Failed to read current TRV setpoint for {trv['entity_id']}")
                all_setpoints_correct = False
                error_messages.append(f"Failed to read {trv['entity_id']}")
                continue

            if abs(current_setpoint - self.expected_setpoint) >= 0.1:
                all_setpoints_correct = False
                error_messages.append(f"{trv['entity_id']}: Expected {self.expected_setpoint}C, got {current_setpoint}C")

        if all_setpoints_correct:
            self.log(f"[OK] All TRVs accepted setpoint: {self.expected_setpoint}C")
            self.activate_listener()
        else:
            self.retry_count += 1
            if self.retry_count < self.max_retries:
                self.log(f"[INFO] Retry {self.retry_count}: " + "; ".join(error_messages))
                self.run_in(self.verify_trv_setpoint, 6)
            else:
                self.log(f"[ERROR] Max retries reached. Failed to set some TRVs to {self.expected_setpoint}C.", level="ERROR")
                self.send_ha_notification(error_messages)
                self.activate_listener()

    def send_ha_notification(self, error_messages):
        """Sends a notification to Home Assistant about the failure."""
        title = f"Heat Control Alert: {self.location}"
        message = (f"Failed to set TRV setpoint to {self.expected_setpoint}°C after {self.max_retries} retries.\n"
                f"Errors: {'; '.join(error_messages)}. Check batteries!")
        self.call_service(self.notifier, title=title, message=message)

    def get_trv_setpoint(self):
        # Return the setpoint of the first TRV (they should all be the same)
        return self.get_state(self.trv_configs[0]['entity_id'], self.trv_configs[0]['attr'])