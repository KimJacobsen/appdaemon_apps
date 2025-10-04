from datetime import datetime, timedelta, timezone
import requests
import debugpy
from appdaemon.adapi import ADAPI
from appdaemon.plugins.hass import Hass
from common.decorators import log_call, handle_errors, time_it

class Energy(Hass):

    def initialize(self):
        '''
        debugpy.listen(("0.0.0.0", 5678))
        self.log("debugpy listening on port 5678")
        debugpy.wait_for_client()
        '''

        self.log("Energy initialized")
        self.extract_config() 
        #self.hass = self.get_plugin_api("HASS")
        
        # New data is only available once pr. day @13:30('ish)
        self.run_daily(self.run_job, self.run_time)
        self.log(f"Scheduled daily cost calculation at {self.run_time}")
        
        self.run_in(self.run_job, 5) # Run on startup to check for gaps

    def extract_config(self):
        config = self.args
        self.access_token   = config["access_token"]
        self.metering_point = config["metering_point"]
        self.product_id     = config["product_id"]
        self.supplier_id    = config["supplier_id"]
        self.aggregation    = config["aggregation"]
        self.run_time       = config["run_time"]
        self.sensor_kwh     = config["sensor_kwh"]
        self.sensor_cost    = config["sensor_cost"]
        self.lookback_days  = config.get("lookback_days", 3) # Default to 3 days

    @log_call
    @handle_errors()
    def run_job(self, kwargs):
        """Main job to find and process missing data days."""
        missing_days = self.find_missing_stat_days()

        if not missing_days: 
            self.log("No missing data found in the lookback period. All up to date.")
            return

        self.log(f"Found {len(missing_days)} missing day(s) of data: {sorted(missing_days)}")

        # Get a single token for all operations in this run
        if not (token := self.get_refresh_token()):
            return
        
        # Process each missing day 
        for day in sorted(missing_days):
            self.log(f"--- Processing data for missing day: {day.strftime('%Y-%m-%d')} ---")
            if not self.process_single_day(day, token):
                self.log(f"Failed to process data for {day}. Will retry on the next run.", level="WARNING")
                # Stop processing further days if one fails, to avoid API rate limits
                break

    # def find_missing_stat_days(self):
    #     """Queries HA statistics to find days with no data."""
    #     today = datetime.now(timezone.utc).date()
    #     start_date = today - timedelta(days=self.lookback_days)
    #     end_date = today - timedelta(days=1) # We only check up to yesterday

    #     # Generate a set of all dates we expect to have data for
    #     expected_dates = {start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)}

    #     try:
    #         # Use AppDaemon's get_history to fetch state changes for the sensor
    #         start_datetime = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    #         history = self.get_history(entity_id=self.sensor_kwh, start_time=start_datetime)
    #         #self.log(history)
    #     except Exception as e:
    #         self.log(f"Error fetching history from Home Assistant: {e}", level="ERROR")
    #         return set()

    #     # Create a set of dates that already have data
    #     existing_dates = set()
    #     if history and history[0]:
    #         for state_change in history[0]:
    #             # The 'last_changed' timestamp is in UTC
    #             dt_object = self.parse_iso_date(state_change['last_changed'])
    #             existing_dates.add(dt_object.date())

    #     missing_dates = expected_dates - existing_dates
    #     return missing_dates

    def find_missing_stat_days(self): 
        """Queries HA statistics to find days with no data."""
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=self.lookback_days)
        end_date = today - timedelta(days=1) # We only check up to yesterday

        # Generate a set of all dates we expect to have data for
        expected_dates = {start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)}

        try:
            # Use recorder.get_statistics service to fetch statistics
            start_datetime = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
            end_datetime = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
            
            response = self.call_service(
                "recorder/get_statistics",
                statistic_ids=[self.sensor_kwh],
                start_time=start_datetime.isoformat(),
                end_time=end_datetime.isoformat(),
                period="day",
                types=["state"]
            )
            
            # Extract statistics from response
            statistics = response.get(self.sensor_kwh, [])
            self.log(statistics)
            
        except Exception as e:
            self.log(f"Error fetching statistics from Home Assistant: {e}", level="ERROR")
            return set()

        # Create a set of dates that already have data
        existing_dates = set()
        for stat in statistics:
            # The 'start' timestamp represents the beginning of the period
            dt_object = self.parse_iso_date(stat['start'])
            existing_dates.add(dt_object.date())

        missing_dates = expected_dates - existing_dates
        return missing_dates

    def process_single_day(self, target_date, token):
        """Fetches and processes data for a single day."""
        date_from = target_date.strftime("%Y-%m-%d")
        date_to = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")

        if not (consumption := self.get_consumption(token, date_from, date_to)):
            return False
        
        return self.calculate_cost(consumption)

    @log_call
    @handle_errors()
    def get_refresh_token(self):
        url = "https://api.eloverblik.dk/customerapi/api/token"
        headers = {
            "accept": "application/json",
            "api-version": "1.0",
            "Authorization": f"Bearer {self.access_token}"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            token = response.json().get("result")
            self.log(f"Refresh token received: ****{token[-10:]}")
            return token
        elif response.status_code == 429:
            # API is rate-limiting, so we stop the current job. It will retry on the next scheduled run.
            self.log(f"Token error: {response.status_code} {response.text}. API rate limit hit. "
                     f"Aborting current run. Will retry later.", level="WARNING")
            return None
        self.log(f"Token error: {response.status_code} {response.text}", level="ERROR")
        return None

    @log_call
    @handle_errors()
    def get_consumption(self, token, date_from, date_to):
        url = f"https://api.eloverblik.dk/customerapi/api/meterdata/gettimeseries/{date_from}/{date_to}/{self.aggregation}"
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
            "api-version": "1.0",
            "Authorization": f"Bearer {token}"
        }
        payload = {
            "meteringPoints": {"meteringPoint": [self.metering_point]}
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            self.log(f"Failed to get consumption data for {date_from}: {response.status_code} {response.text}", level="WARNING")
            return None

        periods = response.json()["result"][0]["MyEnergyData_MarketDocument"]["TimeSeries"][0]["Period"]
        return [
            {
                "date": (self.parse_iso_date(period["timeInterval"]["start"]) + timedelta(hours=i)).isoformat(),
                "amount": float(point["out_Quantity.quantity"])
            }
            for period in periods
            for i, point in enumerate(period["Point"])
        ]

    @log_call
    @handle_errors()
    @time_it
    def calculate_cost(self, consumption):
        url = "https://stromligning.dk/api/calculations/cost"
        headers = {"Content-Type": "application/json", "accept": "application/json"}
        payload = {
            "productId": self.product_id,
            "supplierId": self.supplier_id,
            "consumption": consumption
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            self.log(f"Cost API failed: {response.status_code} {response.text}", level="WARNING")
            return False

        results = response.json().get("details", [])
        return self.prepare_data(results)
    
    
    @log_call
    @handle_errors()
    @time_it
    def prepare_data(self, data):
        total_payout = 0.0
        breakdown = []

        for entry in data:
            date          = entry["date"]
            kwh           = round(entry["amount"]["value"], 3)
            total         = round(entry["cost"]["total"], 2)
            unit_price    = round(float(total) / float(kwh), 6) if kwh else 0.0
            total_payout += total
            breakdown.append({
                "date": date,
                "kwh": kwh,
                "unit_price": unit_price,
                "total": total
            })
            self.log(f"{date} | kWh: {kwh:.3f} | Unit: {unit_price:.4f} DKK | Total: {total:.2f} DKK")
        
        return self.send_statistics_to_ha(breakdown)
    
    @log_call
    @handle_errors()
    @time_it
    def send_statistics_to_ha(self, breakdown: list):
        """
        Convert hourly breakdown into Home Assistant long-term statistics and send via recorder.import_statistics.
        Assumes sensors:
        - sensor.energy_kwh (state_class: total_increasing)
        - sensor.energy_cost (state_class: total)
        """
        energy_stats = []
        cost_stats = []

        running_kwh = 0.0
        running_cost = 0.0

        for item in breakdown:
            # Parse time to ISO format with timezone awareness
            date_val = item["date"]
            if isinstance(date_val, datetime):
                start_time = date_val.isoformat()
            else:
                start_time = self.parse_iso_date(date_val).isoformat()
            kwh         = item["kwh"]
            cost        = item["total"]

            running_kwh += kwh
            running_cost += cost

            energy_stats.append({
                "start": start_time,
                "sum": running_kwh,     # cumulative total
                "state": kwh,           # hourly consumption
                "min": kwh,
                "max": kwh,
                "last_reset": None      # None = part of a continuous series
            })

            cost_stats.append({
                "start": start_time,
                "sum": running_cost,    # cumulative total cost
                "state": cost,          # hourly cost
                "min": cost,
                "max": cost,
                "last_reset": None
            })

        # Build the payloads per sensor
        payloads = []

        if energy_stats:
            payloads.append({
                "statistic_id": "sensor.energy_kwh",
                "name": "Energy kWh",
                "unit_of_measurement": "kWh",
                "source": "recorder",
                "has_mean": False,
                "has_sum": True,
                "stats": energy_stats
            })

        if cost_stats:
            payloads.append({
                "statistic_id": "sensor.energy_cost",
                "name": "Energy Cost",
                "unit_of_measurement": "DKK/kWh",
                "source": "recorder",
                "has_mean": False,
                "has_sum": True,
                "stats": cost_stats
            })

        if payloads:
            total_points = len(energy_stats) + len(cost_stats)
            self.log(f"Sending {total_points} statistics to Home Assistant...")

            for payload in payloads:
                self.call_service("recorder/import_statistics", **payload)

            self.log("Statistics sent successfully.")
            return True
        else:
            self.log("No statistics to send.", level="WARNING")
            return False


    
    def parse_iso_date(self, iso_str):
        """Parses an ISO 8601 string into a timezone-aware datetime object."""
        # Handles both 'Z' and '+HH:MM' timezone formats, with or without microseconds
        if iso_str.endswith('Z'):
            iso_str = iso_str[:-1] + '+00:00'
        return datetime.fromisoformat(iso_str)