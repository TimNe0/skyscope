import json
import unittest

from skyscope import adsb, fixtures, geo, model


class TestObjectWalker(unittest.TestCase):
    def test_finds_every_aircraft(self):
        objects = list(adsb.iter_aircraft(fixtures.SAMPLE_RESPONSE))
        self.assertEqual(len(objects), 5)
        for src in objects:
            # Every yielded slice must be valid JSON on its own.
            json.loads(src)

    def test_handles_the_aircraft_key_variant(self):
        body = '{"now":1,"aircraft":[{"hex":"a"},{"hex":"b"}],"resultCount":2}'
        self.assertEqual(len(list(adsb.iter_aircraft(body))), 2)

    def test_steps_over_nested_arrays(self):
        # mlat/tisb are arrays inside each object; a naive bracket scan would
        # end the walk on the first one.
        body = '{"ac":[{"hex":"a","mlat":[],"tisb":["x"]},{"hex":"b","mlat":[]}]}'
        found = [json.loads(s)["hex"] for s in adsb.iter_aircraft(body)]
        self.assertEqual(found, ["a", "b"])

    def test_braces_inside_strings_do_not_split_objects(self):
        body = '{"ac":[{"hex":"a","t":"we{ird}"},{"hex":"b"}]}'
        found = [json.loads(s)["hex"] for s in adsb.iter_aircraft(body)]
        self.assertEqual(found, ["a", "b"])

    def test_escaped_quote_inside_a_string(self):
        body = '{"ac":[{"hex":"a","r":"G-\\"AB"},{"hex":"b"}]}'
        found = [json.loads(s)["hex"] for s in adsb.iter_aircraft(body)]
        self.assertEqual(found, ["a", "b"])

    def test_nested_object_does_not_end_the_aircraft_early(self):
        body = '{"ac":[{"hex":"a","x":{"y":1}},{"hex":"b"}]}'
        found = [json.loads(s)["hex"] for s in adsb.iter_aircraft(body)]
        self.assertEqual(found, ["a", "b"])

    def test_empty_array(self):
        self.assertEqual(list(adsb.iter_aircraft('{"ac":[],"total":0}')), [])

    def test_missing_array_yields_nothing(self):
        self.assertEqual(list(adsb.iter_aircraft('{"error":"nope"}')), [])

    def test_truncated_body_does_not_hang(self):
        self.assertEqual(list(adsb.iter_aircraft('{"ac":[{"hex":"a"')), [])

    def test_garbage_is_safe(self):
        self.assertEqual(list(adsb.iter_aircraft("")), [])
        self.assertEqual(list(adsb.iter_aircraft("not json at all")), [])

    def test_walker_matches_a_full_json_parse(self):
        walked = [json.loads(s) for s in adsb.iter_aircraft(fixtures.SAMPLE_RESPONSE)]
        whole = json.loads(fixtures.SAMPLE_RESPONSE)["ac"]
        self.assertEqual(walked, whole)


class TestParse(unittest.TestCase):
    def test_sample_payload(self):
        contacts, total = adsb.parse(fixtures.SAMPLE_RESPONSE)
        # Five aircraft in, one without a position, so four contacts out.
        self.assertEqual(total, 5)
        self.assertEqual(len(contacts), 4)

    def test_callsign_is_trimmed(self):
        contacts, _ = adsb.parse(fixtures.SAMPLE_RESPONSE)
        self.assertEqual(contacts[0].callsign, "SWR48X")

    def test_ground_altitude_string(self):
        contacts, _ = adsb.parse(fixtures.SAMPLE_RESPONSE)
        ground = [c for c in contacts if c.icao == "406d1f"][0]
        self.assertTrue(ground.on_ground)
        self.assertIsNone(ground.alt_ft)

    def test_aircraft_without_position_is_dropped(self):
        contacts, _ = adsb.parse(fixtures.SAMPLE_RESPONSE)
        self.assertEqual([c for c in contacts if c.icao == "aa8f21"], [])

    def test_stale_aircraft_is_dropped(self):
        body = '{"ac":[{"hex":"a","lat":52.0,"lon":-2.0,"seen_pos":900.0}]}'
        contacts, total = adsb.parse(body)
        self.assertEqual(total, 1)
        self.assertEqual(contacts, [])

    def test_missing_optional_fields_are_none(self):
        body = '{"ac":[{"hex":"a","lat":52.0,"lon":-2.0}]}'
        contacts, _ = adsb.parse(body)
        c = contacts[0]
        self.assertIsNone(c.gs_kt)
        self.assertIsNone(c.track_deg)
        self.assertEqual(c.label, "a")

    def test_out_of_range_coordinates_rejected(self):
        body = '{"ac":[{"hex":"a","lat":99.0,"lon":-2.0}]}'
        self.assertEqual(adsb.parse(body)[0], [])

    def test_label_falls_back_through_reg_to_hex(self):
        body = '{"ac":[{"hex":"abc123","r":"G-TEST","lat":52.0,"lon":-2.0}]}'
        self.assertEqual(adsb.parse(body)[0][0].label, "G-TEST")

    def test_one_malformed_object_does_not_kill_the_batch(self):
        body = '{"ac":[{"hex":"a","lat":52.0,"lon":-2.0},{bad},{"hex":"c","lat":52.1,"lon":-2.0}]}'
        contacts, total = adsb.parse(body)
        self.assertEqual(total, 3)
        self.assertEqual([c.icao for c in contacts], ["a", "c"])


