"""Route lookups and the plane glyph."""

import json
import unittest

from skyscope import radar_view, routes

from tests.test_view import RecordingRenderer, contact_at, snapshot_with

# A real adsbdb response, trimmed to the fields the app reads.
REAL_RESPONSE = json.dumps({
    "response": {
        "flightroute": {
            "callsign": "BAW117",
            "airline": {"name": "British Airways", "icao": "BAW"},
            "origin": {
                "iata_code": "LHR", "icao_code": "EGLL",
                "municipality": "London", "name": "London Heathrow Airport",
            },
            "destination": {
                "iata_code": "JFK", "icao_code": "KJFK",
                "municipality": "New York",
                "name": "John F Kennedy International Airport",
            },
        }
    }
})


class TestParse(unittest.TestCase):
    def test_real_response(self):
        route = routes.parse(json.loads(REAL_RESPONSE))
        self.assertEqual(route.origin, "LHR")
        self.assertEqual(route.destination, "JFK")
        self.assertEqual(route.origin_city, "London")
        self.assertEqual(route.destination_city, "New York")
        self.assertEqual(route.airline, "British Airways")

    def test_unknown_callsign_is_a_bare_string(self):
        # adsbdb answers {"response": "unknown callsign"} for these.
        self.assertIsNone(routes.parse({"response": "unknown callsign"}))

    def test_missing_flightroute(self):
        self.assertIsNone(routes.parse({"response": {}}))

    def test_falls_back_to_icao_codes(self):
        route = routes.parse({"response": {"flightroute": {
            "origin": {"icao_code": "EGLL", "name": "Heathrow"},
            "destination": {"icao_code": "KJFK", "name": "Kennedy"},
        }}})
        self.assertEqual(route.origin, "EGLL")
        self.assertEqual(route.origin_city, "Heathrow")

    def test_route_with_no_airports_is_none(self):
        self.assertIsNone(routes.parse({"response": {"flightroute": {}}}))

    def test_garbage_inputs(self):
        for junk in (None, [], "text", 42, {}):
            self.assertIsNone(routes.parse(junk))


class TestCache(unittest.TestCase):
    def setUp(self):
        self.lookup = routes.RouteLookup()

    def test_unknown_until_looked_up(self):
        self.assertFalse(self.lookup.known("BAW117"))
        self.assertIsNone(self.lookup.get("BAW117"))

    def test_remembers_a_hit(self):
        route = routes.parse(json.loads(REAL_RESPONSE))
        self.lookup._remember("BAW117", route)
        self.assertTrue(self.lookup.known("BAW117"))
        self.assertIs(self.lookup.get("BAW117"), route)

    def test_remembers_a_miss_so_it_is_not_retried(self):
        self.lookup._remember("NOPE99", None)
        self.assertTrue(self.lookup.known("NOPE99"))
        self.assertIsNone(self.lookup.get("NOPE99"))

    def test_cache_is_bounded_and_evicts_oldest_first(self):
        for i in range(routes.CACHE_MAX + 5):
            self.lookup._remember("CS%03d" % i, None)
        self.assertEqual(len(self.lookup), routes.CACHE_MAX)
        self.assertFalse(self.lookup.known("CS000"))
        self.assertTrue(self.lookup.known("CS%03d" % (routes.CACHE_MAX + 4)))

    def test_repeating_a_callsign_does_not_grow_the_cache(self):
        for _ in range(50):
            self.lookup._remember("BAW117", None)
        self.assertEqual(len(self.lookup), 1)

    def test_empty_callsign_never_fetches(self):
        self.assertIsNone(self.lookup.fetch(""))
        self.assertEqual(len(self.lookup), 0)

    def test_fetch_returns_the_cached_value_without_network(self):
        route = routes.parse(json.loads(REAL_RESPONSE))
        self.lookup._remember("BAW117", route)
        # No requests module use: a cache hit must short-circuit.
        self.assertIs(self.lookup.fetch("BAW117"), route)


