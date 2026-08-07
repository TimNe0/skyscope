"""Reduced aircraft contacts and the snapshot the radar view renders.

A Contact holds only the fields the app actually draws. The provider builds
these straight from each aircraft object and drops the parsed dict immediately,
so a dense-airspace response never lives in memory as hundreds of 50-key dicts.
"""

from . import geo

# Snapshot.state values. "ok" is the only one that carries fresh contacts.
STATE_OK = "ok"
STATE_ERROR = "error"
STATE_CONNECTING = "connecting"
STATE_UPDATING = "updating"
STATE_IDLE = "idle"

# Seconds since the aircraft's last positional message.
STALE_AFTER_S = 30.0
DROP_AFTER_S = 60.0


class Contact:
    """One aircraft, already reduced to range/bearing from the observer."""

    def __init__(
        self,
        icao,
        callsign,
        lat,
        lon,
        alt_ft=None,
        gs_kt=None,
        track_deg=None,
        baro_rate=None,
        ac_type=None,
        reg=None,
        squawk=None,
        seen_pos=0.0,
        on_ground=False,
    ):
        self.icao = icao
        self.callsign = callsign
        self.lat = lat
        self.lon = lon
        self.alt_ft = alt_ft
        self.gs_kt = gs_kt
        self.track_deg = track_deg
        self.baro_rate = baro_rate
        self.ac_type = ac_type
        self.reg = reg
        self.squawk = squawk
        self.seen_pos = seen_pos
        self.on_ground = on_ground
        # Filled in by locate(); kept on the instance so the projection maths
        # runs once per snapshot rather than once per frame.
        self.dist_km = 0.0
        self.bearing = 0.0

    def locate(self, obs_lat, obs_lon):
        self.dist_km = geo.haversine_km(obs_lat, obs_lon, self.lat, self.lon)
        self.bearing = geo.initial_bearing(obs_lat, obs_lon, self.lat, self.lon)
        return self

    @property
    def stale(self):
        return self.seen_pos > STALE_AFTER_S

    @property
    def label(self):
        """Best available identifier: callsign, else registration, else ICAO24."""
        return self.callsign or self.reg or self.icao or "????"

    def __repr__(self):
        return "<Contact %s %.1fkm @%03.0f>" % (self.label, self.dist_km, self.bearing)


class Snapshot:
    """The result of one poll, plus enough context for the status line."""

    def __init__(
        self,
        contacts=None,
        ts_ms=0,
        state=STATE_IDLE,
        message="",
        total=0,
        obs_lat=0.0,
        obs_lon=0.0,
        radius_km=0.0,
    ):
        self.contacts = contacts if contacts is not None else []
        self.ts_ms = ts_ms
        self.state = state
        self.message = message
        # Aircraft the provider reported before our own distance/count capping.
        self.total = total
        self.obs_lat = obs_lat
        self.obs_lon = obs_lon
        self.radius_km = radius_km

    @property
    def ok(self):
        return self.state == STATE_OK


def prepare(contacts, obs_lat, obs_lon, radius_km, max_aircraft):
    """Locate, filter and cap a raw contact list.

    Drops aircraft that have gone quiet or fallen outside the selected radius,
    then keeps the nearest `max_aircraft`. Returns a list sorted by distance so
    the view can take the nearest N for full labels without re-sorting.
    """
    kept = []
    for c in contacts:
        if c.seen_pos > DROP_AFTER_S:
            continue
        c.locate(obs_lat, obs_lon)
        if c.dist_km > radius_km:
            continue
        kept.append(c)
    kept.sort(key=lambda c: c.dist_km)
    if max_aircraft and len(kept) > max_aircraft:
        del kept[max_aircraft:]
    return kept
