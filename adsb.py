"""ADS-B aggregator clients and a low-memory response parser.

All three supported aggregators return the same tar1090 / ADSBExchange-v2 shape:

    {"ac": [ {aircraft...}, ... ], "total": n, "now": ms}

Parsing that with a single json.loads is what kills the badge: 100 nm around a
busy airport is ~130 KB of JSON and ~220 aircraft objects of ~50 keys each, and
the parsed dict tree dwarfs the raw text. So `iter_aircraft` walks the byte
string and hands back one aircraft object at a time; each is decoded, reduced to
a Contact, and dropped before the next is touched. Peak extra memory is one
small dict rather than the whole document.

No firmware imports at module scope -- `requests` is imported inside fetch() so
the parser can be unit-tested under CPython.
"""

import json

from . import geo, model

DEFAULT_UA = "SkyScope-Tildagon/0.0.2 (+https://github.com/TimNe0/skyscope)"

# adsb.lol caps radius requests at 250 nm; we cap much lower to protect memory.
MAX_RADIUS_NM = 100


class ProviderError(Exception):
    """Any failure that should put the app into its backoff path."""

    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.message = message
        self.status = status


# --- response parsing -------------------------------------------------------

_ARRAY_KEYS = ('"ac"', '"aircraft"')


def _find_array(text):
    """Index just after the '[' that opens the aircraft array, or -1."""
    for key in _ARRAY_KEYS:
        at = text.find(key)
        while at >= 0:
            colon = text.find(":", at + len(key))
            if colon >= 0:
                bracket = text.find("[", colon)
                # Only accept the bracket if nothing but whitespace precedes it,
                # otherwise "ac" was a value rather than the array's key.
                if bracket >= 0 and not text[colon + 1:bracket].strip():
                    return bracket + 1
            at = text.find(key, at + len(key))
    return -1


def _skip_string(text, i):
    """Index just past the string literal whose opening quote is at i."""
    j = i + 1
    n = len(text)
    while j <= n:
        k = text.find('"', j)
        if k < 0:
            return n
        # A quote is only a terminator if preceded by an even number of
        # backslashes.
        backslashes = 0
        b = k - 1
        while b >= 0 and text[b] == "\\":
            backslashes += 1
            b -= 1
        if backslashes % 2 == 0:
            return k + 1
        j = k + 1
    return n


def _next_structural(text, i):
    """Index of the next '"', '{', '}' or ']' at or after i, or -1."""
    best = -1
    for ch in ('"', "{", "}", "]"):
        at = text.find(ch, i)
        if at >= 0 and (best < 0 or at < best):
            best = at
    return best


def iter_aircraft(text):
    """Yield the JSON source of each aircraft object, one at a time.

    Uses find() to hop between structural characters rather than stepping
    through every byte, so a 130 KB payload costs a few thousand iterations
    instead of a hundred thousand.
    """
    i = _find_array(text)
    if i < 0:
        return
    depth = 0
    start = -1
    while True:
        j = _next_structural(text, i)
        if j < 0:
            return
        ch = text[j]
        if ch == '"':
            i = _skip_string(text, j)
        elif ch == "{":
            if depth == 0:
                start = j
            depth += 1
            i = j + 1
        elif ch == "}":
            depth -= 1
            i = j + 1
            if depth <= 0 and start >= 0:
                yield text[start:i]
                start = -1
                depth = 0
        else:  # ']'
            if depth == 0:
                return
            i = j + 1


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def contact_from_dict(a):
    """Reduce one ADSBx-v2 aircraft dict to a Contact, or None if unusable."""
    lat = _num(a.get("lat"))
    lon = _num(a.get("lon"))
    if not geo.valid_lat(lat) or not geo.valid_lon(lon):
        return None

    alt = a.get("alt_baro")
    on_ground = alt == "ground"
    alt_ft = None if on_ground else _num(alt)

    flight = a.get("flight")
    callsign = flight.strip() if isinstance(flight, str) else ""

    seen_pos = _num(a.get("seen_pos"))
    if seen_pos is None:
        seen_pos = _num(a.get("seen")) or 0.0

    return model.Contact(
        icao=a.get("hex") or "",
        callsign=callsign,
        lat=lat,
        lon=lon,
        alt_ft=alt_ft,
        gs_kt=_num(a.get("gs")),
        track_deg=_num(a.get("track")),
        baro_rate=_num(a.get("baro_rate")),
        ac_type=a.get("t") or None,
        reg=a.get("r") or None,
        squawk=a.get("squawk") or None,
        seen_pos=seen_pos,
        on_ground=on_ground,
    )


