# SkyScope controls

Single source of truth for the button map. Keep this in step with
`_handle_buttons()` in `app.py`.

| Button | Radar scope | Settings menu | Detail / About |
|---|---|---|---|
| **UP** | zoom out — next range step | previous item | — |
| **DOWN** | zoom in — previous range step | next item | — |
| **CONFIRM** | cycle labels: off → callsign → full | select / edit | — |
| **RIGHT** | refresh now | show item info (where available) | — |
| **LEFT** | open settings | back one level | back |
| **CANCEL** | minimise SkyScope | back one level, close at the top | back |

Range steps: **5 · 10 · 20 · 40 · 80 · 160 km**. The settings menu offers a
longer list (5–200 km); zooming from a value between steps moves to the next
step beyond it.

Changing the range refetches rather than re-projecting, because contacts outside
the new ring were filtered out of the previous snapshot.

## Text entry

Manual latitude/longitude entry uses the firmware's keypad dialog with a
SkyScope-specific alphabet — `0123456789.-` — so southern latitudes and western
longitudes can actually be typed. `Done` commits, `Cancel` (via the `...` key)
abandons the entry and leaves the location unchanged.
