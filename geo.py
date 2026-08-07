"""Great-circle maths and the north-up polar projection used by the radar view.

Pure maths, no firmware imports, so this module runs unchanged under CPython for
the unit tests in tests/test_geo.py.
"""

import math

EARTH_R_KM = 6371.0

KM_PER_NM = 1.852
M_PER_FT = 0.3048
KMH_PER_KT = 1.852
MS_PER_KT = 0.5144444444444445

_DEG = math.pi / 180.0


def km_to_nm(km):
    return km / KM_PER_NM


def nm_to_km(nm):
    return nm * KM_PER_NM


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two WGS84 points, in kilometres."""
    phi1 = lat1 * _DEG
    phi2 = lat2 * _DEG
    dphi = (lat2 - lat1) * _DEG
    dlam = (lon2 - lon1) * _DEG
    s1 = math.sin(dphi * 0.5)
    s2 = math.sin(dlam * 0.5)
    a = s1 * s1 + math.cos(phi1) * math.cos(phi2) * s2 * s2
    # Clamp guards against a > 1.0 from floating point noise at antipodes.
    if a > 1.0:
        a = 1.0
    return 2.0 * EARTH_R_KM * math.asin(math.sqrt(a))


def initial_bearing(lat1, lon1, lat2, lon2):
    """Initial true bearing from point 1 to point 2, degrees clockwise from north."""
    phi1 = lat1 * _DEG
    phi2 = lat2 * _DEG
    dlam = (lon2 - lon1) * _DEG
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.atan2(y, x) / _DEG) % 360.0


def polar_to_screen(distance_km, bearing_deg, radius_km, screen_r):
    """Project a range/bearing onto a north-up scope centred on (0, 0).

    Screen y grows downwards, so north (bearing 0) maps to negative y.
    Returns floats; the caller rounds.
    """
    if radius_km <= 0:
        return (0.0, 0.0)
    r_px = (distance_km / radius_km) * screen_r
    theta = bearing_deg * _DEG
    return (r_px * math.sin(theta), -r_px * math.cos(theta))


def bearing_to_unit(bearing_deg):
    """Unit vector on screen for a compass bearing (north-up, y down)."""
    theta = bearing_deg * _DEG
    return (math.sin(theta), -math.cos(theta))


def rotate(x, y, bearing_deg):
    """Rotate a screen-space offset clockwise by a compass bearing.

    Used to orient aircraft glyphs: a glyph drawn pointing "up" (-y) ends up
    pointing along its track.
    """
    theta = bearing_deg * _DEG
    c = math.cos(theta)
    s = math.sin(theta)
    return (x * c - y * s, x * s + y * c)


def valid_lat(lat):
    return isinstance(lat, (int, float)) and -90.0 <= lat <= 90.0


def valid_lon(lon):
    return isinstance(lon, (int, float)) and -180.0 <= lon <= 180.0