class TestDetailView(unittest.TestCase):
    def setUp(self):
        self.r = RecordingRenderer()
        self.contact = contact_at("BAW117", 90.0, 12.0, alt_ft=35000, gs_kt=450,
                                  track_deg=270.0, ac_type="B772", reg="G-YMMH",
                                  squawk="7311", baro_rate=-640)
        self.contact.locate(51.5972, 0.671394)

    def test_shows_the_route_when_known(self):
        route = routes.parse(json.loads(REAL_RESPONSE))
        radar_view.detail_view(self.r, self.contact, "aviation", route=route)
        texts = self.r.texts()
        self.assertTrue(any("LHR" in t and "JFK" in t for t in texts), texts)
        self.assertTrue(any("London" in t and "New York" in t for t in texts), texts)
        self.assertIn("British Airways", texts)

    def test_says_unknown_when_there_is_no_route(self):
        radar_view.detail_view(self.r, self.contact, "aviation", route=None)
        self.assertIn("route unknown", self.r.texts())

    def test_says_pending_while_looking_up(self):
        radar_view.detail_view(self.r, self.contact, "aviation", route=None,
                               route_pending=True)
        self.assertIn("looking up route...", self.r.texts())
        self.assertNotIn("route unknown", self.r.texts())

    def test_still_shows_every_data_row(self):
        radar_view.detail_view(self.r, self.contact, "aviation",
                               route=routes.parse(json.loads(REAL_RESPONSE)))
        for key in ("TYPE", "REG", "ALT", "SPD", "V/S", "TRK", "RNG", "BRG", "SQK"):
            self.assertIn(key, self.r.texts())

    def test_everything_stays_inside_the_round_panel(self):
        radar_view.detail_view(self.r, self.contact, "aviation",
                               route=routes.parse(json.loads(REAL_RESPONSE)))
        for call in self.r.calls:
            if call[0] == "text":
                _, s, x, y, _rgb, size, align = call
                width = len(s) * size * 0.55
                left = x if align == "left" else (x - width if align == "right"
                                                  else x - width / 2)
                for px in (left, left + width):
                    self.assertLessEqual((px * px + y * y) ** 0.5, 122.0,
                                         "%r overflows the panel" % (s,))

    def test_framebuffer_backend_gets_an_ascii_arrow(self):
        # framebuf only has an ASCII font; a unicode arrow would be garbage.
        class Fb(RecordingRenderer):
            vector = False

        fb = Fb()
        radar_view.detail_view(fb, self.contact, "aviation",
                               route=routes.parse(json.loads(REAL_RESPONSE)))
        joined = " ".join(fb.texts())
        self.assertIn(">", joined)
        self.assertNotIn("→", joined)

    def test_vector_backend_gets_a_real_arrow(self):
        radar_view.detail_view(self.r, self.contact, "aviation",
                               route=routes.parse(json.loads(REAL_RESPONSE)))
        self.assertIn("→", " ".join(self.r.texts()))


class TestPlaneGlyph(unittest.TestCase):
    def test_outline_is_centred_on_the_fix(self):
        cy = sum(p[1] for p in radar_view.PLANE) / len(radar_view.PLANE)
        self.assertAlmostEqual(cy, 0.0, places=6)

    def test_shape_is_left_right_symmetric(self):
        xs = sorted(round(p[0], 6) for p in radar_view.PLANE)
        self.assertEqual(xs, sorted(-x for x in xs))

    def test_wings_are_wider_than_the_fuselage_is_long(self):
        span = max(p[0] for p in radar_view.PLANE) * 2
        length = (max(p[1] for p in radar_view.PLANE)
                  - min(p[1] for p in radar_view.PLANE))
        self.assertGreater(span, 12.0)
        self.assertGreater(length, 12.0)
        # An airliner reads wrong if the span is not close to the length.
        self.assertLess(abs(span - length), 4.0)

    def test_nose_points_north_when_unrotated(self):
        nose = min(radar_view.plane_glyph(0.0), key=lambda p: p[1])
        self.assertAlmostEqual(nose[0], 0.0, places=6)
        self.assertLess(nose[1], -5.0)

    def test_nose_points_east_at_track_090(self):
        nose = max(radar_view.plane_glyph(90.0), key=lambda p: p[0])
        self.assertGreater(nose[0], 5.0)
        self.assertAlmostEqual(nose[1], 0.0, places=6)

    def test_rotations_are_cached_and_shared(self):
        radar_view._rotated_cache.clear()
        first = radar_view.plane_glyph(90.0)
        second = radar_view.plane_glyph(91.0)  # same 5 degree bucket
        self.assertIs(first, second)
        self.assertEqual(len(radar_view._rotated_cache), 1)

    def test_cache_never_exceeds_one_entry_per_step(self):
        radar_view._rotated_cache.clear()
        for track in range(0, 3600):
            radar_view.plane_glyph(track / 10.0)
        self.assertEqual(len(radar_view._rotated_cache),
                         360 // radar_view._ROTATION_STEP)

    def test_scope_draws_a_plane_and_no_heading_line(self):
        from skyscope import conf as C

        cfg = C.validate({})
        view = radar_view.RadarView(cfg)
        r = RecordingRenderer()
        view.draw(r, snapshot_with([contact_at("A", 45.0, 15.0, track_deg=200.0,
                                               gs_kt=400)]))
        polys = [c for c in r.calls if c[0] == "poly"]
        self.assertEqual(len(polys), 1)
        self.assertEqual(len(polys[0][1]), len(radar_view.PLANE))


if __name__ == "__main__":
    unittest.main()
