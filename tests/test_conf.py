import json
import os
import tempfile
import unittest

from skyscope import conf as C, units as U


class TestValidation(unittest.TestCase):
    def test_empty_config_yields_defaults(self):
        cfg = C.validate({})
        self.assertEqual(cfg["radius_km"], C.DEFAULTS["radius_km"])
        self.assertEqual(cfg["location"]["name"], "East Essex Hackspace")
        self.assertAlmostEqual(cfg["location"]["lat"], 51.5972, places=4)
        self.assertAlmostEqual(cfg["location"]["lon"], 0.671394, places=5)

    def test_unknown_keys_are_discarded(self):
        cfg = C.validate({"evil": True, "radius_km": 20})
        self.assertNotIn("evil", cfg)
        self.assertEqual(cfg["radius_km"], 20)

    def test_missing_keys_are_filled_from_defaults(self):
        # A config written by an older version must not lose new settings.
        cfg = C.validate({"radius_km": 10})
        self.assertIn("trails", cfg)
        self.assertIn("pins", cfg["display"])

    def test_radius_is_clamped(self):
        self.assertEqual(C.validate({"radius_km": 5000})["radius_km"], C.MAX_RADIUS_KM)
        self.assertEqual(C.validate({"radius_km": 1})["radius_km"], C.MIN_RADIUS_KM)
        self.assertEqual(C.validate({"radius_km": "big"})["radius_km"], 40)

    def test_interval_never_goes_below_the_etiquette_floor(self):
        self.assertEqual(C.validate({"interval_s": 1})["interval_s"], 15)
        self.assertEqual(C.validate({"interval_s": 30})["interval_s"], 30)

    def test_enums_fall_back(self):
        self.assertEqual(C.validate({"units": "furlongs"})["units"], U.AVIATION)
        self.assertEqual(C.validate({"provider": "opensky"})["provider"], "adsb_lol")
        self.assertEqual(C.validate({"labels": "loud"})["labels"], "full")

    def test_bad_location_falls_back(self):
        cfg = C.validate({"location": {"name": "Nowhere", "lat": 999, "lon": 0}})
        self.assertEqual(cfg["location"]["name"], "East Essex Hackspace")

    def test_good_location_is_kept(self):
        cfg = C.validate({"location": {"name": "Home", "lat": 51.5, "lon": -0.1}})
        self.assertEqual(cfg["location"]["name"], "Home")
        self.assertEqual(cfg["location"]["lat"], 51.5)

    def test_unset_home_stays_unset(self):
        self.assertIsNone(C.validate({})["home"]["lat"])

    def test_display_slot_is_clamped_into_range(self):
        cfg = C.validate({"display": {"target": "hexpansion", "slot": 42}})
        self.assertEqual(cfg["display"]["slot"], 6)
        cfg = C.validate({"display": {"target": "hexpansion", "slot": 0}})
        self.assertEqual(cfg["display"]["slot"], 1)

    def test_non_numeric_display_slot_falls_back_to_the_default(self):
        cfg = C.validate({"display": {"target": "hexpansion", "slot": "left"}})
        self.assertEqual(cfg["display"]["slot"], 2)

    def test_unknown_pin_role_falls_back(self):
        cfg = C.validate({"display": {"pins": {"sck": "HS9", "mosi": "HS4"}}})
        self.assertEqual(cfg["display"]["pins"]["sck"], "HS1")
        self.assertEqual(cfg["display"]["pins"]["mosi"], "HS4")
        self.assertEqual(cfg["display"]["pins"]["dc"], "HS3")

    def test_corrupt_input_types_do_not_raise(self):
        for junk in (None, [], "string", 42):
            self.assertEqual(C.validate(junk)["radius_km"], 40)


class TestPresets(unittest.TestCase):
    def test_east_essex_hackspace_leads_and_is_the_default(self):
        self.assertEqual(C.PRESETS[0][0], "East Essex Hackspace")
        self.assertEqual(C.validate({})["location"]["name"], C.PRESETS[0][0])

    def test_emf_is_second(self):
        self.assertEqual(C.PRESETS[1][0], "EMF (Eastnor Deer Park)")

    def test_emf_coordinates_are_in_eastnor_deer_park(self):
        from skyscope import geo

        _name, lat, lon = C.PRESETS[1]
        # OSM puts Eastnor Deer Park at 52.0380, -2.3780; EMF's own weather
        # example quotes a point ~170 m away. Either is fine, a mistyped
        # coordinate is not.
        self.assertLess(geo.haversine_km(lat, lon, 52.0380, -2.3780), 1.0)

    def test_every_preset_has_sane_coordinates(self):
        from skyscope import geo

        names = set()
        for name, lat, lon in C.PRESETS:
            self.assertTrue(name and isinstance(name, str))
            self.assertNotIn(name, names, "duplicate preset name")
            names.add(name)
            self.assertTrue(geo.valid_lat(lat), name)
            self.assertTrue(geo.valid_lon(lon), name)

    def test_preset_names_fit_the_menu(self):
        # The Menu shrinks long items, but beyond ~28 characters they become
        # unreadable on a 240px round screen.
        for name, _lat, _lon in C.PRESETS:
            self.assertLessEqual(len(name), 28, name)


class TestRadiusSteps(unittest.TestCase):
    def test_zoom_out(self):
        self.assertEqual(C.next_radius(40, 1), 80)
        self.assertEqual(C.next_radius(160, 1), 160)

    def test_zoom_in(self):
        self.assertEqual(C.next_radius(40, -1), 20)
        self.assertEqual(C.next_radius(5, -1), 5)

    def test_between_steps_moves_to_the_next_step_beyond(self):
        self.assertEqual(C.next_radius(60, 1), 80)
        self.assertEqual(C.next_radius(60, -1), 40)


class TestFilePersistence(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.path)

    def tearDown(self):
        for path in (self.path, self.path + ".tmp"):
            if os.path.exists(path):
                os.unlink(path)

    def test_round_trip(self):
        cfg = C.validate({})
        cfg["radius_km"] = 80
        cfg["units"] = U.METRIC
        self.assertTrue(C._save_file(cfg, self.path))
        loaded = C.validate(C._load_file(self.path))
        self.assertEqual(loaded["radius_km"], 80)
        self.assertEqual(loaded["units"], U.METRIC)

    def test_overwrites_an_existing_file(self):
        cfg = C.validate({})
        C._save_file(cfg, self.path)
        cfg["radius_km"] = 10
        C._save_file(cfg, self.path)
        self.assertEqual(C._load_file(self.path)["radius_km"], 10)

    def test_missing_file_returns_none(self):
        self.assertIsNone(C._load_file(self.path))

    def test_corrupt_file_degrades_to_defaults(self):
        with open(self.path, "w") as f:
            f.write("{ this is not json")
        self.assertIsNone(C._load_file(self.path))
        self.assertEqual(C.validate(C._load_file(self.path) or {})["radius_km"], 40)

    def test_saved_config_is_json_serialisable(self):
        json.dumps(C.validate({}))


if __name__ == "__main__":
    unittest.main()
