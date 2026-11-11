import octoprint.plugin
import os
from octoprint_physical_control.hardware import HardwareController


class PhysicalControlPlugin(octoprint.plugin.StartupPlugin,
                             octoprint.plugin.EventHandlerPlugin,
                             octoprint.plugin.SettingsPlugin,
                             octoprint.plugin.TemplatePlugin,
                             octoprint.plugin.SimpleApiPlugin):
    """
    Main plugin class integrating OctoPrint APIs with physical hardware controls.
    """

    def __init__(self):
        self.hardware = None

    def on_after_startup(self) -> None:
        """Called upon OctoPrint startup. Initializes the hardware controller."""
        self._logger.info("Initializing Physical Control Plugin.")
        self.hardware = HardwareController(
            shutdown_callback=self._perform_shutdown,
            button_pin=int(self._settings.get(["pin_button"])),
            led_pin=int(self._settings.get(["pin_led"])),
            buzzer_pin=int(self._settings.get(["pin_buzzer"])),
            brightness_status=int(self._settings.get(["brightness_status"])),
            brightness_events=int(self._settings.get(["brightness_events"])),
            brightness_bed=int(self._settings.get(["brightness_bed"])),
            rgb_split=bool(self._settings.get(["rgb_split"])),
            enable_ble=bool(self._settings.get(["enable_ble"]))
        )

    def _perform_shutdown(self) -> None:
        """Executes the system command to shut down the host machine."""
        self._logger.warning("Shutdown button pressed. Shutting down system now.")
        os.system("/usr/bin/sudo /sbin/shutdown -h now")

    def get_settings_defaults(self) -> dict:
        """Returns default settings for the plugin."""
        return {
            "buzz_on_start": True,
            "buzz_on_finish": True,
            "buzz_start_pattern": "short",
            "buzz_finish_pattern": "long",
            "buzz_error_pattern": "intermittent",
            "buzz_intermittent_pause": 0.5,
            "active_print_pattern": "cycle",
            "brightness_status": 100,
            "color_status_idle": "#ffffff",
            "brightness_events": 100,
            "color_events_heating": "#0000ff",
            "color_events_ready": "#ff0000",
            "brightness_bed": 100,
            "rgb_split": False,
            "enable_ble": True,
            "pin_button": 11,
            "pin_led": 13,
            "pin_buzzer": 15
        }

    def get_template_configs(self):
        return [
            dict(type="settings", name="Physical Control", custom_bindings=False)
        ]

    def on_event(self, event: str, payload: dict) -> None:
        """
        Handles print events to drive LED and buzzer indicators.
        
        :param event: Name of the event triggered by OctoPrint
        :param payload: Data associated with the event
        """
        if not self.hardware:
            return

        pause_duration = float(self._settings.get(["buzz_intermittent_pause"]))

        if event == "Connected":
            self.hardware.set_led_connected()
        elif event == "Disconnected":
            self.hardware.set_led_disconnected()
        elif event == "PrinterStateChanged":
            state_text = payload.get("state", {}).get("text", "")
            if state_text == "Heating":
                self.hardware.set_rgb_heating()
            elif state_text == "Operational":
                self.hardware.set_rgb_idle()
        elif event == "MetadataUpdated":
            temps = self._printer.get_current_temperatures()
            bed_temps = temps.get("bed", {})
            current_temp = bed_temps.get("actual", 0.0)
            target_temp = bed_temps.get("target", 0.0)
            if target_temp is None: target_temp = 0.0
            if current_temp is None: current_temp = 0.0
            self.hardware.set_rgb_bed_heating(current=current_temp, target=target_temp)
        elif event == "PrintStarted":
            if bool(self._settings.get(["buzz_on_start"])):
                pattern = self._settings.get(["buzz_start_pattern"])
                self.hardware.buzz_pattern(pattern_name=pattern, pause_duration=pause_duration)
            rgb_pattern = self._settings.get(["active_print_pattern"])
            self.hardware.set_rgb_cycling(pattern=rgb_pattern)
        elif event == "PrintDone":
            if bool(self._settings.get(["buzz_on_finish"])):
                pattern = self._settings.get(["buzz_finish_pattern"])
                self.hardware.buzz_pattern(pattern_name=pattern, pause_duration=pause_duration)
            self.hardware.set_rgb_ready()
        elif event == "PrintFailed" or event == "PrintCancelled":
            pattern = self._settings.get(["buzz_error_pattern"])
            self.hardware.buzz_pattern(pattern_name=pattern, pause_duration=pause_duration)
            self.hardware.set_rgb_ready()

    def get_api_commands(self):
        return dict(
            turn_off_leds=[],
            change_rgb_color=["color"],
            toggle_ble=["enabled"]
        )

    def on_api_command(self, command, data):
        if not self.hardware:
            return

        if command == "turn_off_leds":
            self.hardware.set_rgb_off()
        elif command == "change_rgb_color":
            self.hardware.set_rgb_manual(data.get('color'))
        elif command == "toggle_ble":
            enabled = bool(data.get('enabled'))
            if enabled:
                self.hardware.start_ble()
            else:
                self.hardware.stop_ble()
            self._settings.set(["enable_ble"], enabled)
            self._settings.save()

    def __plugin_unload__(self):
        if self.hardware:
            self.hardware.cleanup()

__plugin_name__ = "Physical Control"
__plugin_pythoncompat__ = ">=3,<4"

def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = PhysicalControlPlugin()
