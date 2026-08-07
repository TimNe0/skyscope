# SkyScope controls

Single source of truth for the button map. Keep this in step with
`_handle_buttons()` in `app.py`.

| Button | Radar scope | Settings menu | Detail / About |
|---|---|---|---|
| **UP** | zoom out — next range step | previous item | — |
| **DOWN** | zoom in — previous range step | next item | — |
| **CONFIRM** | open detail for the touched aircraft, else cycle labels | select / edit | — |
| **RIGHT** | refresh now | show item info (where available) | — |
| **LEFT** | open settings | back one level | back |
| **CANCEL** | clear touch state, else minimise | back one level, close at the top | back |

Two of those are context-sensitive, both on the radar screen:

- **CONFIRM** acts on the aircraft picked with the touch ring if there is one,
  because that is the obvious thing to do with a selection. With nothing
  selected it keeps its original job of cycling label density.
- **CANCEL** first undoes whatever the touch ring is doing — a sector filter, a
  course-up rotation, a selection — and only minimises the app once the scope is
  back to plain north-up. A filtered scope should never be a state you can only
  escape by quitting.

Range steps: **5 · 10 · 20 · 40 · 80 · 160 km**. The settings menu offers a
longer list (5–200 km); zooming from a value between steps moves to the next
step beyond it.

Changing the range refetches rather than re-projecting, because contacts outside
the new ring were filtered out of the previous snapshot.

## Touch ring (2026 Spaceagon only)

The badge's twelve capacitive touch points sit in a ring, one per LED. A radar is
a polar display and the ring is a polar input, so they map onto each other
exactly: twelve pads, twelve 30° compass sectors, matching the ticks already on
the scope. `TOUCH01` is at the top and reads as north.

The gesture vocabulary is the same in every mode:

| Gesture | What it does |
|---|---|
| **Touch a pad** | highlights that 30° wedge, lights the LED, and selects the nearest aircraft in it. Sticky, so CONFIRM can act on it after you lift off. |
| **Slide round the bezel** | scrubs through bearings — sweep the horizon with a finger |
| **Hold ~0.8 s** | does the mode-specific action below |

Only the hold action changes between modes, selected in Settings → Touch:

| Mode | Hold a sector to… |
|---|---|
| **Off** | nothing; the ring is ignored |
| **Scrub bearings** | nothing — tap-to-pick only |
| **Hold to arm alert** | watch that bearing. The arc thickens in amber and pulses, the LED breathes, and you get a notification when an aircraft enters it. Armed sectors persist across reboots. |
| **Hold to filter** | show only that sector. Labels expand to cover everything in it, and the status line shows the bearing so a filtered scope is never mistaken for empty sky. |
| **Hold for course-up** | turn the scope so that bearing is at the top. The header shows `^090`, and the cardinal letters, ticks, glyphs and LED ring all rotate with it. |

Holding the same sector again undoes it. CANCEL clears everything.

On a 2024 Tildagon there is no touch hardware, so the setting is still visible
but every mode is a no-op — selecting one says so rather than looking broken.

## Text entry

Manual latitude/longitude entry uses the firmware's keypad dialog with a
SkyScope-specific alphabet — `0123456789.-` — so southern latitudes and western
longitudes can actually be typed. `Done` commits, `Cancel` (via the `...` key)
abandons the entry and leaves the location unchanged.
