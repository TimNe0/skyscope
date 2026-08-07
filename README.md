# SkyScope

A live ADS-B flight radar for the [EMF Tildagon / Spaceagon
badge](https://tildagon.badge.emfcamp.org/). Set your location and range, and
watch real aircraft move across a north-up radar scope on the badge's round
screen.

Contacts are plotted by bearing and distance from wherever you put the observer,
each with a heading vector and a decluttered label showing callsign, altitude and
speed. Data comes from free, keyless community ADS-B aggregators over the badge's
Wi-Fi.

Pure MicroPython — it runs on stock Tildagon OS, with no custom firmware.

## Features

- **North-up radar scope** — three range rings, ticks every 30°, cardinal marks,
  aircraft glyphs rotated to their track with a speed-scaled heading vector.
- **Collision-avoiding labels** — the nearest aircraft get labels, and any block
  that would land on another label, another aircraft or the scope's own chrome is
  dropped instead of drawn on top. Busy airspace stays readable.
- **Configurable observer** — presets (including East Essex Hackspace and EMF at
  Eastnor), a saved "Home", manual lat/lon entry, or an approximate IP lookup.
- **Configurable range** — 5–200 km, with UP/DOWN zoom through six steps.
- **Units** — aviation (ft/kt/nm), metric (m, m/s, km) or mixed (m, km/h, km).
- **Three data sources** — adsb.lol, adsb.fi and airplanes.live, switchable from
  the menu if one starts rate-limiting or requiring a key.
- **Aircraft detail page** — type, registration, altitude, speed, vertical rate,
  track, range, bearing and squawk for any selected contact.
- **LED ring** points at the nearest aircraft's bearing.
- **Sweep and trails** — optional radar-scope theatre.
- **Settings persist** across reboots.
- **Optional external panel** — render the radar on a GC9A01 round LCD carried by
  a hexpansion (see [Hexpansion display](#hexpansion-display-optional)).

## Install

**From the app store:** find *SkyScope* in the [Tildagon App
Directory](https://apps.badge.emfcamp.org/) and enter the install code on your
badge.

**From source, with `mpremote`:**

```sh
mpremote mkdir apps
mpremote mkdir apps/skyscope
mpremote cp *.py :/apps/skyscope/
mpremote cp tildagon.toml metadata.json :/apps/skyscope/
```

Then hold the reboop button for two seconds.

## Controls

| Button | Radar scope | Settings |
|---|---|---|
| UP / DOWN | zoom range through 5·10·20·40·80·160 km | navigate |
| CONFIRM | cycle labels: off → callsign → full | select / edit |
| RIGHT | refresh now | item info |
| LEFT | open settings | back |
| CANCEL | minimise | back / close |

Full details, including text entry, are in [controls.md](controls.md).

## Settings

Reached with LEFT from the radar screen.

| Setting | Options |
|---|---|
| Location | presets, Home, manual lat/lon, approximate IP lookup |
| Radius | 5–200 km |
| Update | 10 / 15 / 30 / 60 s |
| Units | aviation / metric / mixed |
| Source | adsb.lol / adsb.fi / airplanes.live |
| Labels | off / callsign / full |
| Sweep, Trails, LED ring | on / off |
| Display | main screen or hexpansion slot 1–6 |
| Contacts | list of current aircraft → detail page |
| About | version, data attribution, licence |

Settings are stored under the `skyscope` key in the badge's shared
`/settings.json`, so they never collide with other apps or with badge-level
settings.

## Data sources and etiquette

SkyScope is a polite client of community-run, volunteer-funded aggregators:

- [adsb.lol](https://api.adsb.lol/docs) (default)
- [adsb.fi](https://github.com/adsbfi/opendata) — public endpoints are limited to
  one request per second and are for personal, non-commercial use
- [airplanes.live](https://airplanes.live/)

Aircraft data is contributed by the volunteers who run the feeders. Please do not
lower the poll interval below the 10 s floor, and consider feeding data back to
one of these networks if you have a receiver.

The app sends one request per poll, identifies itself with a
`SkyScope-Tildagon/<version>` User-Agent, and backs off exponentially (to a
five-minute maximum) after errors. Polling only happens while SkyScope is in the
foreground; minimising it stops the requests.

The optional IP-based location estimate uses [ip-api.com](http://ip-api.com/),
which is free for non-commercial use. It is city-level accurate at best, so those
locations are shown with a `~` prefix.

## Memory

Dense airspace is the real constraint on a badge with 2 MB of PSRAM: 100 nm
around Heathrow is ~130 KB of JSON and 220 aircraft objects of ~50 fields each,
and the parsed dictionary tree is several times larger than the raw text.

SkyScope never parses the whole document. `adsb.iter_aircraft` walks the response
and hands back one aircraft object at a time; each is decoded, reduced to the
dozen fields the radar actually draws, and dropped before the next is touched, so
peak extra memory is a single small dictionary. On top of that the request radius
is capped at 100 nm, tracked contacts are capped (nearest 30 by default), and
`gc.collect()` runs after every poll.

## Hexpansion display (optional)

SkyScope can render the radar on an external **GC9A01 240×240 round SPI LCD**
carried by a hexpansion, leaving the badge screen showing a status page. This
needs hardware you build yourself — a protoboard hexpansion or a custom PCB.

Wiring, against the connector's four high-speed pins:

| Panel pin | Connect to | Why |
|---|---|---|
| SCL (SCK) | HS1 | hardware SPI clock, routed by the ESP32-S3 GPIO matrix |
| SDA (MOSI) | HS2 | |
| DC | HS3 | toggles per transaction, so it needs a fast pin |
| CS | HS4, **or tie to GND** | only device on the bus; tying it low frees HS4 |
| RST | first LS pin (via the GPIO expander) | a one-off reset, slow is fine |
| BL | 3V3 | or an LS pin if you want on/off control |
| VCC / GND | 3V3 / GND | well inside the 600 mA slot budget |
| **Detect** | **GND** | the slot stays unpowered otherwise |

Pin numbers are read from the firmware's `HexpansionConfig` rather than
hard-coded, so the same wiring works in any of the six slots, and the pin roles
are configurable in `conf.py` if you wire it differently.

A full-frame blit at 24 MHz takes roughly 40 ms, giving 1–2 fps — fine for a
radar that only changes when new data arrives.

**If the panel stays dark:** the badge's own display occupies one of the two SPI
hosts. `render_fb._SPI_HOSTS` decides which is tried first; swap the order there.
If colours look inverted, flip `swap_bytes` on `FbRenderer`.

Auto-detecting the panel from a hexpansion EEPROM is not implemented.

## Development

```sh
# tests (pure CPython, no badge or firmware needed)
python -m unittest discover -t . -s tests -v
```

To run it in the badge simulator, clone
[badge-2024-software](https://github.com/emfcamp/badge-2024-software) next door
and use `tools/sim.sh` (or `tools/sim.ps1` on Windows), which links this folder
into `sim/apps/skyscope` and launches it.

The simulator has no radio. SkyScope tries a real fetch first — which works if
the host machine is online — and falls back to synthetic traffic from
`fixtures.MockProvider` only when there is no Wi-Fi module *and* the fetch fails.
On a real badge a failed fetch is always reported as an error; the app never
shows fake data while pretending it is live.

### Layout

| File | What it does |
|---|---|
| `app.py` | lifecycle, screens, buttons, poll loop, LED ring |
| `conf.py` | defaults, validation, persistence |
| `geo.py` | haversine, bearing, polar projection |
| `adsb.py` | providers plus the low-memory response walker |
| `model.py` | `Contact` / `Snapshot`, distance filtering and capping |
| `radar_view.py` | scope, detail and status rendering |
| `settings_view.py` | menu tree over the config |
| `dialogs.py` | coordinate entry keypad |
| `render_ctx.py` | renderer over the badge's ctx canvas |
| `render_fb.py` | GC9A01 driver and framebuffer renderer |
| `fixtures.py` | offline/demo traffic and a real sample payload |

Everything below `app.py` is free of firmware imports, which is what lets the
test suite run under desktop CPython.

## Naming

Deliberately not "Skyscanner" or "Flightradar" — both are other people's
trademarks. The app name is a one-line change in `tildagon.toml`.

## Licence

MIT. See [LICENSE](LICENSE).

Aircraft data © the contributors to adsb.lol, adsb.fi and airplanes.live.
