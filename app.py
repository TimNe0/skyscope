"""SkyScope -- a live ADS-B flight radar for the EMF Tildagon / Spaceagon badge.

Screens: the radar scope, a settings menu, a per-aircraft detail page and an
about page. One async task does everything; network polling happens inside
run(), which the scheduler only advances while the app is foregrounded, so a
minimised SkyScope costs no battery and no API quota.
"""

import gc
import time

import app
from app_components import Notification, clear_background
from events.input import BUTTON_TYPES, Buttons
from system.eventbus import eventbus
from system.patterndisplay.events import PatternDisable, PatternEnable

from . import adsb, conf as C, fixtures, model, radar_view, touch, units as U
from .render_ctx import CtxRenderer
from .settings_view import SettingsView

VERSION = "0.1.1"
USER_AGENT = "SkyScope-Tildagon/%s (+https://github.com/TimNe0/skyscope)" % VERSION

SCREEN_RADAR = 0
SCREEN_SETTINGS = 1
SCREEN_DETAIL = 2
SCREEN_ABOUT = 3

MAX_BACKOFF_S = 300
# Ignore button state for a moment after launch, so the press that opened the
# app is not read as a command.
STARTUP_GRACE_MS = 400
LED_REFRESH_MS = 1000


class FlightRadarApp(app.App):
    def __init__(self):
        super().__init__()
        self.button_states = Buttons(self)
        self.cfg = C.load()
        self.notification = None

        self.renderer = CtxRenderer()
        self.view = radar_view.RadarView(self.cfg)
        self.snapshot = model.Snapshot(
            state=model.STATE_CONNECTING,
            message="STARTING",
            radius_km=self.cfg["radius_km"],
        )

        self.screen = SCREEN_RADAR
        self.settings = None
        self.selected_icao = None

        # 2026 touch ring. Absent on a 2024 badge and in the simulator, in
        # which case every mode is a no-op.
        self.touch = touch.TouchRing()
        self._active_sector = None
        # Aircraft already reported inside an armed sector, so an alert fires
        # on arrival rather than every poll.
        self._alerted = set()
        self.view.armed_sectors = tuple(self.cfg["touch_alerts"])

        self._held = set()
        self._uptime_ms = 0
        self._last_tick_ms = time.ticks_ms()
        self._next_poll_ms = time.ticks_ms()
        self._backoff_s = 0
        self._errors = 0
        # Set when the firmware has no radio at all (the simulator), which is
        # the only situation where falling back to demo data is honest.
        self._allow_demo = False
        self._demo = None
        self._led_timer = 0

        # External panel, only built when the user selects a hexpansion target.
        self._external = None
        self._external_error = None

        eventbus.emit(PatternDisable())

    # -- lifecycle -----------------------------------------------------------

    async def run(self, render_update):
        await self._connect_wifi(render_update)
        while True:
            now = time.ticks_ms()
            delta = time.ticks_diff(now, self._last_tick_ms)
            self._last_tick_ms = now
            self.update(delta)

            # Dialogs and network lookups parked by the settings menu need the
            # render callback, so they can only run from here.
            if self.settings is not None and self.settings.pending is not None:
                job = self.settings.pending
                self.settings.pending = None
                await self.settings.run_job(job, render_update)

            if self._poll_due(now):
                self.snapshot.state = model.STATE_UPDATING
                await render_update()
                self._poll(time.ticks_ms())

            regained_focus = await render_update()
            if regained_focus:
                # Returning to the foreground: refresh straight away rather
                # than showing however stale the last snapshot has become.
                self._next_poll_ms = time.ticks_ms()
                eventbus.emit(PatternDisable())

    async def _connect_wifi(self, render_update):
        self._set_status(model.STATE_CONNECTING, "CONNECTING WIFI")
        await render_update()
        try:
            import wifi
        except ImportError:
            # No radio (simulator). Live data may still work through the host's
            # network stack, so try it first and only fall back to demo data.
            self._allow_demo = True
            self._set_status(model.STATE_IDLE, "NO WIFI MODULE")
            return
        try:
            if wifi.status():
                connected = True
            else:
                wifi.connect()
                waiter = getattr(wifi, "async_wait", None)
                if waiter is not None:
                    connected = await waiter()
                else:
                    connected = wifi.wait()
        except Exception as exc:
            print("[skyscope] wifi error:", exc)
            connected = False
        if connected:
            self._set_status(model.STATE_IDLE, "READY")
        else:
            self._set_status(model.STATE_ERROR, "NO WIFI - LEFT FOR MENU")
            self._schedule_retry()
        await render_update()

    def _set_status(self, state, message=""):
        self.snapshot.state = state
        self.snapshot.message = message

    # -- polling -------------------------------------------------------------

    def _provider(self):
        if self._demo is not None:
            return self._demo
        return adsb.get_provider(self.cfg["provider"])

    def _poll_due(self, now):
        return time.ticks_diff(now, self._next_poll_ms) >= 0

    def _schedule_next(self, now, seconds):
        self._next_poll_ms = time.ticks_add(now, int(seconds * 1000))

    def _schedule_retry(self):
        interval = self.cfg["interval_s"]
        if self._backoff_s <= 0:
            self._backoff_s = interval
        else:
            self._backoff_s = min(self._backoff_s * 2, MAX_BACKOFF_S)
        self._schedule_next(time.ticks_ms(), self._backoff_s)

    def _poll(self, now):
        cfg = self.cfg
        lat, lon = C.location_of(cfg)
        radius_km = cfg["radius_km"]
        provider = self._provider()
        try:
            contacts, total = provider.poll(
                lat, lon, radius_km, user_agent=USER_AGENT
            )
        except adsb.ProviderError as exc:
            gc.collect()
            self._on_error(exc.message)
            return
        except MemoryError:
            gc.collect()
            self._on_error("OUT OF MEMORY")
            return

        contacts = model.prepare(contacts, lat, lon, radius_km, cfg["max_aircraft"])
        self.snapshot = model.Snapshot(
            contacts=contacts,
            ts_ms=now,
            state=model.STATE_OK,
            message=self._demo.name if self._demo else "",
            total=total,
            obs_lat=lat,
            obs_lon=lon,
            radius_km=radius_km,
        )
        self._errors = 0
        self._backoff_s = 0
        self._schedule_next(now, cfg["interval_s"])
        self._check_alerts()
        if self._active_sector is not None:
            # Keep the touched sector's selection on the aircraft that is
            # nearest *now*, not the one that was nearest a poll ago.
            self._select_in_sector(self._active_sector)
        # The reduced contacts are tiny; what needs collecting is the response
        # body and the per-aircraft dicts the parser churned through.
        gc.collect()

    def _on_error(self, message):
        self._errors += 1
        if self._allow_demo and self._demo is None and self._errors >= 2:
            # Simulator with no usable network: show synthetic traffic rather
            # than an empty scope, and say so on the status line.
            self._demo = fixtures.MockProvider()
            self._errors = 0
            self._backoff_s = 0
            self._next_poll_ms = time.ticks_ms()
            self.notification = Notification("Using demo data")
            return
        self.snapshot.state = model.STATE_ERROR
        self.snapshot.message = message
        self._schedule_retry()

    def force_refresh(self):
        self._backoff_s = 0
        self._errors = 0
        self._next_poll_ms = time.ticks_ms()

    # -- per-frame update ----------------------------------------------------

    def update(self, delta):
        self._uptime_ms += delta
        if self.notification is not None:
            self.notification.update(delta)
            if self.notification._is_closed():
                self.notification = None

        if self.screen == SCREEN_SETTINGS:
            # Menu owns the buttons while it is open.
            self.settings.update(delta)
            return

        self.view.update(delta)
        if self.screen == SCREEN_RADAR:
            self._handle_touch(delta)
        self._handle_buttons()
        self._drive_leds(delta)

    # -- touch ring ----------------------------------------------------------

    def _handle_touch(self, delta):
        mode = self.cfg["touch_mode"]
        if not self.touch.available or mode == touch.MODE_OFF:
            return
        held = self.touch.update(delta)

        sector = self.touch.sector
        if sector is not None and sector != self._active_sector:
            # Sliding a finger round the bezel scrubs through bearings.
            self._active_sector = sector
            self.view.active_sector = sector
            self._select_in_sector(sector)
        if held is not None:
            self._touch_hold(held, mode)

    def _select_in_sector(self, sector):
        """Select the nearest aircraft in a sector; contacts are distance-sorted."""
        for c in self.snapshot.contacts:
            if touch.sector_of(c.bearing) == sector:
                self.selected_icao = c.icao
                return
        self.selected_icao = None

    def _touch_hold(self, sector, mode):
        label = U.fmt_bearing(touch.bearing_of(sector))
        if mode == touch.MODE_ALERTS:
            armed = list(self.cfg["touch_alerts"])
            if sector in armed:
                armed.remove(sector)
                self.notification = Notification("Alert off " + label)
            else:
                armed.append(sector)
                armed.sort()
                self.notification = Notification("Watching " + label)
            self.cfg["touch_alerts"] = armed
            self.view.armed_sectors = tuple(armed)
            self._alerted = set()
            C.save(self.cfg)
        elif mode == touch.MODE_FILTER:
            if self.view.filter_sector == sector:
                self.view.filter_sector = None
                self.notification = Notification("Showing all")
            else:
                self.view.filter_sector = sector
                self.notification = Notification("Only " + label)
        elif mode == touch.MODE_COURSE:
            bearing = touch.bearing_of(sector)
            if self.view.rotation == bearing:
                self.view.rotation = 0.0
                self.notification = Notification("North up")
            else:
                self.view.rotation = bearing
                self.notification = Notification(label + " up")

    def _clear_touch_state(self):
        """Undo whatever the ring is currently doing. Returns True if anything was."""
        cleared = False
        for attr, value in (("filter_sector", None), ("rotation", 0.0),
                            ("active_sector", None)):
            if getattr(self.view, attr) != value:
                setattr(self.view, attr, value)
                cleared = True
        if self._active_sector is not None or self.selected_icao is not None:
            cleared = True
        self._active_sector = None
        self.selected_icao = None
        self.touch.release()
        return cleared

    def _check_alerts(self):
        """Notify when an aircraft appears in a sector the user armed."""
        armed = self.cfg["touch_alerts"]
        if not armed:
            self._alerted = set()
            return
        present = set()
        arrival = None
        for c in self.snapshot.contacts:
            if touch.sector_of(c.bearing) in armed:
                present.add(c.icao)
                if arrival is None and c.icao not in self._alerted:
                    arrival = c
        self._alerted = present
        if arrival is not None:
            self.notification = Notification(
                "%s %s" % (arrival.label, U.fmt_bearing(arrival.bearing))
            )

    def _pressed(self, name):
        """Edge-triggered button read.

        Buttons.pressed() would do this, but it only exists on newer firmware;
        tracking held state here keeps the app working on 2024 badges too.
        """
        button = BUTTON_TYPES[name]
        down = self.button_states.get(button)
        if down and name not in self._held:
            self._held.add(name)
            return True
        if not down:
            self._held.discard(name)
        return False

    def _handle_buttons(self):
        if self._uptime_ms < STARTUP_GRACE_MS and not self._held:
            # Swallow the launch press.
            self.button_states.clear()
            return

        if self.screen == SCREEN_DETAIL:
            if self._pressed("CANCEL") or self._pressed("LEFT"):
                self.selected_icao = None
                self.screen = SCREEN_RADAR
            return

        if self.screen == SCREEN_ABOUT:
            if self._pressed("CANCEL") or self._pressed("LEFT"):
                self.screen = SCREEN_RADAR
            return

        if self._pressed("CANCEL"):
            # Escape unwinds the touch ring first, so a filtered or rotated
            # scope is never a trap you can only leave by quitting.
            if not self._clear_touch_state():
                self._shutdown()
            return
        if self._pressed("LEFT"):
            self._open_settings()
            return
        if self._pressed("RIGHT"):
            self.force_refresh()
            self.notification = Notification("Refreshing")
            return
        if self._pressed("CONFIRM"):
            if self.selected_icao is not None and self._selected_contact() is not None:
                # A touched aircraft is the obvious thing for CONFIRM to act
                # on; without one it keeps its original job.
                self.screen = SCREEN_DETAIL
                return
            modes = C.LABEL_MODES
            idx = (modes.index(self.cfg["labels"]) + 1) % len(modes)
            self.cfg["labels"] = modes[idx]
            C.save(self.cfg)
            self.notification = Notification("Labels: " + modes[idx])
            return
        if self._pressed("UP"):
            self._zoom(1)
            return
        if self._pressed("DOWN"):
            self._zoom(-1)

    def _zoom(self, direction):
        radius = C.next_radius(self.cfg["radius_km"], direction)
        if radius == self.cfg["radius_km"]:
            return
        self.cfg["radius_km"] = radius
        C.save(self.cfg)
        self.view.forget_trails()
        # Contacts outside the new ring have to be dropped, so refetch rather
        # than re-projecting a snapshot that was filtered for the old radius.
        self.force_refresh()

    def _shutdown(self):
        self.button_states.clear()
        self._leds_off()
        eventbus.emit(PatternEnable())
        self.minimise()

    # -- screens -------------------------------------------------------------

    def _open_settings(self):
        self.button_states.clear()
        self.settings = SettingsView(
            self,
            self.cfg,
            on_change=self._settings_changed,
            on_close=self._close_settings,
            on_about=self._open_about,
            on_select_contact=self._open_detail,
            contacts_provider=lambda: self.snapshot.contacts,
        )
        self.screen = SCREEN_SETTINGS

    def _close_settings(self):
        self.settings = None
        self._held = set()
        self.button_states.clear()
        if self.screen == SCREEN_SETTINGS:
            self.screen = SCREEN_RADAR

    def _open_about(self):
        self.settings.close()
        self.screen = SCREEN_ABOUT

    def _open_detail(self, icao):
        self.settings.close()
        self.selected_icao = icao
        self.screen = SCREEN_DETAIL

    def _settings_changed(self, keys):
        if "location" in keys or "radius_km" in keys or "provider" in keys:
            self.view.forget_trails()
            self.force_refresh()
        if "display" in keys:
            self._release_external()
        if "led_ring" in keys and not self.cfg["led_ring"]:
            self._leds_off()
        if "touch_mode" in keys:
            # A mode change should not leave the previous mode's filter or
            # rotation stuck on the scope.
            self._clear_touch_state()
        if "touch_alerts" in keys:
            self.view.armed_sectors = tuple(self.cfg["touch_alerts"])
            self._alerted = set()

    # -- external panel ------------------------------------------------------

    def _release_external(self):
        if self._external is not None:
            try:
                self._external.close()
            except Exception:
                pass
        self._external = None
        self._external_error = None

    def _get_external(self):
        """Lazily build the hexpansion panel renderer; None if unavailable."""
        display = self.cfg["display"]
        if display["target"] != "hexpansion" or not C.FEATURE_HEXPANSION_DISPLAY:
            return None
        if self._external is not None or self._external_error is not None:
            return self._external
        try:
            from .render_fb import open_hexpansion_panel

            self._external = open_hexpansion_panel(display["slot"], display["pins"])
        except Exception as exc:
            print("[skyscope] hexpansion panel failed:", exc)
            self._external_error = str(exc)[:22] or "PANEL ERROR"
            self.notification = Notification("Panel not found")
        return self._external

    # -- drawing -------------------------------------------------------------

    def draw(self, ctx):
        ctx.save()
        if self.screen == SCREEN_SETTINGS:
            clear_background(ctx)
            self.settings.draw(ctx)
        else:
            renderer = self.renderer.begin(ctx)
            external = self._get_external()
            if external is not None:
                # Radar goes to the panel; the badge shows a status page so it
                # is never left frozen.
                self._draw_status_page(renderer)
                self._draw_main(external)
            else:
                self._draw_main(renderer)
        # Dialogs register themselves in self.overlays and are invisible until
        # something draws them.
        self.draw_overlays(ctx)
        if self.notification is not None:
            self.notification.draw(ctx)
        ctx.restore()

    def _draw_main(self, renderer):
        if self.screen == SCREEN_DETAIL:
            contact = self._selected_contact()
            if contact is None:
                self.screen = SCREEN_RADAR
            else:
                radar_view.detail_view(
                    renderer, contact, self.cfg["units"], self.cfg["location"]["name"]
                )
                return
        if self.screen == SCREEN_ABOUT:
            radar_view.status_view(renderer, "SkyScope " + VERSION, self._about_lines())
            return
        age_s = 0
        if self.snapshot.ts_ms:
            age_s = time.ticks_diff(time.ticks_ms(), self.snapshot.ts_ms) / 1000.0
        self.view.draw(renderer, self.snapshot, age_s, self.selected_icao)

    def _draw_status_page(self, renderer):
        display = self.cfg["display"]
        if self.snapshot.state == model.STATE_OK:
            status = "%d contacts" % len(self.snapshot.contacts)
        else:
            status = self.snapshot.message or self.snapshot.state.upper()
        lines = [
            "Radar on hexpansion %d" % display["slot"],
            self.cfg["location"]["name"],
            status,
            "",
            "LEFT settings  CANCEL exit",
        ]
        if self._external_error:
            lines[0] = self._external_error
        radar_view.status_view(renderer, "SkyScope", lines)

    def _about_lines(self):
        provider = self._provider()
        return [
            "Live ADS-B flight radar",
            provider.attribution,
            "Poll %ds - radius %dkm" % (self.cfg["interval_s"], self.cfg["radius_km"]),
            self._touch_hint(),
            "MIT licence",
            "",
            "CANCEL to go back",
        ]

    def _selected_contact(self):
        if self.selected_icao is None:
            return None
        for c in self.snapshot.contacts:
            if c.icao == self.selected_icao:
                return c
        return None

    # -- LED ring ------------------------------------------------------------

    def _drive_leds(self, delta):
        if not self.cfg["led_ring"]:
            return
        self._led_timer += delta
        if self._led_timer < 200:
            return
        self._led_timer = 0
        try:
            from tildagonos import tildagonos
        except ImportError:
            self.cfg["led_ring"] = False
            return
        # The OS pattern generator keeps trying to reclaim the ring.
        eventbus.emit(PatternDisable())

        # Build the frame locally rather than reading LEDs back: the composed
        # NeoPixel wrapper is not guaranteed to support __getitem__.
        frame = [(0, 0, 0)] * 12

        # LED indices come from the *screen* bearing, so in course-up mode the
        # ring and the display agree with each other.
        armed = self.cfg["touch_alerts"]
        if armed:
            level = 0.3 + 0.7 * abs(1.0 - (self._uptime_ms % 1600) / 800.0)
            amber = (int(230 * level), int(140 * level), 0)
            for sector in armed:
                bearing = self.view.screen_bearing(touch.bearing_of(sector))
                frame[_led_slot(bearing)] = amber

        contacts = self.view.visible_contacts(self.snapshot)
        if contacts and self.snapshot.ok:
            slot = _led_slot(self.view.screen_bearing(contacts[0].bearing))
            for neighbour in ((slot - 1) % 12, (slot + 1) % 12):
                if frame[neighbour] == (0, 0, 0):
                    frame[neighbour] = (0, 34, 12)
            frame[slot] = (0, 220, 80)

        if self._active_sector is not None:
            bearing = self.view.screen_bearing(touch.bearing_of(self._active_sector))
            frame[_led_slot(bearing)] = (200, 200, 200)

        for i in range(12):
            tildagonos.leds[i + 1] = frame[i]
        tildagonos.leds.write()

    def _leds_off(self):
        try:
            from tildagonos import tildagonos
        except ImportError:
            return
        for i in range(1, 13):
            tildagonos.leds[i] = (0, 0, 0)
        tildagonos.leds.write()

    def _touch_hint(self):
        """One line telling the user what the ring currently does."""
        if not self.touch.available:
            return "Touch ring: 2026 badge only"
        return touch.MODE_HINTS[self.cfg["touch_mode"]]


def _led_slot(bearing):
    """Ring position 0-11 for a bearing. LED 1 is the top of the badge, so the
    caller writes tildagonos.leds[slot + 1]; 0 and 13-18 are other LEDs."""
    return int((bearing % 360.0 + 15) // 30) % 12


__app_export__ = FlightRadarApp