def parse(text, drop_after_s=model.DROP_AFTER_S):
    """Parse a provider response into (contacts, total_reported).

    Aircraft that already look stale are discarded during the walk so they never
    become Contact objects at all.
    """
    contacts = []
    total = 0
    for src in iter_aircraft(text):
        total += 1
        try:
            a = json.loads(src)
        except (ValueError, MemoryError):
            continue
        c = contact_from_dict(a)
        del a
        if c is None:
            continue
        if c.seen_pos > drop_after_s:
            continue
        contacts.append(c)
    return contacts, total


# --- providers --------------------------------------------------------------


class Provider:
    """A keyless ADS-B aggregator behind a fixed interface.

    Subclasses supply a URL template only; if one of these ever starts requiring
    a key, swapping to another is a settings change rather than a code change.
    """

    key = ""
    name = ""
    attribution = ""
    # Requests per second the endpoint tolerates; our minimum poll interval is
    # 10 s, so this is documentation as much as a constraint.
    min_interval_s = 1

    def url(self, lat, lon, radius_nm):
        raise NotImplementedError

    def fetch(self, lat, lon, radius_km, timeout=10, user_agent=DEFAULT_UA):
        """Blocking GET. Returns the response body as text.

        Raises ProviderError for anything the caller should back off from.
        """
        radius_nm = int(max(1, min(MAX_RADIUS_NM, round(geo.km_to_nm(radius_km)))))
        url = self.url(lat, lon, radius_nm)
        try:
            import requests
        except ImportError:
            raise ProviderError("NO NETWORK STACK")

        response = None
        try:
            headers = {"User-Agent": user_agent, "Accept": "application/json"}
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
            except TypeError:
                # Some MicroPython requests builds have no timeout keyword.
                response = requests.get(url, headers=headers)
            status = getattr(response, "status_code", 200)
            if status != 200:
                raise ProviderError(_status_message(status), status)
            return response.text
        except ProviderError:
            raise
        except MemoryError:
            raise ProviderError("OUT OF MEMORY")
        except Exception as exc:  # network errors vary wildly across ports
            raise ProviderError(_short_error(exc))
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def poll(self, lat, lon, radius_km, timeout=10, user_agent=DEFAULT_UA):
        """fetch() + parse(), releasing the response body before returning."""
        text = self.fetch(lat, lon, radius_km, timeout, user_agent)
        try:
            return parse(text)
        finally:
            del text


class AdsbLol(Provider):
    key = "adsb_lol"
    name = "adsb.lol"
    attribution = "Data (c) adsb.lol contributors"

    def url(self, lat, lon, radius_nm):
        return "https://api.adsb.lol/v2/lat/%s/lon/%s/dist/%d" % (
            _coord(lat), _coord(lon), radius_nm,
        )


class AdsbFi(Provider):
    key = "adsb_fi"
    name = "adsb.fi"
    attribution = "Data (c) adsb.fi contributors"
    # adsb.fi's public endpoints allow one request per second.
    min_interval_s = 1

    def url(self, lat, lon, radius_nm):
        # v3 is current; the v2 lat/lon/dist variant is deprecated.
        return "https://opendata.adsb.fi/api/v3/lat/%s/lon/%s/dist/%d" % (
            _coord(lat), _coord(lon), radius_nm,
        )


class AirplanesLive(Provider):
    key = "airplanes_live"
    name = "airplanes.live"
    attribution = "Data (c) airplanes.live"

    def url(self, lat, lon, radius_nm):
        return "https://api.airplanes.live/v2/point/%s/%s/%d" % (
            _coord(lat), _coord(lon), radius_nm,
        )


PROVIDERS = (AdsbLol(), AdsbFi(), AirplanesLive())
PROVIDER_KEYS = tuple(p.key for p in PROVIDERS)


def get_provider(key):
    for p in PROVIDERS:
        if p.key == key:
            return p
    return PROVIDERS[0]


def _coord(value):
    # Six decimals is ~10 cm; more just makes the URL longer.
    return ("%.6f" % value).rstrip("0").rstrip(".")


def _status_message(status):
    if status == 429:
        return "RATE LIMITED"
    if status in (401, 403):
        return "API KEY NEEDED"
    if status >= 500:
        return "SERVER ERROR %d" % status
    return "HTTP %d" % status


def _short_error(exc):
    """Turn a port-specific exception into something readable on a 240px screen."""
    name = type(exc).__name__.upper()
    if "TIMEOUT" in name:
        return "TIMEOUT"
    if "SSL" in name or "TLS" in name:
        return "TLS ERROR"
    if "GAI" in name or "DNS" in name or "NAMERESOLUTION" in name:
        return "DNS ERROR"
    if "CONNECTION" in name:
        return "NO CONNECTION"
    if "OSERROR" in name or "SOCKET" in name:
        return "NETWORK ERROR"
    if "MEMORY" in name:
        return "OUT OF MEMORY"
    return name[:18]

