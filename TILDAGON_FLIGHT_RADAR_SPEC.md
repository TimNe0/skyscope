# SkyScope — a live flight radar app for the EMF Tildagon / Spaceagon badge

**Project brief & build plan (hand this file to Claude Code as the project spec).**
Research snapshot date: 2026-08-07.

Inspiration: the "Online Flight Tracker" ESP32 + round GC9A01 TFT project by LazyDays Creation — a
radar-scope display showing live aircraft (callsign, speed, altitude) around a fixed point. This
project reimplements that concept as a proper Tildagon OS app in MicroPython, with a configurable
observer location and monitoring radius, rendering either on the badge's own round screen or on an
external round LCD mounted on a hexpansion.

> Naming note: avoid "Skyscanner" (travel-company trademark) and "Flightradar" (Flightradar24
> trademark). Working name **SkyScope** (alternatives: HexRadar, PlaneSpotter). Final name is a
> one-line change in `tildagon.toml`.

---

## 1. Goals

**MVP (must have)**
1. Radar-scope view: north-up polar display centred on the observer — range rings with distance
   labels, cardinal/degree marks, aircraft plotted by bearing + distance with heading vector,
   callsign and altitude/speed labels.
2. User-configurable observer location (presets, manual lat/lon entry, optional IP-based estimate).
3. User-configurable monitoring radius (e.g. 10–200 km) and refresh interval.
4. Live data from a free, key-less ADS-B aggregator over the badge's Wi-Fi.
5. Settings persist across reboots.
6. Runs on stock Tildagon OS firmware — pure MicroPython app, no custom firmware build.
7. Publishable to the Tildagon App Directory.

**Stretch (nice to have, build behind flags)**
- Render on an external round GC9A01 LCD on a hexpansion (user-selectable: main screen or
  hexpansion slot 1–6).
- Sweep animation / fading trails, LED ring pointing at the nearest aircraft's bearing.
- Consume the badge `position` capability (GPS hexpansion) for automatic location.
- Aircraft detail page (select a contact: reg, type, squawk, vertical rate).

**Non-goals (v1):** flight-plan/route lookup, historical playback, audio alerts, MLAT feeding.

---

## 2. Prior-art check — has this been done?

Checked the full Tildagon App Directory (https://apps.badge.emfcamp.org/ — 244 apps listed on
2026-08-07). **No existing app does live ADS-B flight tracking around a configurable location.**
Closest neighbours, and why they differ:

| App | What it does | Why it's not this |
|---|---|---|
| **Overhead** (Alicia Sykes, `42414141`, github.com/lissy93/overhead) | Point the badge at the ISS, satellites, planets and planes overhead | AR-style pointing/compass app, not a radar scope; no configurable observer location + monitoring radius workflow |
| **ISS Countdown** (`11200200`) | Next ISS pass countdown | Satellites only, no aircraft |
| **Radar** (pikesley, `32443243`) | "A Radar Scanner thing" | Visual effect only, no real data |
| **Tildagon WiFi Radar** (`14431310`) | Wi-Fi APs as blips on a polar display | Wi-Fi, not aircraft — but a useful reference for polar rendering on this hardware |

Conclusion: the niche is open. Review **Overhead**'s source before building — it likely solved
Wi-Fi + API-polling + drawing patterns on this exact platform. Differentiate clearly in the store
description ("radar scope of live air traffic around any location you set").

Also check at build time (quick sanity re-check): search the store for "flight", "ADS-B", "plane",
"radar" in case something shipped since this snapshot.

---

## 3. Target platform — verified facts

Docs root: https://tildagon.badge.emfcamp.org/

**Hardware** (same platform for EMF 2024 "Tildagon" and 2026 "Spaceagon"; apps work on both):
- ESP32-S3, **2 MB PSRAM**, 8 MB flash, Wi-Fi, BLE, IMU.
- Round LCD, six buttons, RGB LED ring, six hexpansion slots, USB-C.
- Runs MicroPython ("Tildagon OS").

