"""Touch-ring logic.

The touch hardware itself is 2026-only and the simulator never fires it, so the
gesture machine is driven here against a fake `touch_states` dict shaped exactly
like the firmware's -- {key: [is_down, counter]}.
"""

import unittest

from skyscope import conf as C, model, radar_view, touch

from tests.test_view import OBS, RecordingRenderer, contact_at, snapshot_with


class FakeRing(touch.TouchRing):
    """A TouchRing wired to a dict we control instead of the frontboard."""

    def __init__(self):
        touch.TouchRing.__init__(self)
        self._keys = tuple("TOUCH%02d" % (i + 1) for i in range(touch.SECTORS))
        self._states = {key: [False, 0] for key in self._keys}
        self.available = True

    def _load(self):
        # Skip the hardware probe entirely.
        pass

    def press(self, sector):
        for i, key in enumerate(self._keys):
            self._states[key][0] = i == sector

    def lift(self):
        for key in self._keys:
            self._states[key][0] = False


class TestSectorMaths(unittest.TestCase):
    def test_sector_one_is_north(self):
        self.assertEqual(touch.bearing_of(0), 0.0)
        self.assertEqual(touch.sector_of(0.0), 0)

    def test_sectors_step_thirty_degrees(self):
        self.assertEqual(touch.bearing_of(3), 90.0)
        self.assertEqual(touch.bearing_of(6), 180.0)
        self.assertEqual(touch.bearing_of(9), 270.0)

    def test_round_trip(self):
        for sector in range(touch.SECTORS):
            self.assertEqual(touch.sector_of(touch.bearing_of(sector)), sector)

    def test_boundaries_snap_to_the_nearest_sector(self):
        self.assertEqual(touch.sector_of(14.9), 0)
        self.assertEqual(touch.sector_of(15.1), 1)
        self.assertEqual(touch.sector_of(44.9), 1)

    def test_wraps_around_north(self):
        self.assertEqual(touch.sector_of(350.0), 0)
        self.assertEqual(touch.sector_of(359.9), 0)
        self.assertEqual(touch.sector_of(345.1), 0)
        self.assertEqual(touch.sector_of(344.9), 11)

    def test_bearings_outside_one_turn_normalise(self):
        # A bearing outside 0-360 must land in the same sector as its
        # equivalent inside it, boundary rounding included.
        for raw, equivalent in ((365.0, 5.0), (-15.0, 345.0), (-20.0, 340.0),
                                (720.5, 0.5)):
            self.assertEqual(touch.sector_of(raw), touch.sector_of(equivalent), raw)
        self.assertEqual(touch.sector_of(-20.0), 11)

    def test_in_sector(self):
        self.assertTrue(touch.in_sector(88.0, 3))
        self.assertFalse(touch.in_sector(120.0, 3))


class TestGestures(unittest.TestCase):
    def setUp(self):
        self.ring = FakeRing()

    def test_idle(self):
        self.assertIsNone(self.ring.update(50))
        self.assertIsNone(self.ring.sector)

    def test_touch_reports_the_sector(self):
        self.ring.press(4)
        self.ring.update(50)
        self.assertEqual(self.ring.sector, 4)

    def test_short_touch_never_holds(self):
        self.ring.press(4)
        for _ in range(5):
            self.assertIsNone(self.ring.update(50))
        self.ring.lift()
        self.assertIsNone(self.ring.update(50))

    def test_hold_fires_once_past_the_threshold(self):
        self.ring.press(4)
        self.ring.update(50)  # registers the sector
        fired = []
        for _ in range(40):
            result = self.ring.update(50)
            if result is not None:
                fired.append(result)
        self.assertEqual(fired, [4])

    def test_sliding_to_a_new_sector_restarts_the_hold(self):
        self.ring.press(4)
        for _ in range(10):
            self.ring.update(50)
        self.ring.press(5)
        self.assertIsNone(self.ring.update(50))
        self.assertEqual(self.ring.sector, 5)
        # The new sector needs its own full hold.
        fired = [self.ring.update(50) for _ in range(30)]
        self.assertIn(5, [f for f in fired if f is not None])

    def test_release_clears_the_sector(self):
        self.ring.press(2)
        self.ring.update(50)
        self.ring.lift()
        self.ring.update(50)
        self.assertIsNone(self.ring.sector)

    def test_second_hold_needs_a_release_first(self):
        self.ring.press(2)
        for _ in range(40):
            self.ring.update(50)
        self.ring.lift()
        self.ring.update(50)
        self.ring.press(2)
        self.ring.update(50)
        fired = [f for f in (self.ring.update(50) for _ in range(30)) if f is not None]
        self.assertEqual(fired, [2])

    def test_unavailable_ring_does_nothing(self):
        ring = FakeRing()
        ring.available = False
        ring.press(3)
        self.assertIsNone(ring.update(5000))
        self.assertIsNone(ring.sector)

    def test_release_method_resets_state(self):
        self.ring.press(7)
        self.ring.update(50)
        self.ring.release()
        self.assertIsNone(self.ring.sector)


