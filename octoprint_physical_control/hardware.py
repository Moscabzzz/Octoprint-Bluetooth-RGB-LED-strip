import logging
import time
import asyncio
from threading import Thread, Event
from typing import Optional, Callable
from bleak import BleakScanner

try:
    import RPi.GPIO as GPIO
except ImportError:
    # Fallback for non-RPi systems for testing
    import mock

    GPIO = mock.Mock()
    logging.getLogger(__name__).warning("RPi.GPIO not found. Using mock for hardware interfaces.")

# Configure logging
_logger = logging.getLogger(__name__)


class HardwareController:
    """Manages the physical hardware interfaces: button, LED, and buzzer."""

    def __init__(self, shutdown_callback: Callable[[], None], button_pin: int, led_pin: int, buzzer_pin: int, brightness_status: int, brightness_events: int, brightness_bed: int, rgb_split: bool, enable_ble: bool):
        """
        Initializes the hardware controller.

        :param shutdown_callback: Function to call when the shutdown button is pressed.
        :param button_pin: GPIO pin for the shutdown button.
        :param led_pin: GPIO pin for the status LED.
        :param buzzer_pin: GPIO pin for the buzzer.
        :param brightness_status: Brightness level for status LEDs (0-100).
        :param brightness_events: Brightness level for print event LEDs (0-100).
        :param brightness_bed: Brightness level for bed temperature LEDs (0-100).
        :param rgb_split: Whether the RGB strip is split into two sections.
        :param enable_ble: Whether Bluetooth functionality should be enabled.
        """
        self._shutdown_callback = shutdown_callback
        self._shutdown_requested = False

        self._button_pin = button_pin
        self._led_pin = led_pin
        self._buzzer_pin = buzzer_pin
        self._brightness_status = brightness_status
        self._brightness_events = brightness_events
        self._brightness_bed = brightness_bed
        self._rgb_split = rgb_split

        self._led_running = Event()
        self._led_thread: Optional[Thread] = None

        self._rgb_running = Event()
        self._rgb_thread: Optional[Thread] = None

        self._buzz_running = Event()
        self._buzz_thread: Optional[Thread] = None

        self._ble_running = Event()
        self._ble_thread: Optional[Thread] = None
        self._current_ble_state = 0x00  # Standby

        # Variables for bed temperature monitoring
        self._bed_target_temp = 0.0
        self._bed_actual_temp = 0.0

        self._init_gpio()
        if enable_ble:
            self._start_ble_thread()

    def _init_gpio(self) -> None:
        """Configures the GPIO pins."""
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BOARD)

            # Setup shutdown button
            GPIO.setup(self._button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(self._button_pin, GPIO.FALLING, callback=self._handle_button_press, bouncetime=500)

            # Setup LED and Buzzer outputs
            GPIO.setup(self._led_pin, GPIO.OUT)
            GPIO.setup(self._buzzer_pin, GPIO.OUT)

            # Initialize status LED and buzzer to low
            GPIO.output(self._led_pin, GPIO.LOW)
            GPIO.output(self._buzzer_pin, GPIO.LOW)

            _logger.info("GPIO configuration complete.")

        except Exception as e:
            _logger.error(f"Failed to initialize GPIO: {e}")

    def _handle_button_press(self, channel: int) -> None:
        """
        Internal callback for GPIO event detection.
        Executes the provided shutdown callback.
        """
        _logger.info("Physical shutdown button pressed.")
        self._shutdown_callback()

    def set_led_connected(self) -> None:
        """Turn the status LED solidly on."""
        self._shutdown_led_thread()
        GPIO.output(self._led_pin, GPIO.HIGH)
        _logger.debug("LED status set to Connected")

    def set_led_connecting(self) -> None:
        """Start the status LED blinking pattern."""
        self._shutdown_led_thread()
        self._led_running.set()
        self._led_thread = Thread(target=self._blink_led)
        self._led_thread.daemon = True
        self._led_thread.start()
        _logger.debug("LED status set to Connecting/Printing (Blinking)")

    def set_led_disconnected(self) -> None:
        """Turn the status LED off."""
        self._shutdown_led_thread()
        GPIO.output(self._led_pin, GPIO.LOW)
        _logger.debug("LED status set to Disconnected")

    def _blink_led(self) -> None:
        """Blinking loop run in separate thread."""
        while self._led_running.is_set():
            GPIO.output(self._led_pin, GPIO.HIGH)
            time.sleep(0.5)
            GPIO.output(self._led_pin, GPIO.LOW)
            time.sleep(0.5)

    def _shutdown_led_thread(self) -> None:
        """Safely stops the LED blinking and RGB cycling threads."""
        self._led_running.clear()
        if self._led_thread and self._led_thread.is_alive():
            self._led_thread.join(timeout=1.0)

        self._rgb_running.clear()
        if self._rgb_thread and self._rgb_thread.is_alive():
            self._rgb_thread.join(timeout=1.0)

    def _shutdown_buzz_thread(self) -> None:
        """Safely stops the buzzer pattern thread."""
        self._buzz_running.clear()
        if self._buzz_thread and self._buzz_thread.is_alive():
            self._buzz_thread.join(timeout=1.0)

    def set_rgb_color(self, red: bool, green: bool, blue: bool, Section: int = 1) -> None:
        """
        Deprecated. Now controlled via set_ble_state.
        """
        pass

    def set_rgb_manual(self, val: str) -> None:
        """
        Sets RGB via BLE broadcast based on hex color.

        :param val: Hex color string (e.g., "#FF00FF")
        """
        self._shutdown_led_thread()

        try:
            # Parse hex string
            hex_val = val.lstrip('#')
            r, g, b = tuple(int(hex_val[i:i+2], 16) for i in (0, 2, 4))
            
            rgb_state = (r << 16) | (g << 8) | b
            self.set_ble_state(rgb_state)

            _logger.debug(f"RGB manual state broadcast set to {val} (R:{r}, G:{g}, B:{b})")

        except Exception as e:
            _logger.error(f"Failed to set manual RGB color '{val}': {e}")

    def set_rgb_off(self) -> None:
        """Turns off all RGB LED strip sections."""
        self._shutdown_led_thread()
        self.set_ble_state(0x00)
        _logger.debug("All RGB sections turned off via BLE")

    def set_rgb_idle(self) -> None:
        """Set RGB strip to static white for idle."""
        self._shutdown_led_thread()
        self.set_ble_state(0xFFFFFF)
        _logger.debug("RGB set to white (idle) via BLE")

    def set_rgb_heating(self) -> None:
        """Set RGB strip to blue for heating."""
        self._shutdown_led_thread()
        self.set_ble_state(0x01)
        _logger.debug("RGB set to blue (heating) via BLE")

    def set_rgb_bed_heating(self, current: float, target: float) -> None:
        """
        Updates the bed heating status RGB.
        Calculates and sets the color based on heating progress.

        :param current: Current bed temperature
        :param target: Target bed temperature
        """
        self._shutdown_led_thread()
        self._bed_actual_temp = current
        self._bed_target_temp = target
        self._update_bed_temp_led()

    def _update_bed_temp_led(self) -> None:
        """
        Calculates the color for the bed temperature.
        Transitioning from Blue (cold) to Green (warm) to Red (hot).
        """
        if self._bed_target_temp <= 0:
            self.set_ble_state(0x00)
            return

        start_temp = 20.0
        if self._bed_actual_temp < start_temp:
            progress = 0.0
        elif self._bed_actual_temp >= self._bed_target_temp:
            progress = 1.0
        else:
            progress = (self._bed_actual_temp - start_temp) / (self._bed_target_temp - start_temp)

        progress = max(0.0, min(1.0, progress))

        r_intensity = 0
        g_intensity = 0
        b_intensity = 0

        max_v = 255

        if progress < 0.5:
            p2 = progress * 2.0
            r_intensity = 0
            g_intensity = int(p2 * max_v)
            b_intensity = int((1.0 - p2) * max_v)
        else:
            p2 = (progress - 0.5) * 2.0
            r_intensity = int(p2 * max_v)
            g_intensity = int((1.0 - p2) * max_v)
            b_intensity = 0

        rgb_state = (r_intensity << 16) | (g_intensity << 8) | b_intensity
        self.set_ble_state(rgb_state)
        _logger.debug(f"Bed Temp LED broadcast update: Prog={progress:.2f}, RGB={rgb_state:06X}")

    def set_rgb_ready(self) -> None:
        """Set RGB strip to red for ready."""
        self._shutdown_led_thread()
        self.set_ble_state(0x03)
        _logger.debug("RGB set to red (ready) via BLE")

    def set_rgb_cycling(self, pattern: str = "cycle") -> None:
        """
        Start dynamic RGB color pattern.

        :param pattern: The name of the pattern to execute.
        """
        self._shutdown_led_thread()
        self.set_ble_state(0x02) # Cycle
        _logger.debug(f"RGB set to pattern: {pattern} via BLE")

    def _cycle_rgb(self, pattern: str) -> None:
        """Deprecated."""
        pass

    def buzz(self, duration: float = 0.1) -> None:
        """
        Generates a non-blocking short buzz.

        :param duration: Time in seconds to buzz.
        """
        Thread(target=self._run_buzz, args=(duration,)).start()

    def _run_buzz(self, duration: float) -> None:
        """Synchronous buzz function intended for threaded execution."""
        try:
            GPIO.output(self._buzzer_pin, GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(self._buzzer_pin, GPIO.LOW)
        except Exception as e:
            _logger.error(f"Error during buzzer operation: {e}")

    def buzz_pattern(self, pattern_name: str, pause_duration: float) -> None:
        """
        Starts a non-blocking buzzer pattern in a separate thread.

        :param pattern_name: The name of the pattern to execute.
        :param pause_duration: Time in seconds to pause between intermittent sounds.
        """
        self._shutdown_buzz_thread()
        if pattern_name == "off":
            return
        self._buzz_running.set()
        self._buzz_thread = Thread(target=self._run_buzz_pattern, args=(pattern_name, pause_duration))
        self._buzz_thread.daemon = True
        self._buzz_thread.start()
        _logger.debug(f"Buzzer set to pattern: {pattern_name} with pause {pause_duration}")

    def _run_buzz_pattern(self, pattern_name: str, pause_duration: float) -> None:
        """Threaded buzz pattern execution."""
        try:
            if pattern_name == "short":
                GPIO.output(self._buzzer_pin, GPIO.HIGH)
                time.sleep(0.1)
                GPIO.output(self._buzzer_pin, GPIO.LOW)
            elif pattern_name == "long":
                GPIO.output(self._buzzer_pin, GPIO.HIGH)
                time.sleep(0.5)
                GPIO.output(self._buzzer_pin, GPIO.LOW)
            elif pattern_name == "intermittent":
                for _ in range(3):
                    if not self._buzz_running.is_set():
                        break
                    GPIO.output(self._buzzer_pin, GPIO.HIGH)
                    time.sleep(0.2)
                    GPIO.output(self._buzzer_pin, GPIO.LOW)
                    if self._buzz_running.is_set():
                        time.sleep(pause_duration)
            else:
                _logger.warning(f"Unknown buzzer pattern: {pattern_name}.")
        except Exception as e:
            _logger.error(f"Error during buzzer operation: {e}")

    async def _run_ble_advertisement(self) -> None:
        """Asynchronous BLE advertisement loop."""
        _logger.info("Starting BLE advertisement loop.")
        try:
            from bleak.backends.bluezdbus.advertisement import Advertisement
            ble_advertisement = Advertisement(
                local_name="OctoPrint-Ctrl",
                service_uuids=["0000180F-0000-1000-8000-00805F9B34FB"],
                manufacturer_data={0xFFFF: b'\x00\x00\x00'},
            )
            async with BleakScanner() as scanner:
                while self._ble_running.is_set():
                    state_bytes = self._current_ble_state.to_bytes(3, byteorder='big')
                    ble_advertisement.manufacturer_data = {0xFFFF: state_bytes}
                    await asyncio.sleep(1)
        except Exception as e:
            _logger.error(f"Error in BLE advertisement thread: {e}")

    def _start_ble_thread(self) -> None:
        """Starts the BLE broadcast thread."""
        self._ble_running.set()
        def ble_loop():
            asyncio.run(self._run_ble_advertisement())
        self._ble_thread = Thread(target=ble_loop)
        self._ble_thread.daemon = True
        self._ble_thread.start()

    def _shutdown_ble_thread(self) -> None:
        """Stops the BLE broadcast thread."""
        self._ble_running.clear()
        if self._ble_thread and self._ble_thread.is_alive():
            self._ble_thread.join(timeout=2.0)

    def start_ble(self) -> None:
        """Public method to start the BLE thread directly."""
        if not self._ble_running.is_set():
            _logger.info("Starting BLE thread per API command.")
            self._start_ble_thread()

    def stop_ble(self) -> None:
        """Public method to stop the BLE thread directly."""
        if self._ble_running.is_set():
            _logger.info("Stopping BLE thread per API command.")
            self._shutdown_ble_thread()

    def set_ble_state(self, state_val: int) -> None:
        """Updates the current BLE broadcast state value."""
        self._current_ble_state = state_val
        _logger.debug(f"BLE state set to: {state_val}")

    def cleanup(self) -> None:
        """Cleans up GPIO resources. Should be called explicitly on plugin unload."""
        self._shutdown_led_thread()
        self._shutdown_buzz_thread()
        if self._ble_thread:
            self._shutdown_ble_thread()
        try:
            GPIO.output(self._led_pin, GPIO.LOW)
            GPIO.output(self._buzzer_pin, GPIO.LOW)

            GPIO.cleanup()
            _logger.info("GPIO cleaned up.")
        except Exception as e:
            _logger.error(f"Error during GPIO cleanup: {e}")

if __name__ == "__main__":
    # Test stub for hardware verification
    logging.basicConfig(level=logging.INFO)
    print("Testing HardwareController. Press button to test callback.")

    def test_callback():
        print("Shutdown callback executed!")

    hw = HardwareController(
        shutdown_callback=test_callback,
        button_pin=11,
        led_pin=13,
        buzzer_pin=15,
        brightness_status=100,
        brightness_events=100,
        brightness_bed=100,
        rgb_split=False,
        enable_ble=True
    )
    
    try:
        hw.buzz(0.2)
        print("Blinking for 5 seconds...")
        hw.set_led_connecting()
        time.sleep(5)
        
        hw.buzz(0.2)
        print("Solid on for 5 seconds...")
        hw.set_led_connected()
        time.sleep(5)

        hw.buzz(0.2)
        print("Testing RGB LED Cycle pattern via BLE...")
        hw.set_rgb_cycling("cycle")
        time.sleep(5)
        
        hw.buzz(0.5)
        print("Wait for button press (Ctrl+C to exit)...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        hw.cleanup()
