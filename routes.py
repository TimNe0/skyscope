"""Where a flight came from and where it is going.

ADS-B carries no route information -- the aircraft broadcasts a callsign, not an
itinerary -- so this is a separate lookup against adsbdb.com, which is free and
keyless like the position feeds.

adsb.lol documents a /api/0/routeset endpoint, but it answers 201 with an empty
body, so it is not usable. hexdb.io works and is smaller, but returned visibly
stale routes when checked. adsbdb gives origin and destination with airport
codes and city names for about 700 bytes.

Lookups happen on demand, only when a detail page is opened, never during the
normal poll loop -- one aircraft at a time is a polite amount of traffic to send
a volunteer-run service. Results, including "no route known", are cached so the
same flight is never asked for twice.

No firmware imports at module scope; `requests` is imported inside fetch().
"""

import json

ENDPOINT = "https://api.adsbdb.com/v0/callsign/%s"
ATTRIBUTION = "Routes (c) adsbdb.com"

# Plenty for a session's worth of curiosity, and bounded so a long run cannot
# grow it without limit.
CACHE_MAX = 24


class Route:
    """One flight's origin and destination."""

    def __init__(self, origin, origin_city, destination, destination_city,
                 airline=None):
        self.origin = origin
        self.origin_city = origin_city
        self.destination = destination
        self.destination_city = destination_city
        self.airline = airline

    def __repr__(self):
        return "<Route %s-%s>" % (self.origin, self.destination)


def _airport(node):
    """(code, city) from an adsbdb airport node, preferring the IATA code."""
    if not isinstance(node, dict):
        return None, None
    code = node.get("iata_code") or node.get("icao_code") or None
    city = node.get("municipality") or node.get("name") or None
    return code, city


def parse(payload):
    """adsbdb response -> Route, or None when the callsign has no known route."""
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    # Unknown callsigns come back as the bare string "unknown callsign".
    if not isinstance(response, dict):
        return None
    flightroute = response.get("flightroute")
    if not isinstance(flightroute, dict):
        return None
    origin, origin_city = _airport(flightroute.get("origin"))
    destination, destination_city = _airport(flightroute.get("destination"))
    if not origin and not destination:
        return None
    airline = flightroute.get("airline")
    name = airline.get("name") if isinstance(airline, dict) else None
    return Route(origin, origin_city, destination, destination_city, name)


class RouteLookup:
    """On-demand, cached route lookups."""

    def __init__(self):
        # callsign -> Route or None. None means "asked, nothing known", which
        # is cached too so a routeless flight is not retried on every visit.
        self._cache = {}
        self._order = []

    def __len__(self):
        return len(self._cache)

    def known(self, callsign):
        return bool(callsign) and callsign in self._cache

    def get(self, callsign):
        return self._cache.get(callsign)

    def _remember(self, callsign, route):
        if callsign not in self._cache:
            self._order.append(callsign)
            if len(self._order) > CACHE_MAX:
                del self._cache[self._order.pop(0)]
        self._cache[callsign] = route

    def fetch(self, callsign, timeout=8, user_agent=None):
        """Blocking lookup. Never raises: an unknown route is just None."""
        if not callsign:
            return None
        if callsign in self._cache:
            return self._cache[callsign]
        try:
            import requests
        except ImportError:
            self._remember(callsign, None)
            return None

        response = None
        route = None
        try:
            headers = {"Accept": "application/json"}
            if user_agent:
                headers["User-Agent"] = user_agent
            url = ENDPOINT % callsign
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
            except TypeError:
                response = requests.get(url, headers=headers)
            if getattr(response, "status_code", 200) == 200:
                route = parse(json.loads(response.text))
        except Exception as exc:
            print("[skyscope] route lookup failed:", exc)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        self._remember(callsign, route)
        return route
