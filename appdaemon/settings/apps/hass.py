"""Define automations for Home Assistant itself."""

# pylint: disable=attribute-defined-outside-init,import-error,unused-argument

from datetime import timedelta
from enum import Enum
from typing import Union

from packaging import version  # type: ignore

from automation import Automation  # type: ignore
from const import BLACKOUT_END, BLACKOUT_START  # type: ignore


class AutoThemes(Automation):
    """Define a feature to automatically change themes on time of day."""

    class Themes(Enum):
        """Define an enum for themes."""

        default = 'default'
        dark = 'dark_theme'

    @property
    def current_theme(self) -> Union[Enum, None]:
        """Define a property to store the current theme."""
        return self._current_theme

    @current_theme.setter
    def current_theme(self, value: Enum) -> None:
        """Define a setter for the current theme."""
        self.call_service('frontend/set_theme', name=value.value)
        self._current_theme = value

    def initialize(self) -> None:
        """Initialize."""
        super().initialize()

        if not self.now_is_between(BLACKOUT_END, BLACKOUT_START):
            self.current_theme = self.Themes.dark
        else:
            self.current_theme = self.Themes.default

        self.run_daily(
            self.theme_changed,
            self.parse_time(self.properties['light_schedule_time']),
            theme=self.Themes.default,
            constrain_input_boolean=self.enabled_entity_id)
        self.run_daily(
            self.theme_changed,
            self.parse_time(self.properties['dark_schedule_time']),
            theme=self.Themes.dark,
            constrain_input_boolean=self.enabled_entity_id)

    def theme_changed(self, kwargs: dict) -> None:
        """Change the theme to a "day" variant in the morning."""
        self.current_theme = kwargs['theme']


class AutoVacationMode(Automation):
    """Define automated alterations to vacation mode."""

    def initialize(self) -> None:
        """Initialize."""
        super().initialize()

        self.listen_event(
            self.presence_changed,
            'PRESENCE_CHANGE',
            new=self.presence_manager.HomeStates.extended_away.value,
            first=False,
            action='on',
            constrain_input_boolean=self.enabled_entity_id)
        self.listen_event(
            self.presence_changed,
            'PRESENCE_CHANGE',
            new=self.presence_manager.HomeStates.just_arrived.value,
            first=True,
            action='off',
            constrain_input_boolean=self.enabled_entity_id)

    def presence_changed(
            self, event_name: str, data: dict, kwargs: dict) -> None:
        """Alter Vacation Mode based on presence."""
        if (kwargs['action'] == 'on'
                and self.vacation_mode.state == 'off'):
            self.log('Setting vacation mode to "on"')

            self.vacation_mode.state = 'on'
        elif (kwargs['action'] == 'off'
              and self.vacation_mode.state == 'on'):
            self.log('Setting vacation mode to "off"')

            self.vacation_mode.state = 'off'


class BadLoginNotification(Automation):
    """Define a feature to notify me of unauthorized login attempts."""

    def initialize(self) -> None:
        """Initialize."""
        super().initialize()

        self.listen_state(
            self.bad_login_detected,
            self.entities['notification'],
            constrain_input_boolean=self.enabled_entity_id)

    def bad_login_detected(  # pylint: disable=too-many-arguments
            self, entity: Union[str, dict], attribute: str, old: str, new: str,
            kwargs: dict) -> None:
        """Send a notification when there's a bad login attempt."""
        self.log('Registering a hack attempt: {0}'.format(new))

        if new != 'unknown':
            self.notification_manager.send(
                'Hack Attempt', new, target='Aaron')


class DetectBlackout(Automation):
    """Define a feature to manage blackout awareness."""

    def initialize(self) -> None:
        """Initialize."""
        super().initialize()

        if self.now_is_between(BLACKOUT_START, BLACKOUT_END):
            self.turn_on(self.entities['blackout_switch'])
        else:
            self.turn_off(self.entities['blackout_switch'])

        self.run_daily(
            self.boundary_reached,
            self.parse_time(BLACKOUT_START),
            state='on',
            constrain_input_boolean=self.enabled_entity_id)
        self.run_daily(
            self.boundary_reached,
            self.parse_time(BLACKOUT_END),
            state='off',
            constrain_input_boolean=self.enabled_entity_id)

    def boundary_reached(self, kwargs: dict) -> None:
        """Set the blackout sensor appropriately based on time."""
        self.log('Setting blackout sensor: {0}'.format(kwargs['state']))

        if kwargs['state'] == 'on':
            self.turn_on(self.entities['blackout_switch'])
        else:
            self.turn_off(self.entities['blackout_switch'])


class NewVersionNotification(Automation):
    """Define a feature to detect new versions of key apps."""

    def initialize(self) -> None:
        """Initialize."""
        super().initialize()

        self.listen_state(
            self.version_change_detected,
            self.entities['available'],
            constrain_input_boolean=self.enabled_entity_id)

    def version_change_detected(  # pylint: disable=too-many-arguments
            self, entity: Union[str, dict], attribute: str, old: str, new: str,
            kwargs: dict) -> None:
        """Notify me when there's a new app version."""
        new_version = version.parse(
            self.get_state(self.entities['available']))
        installed_version = version.parse(
            self.get_state(self.entities['installed']))

        if new_version > installed_version:
            self.log(
                'New {0} version detected: {1}'.format(
                    self.properties['app_name'], new))

            self.notification_manager.send(
                'New {0}'.format(self.properties['app_name']),
                'Version detected: {0}'.format(new),
                target='Aaron')


class NewTasmotaVersionNotification(NewVersionNotification):
    """Define a feature to detect new versions of Tasmota."""

    @property
    def lowest_tasmota_installed(self) -> version.Version:
        """Get the lowest Tasmota version from all Sonoffs."""
        import requests

        lowest_version = None
        status_uri = 'cm?cmnd=Status%202'
        tasmota_version = None

        for host in self.properties['tasmota_hosts']:
            try:
                json = requests.get('http://{0}/{1}'.format(
                    host, status_uri)).json()
                tasmota_version = json['StatusFWR']['Version']
            except KeyError:
                self.error('Malformed JSON from host {0}'.format(host))
                continue
            except requests.exceptions.ConnectionError:
                self.error('Unable to connect to host: {0}'.format(host))
                continue

            try:
                if lowest_version > tasmota_version:
                    lowest_version = tasmota_version
            except TypeError:
                lowest_version = tasmota_version

        return lowest_version

    def initialize(self) -> None:
        """Initialize."""
        super().initialize()

        self.run_every(
            self.update_tasmota_sensor,
            self.datetime(),
            timedelta(minutes=5).total_seconds())

    def update_tasmota_sensor(self, kwargs: dict) -> None:
        """Update installed Tasmota version every so often."""
        self.set_state(
            'sensor.lowest_tasmota_installed',
            state=str(self.lowest_tasmota_installed),
            attributes={
                'friendly_name': 'Lowest Tasmota Installed',
                'icon': 'mdi:wifi'
            })
