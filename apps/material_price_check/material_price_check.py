from datetime import datetime, timedelta, timezone
from appdaemon.adapi import ADAPI
from common.decorators import log_call, handle_errors, time_it
import re
import requests
from bs4 import BeautifulSoup


NOTIFICATION_COOLDOWN = timedelta(days=1)

class PriceTracker(ADAPI):
    """
    An AppDaemon app to track product prices and send Home Assistant notifications.
    Configuration is done in the apps.yaml file.
    """

    def extract_config(self):
        # Extract configs from apps.yaml
        config = self.args    
        self.products_to_track      = config.get("products")
        self.price_selector         = config.get("price_selector")
        self.notifier               = config.get("notifier", "notify/notify")
        self.check_interval_hours   = config.get("check_interval_hours", 24)

    def initialize(self):
        """
        Initializes the AppDaemon app, gets configuration, and schedules the price check.
        """
        self.log("--- Starting Price Tracker ---")

        # --- Get configuration from apps.yaml ---
        self.extract_config()
        
        # --- State for notification cooldown ---
        self.last_notified = {}

        # --- Validate configuration ---
        if not self.products_to_track or not self.price_selector:
            self.log("Missing required configuration in apps.yaml ('products' list or 'price_selector'). App will not run.")
            return

        # --- Schedule the price check ---
        # Run the check immediately on startup, then every X hours
        self.run_every(
            self.check_all_prices,
            "now",
            self.check_interval_hours * 3600
        )
        self.log(f"Scheduled to check price every {self.check_interval_hours} hours.")
        self.check_all_prices() # Run once at startup

    def check_all_prices(self, kwargs=None):
        """
        Iterates through the list of products and checks the price for each one.
        """
        self.log(f"--- Running scheduled price check for {len(self.products_to_track)} product(s) ---")
        for product in self.products_to_track:
            self.check_single_product(product)

    def check_single_product(self, product):
        """
        Fetches a single product page, parses it, and checks the price.
        """
        product_url  = product.get("url")
        target_price = float(product.get("target_price"))
        unit         = product.get("unit")
        product_name = product.get("friendly_name", "Unnamed Product")
        entity_id    = f"sensor.price_{self.sanitize_entity_id(product_name)}"

        if not all([product_url, target_price]):
            self.log(f"Skipping a product due to missing 'url' or 'target_price': {product}")
            return

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }

            self.log(f"Fetching '{product_name}'")
            response = requests.get(product_url, headers=headers)
            response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find the price element using the selector
            price_element = soup.select_one(self.price_selector)
            

            if not price_element:
                self.log(f"Could not find the price element for '{product_name}'. The website structure may have changed or the selector is incorrect.")
                return

            # Clean up the price string. Davidsen.dk uses ',' as a decimal separator.
            # We replace it with '.' to convert it to a float.
            price_text = price_element.get_text(strip=True).replace(".", "").replace(",", ".")

            current_price = float(price_text) # Convert to float for comparison
            self.log(f"{product_name} | {current_price=:.2f} {unit} | {target_price=} {unit}")
            
            # Create/update the Home Assistant sensor
            self.set_state(entity_id, state=current_price, attributes={
                "friendly_name": product_name,
                "unit_of_measurement": unit,
                "target_price": target_price,
                "url": product_url,
                "last_updated": self.datetime().isoformat()
            })

            # Check if the price is below the target
            now = self.datetime()
            last_notified_time = self.last_notified.get(entity_id)

            if current_price < target_price and (not last_notified_time or now - last_notified_time > NOTIFICATION_COOLDOWN):
                self.log(f"Price for '{product_name}' is below target! Sending notification...")
                self.send_ha_notification(product_name, current_price, unit, target_price, product_url)
                self.last_notified[entity_id] = now # Update notification timestamp
            elif current_price >= target_price:
                # Reset notification status if price goes back up
                self.last_notified.pop(entity_id, None)


        except requests.exceptions.RequestException as e:
            self.log(f"HTTP Request failed for '{product_name}': {e}")
        except (ValueError, AttributeError) as e:
            self.log(f"Failed to parse price for '{product_name}'. The page structure might be different. Error: {e}")
        except Exception as e:
            self.log(f"An unexpected error occurred while checking '{product_name}': {e}")

    def sanitize_entity_id(self, name: str) -> str:
        """Converts a friendly name to a Home Assistant entity ID-safe string."""
        name = name.lower()
        name = re.sub(r'\s+', '_', name)       # Replace spaces with underscores
        return re.sub(r'[^a-z0-9_]', '', name) # Remove invalid characters

    def send_ha_notification(self, name, price, unit, target_price, url):
        """
        Sends a notification to Home Assistant.
        """
        title = f"Price Drop: {name}"
        message = (
            f"The price for '{name}' is now {price:.2f} {unit}.\n"
            f"This is below your target of {target_price:.2f} {unit}.\n\n"
            f"Buy it here: {url}"
        )

        self.call_service(
            self.notifier,
            title=title,
            message=message
        )