class TestConfig(unittest.TestCase):
    def test_default_mode(self):
        self.assertEqual(C.validate({})["touch_mode"], touch.MODE_SCRUB)
        self.assertEqual(C.validate({})["touch_alerts"], [])

    def test_unknown_mode_falls_back(self):
        self.assertEqual(C.validate({"touch_mode": "wiggle"})["touch_mode"],
                         touch.MODE_SCRUB)

    def test_every_mode_is_accepted(self):
        for mode in touch.MODES:
            self.assertEqual(C.validate({"touch_mode": mode})["touch_mode"], mode)

    def test_armed_sectors_are_sorted_and_deduplicated(self):
        cfg = C.validate({"touch_alerts": [5, 1, 5, 1]})
        self.assertEqual(cfg["touch_alerts"], [1, 5])

    def test_out_of_range_sectors_are_dropped(self):
        cfg = C.validate({"touch_alerts": [0, 12, -1, 11, "x", True, None]})
        self.assertEqual(cfg["touch_alerts"], [0, 11])

    def test_garbage_alerts_value(self):
        self.assertEqual(C.validate({"touch_alerts": "north"})["touch_alerts"], [])

    def test_every_mode_has_a_label_and_hint(self):
        for mode in touch.MODES:
            self.assertTrue(touch.MODE_LABELS[mode])
            self.assertTrue(touch.MODE_HINTS[mode])


