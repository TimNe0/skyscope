"""Offline data source for the simulator and for development without Wi-Fi.

The simulator has no guaranteed network stack, so app.py falls back to
MockProvider whenever a real fetch is impossible. It synthesises traffic around
whatever observer location is configured -- and moves it between polls -- so the
scope, the declutter rules and the trails all exercise properly offline.

SAMPLE_RESPONSE is a real (trimmed) adsb.lol v2 body, kept so the parser can be
exercised against genuine field shapes including the "ground" altitude string
and the mlat/tisb arrays that the object walker has to step over.
"""

import math

from .adsb import Provider

SAMPLE_RESPONSE = (
    '{"ac":['
    '{"hex":"4b17fb","type":"adsb_icao","flight":"SWR48X  ","r":"HB-JCD","t":"BCS3",'
    '"alt_baro":38000,"alt_geom":39425,"gs":398.3,"track":294.31,"baro_rate":32,'
    '"squawk":"5750","category":"A3","lat":51.537277,"lon":-1.084083,"seen_pos":0.297,'
    '"mlat":[],"tisb":[],"messages":77669,"seen":0.3,"rssi":-13.1,"dst":23.988,"dir":279.9},'
    '{"hex":"4402f0","type":"adsb_icao","flight":"EJU12DQ ","r":"OE-INO","t":"A320",'
    '"alt_baro":36025,"gs":451.1,"track":7.77,"baro_rate":0,"squawk":"5547",'
    '"lat":51.337435,"lon":-1.055908,"seen_pos":0.220,"mlat":[],"tisb":[],"seen":0.0},'
    '{"hex":"406d1f","flight":"BAW117  ","r":"G-YMMH","t":"B772","alt_baro":"ground",'
    '"gs":12.5,"track":88.4,"squawk":"6142","lat":51.470100,"lon":-0.452000,'
    '"seen_pos":1.1,"mlat":[],"tisb":[],"seen":1.1},'
    '{"hex":"3c0c0c","flight":"VJH728  ","t":"E35L","alt_baro":12800,"gs":373.5,'
    '"track":13.63,"baro_rate":960,"lat":51.524231,"lon":-0.980456,"seen_pos":0.177,'
    '"mlat":[],"tisb":[],"seen":0.0},'
    '{"hex":"aa8f21","flight":"NOPOS12 ","alt_baro":9000,"gs":210.0,"track":45.0,'
    '"seen_pos":0.5,"mlat":[],"tisb":[]}'
    '],"msg":"No error","now":1754563200000,"total":5,"ctime":1754563200000,"ptime":3}'
)

# name, bearing from observer, range in km, altitude ft, speed kt, track, type
_TRAFFIC = (
    ("BAW238", 12.0, 8.0, 3200, 190, 190.0, "A320"),
    ("EZY41KP", 74.0, 19.0, 24000, 380, 250.0, "A20N"),
    ("RYR7GT", 141.0, 27.0, 37000, 452, 318.0, "B738"),
    ("DLH8MW", 208.0, 33.5, 38000, 471, 42.0, "A359"),
    ("EIN18J", 266.0, 12.5, 8500, 265, 95.0, "AT76"),
    ("GABCD", 318.0, 5.0, 1800, 105, 150.0, "C172"),
    ("NJE712P", 35.0, 46.0, 41000, 448, 205.0, "GL5T"),
    ("SHT3C", 175.0, 61.0, 15000, 300, 12.0, "A319"),
)

_KM_PER_DEG_LAT = 111.32


class MockProvider(Provider):
    """Synthesises a plausible ADSBx-v2 response around the observer."""

    key = "mock"
    name = "Demo data"
    attribution = "Simulated traffic - no live data"

    def __init__(self):
        self.tick = 0

    def url(self, lat, lon, radius_nm):
        return "mock://%s/%s/%d" % (lat, lon, radius_nm)

    def fetch(self, lat, lon, radius_km, timeout=10, user_agent=None):
        self.tick += 1
        parts = []
        for i, (call, bearing, dist, alt, gs, track, ac_type) in enumerate(_TRAFFIC):
            # Advance each aircraft along its own track between polls.
            travel = (self.tick * gs * 0.0004) % 40.0
            b = (bearing + travel * (1 if i % 2 else -1)) % 360.0
            d = dist + math.sin((self.tick + i) * 0.35) * 2.0
            a_lat, a_lon = _offset(lat, lon, b, d)
            parts.append(
                # Trailing spaces mirror the real feed, which pads callsigns.
                '{"hex":"%06x","flight":"%s  ","t":"%s","r":"G-DEMO",'
                '"alt_baro":%d,"gs":%.1f,"track":%.2f,"baro_rate":%d,'
                '"squawk":"7000","lat":%.6f,"lon":%.6f,"seen_pos":0.4,'
                '"mlat":[],"tisb":[],"seen":0.4}'
                % (0xA00000 + i, call, ac_type, alt, gs, track,
                   -64 if i % 3 else 640, a_lat, a_lon)
            )
        return '{"ac":[%s],"msg":"No error","now":0,"total":%d}' % (
            ",".join(parts), len(parts),
        )


def _offset(lat, lon, bearing_deg, distance_km):
    """Destination point, flat-earth approximation. Fine at radar ranges."""
    rad = bearing_deg * math.pi / 180.0
    dlat = (distance_km / _KM_PER_DEG_LAT) * math.cos(rad)
    scale = math.cos(lat * math.pi / 180.0)
    if abs(scale) < 1e-6:
        scale = 1e-6
    dlon = (distance_km / (_KM_PER_DEG_LAT * scale)) * math.sin(rad)
    return lat + dlat, lon + dlon
