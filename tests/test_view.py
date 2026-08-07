"""Render the radar through a recording renderer and assert on what it drew.

There is no ctx canvas off-badge, so these tests use a stub that records every
primitive. That is enough to check the geometry the spec's acceptance criteria
call for: contacts land where the projection says, labels declutter, and the
status line reports the right thing.
"""

import unittest

from skyscope import adsb, conf as C, fixtures, model, radar_view, units as U


class RecordingRenderer:
    size = 240
    vector = True

    def __init__(self):
        self.calls = []
        self.flushed = 0
        self.batches = 0

    def clear(self, rgb):
        self.calls.append(("clear", rgb))

    def line(self, x0, y0, x1, y1, rgb, w=1):
        self.calls.append(("line", x0, y0, x1, y1, rgb))

    def circle(self, x, y, r, rgb, fill=False, w=1):
        self.calls.append(("circle", x, y, r, rgb, fill))

    def poly(self, pts, rgb, fill=True, w=1):
        self.calls.append(("poly", tuple(pts), rgb, fill))

    def polys(self, shapes, rgb, fill=True, w=1):
        # Recorded as individual polys so tests can keep counting shapes,
        # while `batches` exposes how many draw calls the backend really got.
        self.batches += 1
        for pts in shapes:
            self.poly(pts, rgb, fill, w)

    def text(self, s, x, y, rgb, size=12, align="left"):
        self.calls.append(("text", s, x, y, rgb, size, align))

    def text_width(self, s, size=12):
        return len(s) * size * 0.5

    def flush(self):
        self.flushed += 1

    # -- helpers ---------------------------------------------------------

    def texts(self):
        return [c[1] for c in self.calls if c[0] == "text"]

    def poly_calls(self):
        return [c for c in self.calls if c[0] == "poly"]

    def rings(self):
        return [c for c in self.calls if c[0] == "circle"]


OBS = (51.5972, 0.671394)


def snapshot_with(contacts, radius_km=40.0, state=model.STATE_OK, total=None):
    prepared = model.prepare(contacts, OBS[0], OBS[1], radius_km, 30)
    return model.Snapshot(
        contacts=prepared,
        ts_ms=1000,
        state=state,
        total=len(prepared) if total is None else total,
        obs_lat=OBS[0],
        obs_lon=OBS[1],
        radius_km=radius_km,
    )


def contact_at(icao, bearing_deg, distance_km, **kwargs):
    lat, lon = fixtures._offset(OBS[0], OBS[1], bearing_deg, distance_km)
    return model.Contact(icao, icao, lat, lon, **kwargs)