class TestRotation(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()

    def _glyph_centre(self):
        poly = [c for c in self.r.calls if c[0] == "poly"][0]
        xs = [p[0] for p in poly[1]]
        ys = [p[1] for p in poly[1]]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def test_north_up_by_default(self):
        self.assertEqual(self.view.screen_bearing(90.0), 90.0)

    def test_rotation_moves_the_chosen_bearing_to_the_top(self):
        self.view.rotation = 90.0
        self.assertEqual(self.view.screen_bearing(90.0), 0.0)
        self.assertEqual(self.view.screen_bearing(180.0), 90.0)
        self.assertEqual(self.view.screen_bearing(0.0), 270.0)

    def test_an_east_contact_draws_at_the_top_when_course_up_east(self):
        self.view.rotation = 90.0
        self.view.draw(self.r, snapshot_with([contact_at("E1", 90.0, 20.0)]))
        x, y = self._glyph_centre()
        self.assertTrue(abs(x) <= 3.0, x)
        self.assertTrue(y < -40.0, y)

    def test_header_announces_the_course(self):
        self.view.rotation = 90.0
        self.view.draw(self.r, snapshot_with([]))
        self.assertTrue(any("^090" in t for t in self.r.texts()), self.r.texts())

    def test_north_up_header_has_no_course(self):
        self.view.draw(self.r, snapshot_with([]))
        self.assertFalse(any("^" in t for t in self.r.texts()))

    def test_header_fits_the_round_panel_in_every_mode(self):
        # The header sits near the top edge, where the panel is only a chord
        # wide -- a long one gets clipped by the bezel.
        import math

        half_chord = math.sqrt(120.0 ** 2 - abs(radar_view.HEADER_Y) ** 2)
        for units in ("aviation", "metric"):
            for rotation in (0.0, 90.0, 270.0):
                self.cfg["units"] = units
                self.view.rotation = rotation
                r = RecordingRenderer()
                self.view.draw(r, snapshot_with([], radius_km=200.0))
                header = [c for c in r.calls
                          if c[0] == "text" and c[1].startswith("R ")][0]
                width = len(header[1]) * header[5] * 0.55
                self.assertLessEqual(width, half_chord * 2,
                                     (header[1], units, rotation))


class TestSectorFilter(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()
        self.snap = snapshot_with([
            contact_at("NORTH", 0.0, 10.0),
            contact_at("EAST", 90.0, 12.0),
            contact_at("SOUTH", 180.0, 14.0),
        ])

    def test_no_filter_shows_everything(self):
        self.assertEqual(len(self.view.visible_contacts(self.snap)), 3)

    def test_filter_keeps_only_its_sector(self):
        self.view.filter_sector = touch.sector_of(90.0)
        visible = self.view.visible_contacts(self.snap)
        self.assertEqual([c.icao for c in visible], ["EAST"])

    def test_filtered_scope_draws_one_glyph(self):
        self.view.filter_sector = touch.sector_of(180.0)
        self.view.draw(self.r, self.snap)
        self.assertEqual(len([c for c in self.r.calls if c[0] == "poly"]), 2)
        self.assertIn("SOUTH", self.r.texts())
        self.assertNotIn("NORTH", self.r.texts())

    def test_empty_sector_says_clear_not_no_contacts(self):
        self.view.filter_sector = touch.sector_of(270.0)
        self.view.draw(self.r, self.snap)
        self.assertIn("SECTOR CLEAR", self.r.texts())
        self.assertNotIn("NO CONTACTS", self.r.texts())

    def test_status_line_names_the_filtered_bearing(self):
        self.view.filter_sector = touch.sector_of(90.0)
        self.view.draw(self.r, self.snap, age_s=3)
        self.assertIn("090  1 AC  3s", self.r.texts())

    def test_filter_survives_rotation(self):
        self.view.filter_sector = touch.sector_of(90.0)
        self.view.rotation = 90.0
        visible = self.view.visible_contacts(self.snap)
        # Filtering is by true bearing, so rotating the display must not
        # change which aircraft are in the sector.
        self.assertEqual([c.icao for c in visible], ["EAST"])


class TestWedgeAndArmedMarkers(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()

    def test_no_wedge_when_nothing_is_touched(self):
        self.view.draw(self.r, snapshot_with([]))
        wedges = [c for c in self.r.calls if c[0] == "poly"]
        self.assertEqual(wedges, [])

    def test_active_sector_draws_a_wedge(self):
        self.view.active_sector = 3
        self.view.draw(self.r, snapshot_with([]))
        wedges = [c for c in self.r.calls if c[0] == "poly"]
        self.assertEqual(len(wedges), 1)
        # It starts at the centre and reaches the outer ring.
        pts = wedges[0][1]
        self.assertEqual(pts[0], (0.0, 0.0))
        for x, y in pts[1:]:
            self.assertAlmostEqual((x * x + y * y) ** 0.5, radar_view.R_SCREEN, places=3)

    def test_wedge_points_at_the_sector_bearing(self):
        self.view.active_sector = touch.sector_of(90.0)
        self.view.draw(self.r, snapshot_with([]))
        pts = [c for c in self.r.calls if c[0] == "poly"][0][1]
        # Centre of the arc should be due east: +x, y near zero.
        mid = pts[1 + (len(pts) - 1) // 2]
        self.assertGreater(mid[0], 80.0)
        self.assertTrue(abs(mid[1]) < 20.0, mid)

    def test_armed_sectors_thicken_the_outer_ring(self):
        self.view.armed_sectors = (0,)
        self.view.draw(self.r, snapshot_with([]))
        arc = [c for c in self.r.calls
               if c[0] == "line"
               and abs((c[1] ** 2 + c[2] ** 2) ** 0.5 - radar_view.R_SCREEN) < 0.01
               and abs((c[3] ** 2 + c[4] ** 2) ** 0.5 - radar_view.R_SCREEN) < 0.01]
        self.assertTrue(arc, "expected an arc drawn on the outer ring")
        # Sector 0 is north, so the arc sits above the centre.
        for call in arc:
            self.assertLess(call[2], 0)

    def test_armed_arc_stays_inside_the_range_header(self):
        # The header lives just outside the ring; the arc must not reach it.
        self.view.armed_sectors = (0,)
        self.view.draw(self.r, snapshot_with([]))
        for call in [c for c in self.r.calls if c[0] == "line"]:
            for x, y in ((call[1], call[2]), (call[3], call[4])):
                self.assertLessEqual((x * x + y * y) ** 0.5,
                                     radar_view.R_SCREEN + 0.01)

    def test_pulse_only_advances_when_something_is_armed(self):
        self.view.update(400)
        self.assertEqual(self.view._pulse_ms, 0)
        self.view.armed_sectors = (2,)
        self.view.update(400)
        self.assertEqual(self.view._pulse_ms, 400)


class TestLedSlots(unittest.TestCase):
    """The ring maps bearings onto twelve LEDs the same way the firmware does."""

    def test_mapping(self):
        # Imported lazily: app.py needs firmware modules, so this only runs
        # where they exist. The maths is duplicated here deliberately.
        def led_slot(bearing):
            return int((bearing % 360.0 + 15) // 30) % 12

        self.assertEqual(led_slot(0.0), 0)
        self.assertEqual(led_slot(90.0), 3)
        self.assertEqual(led_slot(180.0), 6)
        self.assertEqual(led_slot(270.0), 9)
        self.assertEqual(led_slot(350.0), 0)
        self.assertEqual(led_slot(359.9), 0)
        for sector in range(touch.SECTORS):
            self.assertEqual(led_slot(touch.bearing_of(sector)), sector)


class TestAlertSelection(unittest.TestCase):
    """The 'nearest aircraft in this sector' rule the scrub gesture relies on."""

    def test_contacts_are_distance_sorted_so_first_match_is_nearest(self):
        contacts = model.prepare(
            [contact_at("FAR", 90.0, 30.0), contact_at("NEAR", 90.0, 6.0)],
            OBS[0], OBS[1], 40.0, 30,
        )
        sector = touch.sector_of(90.0)
        first = [c for c in contacts if touch.sector_of(c.bearing) == sector][0]
        self.assertEqual(first.icao, "NEAR")


if __name__ == "__main__":
    unittest.main()
