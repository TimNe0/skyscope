"""Settings screens, built from the firmware's Menu component.

Menu registers its own button handler on the eventbus, so while a menu is open
the radar screen must not act on buttons -- app.py gates on `self.screen`.

Anything needing a dialog (manual lat/lon, IP lookup) cannot run from a Menu
handler, because dialogs are async and need the render_update callback that only
exists inside App.run(). Those handlers park a coroutine factory on
`self.pending`, which app.py awaits on its next pass.
"""

from app_components import Menu

from . import adsb, conf as C, geo, touch, units as U

BACK = "< Back"


class SettingsView:
    """A stack of Menus over the config dict.

    `on_change(keys)` fires after every edit so the app can react -- refetch on a
    location change, re-project on a radius change, and so on.
    """

    def __init__(self, app, cfg, on_change, on_close, on_about, on_select_contact,
                 contacts_provider):
        self.app = app
        self.cfg = cfg
        self.on_change = on_change
        self.on_close = on_close
        self.on_about = on_about
        self.on_select_contact = on_select_contact
        self.contacts_provider = contacts_provider
        self.pending = None
        self.menu = None
        self._closed = False
        self._contacts = []
        # Openers for every level currently on screen; the first is the root.
        self._stack = [self._open_root]
        self._open_root()

    # -- menu plumbing -------------------------------------------------------

    def _open(self, items, select_handler, position=0):
        self._close_menu()
        self.menu = Menu(
            self.app,
            menu_items=items,
            position=position,
            select_handler=select_handler,
            back_handler=self._back,
        )

    def _close_menu(self):
        if self.menu is not None:
            # Menu keeps an eventbus subscription; leaking one would leave a
            # dead menu eating button presses.
            self.menu._cleanup()
            self.menu = None

    def _push(self, opener):
        self._stack.append(opener)
        opener()

    def _back(self):
        if len(self._stack) > 1:
            self._stack.pop()
            self._stack[-1]()
        else:
            self.close()

    def close(self):
        self._closed = True
        self._close_menu()
        self._stack = [self._open_root]
        self.on_close()

    async def run_job(self, job, render_update):
        """Run a parked dialog/lookup coroutine with the menu out of the way.

        Menu and the dialogs both subscribe to ButtonDownEvent, so leaving the
        menu open underneath a dialog would drive both at once.
        """
        self._close_menu()
        try:
            await job(render_update)
        finally:
            if not self._closed and self.menu is None and self._stack:
                self._stack[-1]()

    def update(self, delta):
        if self.menu is not None:
            self.menu.update(delta)

    def draw(self, ctx):
        if self.menu is not None:
            self.menu.draw(ctx)

    @property
    def _position(self):
        return self.menu.position if self.menu is not None else 0

    # -- root ----------------------------------------------------------------

    def _open_root(self, position=0):
        cfg = self.cfg
        unit = cfg["units"]
        items = [
            "Location: " + cfg["location"]["name"],
            "Radius: " + U.fmt_radius(cfg["radius_km"], unit),
            "Update: %ds" % cfg["interval_s"],
            "Units: " + cfg["units"],
            "Source: " + adsb.get_provider(cfg["provider"]).name,
            "Labels: " + cfg["labels"],
            "Sweep: " + _onoff(cfg["sweep"]),
            "Trails: " + _onoff(cfg["trails"]),
            "LED ring: " + _onoff(cfg["led_ring"]),
            "Touch: " + touch.MODE_LABELS[cfg["touch_mode"]],
        ]
        if C.FEATURE_HEXPANSION_DISPLAY:
            items.append("Display: " + _display_label(cfg))
        items.append("Contacts")
        items.append("About")
        items.append(BACK)
        self._open(items, self._select_root, position)

    def _select_root(self, item, idx):
        if item == BACK:
            self.close()
        elif item.startswith("Location"):
            self._push(self._open_location)
        elif item.startswith("Radius"):
            self._push(self._open_radius)
        elif item.startswith("Update"):
            self._push(self._open_interval)
        elif item.startswith("Units"):
            self._push(self._open_units)
        elif item.startswith("Source"):
            self._push(self._open_provider)
        elif item.startswith("Labels"):
            self._push(self._open_labels)
        elif item.startswith("Touch"):
            self._push(self._open_touch)
        elif item.startswith("Display"):
            self._push(self._open_display)
        elif item == "Contacts":
            self._push(self._open_contacts)
        elif item == "About":
            self.on_about()
        elif item.startswith("Sweep"):
            self._toggle("sweep", idx)
        elif item.startswith("Trails"):
            self._toggle("trails", idx)
        elif item.startswith("LED ring"):
            self._toggle("led_ring", idx)

    def _toggle(self, key, position):
        self.cfg[key] = not self.cfg.get(key)
        self._changed(key)
        # Rebuild in place so the label updates without losing the cursor.
        self._open_root(position)

    def _changed(self, *keys):
        C.save(self.cfg)
        self.on_change(keys)

    # -- location ------------------------------------------------------------

    def _open_location(self):
        # Your own place first, then the fixed presets. Home is always present
        # even when unset, so the customisable slot never moves around.
        items = [_home_label(self.cfg)]
        items += [name for name, _lat, _lon in C.PRESETS]
        items += ["Set Home to here", "Manual entry", "Auto (IP)", BACK]
        self._open(items, self._select_location)

    def _select_location(self, item, idx):
        if item == BACK:
            self._back()
        elif idx == 0:
            home = self.cfg["home"]
            if home.get("lat") is None:
                # Nothing saved yet, so go straight to defining it.
                self.pending = lambda ru: self._enter_coords(ru, as_home=True)
            else:
                self._set_location(home.get("name") or "Home",
                                   home["lat"], home["lon"], "home")
        elif item == "Manual entry":
            self.pending = self._enter_coords
        elif item == "Auto (IP)":
            self.pending = self._locate_by_ip
        elif item == "Set Home to here":
            place = self.cfg["location"]
            self.cfg["home"] = {
                "name": "Home", "lat": place["lat"], "lon": place["lon"],
            }
            self._changed("home")
            self._notify("Home saved")
            self._open_location()
        else:
            for name, lat, lon in C.PRESETS:
                if name == item:
                    self._set_location(name, lat, lon, "preset")
                    return

    def _set_location(self, name, lat, lon, source):
        self.cfg["location"] = {
            "name": name, "lat": float(lat), "lon": float(lon), "source": source,
        }
        self._changed("location")

    async def _enter_coords(self, render_update, as_home=False):
        from .dialogs import CoordDialog

        prompt = "Home latitude" if as_home else "Latitude"
        lat = await _ask_number(self.app, render_update, prompt, CoordDialog)
        if lat is None:
            return
        prompt = "Home longitude" if as_home else "Longitude"
        lon = await _ask_number(self.app, render_update, prompt, CoordDialog)
        if lon is None:
            return
        if not geo.valid_lat(lat) or not geo.valid_lon(lon):
            self._notify("Out of range")
            return
        if as_home:
            self.cfg["home"] = {"name": "Home", "lat": lat, "lon": lon}
            self._changed("home")
            self._set_location("Home", lat, lon, "home")
            self._notify("Home set")
        else:
            self._set_location("Manual", lat, lon, "manual")
            self._notify("Location set")
        self._back()

    async def _locate_by_ip(self, render_update):
        self._notify("Locating...")
        await render_update()
        try:
            import requests
        except ImportError:
            self._notify("No network")
            return
        response = None
        data = None
        try:
            # Plain HTTP on purpose: ip-api's free tier is HTTP-only, and it
            # keeps this off the badge's TLS stack.
            response = requests.get(
                "http://ip-api.com/json",
                headers={"User-Agent": adsb.DEFAULT_UA},
            )
            data = response.json()
        except Exception:
            self._notify("Lookup failed")
            return
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        lat = data.get("lat") if isinstance(data, dict) else None
        lon = data.get("lon") if isinstance(data, dict) else None
        if not geo.valid_lat(lat) or not geo.valid_lon(lon):
            self._notify("Lookup failed")
            return
        city = data.get("city") or "IP estimate"
        # City-level accuracy only -- the "~" keeps that visible on the scope.
        self._set_location("~" + city, lat, lon, "ip")
        self._notify("Approx: " + city)
        self._back()

    # -- simple choice lists -------------------------------------------------

    def _open_radius(self):
        unit = self.cfg["units"]
        items = [U.fmt_radius(km, unit) for km in C.RADIUS_CHOICES] + [BACK]
        self._open(items, self._select_radius, _index_of(C.RADIUS_CHOICES, self.cfg["radius_km"]))

    def _select_radius(self, item, idx):
        if item != BACK:
            self.cfg["radius_km"] = C.RADIUS_CHOICES[idx]
            self._changed("radius_km")
        self._back()

    def _open_interval(self):
        items = ["%ds" % s for s in C.INTERVAL_CHOICES] + [BACK]
        self._open(items, self._select_interval, _index_of(C.INTERVAL_CHOICES, self.cfg["interval_s"]))

    def _select_interval(self, item, idx):
        if item != BACK:
            self.cfg["interval_s"] = C.INTERVAL_CHOICES[idx]
            self._changed("interval_s")
        self._back()

    def _open_units(self):
        items = [U.LABELS[u] for u in U.CHOICES] + [BACK]
        self._open(items, self._select_units, _index_of(U.CHOICES, self.cfg["units"]))

    def _select_units(self, item, idx):
        if item != BACK:
            self.cfg["units"] = U.CHOICES[idx]
            self._changed("units")
        self._back()

    def _open_provider(self):
        items = [p.name for p in adsb.PROVIDERS] + [BACK]
        self._open(items, self._select_provider, _index_of(adsb.PROVIDER_KEYS, self.cfg["provider"]))

    def _select_provider(self, item, idx):
        if item != BACK:
            self.cfg["provider"] = adsb.PROVIDERS[idx].key
            self._changed("provider")
        self._back()

    def _open_labels(self):
        items = list(C.LABEL_MODES) + [BACK]
        self._open(items, self._select_labels, _index_of(C.LABEL_MODES, self.cfg["labels"]))

    def _select_labels(self, item, idx):
        if item != BACK:
            self.cfg["labels"] = C.LABEL_MODES[idx]
            self._changed("labels")
        self._back()

    # -- touch ring ----------------------------------------------------------

    def _open_touch(self):
        items = [touch.MODE_LABELS[m] for m in touch.MODES]
        if self.cfg["touch_alerts"]:
            items.append("Clear %d armed" % len(self.cfg["touch_alerts"]))
        items.append(BACK)
        position = _index_of(touch.MODES, self.cfg["touch_mode"])
        self._open(items, self._select_touch, position)

    def _select_touch(self, item, idx):
        if item == BACK:
            self._back()
            return
        if item.startswith("Clear "):
            self.cfg["touch_alerts"] = []
            self._changed("touch_alerts")
            self._notify("Alerts cleared")
            self._open_touch()
            return
        self.cfg["touch_mode"] = touch.MODES[idx]
        self._changed("touch_mode")
        if touch.MODES[idx] != touch.MODE_OFF and not self.app.touch.available:
            # Say so rather than letting the setting look broken.
            self._notify("Needs a 2026 badge")
        else:
            self._notify(touch.MODE_HINTS[touch.MODES[idx]])
        self._back()

    # -- display target ------------------------------------------------------

    def _open_display(self):
        items = ["Main screen"] + ["Hexpansion slot %d" % s for s in range(1, 7)]
        items.append(BACK)
        display = self.cfg["display"]
        position = 0 if display["target"] == "main" else display["slot"]
        self._open(items, self._select_display, position)

    def _select_display(self, item, idx):
        if item != BACK:
            if idx == 0:
                self.cfg["display"]["target"] = "main"
            else:
                self.cfg["display"]["target"] = "hexpansion"
                self.cfg["display"]["slot"] = idx
            self._changed("display")
        self._back()

    # -- contacts ------------------------------------------------------------

    def _open_contacts(self):
        self._contacts = list(self.contacts_provider())
        unit = self.cfg["units"]
        items = ["%s %s" % (c.label, U.fmt_dist(c.dist_km, unit)) for c in self._contacts]
        if not items:
            items = ["(no contacts)"]
        items.append(BACK)
        self._open(items, self._select_contact)

    def _select_contact(self, item, idx):
        if item == BACK or idx >= len(self._contacts):
            self._back()
            return
        # Handlers run from the eventbus, not from the app's update pass, so
        # this has to hand the choice over directly rather than parking it on
        # an attribute the app would have to poll.
        self.on_select_contact(self._contacts[idx].icao)

    # -- helpers -------------------------------------------------------------

    def _notify(self, message):
        from app_components import Notification

        self.app.notification = Notification(message)


def _onoff(value):
    return "on" if value else "off"


def _home_label(cfg):
    home = cfg.get("home") or {}
    if home.get("lat") is None:
        return "Home (not set)"
    return "Home"


def _index_of(choices, value):
    try:
        return list(choices).index(value)
    except ValueError:
        return 0


def _display_label(cfg):
    display = cfg["display"]
    if display["target"] == "main":
        return "Main"
    return "Hex %d" % display["slot"]


async def _ask_number(app, render_update, prompt, dialog_class):
    dialog = dialog_class(prompt, app)
    result = await dialog.run(render_update)
    if result is False or result is None or result == "":
        return None
    try:
        return float(result)
    except ValueError:
        return None
