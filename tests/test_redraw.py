"""The redraw policy.

app.py cannot be imported off-badge, so this exercises the decision logic itself
against a stand-in that mirrors the real _draw_state / _needs_draw pair. If those
two methods change in app.py, this file has to change with them -- the point is
to pin the *policy*: a static scope must not redraw, and anything that moves
must.

The cost this guards is real. Before the change the app redrew ~15 times a
second and issued ~4,600 ctx calls a second, for a display whose data changes
once every 15 seconds.
"""

import unittest

from skyscope import conf as C, model, radar_view, touch

from tests.test_view import OBS, contact_at

SCREEN_RADAR = 0
SCREEN_SETTINGS = 1
SCREEN_DETAIL = 2
SCREEN_ABOUT = 3

IDLE_REDRAW_MS = 1000


class FakeApp:
    """Mirror of the redraw decision in app.FlightRadarApp."""

    def __init__(self, cfg=None):
        self.cfg = cfg or C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.snapshot = model.Snapshot(state=model.STATE_OK, ts_ms=1000)
        self.screen = SCREEN_RADAR
        self.selected_icao = None
        self.notification = None
        self.overlays = []
        self.touch = touch.TouchRing()
        self._route_wanted = None
        self._dirty = True
        self._since_draw_ms = 0
        self._last_draw_state = None

    def _draw_state(self):
        snapshot = self.snapshot
        view = self.view
        return (
            self.screen,
            snapshot.ts_ms, snapshot.state, snapshot.message,
            len(snapshot.contacts),
            self.selected_icao,
            view.active_sector, view.filter_sector, view.rotation,
            view.armed_sectors,
            self.cfg["labels"], self.cfg["radius_km"], self.cfg["units"],
            self._route_wanted is not None,
        )

    def _needs_draw(self):
        if self._dirty or self.notification is not None or self.overlays:
            return True
        if self.screen == SCREEN_SETTINGS:
            return True
        if self.screen == SCREEN_RADAR:
            if self.cfg["sweep"] or self.view.armed_sectors:
                return True
            if self.touch.sector is not None:
                return True
        if self._draw_state() != self._last_draw_state:
            return True
        return (self.screen == SCREEN_RADAR
                and self._since_draw_ms >= IDLE_REDRAW_MS)

    # -- helpers ---------------------------------------------------------

    def drew(self):
        self._dirty = False
        self._since_draw_ms = 0
        self._last_draw_state = self._draw_state()

    def tick(self, ms):
        self._since_draw_ms += ms


class TestIdleScope(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.app.drew()

    def test_settled_scope_does_not_redraw_immediately(self):
        self.app.tick(50)
        self.assertFalse(self.app._needs_draw())

    def test_scope_ticks_once_a_second_for_the_age_counter(self):
        self.app.tick(999)
        self.assertFalse(self.app._needs_draw())
        self.app.tick(2)
        self.assertTrue(self.app._needs_draw())

    def test_a_settled_second_costs_one_redraw_not_fifteen(self):
        draws = 0
        for _ in range(20):          # 20 x 50 ms == 1 second
            self.app.tick(50)
            if self.app._needs_draw():
                draws += 1
                self.app.drew()
        self.assertEqual(draws, 1)

    def test_static_pages_settle_completely(self):
        for screen in (SCREEN_DETAIL, SCREEN_ABOUT):
            self.app.screen = screen
            self.app.drew()
            self.app.tick(60000)
            self.assertFalse(self.app._needs_draw(), screen)


class TestThingsThatMustRedraw(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.app.drew()

    def _changed(self):
        return self.app._needs_draw()

    def test_new_snapshot(self):
        self.app.snapshot = model.Snapshot(state=model.STATE_OK, ts_ms=2000)
        self.assertTrue(self._changed())

    def test_status_change_in_place(self):
        self.app.snapshot.state = model.STATE_UPDATING
        self.assertTrue(self._changed())

    def test_error_message_change(self):
        self.app.snapshot.message = "RATE LIMITED"
        self.assertTrue(self._changed())

    def test_contact_count_change(self):
        self.app.snapshot.contacts = [contact_at("A", 0.0, 5.0)]
        self.assertTrue(self._changed())

    def test_screen_change(self):
        self.app.screen = SCREEN_DETAIL
        self.assertTrue(self._changed())

    def test_selection_change(self):
        self.app.selected_icao = "abc123"
        self.assertTrue(self._changed())

    def test_zoom(self):
        self.app.cfg["radius_km"] = 80
        self.assertTrue(self._changed())

    def test_label_mode(self):
        self.app.cfg["labels"] = "off"
        self.assertTrue(self._changed())

    def test_units(self):
        self.app.cfg["units"] = "metric"
        self.assertTrue(self._changed())

    def test_touch_wedge(self):
        self.app.view.active_sector = 4
        self.assertTrue(self._changed())

    def test_sector_filter(self):
        self.app.view.filter_sector = 4
        self.assertTrue(self._changed())

    def test_course_up_rotation(self):
        self.app.view.rotation = 90.0
        self.assertTrue(self._changed())

    def test_route_lookup_starting_and_finishing(self):
        self.app._route_wanted = "BAW117"
        self.assertTrue(self._changed())
        self.app.drew()
        self.app._route_wanted = None
        self.assertTrue(self._changed())

    def test_notification_animates(self):
        self.app.notification = object()
        self.assertTrue(self._changed())

    def test_open_dialog_animates(self):
        self.app.overlays = [object()]
        self.assertTrue(self._changed())

    def test_menu_animates(self):
        self.app.screen = SCREEN_SETTINGS
        self.app.drew()
        self.assertTrue(self._changed())

    def test_sweep_animates(self):
        self.app.cfg["sweep"] = True
        self.app.drew()
        self.assertTrue(self._changed())

    def test_armed_sectors_pulse(self):
        self.app.view.armed_sectors = (0,)
        self.app.drew()
        self.assertTrue(self._changed())

    def test_finger_on_the_ring(self):
        self.app.touch.sector = 3
        self.app.drew()
        self.assertTrue(self._changed())

    def test_explicit_dirty_flag(self):
        self.app._dirty = True
        self.assertTrue(self._changed())


class TestAnimationsDoNotLeakIntoOtherScreens(unittest.TestCase):
    def test_sweep_does_not_force_redraws_on_the_detail_page(self):
        app = FakeApp()
        app.cfg["sweep"] = True
        app.screen = SCREEN_DETAIL
        app.drew()
        app.tick(60000)
        self.assertFalse(app._needs_draw())

    def test_armed_sectors_do_not_force_redraws_on_the_about_page(self):
        app = FakeApp()
        app.view.armed_sectors = (2,)
        app.screen = SCREEN_ABOUT
        app.drew()
        app.tick(60000)
        self.assertFalse(app._needs_draw())


class TestMaxAircraftSetting(unittest.TestCase):
    def test_choices_are_offered_and_validated(self):
        self.assertIn(30, C.MAX_AIRCRAFT_CHOICES)
        for n in C.MAX_AIRCRAFT_CHOICES:
            self.assertEqual(C.validate({"max_aircraft": n})["max_aircraft"], n)

    def test_capping_reduces_the_contacts_drawn(self):
        contacts = [contact_at("AC%02d" % i, i * 7.0, 5.0 + i) for i in range(40)]
        for cap in C.MAX_AIRCRAFT_CHOICES:
            kept = model.prepare(contacts, OBS[0], OBS[1], 200.0, cap)
            self.assertLessEqual(len(kept), cap)


if __name__ == "__main__":
    unittest.main()