**App model** (https://tildagon.badge.emfcamp.org/tildagon-apps/development/):
- Subclass `app.App` in `app.py`; export with `__app_export__ = YourApp`.
- `update(self, delta)` — called ~every 0.05 s (foreground only). Handle buttons here.
- `draw(self, ctx)` — called ~every 0.05 s; draw with the **ctx** vector canvas. Screen coordinate
  space is **240×240 centred on 0,0** (i.e. −120…+120 both axes); the visible panel is round.
- `background_update(self)` / `background_task()` — run even when minimised.
- `async def run(self, render_update)` — override for an async main loop (this is where our
  network polling lives). Call `await render_update()` to trigger draws.
- `self.minimise()` on CANCEL to exit; call `self.button_states.clear()` first.
- Buttons: `from events.input import Buttons, BUTTON_TYPES` — keys `UP, DOWN, LEFT, RIGHT,
  CONFIRM, CANCEL`.
- Ready-made UI widgets in `app_components`: `Menu`, `Notification`, `YesNoDialog`, `TextDialog`,
  `Layout`, `Tokens`, `clear_background(ctx)`
  (https://tildagon.badge.emfcamp.org/tildagon-apps/reference/ui-elements/).
- ctx API reference: https://tildagon.badge.emfcamp.org/tildagon-apps/reference/ctx/
  (chainable: `ctx.rgb(...).move_to(...).text(...)`, `rectangle`, arcs/paths, `ctx.save()/restore()`).

**Networking:**
- `requests` is bundled in the badge firmware; `import wifi; wifi.connect()` brings the radio up
  (takes a few seconds on app start). See the API example:
  https://tildagon.badge.emfcamp.org/tildagon-apps/examples/api/
- `tildagon.toml` supports `wifi_preference = true` to ask the OS to enable Wi-Fi on app entry.

**Config persistence:** follow the official pattern —
https://tildagon.badge.emfcamp.org/tildagon-apps/configuration/. Fallback: a small JSON file
written next to the app.

**Dev loop:**
- Local simulator: https://tildagon.badge.emfcamp.org/tildagon-apps/simulate/
  (in https://github.com/emfcamp/badge-2024-software `sim/`). Note: the sim has no real Wi-Fi
  stack guarantees — abstract the data provider so a mock/fixture provider runs in the sim.
- On-device testing with `mpremote`:
  https://tildagon.badge.emfcamp.org/tildagon-apps/run-on-badge/
- Web emulator (published apps only): https://emulator.badge.emfcamp.org/

**Hexpansion connector** (https://tildagon.badge.emfcamp.org/hexpansions/creating-hexpansions/):
- 3.3 V @ up to 600 mA (current-limited); Detect pin must be tied to GND to get power.
- Per-slot isolated I2C bus (address 0x77 forbidden; you supply pullups).
- **4 high-speed (HS) GPIO pins wired directly to the ESP32-S3** — attachable to any free
  peripheral (SPI is possible via the ESP32-S3 GPIO matrix). Three of the six slots also have
  ADC-capable HS pins (not needed here).
- 5 low-speed (LS) pins via an I2C GPIO expander/LED driver — slow, fine for reset/backlight.
- Optional I2C EEPROM for auto-detection/auto-run.
- App-side access: see https://tildagon.badge.emfcamp.org/hexpansions/writing-hexpansion-apps/ and
  https://tildagon.badge.emfcamp.org/tildagon-apps/examples/detect-hexpansion/ (the firmware
  exposes a hexpansion config object with the slot's pins + I2C — **verify exact class/attribute
  names against current firmware source before coding**, task V-2 below).

---

## 4. Data source

Requirement: free, no API key, simple REST + JSON, radius query, usable from MicroPython.

**Primary: adsb.lol** (community ADS-B aggregator; ADSBExchange-v2-compatible responses)
- Endpoint: `https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist_nm}`
  (also `/v2/point/{lat}/{lon}/{radius_nm}`). Interactive docs: https://api.adsb.lol/docs
- No key required today; rate limits are dynamic; the project has said keys may be required in the
  future (feeders get access) — so **isolate the provider behind an interface** and handle 4xx by
  backing off and surfacing a clear on-screen message.
- Response: `{ "ac": [ {aircraft…}, … ], "total": n, "now": ms }`.

**Fallbacks (same tar1090/ADSBx-v2 response shape, swap via config):**
- adsb.fi: `https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{nm}` — public endpoints rate
  limited to **1 request/second**, personal non-commercial use (fits a badge). The v2 lat/lon/dist
  variant is deprecated; use v3. https://github.com/adsbfi/opendata
- airplanes.live: `/v2/point/{lat}/{lon}/{radius}` style API — verify current terms/limits at
  build time (task V-4).

**Rejected for MVP:** OpenSky Network (now OAuth2 client-credentials for registered use; anonymous
access is heavily credit-limited) — revisit only if the aggregators above become key-gated.

**Aircraft fields to consume** (ADSBx-v2 style; all optional per aircraft — code defensively):

| Field | Meaning | Notes |
|---|---|---|
| `hex` | ICAO24 address | stable contact ID for trails/selection |
| `flight` | callsign | trim whitespace; fall back to `r` (registration) or `hex` |
| `lat`, `lon` | position | skip aircraft without a position |
| `alt_baro` | barometric altitude, **feet** | may be the string `"ground"` |
| `gs` | ground speed, **knots** | |
| `track` | true track, degrees | heading vector |
| `baro_rate` | climb/descent ft/min | detail page |
| `t`, `r`, `squawk`, `category` | type / reg / squawk / wake cat | detail page |
| `seen`, `seen_pos` | seconds since last message/position | drop if `seen_pos` > ~60 s |

Units: source is aviation units (ft, kt). Provide a **units setting**: `aviation` (ft, kt) or
`metric` (m, m/s — like the inspiration photo) or `mixed` (m + km/h). Convert at render time only.

**Payload/memory budget (important, 2 MB PSRAM):** dense airspace at 100 nm can return several
hundred aircraft → hundreds of KB of JSON. Mitigations, in order: (a) default radius 40 km
(~22 nm), hard-cap the request at 100 nm; (b) read the body once, `json.loads`, then immediately
reduce each aircraft to a small tuple and drop the parsed dict; (c) run `gc.collect()` after each
poll; (d) cap tracked contacts (sort by distance, keep nearest `max_aircraft`, default 30);
(e) stretch: incremental "budget parser" that scans the byte stream for `"ac":[…]` objects and
extracts only needed keys without materialising the full document.

**Etiquette:** one request per poll, default interval 15 s (min 10 s), exponential backoff on
errors (×2 up to 5 min), send a `User-Agent: SkyScope-Tildagon/<version> (+repo URL)` header.

---

## 5. Functional requirements

### 5.1 Radar screen (default view)
- Black background, phosphor-green theme (configurable palette later).
- 3 range rings + outer ring = selected radius; label the radius (`R: 40km`) at 12 o'clock; tick
  marks every 30°, labels at 0/90/180/270.
- Observer at centre. Each aircraft: small plane glyph or triangle rotated to `track`, short
  heading vector, label block (callsign; altitude; speed per units setting). Declutter: full
  labels only for the N nearest (default 6), glyph-only beyond that; never draw labels for
  contacts outside the ring.
- Staleness: contact dims after `seen_pos` > 30 s; removed > 60 s. A status line (bottom arc)
  shows `n aircraft · updated Xs ago` or the current error/backoff state.
- Optional (flagged): rotating sweep with afterglow, contact trails (last ~5 positions).

### 5.2 Controls (proposal — keep in one place, `controls.md` table in repo)
| Button | Radar view | Settings |
|---|---|---|
| UP / DOWN | zoom radius through steps 5·10·20·40·80·160 km | navigate |
| CONFIRM | cycle label density (off → callsign → full) | select/edit |
| RIGHT | force refresh now | — |
| LEFT | open settings menu | back |
| CANCEL | minimise app (clear button state first) | back/close |

### 5.3 Settings menu (built from `app_components` Menu/TextDialog/YesNoDialog)
1. **Location** → Presets (EMF/Eastnor `52.039554, -2.378344` — the coords used in the official
   weather example — plus user-saved "Home"), Manual entry (lat then lon via `TextDialog`;
   validate ranges), **Auto (IP)** — optional single call to `http://ip-api.com/json` (free for
   non-commercial, no key; city-level accuracy; clearly label it "approximate"), and *(stretch)*
   **GPS** via the badge `position` capability
   (https://tildagon.badge.emfcamp.org/capabilities/registry/position/) when a GPS hexpansion app
   provides it.
2. **Radius** (5–200 km list).
3. **Update interval** (10/15/30/60 s).
4. **Units** (aviation / metric / mixed).
5. **Data source** (adsb.lol / adsb.fi / airplanes.live).
6. **Display** (Main screen / Hexpansion slot 1–6) — hexpansion entries visible only when the
   feature flag is on.
7. **About** (version, data attribution, licence).

### 5.4 Wi-Fi & lifecycle
- `wifi_preference = true` in `tildagon.toml`; on start, show "Connecting to Wi-Fi…", call
  `wifi.connect()`, handle failure with a retry option (mirror the official weather example's
  `try_connect` pattern).
- Poll only while the app is foregrounded (don't burn battery/quota from `background_task`).
  On minimise, stop polling; on return, poll immediately.

---

## 6. Display targets ("main or hexpansion round screen")

### 6.1 Renderer abstraction (build first — everything draws through this)
```python
class Renderer(Protocol):
    size: int                      # 240
    def clear(self, rgb): ...
    def line(self, x0, y0, x1, y1, rgb, w=1): ...
    def circle(self, x, y, r, rgb, fill=False): ...
    def poly(self, pts, rgb, fill=True): ...      # aircraft glyph
    def text(self, s, x, y, rgb, size=12, align="left"): ...
    def flush(self): ...           # no-op for ctx; SPI blit for framebuffer
```
- `CtxRenderer` maps to ctx calls (coords already centred at 0,0). MVP ships with this only.
- `FbRenderer` draws into a `framebuf.FrameBuffer` (RGB565, 240×240 = **115,200 B** — allocate one
  `bytearray` up front in PSRAM) and `flush()` blits it to the panel driver. Text via framebuf's
  8×8 font (scaled 1–2×) is acceptable for v1.

### 6.2 Hexpansion round screen — hardware assumptions & pin budget
Target panel: **GC9A01 240×240 round SPI LCD** (the exact module family in the inspiration photo:
pins RST/CS/DC/SDA(MOSI)/SCL(SCK)/GND/VCC, 3.3 V). Precedent exists — a "Screen hexpansion" by
mbooth (round displays) is in the official hexpansion gallery, but no public driver/files are
linked, so treat the hardware as DIY: a protoboard hexpansion (e.g. Jake Walker's Protoboard
Hexpansion) or custom PCB carrying the panel.

Pin budget vs the connector's **4 HS pins**:

| Panel pin | Connect to | Rationale |
|---|---|---|
| SCL (SCK) | HS1 | hardware SPI clock (ESP32-S3 GPIO matrix → any pin) |
| SDA (MOSI) | HS2 | |
| DC | HS3 | data/command toggles per transaction — needs a fast pin |
| CS | HS4, **or tie to GND** | single device on the bus; tying low frees HS4 |
| RST | LS pin (expander) or RC-delay to 3V3 | reset is a one-off, slow is fine |
| BL | tie to 3V3 (or LS pin for on/off) | |
| VCC/GND | 3V3 / GND | well under the 600 mA budget |
Tie the hexpansion **Detect pin to GND** or the slot stays unpowered.

### 6.3 Hexpansion driver plan (feature-flagged, milestone M5)
- Pure-MicroPython GC9A01 driver: init command sequence, `set_window`, `write_pixels(buf)`;
  `machine.SPI` at 20–40 MHz on the slot's HS GPIOs (full-frame blit ≈ 30–60 ms → 1–2 fps radar
  refresh is fine; only redraw on data/zoom changes). Mind RGB565 byte order (swap if colours
  look inverted).
- Get pin objects/GPIO numbers for the chosen slot from the firmware's hexpansion config API
  (task V-2) rather than hard-coding GPIO numbers, so it works in any slot.
- Config: `display.target = "main" | "hexpansion"`, `display.slot = 1..6`, plus pin-role mapping
  (which HS index is SCK/MOSI/DC/CS) so other wirings work.
- When targeting the hexpansion, the main screen shows a status/config view (connection state,
  aircraft count, controls hint) — the badge screen must never be left frozen.
- Out of scope v1: EEPROM auto-detect of the screen hexpansion (document as future work).

---

## 7. Architecture

```
skyscope/                      # repo root == app folder on badge (/apps/skyscope/)
├── app.py                     # FlightRadarApp(app.App): lifecycle, screens, buttons
├── tildagon.toml
├── conf.py                    # load/save/validate settings (named conf, not config —
│                              #   avoid shadowing any firmware module)
├── geo.py                     # haversine, initial bearing, polar→screen projection
├── adsb.py                    # Provider base + AdsbLol/AdsbFi/AirplanesLive + normaliser
├── model.py                   # Contact tuple/class, Snapshot (contacts, ts, error state)
├── radar_view.py              # draws a Snapshot through a Renderer
├── settings_view.py           # app_components Menu/TextDialog wiring
├── render_ctx.py              # CtxRenderer
├── render_fb.py               # FbRenderer + GC9A01 driver (flagged)
├── fixtures.py                # canned API response for the simulator / offline dev
└── README.md (export-ignore via .gitattributes so it isn't downloaded to badges)
```
Keep imports flat (`import geo`) — apps are loaded from their folder; verify import style in the
simulator early (task V-1). Total code + assets should stay small (tens of KB); no binary assets.

**Threading model:** single async task. Override `run(self, render_update)`:

```python
async def run(self, render_update):
    await self._connect_wifi()            # status text while connecting
    while True:
        if self._foreground and self._due():
            self._set_status("updating")
            snap = self._fetch()          # blocking requests.get — acceptable 1–3 s stall
            self._apply(snap)             # or record error + schedule backoff
            gc.collect()
        await render_update()
        await asyncio.sleep(0.05)
```
Buttons stay in `update(delta)` (they don't fire while backgrounded). A blocking fetch briefly
freezes animation — mitigate by fetching at most every `interval` seconds and keeping payloads
small; a non-blocking socket client is a later optimisation, not MVP.

## 8. Algorithms (geo.py)

- Distance (haversine, km): `d = 2R·asin(√(sin²(Δφ/2) + cosφ1·cosφ2·sin²(Δλ/2)))`, R = 6371.
- Initial bearing: `θ = atan2(sin Δλ·cos φ2, cos φ1·sin φ2 − sin φ1·cos φ2·cos Δλ)` → 0–360°.
- Projection (north-up): `r_px = (d / radius_km) · R_screen` (R_screen ≈ 110 to leave a label
  margin); `x = r_px·sin θ`, `y = −r_px·cos θ` (ctx is centred at 0,0; for framebuf add 120,120).
- Aircraft glyph: triangle rotated by `track`; heading vector = short line from glyph along track.
- Precompute sin/cos per contact once per snapshot, not per frame.

## 9. Config schema (persisted JSON; all fields have defaults)

```json
{
  "version": 1,
  "location": {"name": "EMF (Eastnor)", "lat": 52.039554, "lon": -2.378344, "source": "preset"},
  "home":     {"name": "Home", "lat": null, "lon": null},
  "radius_km": 40,
  "interval_s": 15,
  "units": "aviation",
  "provider": "adsb_lol",
  "labels": "full",
  "max_aircraft": 30,
  "display": {"target": "main", "slot": 2,
              "pins": {"sck": "HS1", "mosi": "HS2", "dc": "HS3", "cs": "HS4"}}
}
```
Follow https://tildagon.badge.emfcamp.org/tildagon-apps/configuration/ for the storage mechanism;
if rolling our own, write atomically (temp file + rename) and tolerate a missing/corrupt file.

## 10. Publishing (https://tildagon.badge.emfcamp.org/tildagon-apps/publish/)

GitHub (or Codeberg) repo with `app.py`, `tildagon.toml`, a **release** (tag `v0.0.1`), and the
**`tildagon-app` topic** on the repo. The store picks it up within ~15 minutes and issues the
6-button install code. Category: **Apps**. Description ≤ 140 chars, e.g.
*"Live ADS-B flight radar: set your location and range, watch nearby aircraft on a radar scope.
Data: adsb.lol."*

```toml
[app]
name = "SkyScope"
category = "Apps"
wifi_preference = true

[entry]
class = "FlightRadarApp"

[metadata]
author = "<you>"
license = "MIT"
url = "https://github.com/<you>/skyscope"
description = "Live ADS-B flight radar: set location + range, see nearby aircraft on a radar scope."
version = "0.0.1"
```
Credit data sources in README + About screen (adsb.lol is community-run; be a polite client).

## 11. Milestones & acceptance criteria

- **M0 Scaffold** — repo, toml, hello-radar (static rings + ticks) running in the local simulator
  and on-badge via mpremote. ✅ App opens/minimises cleanly; 60 s soak with no crash.
- **M1 Radar core** — geo.py with unit tests (run under CPython on desktop), fixtures.py mock
  provider, contacts plotted correctly (validate 3 hand-computed bearing/distance cases), zoom
  steps, label declutter. ✅ Mock scene matches expected screen positions within ±2 px.
- **M2 Live data** — wifi connect flow, adsb.lol client, normaliser, status line, error/backoff,
  staleness handling, `gc.collect` discipline. ✅ 30 min soak on badge in real airspace without
  MemoryError; graceful message with Wi-Fi off.
- **M3 Settings** — full settings menu, persistence, manual lat/lon entry, IP locate, units,
  provider switch. ✅ Settings survive reboot; invalid input rejected with a Notification.
- **M4 Polish** — sweep/trails flags, LED-ring nearest-aircraft bearing (stretch), palette, About.
- **M5 Hexpansion display** — GC9A01 driver + FbRenderer + display-target setting + docs page in
  README describing the wiring table from §6.2. ✅ Radar renders on external panel; main screen
  shows status view; app still fully works with no hexpansion attached.
- **M6 Publish** — release, topic, store listing verified, install-code tested on a clean badge.

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| adsb.lol adds API keys / rate limits | provider abstraction + adsb.fi & airplanes.live fallbacks; clear on-screen error, backoff |
| Big JSON → MemoryError (2 MB PSRAM) | radius caps, contact cap, immediate dict→tuple reduction, gc.collect, budget parser as stretch |
| Blocking HTTP stalls UI | short payloads, poll interval ≥10 s, status text during fetch; async sockets later |
| HTTPS/TLS memory or cert issues in MicroPython `requests` | verify early on-device (V-3); adsb.fi/http fallback path if a provider offers it |
| Hexpansion pin API differs from assumption | V-2 verification before M5; keep pin roles configurable |
| ctx API details (arc/text metrics) differ from memory | code against the ctx reference page + simulator, not assumptions |
| App-store name collision / trademarks | search store before release; avoid Skyscanner/Flightradar marks |
| Simulator lacks networking | fixtures.py mock provider selected automatically when `wifi` import fails |

## 13. Verification tasks for Claude Code (do these FIRST, before writing app code)

- **V-1** Read in full: development guide, ctx reference, ui-elements reference, configuration
  page, run-on-badge, simulate, publish (URLs in §3/§10). Confirm app-folder import style in the
  simulator.
- **V-2** Read https://tildagon.badge.emfcamp.org/hexpansions/writing-hexpansion-apps/ and the
  firmware source (https://github.com/emfcamp/badge-2024-software) to confirm the exact
  hexpansion pin-access API (config object name, HS pin objects, LS/eGPIO access) and whether
  `machine.SPI` can be constructed on HS pins from app code.
- **V-3** On-device smoke test: `wifi.connect()` then `requests.get("https://api.adsb.lol/v2/lat/52.04/lon/-2.38/dist/25")`
  — confirm TLS works, measure response size/time, inspect real field names.
- **V-4** Re-check provider terms/limits (adsb.lol docs page, adsb.fi README, airplanes.live) and
  the app store for new flight-tracking apps.
- **V-5** Read Overhead (github.com/lissy93/overhead) and Tildagon WiFi Radar sources for platform
  idioms (polling, polar drawing, wifi handling).

## 14. References

- Docs root: https://tildagon.badge.emfcamp.org/
- App dev: …/tildagon-apps/development/ · ctx: …/reference/ctx/ · UI: …/reference/ui-elements/
- API example (requests + wifi): …/tildagon-apps/examples/api/
- Config: …/tildagon-apps/configuration/ · Publish: …/tildagon-apps/publish/
- Hexpansion spec/pinout: …/hexpansions/creating-hexpansions/ · hexpansion apps: …/hexpansions/writing-hexpansion-apps/
- Position capability: …/capabilities/registry/position/
- Firmware + simulator: https://github.com/emfcamp/badge-2024-software
- App directory: https://apps.badge.emfcamp.org/ · Overhead app: https://apps.badge.emfcamp.org/apps/42414141
- adsb.lol API docs: https://api.adsb.lol/docs · adsb.fi open data: https://github.com/adsbfi/opendata
