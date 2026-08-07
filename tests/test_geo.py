import math
import unittest

from skyscope import geo


class TestDistance(unittest.TestCase):
    def test_zero(self):
        self.assertAlmostEqual(geo.haversine_km(52.0, -2.0, 52.0, -2.0), 0.0, places=6)

    def test_one_degree_of_latitude(self):
        # A degree of latitude is ~111.19 km on a sphere of radius 6371 km.
        d = geo.haversine_km(52.0, -2.0, 53.0, -2.0)
        self.assertAlmostEqual(d, 111.195, places=2)

    def test_known_pair_london_paris(self):
        # Reference value from the standard haversine on R=6371: ~343.5 km.
        d = geo.haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        self.assertTrue(340.0 < d < 347.0, d)

    def test_antipodal_does_not_blow_up(self):
        d = geo.haversine_km(0.0, 0.0, 0.0, 180.0)
        self.assertAlmostEqual(d, math.pi * geo.EARTH_R_KM, places=3)


class TestBearing(unittest.TestCase):
    def test_due_north(self):
        self.assertAlmostEqual(geo.initial_bearing(52.0, -2.0, 53.0, -2.0), 0.0, places=4)

    def test_due_south(self):
        self.assertAlmostEqual(geo.initial_bearing(52.0, -2.0, 51.0, -2.0), 180.0, places=4)

    def test_due_east_on_the_equator(self):
        self.assertAlmostEqual(geo.initial_bearing(0.0, 0.0, 0.0, 1.0), 90.0, places=4)

    def test_due_west(self):
        self.assertAlmostEqual(geo.initial_bearing(0.0, 0.0, 0.0, -1.0), 270.0, places=4)

    def test_northeast_leans_east_of_45_at_latitude(self):
        # Meridian convergence pulls the initial bearing north of the rhumb
        # line, so a NE-ish target from 52N reads under 45 degrees.
        b = geo.initial_bearing(52.0, -2.0, 52.5, -1.5)
        self.assertTrue(30.0 < b < 45.0, b)


class TestProjection(unittest.TestCase):
    def test_centre(self):
        x, y = geo.polar_to_screen(0.0, 0.0, 40.0, 100.0)
        self.assertEqual((round(x), round(y)), (0, 0))

    def test_north_edge_is_negative_y(self):
        x, y = geo.polar_to_screen(40.0, 0.0, 40.0, 100.0)
        self.assertEqual((round(x), round(y)), (0, -100))

    def test_east_is_positive_x(self):
        x, y = geo.polar_to_screen(20.0, 90.0, 40.0, 100.0)
        self.assertEqual((round(x), round(y)), (50, 0))

    def test_southwest_quadrant(self):
        x, y = geo.polar_to_screen(40.0, 225.0, 40.0, 100.0)
        self.assertEqual((round(x), round(y)), (-71, 71))

    def test_zero_radius_is_safe(self):
        self.assertEqual(geo.polar_to_screen(10.0, 90.0, 0.0, 100.0), (0.0, 0.0))


class TestHandComputedScene(unittest.TestCase):
    """The M1 acceptance check: three contacts placed by hand, to +/-2 px."""

    OBS = (52.039554, -2.378344)
    RADIUS_KM = 40.0
    SCREEN_R = 100.0

    def _place(self, lat, lon):
        d = geo.haversine_km(self.OBS[0], self.OBS[1], lat, lon)
        b = geo.initial_bearing(self.OBS[0], self.OBS[1], lat, lon)
        x, y = geo.polar_to_screen(d, b, self.RADIUS_KM, self.SCREEN_R)
        return d, b, x, y

    def test_due_north_20km(self):
        # 20 km north of the observer: 0.17983 degrees of latitude.
        d, b, x, y = self._place(52.039554 + 0.179829, -2.378344)
        self.assertAlmostEqual(d, 20.0, places=1)
        self.assertAlmostEqual(b, 0.0, places=3)
        self.assertTrue(abs(x - 0.0) <= 2.0, x)
        self.assertTrue(abs(y - -50.0) <= 2.0, y)

    def test_due_east_10km(self):
        # 10 km east: 0.179829 / cos(52.04) degrees of longitude.
        dlon = 0.089914 / math.cos(math.radians(52.039554))
        d, b, x, y = self._place(52.039554, -2.378344 + dlon)
        self.assertAlmostEqual(d, 10.0, places=1)
        self.assertTrue(abs(b - 90.0) < 0.5, b)
        self.assertTrue(abs(x - 25.0) <= 2.0, x)
        self.assertTrue(abs(y - 0.0) <= 2.0, y)

    def test_due_south_40km_lands_on_the_outer_ring(self):
        d, b, x, y = self._place(52.039554 - 0.359658, -2.378344)
        self.assertAlmostEqual(d, 40.0, places=1)
        self.assertAlmostEqual(b, 180.0, places=3)
        self.assertTrue(abs(x - 0.0) <= 2.0, x)
        self.assertTrue(abs(y - 100.0) <= 2.0, y)


class TestRotate(unittest.TestCase):
    def test_north_stays_north(self):
        x, y = geo.rotate(0.0, -5.0, 0.0)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, -5.0, places=6)

    def test_ninety_degrees_points_east(self):
        x, y = geo.rotate(0.0, -5.0, 90.0)
        self.assertAlmostEqual(x, 5.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_one_eighty_points_south(self):
        x, y = geo.rotate(0.0, -5.0, 180.0)
        self.assertAlmostEqual(y, 5.0, places=6)


class TestValidation(unittest.TestCase):
    def test_bounds(self):
        self.assertTrue(geo.valid_lat(-90.0))
        self.assertTrue(geo.valid_lat(90.0))
        self.assertFalse(geo.valid_lat(90.1))
        self.assertFalse(geo.valid_lat(None))
        self.assertFalse(geo.valid_lat("52"))
        self.assertTrue(geo.valid_lon(-180.0))
        self.assertFalse(geo.valid_lon(180.5))


if __name__ == "__main__":
    unittest.main()