class TestGrid(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()

    def test_draws_three_rings(self):
        self.view.draw(self.r, snapshot_with([]))
        radii = sorted(c[3] for c in self.r.rings())
        self.assertEqual(radii, list(radar_view.RINGS))

    def test_cardinal_letters_are_present(self):
        self.view.draw(self.r, snapshot_with([]))
        for letter in ("N", "E", "S", "W"):
            self.assertIn(letter, self.r.texts())

    def test_range_label_uses_the_configured_units(self):
        self.view.draw(self.r, snapshot_with([], radius_km=40.0))
        self.assertIn("R 22nm", self.r.texts())

        self.cfg["units"] = U.METRIC
        self.r = RecordingRenderer()
        self.view.draw(self.r, snapshot_with([], radius_km=40.0))
        self.assertIn("R 40km", self.r.texts())

    def test_frame_is_flushed_once(self):
        self.view.draw(self.r, snapshot_with([]))
        self.assertEqual(self.r.flushed, 1)

    def test_empty_scope_says_so(self):
        self.view.draw(self.r, snapshot_with([]))
        self.assertIn("NO CONTACTS", self.r.texts())


class TestContactPlacement(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()

    def _glyph_centre(self):
        poly = self.r.poly_calls()[0]
        xs = [p[0] for p in poly[1]]
        ys = [p[1] for p in poly[1]]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def test_north_contact_is_above_centre(self):
        self.view.draw(self.r, snapshot_with([contact_at("N1", 0.0, 20.0)]))
        x, y = self._glyph_centre()
        self.assertTrue(abs(x) <= 2.0, x)
        self.assertTrue(abs(y - -50.0) <= 2.5, y)

    def test_east_contact_is_right_of_centre(self):
        self.view.draw(self.r, snapshot_with([contact_at("E1", 90.0, 20.0)]))
        x, y = self._glyph_centre()
        self.assertTrue(abs(x - 50.0) <= 2.5, x)
        self.assertTrue(abs(y) <= 2.0, y)

    def test_south_west_contact(self):
        self.view.draw(self.r, snapshot_with([contact_at("SW", 225.0, 28.28)]))
        x, y = self._glyph_centre()
        self.assertTrue(abs(x - -50.0) <= 3.0, x)
        self.assertTrue(abs(y - 50.0) <= 3.0, y)

    def test_contacts_beyond_the_radius_are_not_drawn(self):
        snap = snapshot_with([contact_at("FAR", 45.0, 90.0)], radius_km=40.0)
        self.view.draw(self.r, snap)
        self.assertEqual(self.r.poly_calls(), [])
        self.assertNotIn("FAR", self.r.texts())


class TestLabels(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()
        self.contacts = [
            contact_at("AC%d" % i, i * 30.0, 5.0 + i * 2.0, alt_ft=10000, gs_kt=300,
                       track_deg=90.0)
            for i in range(10)
        ]

    def test_label_count_is_a_hard_cap(self):
        self.cfg["label_count"] = 3
        self.view.draw(self.r, snapshot_with(self.contacts))
        labelled = [t for t in self.r.texts() if t.startswith("AC")]
        self.assertTrue(len(labelled) <= 3, labelled)

    def test_labels_are_taken_in_distance_order(self):
        self.cfg["label_count"] = 3
        self.view.draw(self.r, snapshot_with(self.contacts))
        labelled = [t for t in self.r.texts() if t.startswith("AC")]
        # Whichever survive decluttering, they must be a subsequence of the
        # distance-sorted list -- never a far contact ahead of a near one.
        order = ["AC%d" % i for i in range(10)]
        self.assertEqual(labelled, [n for n in order if n in labelled])

    def test_labels_never_overlap_each_other(self):
        self.cfg["label_count"] = 10
        self.view.draw(self.r, snapshot_with(self.contacts))
        boxes = self.view.label_boxes
        self.assertTrue(len(boxes) >= 2, boxes)
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ax0, ay0, ax1, ay1 = boxes[i]
                bx0, by0, bx1, by1 = boxes[j]
                overlap = ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1
                self.assertFalse(overlap, (boxes[i], boxes[j]))

    def test_labels_stay_inside_the_round_panel(self):
        self.cfg["label_count"] = 10
        self.view.draw(self.r, snapshot_with(self.contacts))
        for x0, y0, x1, y1 in self.view.label_boxes:
            for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                self.assertLessEqual((px * px + py * py) ** 0.5,
                                     radar_view.PANEL_R + 0.001)

    def test_all_contacts_still_get_a_glyph(self):
        self.cfg["label_count"] = 3
        self.view.draw(self.r, snapshot_with(self.contacts))
        self.assertEqual(len(self.r.poly_calls()), 10)

    def test_labels_off_hides_every_label(self):
        self.cfg["labels"] = "off"
        self.view.draw(self.r, snapshot_with(self.contacts))
        self.assertEqual([t for t in self.r.texts() if t.startswith("AC")], [])
        self.assertEqual(self.view.label_boxes, [])

    def test_callsign_mode_omits_altitude_and_speed(self):
        self.cfg["labels"] = "callsign"
        self.view.draw(self.r, snapshot_with(self.contacts[:1]))
        self.assertIn("AC0", self.r.texts())
        self.assertNotIn("10000ft 300kt", self.r.texts())

    def test_full_mode_shows_altitude_and_speed_on_one_line(self):
        self.cfg["labels"] = "full"
        self.view.draw(self.r, snapshot_with(self.contacts[:1]))
        self.assertIn("10000ft 300kt", self.r.texts())

    def test_metric_units_change_the_label_text(self):
        self.cfg["units"] = U.METRIC
        self.view.draw(self.r, snapshot_with(self.contacts[:1]))
        self.assertIn("3048m 154m/s", self.r.texts())


class TestStatusLine(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)
        self.r = RecordingRenderer()

    def test_reports_count_and_age(self):
        snap = snapshot_with([contact_at("A", 0.0, 10.0)])
        self.view.draw(self.r, snap, age_s=7)
        self.assertIn("1 AC  7s", self.r.texts())

    def test_reports_capping(self):
        snap = snapshot_with([contact_at("A", 0.0, 10.0)], total=99)
        self.view.draw(self.r, snap, age_s=0)
        self.assertIn("1/99 AC  0s", self.r.texts())

    def test_error_message_is_shown(self):
        snap = model.Snapshot(state=model.STATE_ERROR, message="RATE LIMITED")
        self.view.draw(self.r, snap)
        self.assertIn("RATE LIMITED", self.r.texts())

    def test_updating_state(self):
        snap = model.Snapshot(state=model.STATE_UPDATING)
        self.view.draw(self.r, snap)
        self.assertIn("UPDATING", self.r.texts())


class TestTrailsAndSweep(unittest.TestCase):
    def setUp(self):
        self.cfg = C.validate({})
        self.view = radar_view.RadarView(self.cfg)

    def test_trails_accumulate_then_cap(self):
        self.cfg["trails"] = True
        for i in range(radar_view.TRAIL_MAX + 4):
            snap = snapshot_with([contact_at("A", 0.0, 10.0 + i)])
            snap.ts_ms = 1000 + i
            self.view.draw(RecordingRenderer(), snap)
        self.assertEqual(len(self.view._trails["A"]), radar_view.TRAIL_MAX)

    def test_trails_for_departed_contacts_are_forgotten(self):
        self.cfg["trails"] = True
        snap = snapshot_with([contact_at("A", 0.0, 10.0)])
        self.view.draw(RecordingRenderer(), snap)
        gone = snapshot_with([contact_at("B", 0.0, 10.0)])
        gone.ts_ms = 2000
        self.view.draw(RecordingRenderer(), gone)
        self.assertNotIn("A", self.view._trails)

    def test_same_snapshot_is_only_recorded_once(self):
        self.cfg["trails"] = True
        snap = snapshot_with([contact_at("A", 0.0, 10.0)])
        for _ in range(4):
            self.view.draw(RecordingRenderer(), snap)
        self.assertEqual(len(self.view._trails["A"]), 1)

    def test_sweep_advances_and_wraps(self):
        self.cfg["sweep"] = True
        self.view.update(1000)
        self.assertTrue(0 < self.view.sweep_deg < 360)
        self.view.update(100000)
        self.assertTrue(0 <= self.view.sweep_deg < 360)

    def test_sweep_stays_put_when_disabled(self):
        self.cfg["sweep"] = False
        self.view.update(1000)
        self.assertEqual(self.view.sweep_deg, 0.0)


class TestDetailView(unittest.TestCase):
    def test_renders_every_field(self):
        r = RecordingRenderer()
        contacts, _ = adsb.parse(fixtures.SAMPLE_RESPONSE)
        c = model.prepare(contacts, 51.53, -1.08, 500.0, 30)[0]
        radar_view.detail_view(r, c, U.AVIATION)
        texts = r.texts()
        for key in ("TYPE", "REG", "ALT", "SPD", "V/S", "TRK", "RNG", "BRG", "SQK"):
            self.assertIn(key, texts)
        self.assertEqual(r.flushed, 1)


class TestEndToEnd(unittest.TestCase):
    def test_mock_provider_through_to_a_drawn_frame(self):
        cfg = C.validate({})
        view = radar_view.RadarView(cfg)
        r = RecordingRenderer()
        provider = fixtures.MockProvider()
        body = provider.fetch(OBS[0], OBS[1], cfg["radius_km"])
        contacts, total = adsb.parse(body)
        contacts = model.prepare(contacts, OBS[0], OBS[1], cfg["radius_km"],
                                 cfg["max_aircraft"])
        snap = model.Snapshot(contacts, 1000, model.STATE_OK, "", total,
                              OBS[0], OBS[1], cfg["radius_km"])
        view.draw(r, snap, age_s=2)
        self.assertTrue(len(r.poly_calls()) >= 3)
        # Every glyph must sit inside the outer ring.
        for poly in r.poly_calls():
            for x, y in poly[1]:
                self.assertLess((x * x + y * y) ** 0.5, radar_view.R_SCREEN + 12)


if __name__ == "__main__":
    unittest.main()