class TestProviders(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(
            adsb.AdsbLol().url(52.039554, -2.378344, 22),
            "https://api.adsb.lol/v2/lat/52.039554/lon/-2.378344/dist/22",
        )
        self.assertEqual(
            adsb.AdsbFi().url(51.5972, 0.671394, 25),
            "https://opendata.adsb.fi/api/v3/lat/51.5972/lon/0.671394/dist/25",
        )
        self.assertEqual(
            adsb.AirplanesLive().url(51.5, -0.1, 10),
            "https://api.airplanes.live/v2/point/51.5/-0.1/10",
        )

    def test_whole_number_coordinates_do_not_end_in_a_dot(self):
        self.assertEqual(adsb.AdsbLol().url(52.0, 0.0, 5),
                         "https://api.adsb.lol/v2/lat/52/lon/0/dist/5")

    def test_lookup_falls_back_to_the_first_provider(self):
        self.assertIs(adsb.get_provider("nope"), adsb.PROVIDERS[0])
        self.assertEqual(adsb.get_provider("adsb_fi").key, "adsb_fi")

    def test_radius_is_capped(self):
        # 2000 km would be ~1080 nm; the request must clamp to the hard cap.
        captured = {}

        class Spy(adsb.AdsbLol):
            def url(self, lat, lon, radius_nm):
                captured["nm"] = radius_nm
                return "http://example.invalid"

        try:
            Spy().fetch(52.0, -2.0, 2000.0)
        except adsb.ProviderError:
            pass
        self.assertEqual(captured["nm"], adsb.MAX_RADIUS_NM)


class TestErrorMessages(unittest.TestCase):
    def test_http_statuses_map_to_readable_text(self):
        self.assertEqual(adsb._status_message(429), "RATE LIMITED")
        self.assertEqual(adsb._status_message(403), "API KEY NEEDED")
        self.assertEqual(adsb._status_message(401), "API KEY NEEDED")
        self.assertEqual(adsb._status_message(503), "SERVER ERROR 503")
        self.assertEqual(adsb._status_message(418), "HTTP 418")

    def test_exceptions_map_to_readable_text(self):
        class ConnectionError_(Exception):
            pass

        class ReadTimeout(Exception):
            pass

        class SSLError(Exception):
            pass

        self.assertEqual(adsb._short_error(ReadTimeout()), "TIMEOUT")
        self.assertEqual(adsb._short_error(SSLError()), "TLS ERROR")
        self.assertEqual(adsb._short_error(OSError()), "NETWORK ERROR")
        self.assertEqual(adsb._short_error(MemoryError()), "OUT OF MEMORY")

    def test_requests_connection_error(self):
        try:
            import requests
        except ImportError:
            self.skipTest("requests not installed")
        self.assertEqual(
            adsb._short_error(requests.exceptions.ConnectionError()), "NO CONNECTION"
        )

    def test_every_message_fits_the_status_line(self):
        for status in (401, 403, 429, 500, 503, 418):
            self.assertLessEqual(len(adsb._status_message(status)), 18)


class TestMockProvider(unittest.TestCase):
    def test_generates_parseable_traffic_around_the_observer(self):
        provider = fixtures.MockProvider()
        body = provider.fetch(51.5972, 0.671394, 40.0)
        contacts, total = adsb.parse(body)
        self.assertEqual(total, len(fixtures._TRAFFIC))
        self.assertEqual(len(contacts), total)
        for c in contacts:
            distance = geo.haversine_km(51.5972, 0.671394, c.lat, c.lon)
            self.assertLess(distance, 70.0)

    def test_traffic_moves_between_polls(self):
        provider = fixtures.MockProvider()
        first = adsb.parse(provider.fetch(51.0, 0.0, 40.0))[0]
        second = adsb.parse(provider.fetch(51.0, 0.0, 40.0))[0]
        self.assertNotEqual(
            [(c.lat, c.lon) for c in first],
            [(c.lat, c.lon) for c in second],
        )


class TestPrepare(unittest.TestCase):
    def _contact(self, icao, lat, lon, seen_pos=0.0):
        return model.Contact(icao, icao, lat, lon, seen_pos=seen_pos)

    def test_sorted_by_distance_and_capped(self):
        contacts = [
            self._contact("far", 52.4, -2.378344),
            self._contact("near", 52.05, -2.378344),
            self._contact("mid", 52.2, -2.378344),
        ]
        kept = model.prepare(contacts, 52.039554, -2.378344, 100.0, 2)
        self.assertEqual([c.icao for c in kept], ["near", "mid"])

    def test_outside_the_radius_is_dropped(self):
        contacts = [self._contact("far", 53.5, -2.378344)]
        self.assertEqual(model.prepare(contacts, 52.039554, -2.378344, 40.0, 30), [])

    def test_quiet_contacts_are_dropped(self):
        contacts = [self._contact("quiet", 52.05, -2.378344, seen_pos=120.0)]
        self.assertEqual(model.prepare(contacts, 52.039554, -2.378344, 40.0, 30), [])

    def test_staleness_flag(self):
        fresh = self._contact("a", 52.05, -2.378344, seen_pos=5.0)
        stale = self._contact("b", 52.06, -2.378344, seen_pos=45.0)
        self.assertFalse(fresh.stale)
        self.assertTrue(stale.stale)


if __name__ == "__main__":
    unittest.main()
