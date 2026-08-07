"""Settings: defaults, validation and persistence.

Named `conf` rather than `config` so it cannot shadow anything the firmware
imports. Storage goes through the badge's own `settings` module (a single
/settings.json shared by every app), namespaced under one key. If that module
is unavailable -- desktop tests, or a future firmware that drops it -- we fall
back to a JSON file written atomically next to the app.

Every value is validated on load, so a hand-edited or half-written settings
file degrades to defaults instead of crashing the app.
"""

import json

CONFIG_KEY = "skyscope"
FALLBACK_PATH = "/apps/skyscope/conf.json"

VERSION = 1

# Zoom steps for UP/DOWN on the radar screen.
RADIUS_STEPS = (5, 10, 20, 40, 80, 160)
# The fuller list offered in the settings menu.
RADIUS_CHOICES = (5, 10, 20, 40, 60, 80, 100, 120, 160, 200)
INTERVAL_CHOICES = (10, 15, 30, 60)
LABEL_MODES = ("off", "callsign", "full")
DISPLAY_TARGETS = ("main", "hexpansion")

# M5 is built but off by default: the hexpansion display options only appear in
# the settings menu when this is on, per the spec's feature-flag requirement.
FEATURE_HEXPANSION_DISPLAY = True

MIN_INTERVAL_S = 10
MAX_RADIUS_KM = 200
MIN_RADIUS_KM = 5

# East Essex Hackspace is the Hawkwell Pavilion, Park Gardens, Hockley SS5 4HF
# (postcode centroid) -- close enough for a radar observer, and it sits under
# the Southend and London approach traffic.
EEHACK = ("East Essex Hackspace", 51.5972, 0.671394)

# The EMF camp site: Eastnor Deer Park, Eastnor, Herefordshire (OSM centroid).
# EMF's own weather example quotes 52.039554, -2.378344, about 170 m north --
# indistinguishable at radar ranges.
EMF = ("EMF (Eastnor Deer Park)", 52.038012, -2.377969)

# Order matters: the settings menu shows Home first, then these. East Essex
# Hackspace leads as the default observer, with the EMF site next.
PRESETS = (
    EEHACK,
    EMF,
    ("London", 51.5074, -0.1278),
    ("Manchester", 53.4808, -2.2426),
    ("Edinburgh", 55.9533, -3.1883),
    ("Amsterdam", 52.3676, 4.9041),
    ("Berlin", 52.5200, 13.4050),
    ("New York", 40.7128, -74.0060),
)

DEFAULTS = {
    "version": VERSION,
    "location": {
        "name": EEHACK[0],
        "lat": EEHACK[1],
        "lon": EEHACK[2],
        "source": "preset",
    },
    "home": {"name": "Home", "lat": None, "lon": None},
    "radius_km": 40,
    "interval_s": 15,
    "units": "aviation",
    "provider": "adsb_lol",
    "labels": "full",
    "label_count": 6,
    "max_aircraft": 30,
    "sweep": False,
    "trails": False,
    "led_ring": True,
    # What the 2026 touch ring does. Harmless on a 2024 badge, where there is
    # no touch hardware and the whole thing is a no-op.
    "touch_mode": "scrub",
    # Sectors armed in alerts mode, as indices 0-11.
    "touch_alerts": [],
    "display": {
        "target": "main",
        "slot": 2,
        "pins": {"sck": "HS1", "mosi": "HS2", "dc": "HS3", "cs": "HS4"},
    },
}


def _settings_module():
    try:
        import settings
        return settings
    except ImportError:
        return None


def _deep_merge(base, override):
    """Copy of `base` with `override`'s values layered on, one level deep.

    New keys added by a later app version therefore appear with their defaults
    rather than going missing.
    """
    out = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = dict(v)
        else:
            out[k] = v
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def _clamp_choice(value, choices, default):
    return value if value in choices else default


def _clamp_number(value, low, high, default):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    if value < low:
        return low
    if value > high:
        return high
    return value


def _valid_place(place, fallback):
    """A location dict is only usable if both coordinates are in range."""
    from . import geo

    if not isinstance(place, dict):
        return dict(fallback)
    lat = place.get("lat")
    lon = place.get("lon")
    if not geo.valid_lat(lat) or not geo.valid_lon(lon):
        return dict(fallback)
    name = place.get("name")
    return {
        "name": name if isinstance(name, str) and name else "Custom",
        "lat": float(lat),
        "lon": float(lon),
        "source": place.get("source", "manual"),
    }


