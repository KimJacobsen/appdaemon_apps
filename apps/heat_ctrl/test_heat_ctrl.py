import unittest
from unittest.mock import MagicMock, patch
import os
from datetime import datetime

# As AppDaemon apps are not standard packages, we need to adjust the path
# to allow importing the HeatControl class for testing.
import sys
# Add the 'apps' directory to the path to allow imports of other apps/common libs
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from heat_ctrl import HeatControl

class TestHeatControl(unittest.TestCase):

    def setUp(self):
        """Set up a mock instance of the HeatControl app for testing."""
        # We need to mock the parent ADAPI class and its methods
        # that are called during instantiation or in the method we're testing.
        with patch('appdaemon.adapi.ADAPI.__init__') as mock_adapi_init:
            mock_adapi_init.return_value = None
            self.heat_control = HeatControl(None, None, None, None, None, None, None)
            # Mock the logger to prevent errors and allow inspection
            self.heat_control.log = MagicMock()
            # Mock methods that would otherwise require a running AppDaemon instance
            self.heat_control.get_now = MagicMock()
            self.heat_control.now_is_between = MagicMock()
            self.heat_control.suspend_listener = MagicMock()
            self.heat_control.control_trv = MagicMock()
            self.heat_control.get_trv_setpoint = MagicMock()
            self.heat_control.set_trv_setpoint = MagicMock()
            self.heat_control.get_state = MagicMock()

            # Set default state for the listener
            self.heat_control.active = True

            # Provide a default empty config to prevent crashes on methods that need it
            self.heat_control.periods = {}
            # Define config values needed by control_trv
            self.heat_control.setpoint_on = 24.0
            self.heat_control.setpoint_off = 6.0
            self.heat_control.temp_tol_min = 0.5
            self.heat_control.temp_tol_max = 0.5
            self.heat_control.window_sensor = None # Default to no window sensor


    def test_is_day_in_range_single_day(self):
        """Test matching a single day."""
        self.assertTrue(self.heat_control.is_day_in_range("mon", "mon"))
        self.assertFalse(self.heat_control.is_day_in_range("mon", "tue"))

    def test_is_day_in_range_standard_range(self):
        """Test a standard weekday range (e.g., mon_fri)."""
        day_range = "tue_fri"
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "wed"))
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "tue")) # Edge case: start
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "fri")) # Edge case: end
        self.assertFalse(self.heat_control.is_day_in_range(day_range, "mon"))
        self.assertFalse(self.heat_control.is_day_in_range(day_range, "sat"))

    def test_is_day_in_range_wraparound_range(self):
        """Test a range that wraps around the end of the week (e.g., sat_tue)."""
        day_range = "sat_tue"
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "sun"))
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "mon"))
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "sat")) # Edge case: start
        self.assertTrue(self.heat_control.is_day_in_range(day_range, "tue")) # Edge case: end
        self.assertFalse(self.heat_control.is_day_in_range(day_range, "wed"))
        self.assertFalse(self.heat_control.is_day_in_range(day_range, "fri"))

    def test_is_day_in_range_full_week(self):
        """Test a range that covers the entire week."""
        day_range = "mon_sun"
        all_days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        for day in all_days:
            with self.subTest(day=day):
                self.assertTrue(self.heat_control.is_day_in_range(day_range, day))

    def test_is_day_in_range_invalid_inputs(self):
        """Test invalid day names in the range or current day."""
        # Invalid day in range
        self.assertFalse(self.heat_control.is_day_in_range("foo_bar", "mon"))
        self.heat_control.log.assert_called_with("Invalid day range 'foo_bar' in config.", level="WARNING")

        # Invalid current day
        self.assertFalse(self.heat_control.is_day_in_range("mon_fri", "baz"))
        self.heat_control.log.assert_called_with("Invalid current_day 'baz' provided.", level="WARNING")

    def test_on_temperature_change_triggers_control(self):
        """Test that on_temperature_change correctly triggers TRV control when in a scheduled period."""
        # 1. Setup: Configure the mock app instance
        self.heat_control.periods = {
            "mon": [
                "05:00:00,08:00:00,19"
            ],
            "tue_fri": [
                "09:00:00,13:00:00,19",
                "14:00:00,20:00:00,20.5" # This is the period we'll match
            ]
        }
        # Simulate being on a Wednesday at 3 PM
        self.heat_control.get_now.return_value = datetime(2024, 1, 17, 15, 0, 0) # A Wednesday
        # Make the mock return False for the first period and True for the second,
        # to ensure we test the correct logic branch.
        self.heat_control.now_is_between.side_effect = [False, True]

        # 2. Action: Call the method with a new temperature
        new_temp = "18.5"
        self.heat_control.on_temperature_change("sensor.temp", "state", "19.0", new_temp)

        # 3. Assertions: Verify the correct methods were called
        self.heat_control.now_is_between.assert_any_call("14:00:00", "20:00:00")
        self.heat_control.suspend_listener.assert_called_once()
        # Check that control_trv was called with the correct setpoint and the new temperature
        self.heat_control.control_trv.assert_called_once_with("20.5", new_temp)

    def test_on_temperature_change_outside_scheduled_period(self):
        """Test that no action is taken when the temperature changes outside a scheduled period."""
        # 1. Setup: Configure the mock app instance
        self.heat_control.periods = {
            "mon": [
                "09:00:00,17:00:00,21"
            ]
        }
        # Simulate being on a Monday at 6 PM, which is outside the schedule
        self.heat_control.get_now.return_value = datetime(2024, 1, 15, 18, 0, 0) # A Monday
        # Ensure now_is_between returns False, as we are outside the time window
        self.heat_control.now_is_between.return_value = False

        # 2. Action: Call the method with a new temperature
        new_temp = "18.5"
        self.heat_control.on_temperature_change("sensor.temp", "state", "19.0", new_temp)

        # 3. Assertions: Verify that no control actions were taken
        self.heat_control.suspend_listener.assert_not_called()
        self.heat_control.control_trv.assert_not_called()

    def test_control_trv_turns_heat_on(self):
        """Test that control_trv turns the heat on when the temperature is too low."""
        # 1. Setup
        target_temp = "20.0"
        current_temp = "19.4" # Below the lower bound of 19.5
        self.heat_control.get_trv_setpoint.return_value = str(self.heat_control.setpoint_off)

        # 2. Action
        self.heat_control.control_trv(target_temp, current_temp)

        # 3. Assertion
        self.heat_control.set_trv_setpoint.assert_called_once_with(self.heat_control.setpoint_on)

    def test_control_trv_turns_heat_off(self):
        """Test that control_trv turns the heat off when the temperature is too high."""
        # 1. Setup
        target_temp = "20.0"
        current_temp = "20.6" # Above the upper bound of 20.5
        self.heat_control.get_trv_setpoint.return_value = str(self.heat_control.setpoint_on)

        # 2. Action
        self.heat_control.control_trv(target_temp, current_temp)

        # 3. Assertion
        self.heat_control.set_trv_setpoint.assert_called_once_with(self.heat_control.setpoint_off)

    def test_control_trv_does_nothing_within_tolerance(self):
        """Test that control_trv takes no action when the temperature is within the tolerance band."""
        # 1. Setup
        target_temp = "20.0"
        current_temp = "20.2" # Within the 19.5-20.5 tolerance band
        self.heat_control.get_trv_setpoint.return_value = str(self.heat_control.setpoint_on)

        # 2. Action
        self.heat_control.control_trv(target_temp, current_temp)

        # 3. Assertion
        self.heat_control.set_trv_setpoint.assert_not_called()

    def test_control_trv_turns_off_when_window_is_open(self):
        """Test that control_trv turns the heat off if the window sensor is 'on'."""
        self.heat_control.window_sensor = "binary_sensor.window"
        self.heat_control.get_state.return_value = "on"
        # Simulate that the TRV is currently ON, so it needs to be turned OFF.
        self.heat_control.get_trv_setpoint.return_value = str(self.heat_control.setpoint_on)
        self.heat_control.control_trv("20.0", "19.0") # Temp is low, but window is open
        self.heat_control.set_trv_setpoint.assert_called_once_with(self.heat_control.setpoint_off)

if __name__ == '__main__':
    unittest.main()