def _valid_sectors(value):
    """Armed touch sectors: a sorted, deduplicated list of indices 0-11."""
    from . import touch

    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if 0 <= item < touch.SECTORS and item not in out:
            out.append(item)
    out.sort()
    return out


def validate(cfg):
    """Coerce a loaded config into something every other module can trust."""
    from . import adsb, touch, units as U

    out = _deep_merge(DEFAULTS, cfg)
    out["version"] = VERSION
    out["location"] = _valid_place(out.get("location"), DEFAULTS["location"])

    home = out.get("home")
    if isinstance(home, dict) and home.get("lat") is not None:
        out["home"] = _valid_place(home, DEFAULTS["home"])
        out["home"]["source"] = "home"
    else:
        out["home"] = dict(DEFAULTS["home"])

    out["radius_km"] = _clamp_number(
        out.get("radius_km"), MIN_RADIUS_KM, MAX_RADIUS_KM, DEFAULTS["radius_km"]
    )
    out["interval_s"] = _clamp_choice(
        out.get("interval_s"), INTERVAL_CHOICES, DEFAULTS["interval_s"]
    )
    if out["interval_s"] < MIN_INTERVAL_S:
        out["interval_s"] = MIN_INTERVAL_S
    out["units"] = _clamp_choice(out.get("units"), U.CHOICES, DEFAULTS["units"])
    out["provider"] = _clamp_choice(
        out.get("provider"), adsb.PROVIDER_KEYS, DEFAULTS["provider"]
    )
    out["labels"] = _clamp_choice(out.get("labels"), LABEL_MODES, DEFAULTS["labels"])
    out["label_count"] = int(_clamp_number(out.get("label_count"), 0, 30, 6))
    out["max_aircraft"] = int(_clamp_number(out.get("max_aircraft"), 5, 100, 30))
    out["sweep"] = bool(out.get("sweep"))
    out["trails"] = bool(out.get("trails"))
    out["led_ring"] = bool(out.get("led_ring"))
    out["touch_mode"] = _clamp_choice(
        out.get("touch_mode"), touch.MODES, DEFAULTS["touch_mode"]
    )
    out["touch_alerts"] = _valid_sectors(out.get("touch_alerts"))

    display = out.get("display")
    if not isinstance(display, dict):
        display = dict(DEFAULTS["display"])
    display["target"] = _clamp_choice(
        display.get("target"), DISPLAY_TARGETS, "main"
    )
    if not FEATURE_HEXPANSION_DISPLAY:
        display["target"] = "main"
    display["slot"] = int(_clamp_number(display.get("slot"), 1, 6, 2))
    pins = display.get("pins")
    if not isinstance(pins, dict):
        pins = dict(DEFAULTS["display"]["pins"])
    for role, default_pin in DEFAULTS["display"]["pins"].items():
        value = pins.get(role)
        if value not in ("HS1", "HS2", "HS3", "HS4", "GND"):
            pins[role] = default_pin
    display["pins"] = pins
    out["display"] = display
    return out


def load(path=FALLBACK_PATH):
    settings = _settings_module()
    raw = None
    if settings is not None:
        try:
            raw = settings.get(CONFIG_KEY)
        except Exception:
            raw = None
    if raw is None:
        raw = _load_file(path)
    return validate(raw or {})


def save(cfg, path=FALLBACK_PATH):
    """Persist. Returns True if it reached storage."""
    cfg = validate(cfg)
    settings = _settings_module()
    if settings is not None:
        try:
            settings.set(CONFIG_KEY, cfg)
            settings.save()
            return True
        except Exception as exc:
            print("[skyscope] settings save failed:", exc)
    return _save_file(cfg, path)


def _load_file(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_file(cfg, path):
    # Temp file plus rename, so an interrupted write cannot leave a truncated
    # config that would wipe the user's settings on next boot.
    import os

    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f)
        try:
            # MicroPython's rename does not replace an existing file.
            os.remove(path)
        except OSError:
            pass
        os.rename(tmp, path)
        return True
    except OSError as exc:
        print("[skyscope] config write failed:", exc)
        return False


def next_radius(current_km, direction):
    """Step through the zoom presets. `direction` is +1 (out) or -1 (in).

    A radius picked from the settings list may sit between two steps, so this
    moves to the next step strictly beyond it rather than snapping first.
    """
    if direction > 0:
        for step in RADIUS_STEPS:
            if step > current_km:
                return step
        return RADIUS_STEPS[-1]
    for step in reversed(RADIUS_STEPS):
        if step < current_km:
            return step
    return RADIUS_STEPS[0]


def location_of(cfg):
    place = cfg["location"]
    return place["lat"], place["lon"]
