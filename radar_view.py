"""Draws a Snapshot as a radar scope through a Renderer.

North-up by default; `rotation` turns the scope so a chosen compass bearing is
at the top instead, for the touch ring's course-up mode.

Everything here goes through the renderer interface, so the same code drives
the badge's ctx canvas and an external GC9A01 panel. No firmware imports.
"""

from . import geo, model, touch, units as U

# --- phosphor-green theme ---------------------------------------------------
BG = (0.0, 0.0, 0.0)
GRID = (0.0, 0.30, 0.13)
GRID_BRIGHT = (0.0, 0.52, 0.22)
CARDINAL = (0.0, 0.70, 0.30)
CONTACT = (0.25, 1.0, 0.40)
CONTACT_STALE = (0.08, 0.42, 0.16)
LABEL = (0.60, 1.0, 0.65)
LABEL_DIM = (0.20, 0.60, 0.28)
STATUS = (0.0, 0.75, 0.32)
WARN = (1.0, 0.68, 0.10)
ERROR = (1.0, 0.28, 0.22)
SELECT = (1.0, 1.0, 0.45)
WEDGE = (0.0, 0.24, 0.11)
WEDGE_EDGE = (0.0, 0.55, 0.24)
ARMED = (0.95, 0.62, 0.08)

# --- layout (centred coordinate space, panel radius 120) --------------------
R_SCREEN = 100          # outer range ring == the configured radius
RINGS = (33, 67, 100)
TICK_INNER = 93
CARDINAL_R = 82
HEADER_Y = -111         # range label, just outside the outer ring
STATUS_Y = 111

GLYPH = ((0.0, -5.0), (3.6, 4.2), (0.0, 1.8), (-3.6, 4.2))
TRAIL_MAX = 5


class RadarView:
    """Stateful scope renderer: owns the sweep phase and per-contact trails."""

    def __init__(self, conf):
        self.conf = conf
        self.sweep_deg = 0.0
        # Bounding boxes of the labels drawn in the last frame; kept so the
        # declutter behaviour is directly assertable in tests.
        self.label_boxes = []

        # Touch-ring state, all owned by the app and simply rendered here.
        self.rotation = 0.0          # compass bearing shown at the top
        self.active_sector = None    # highlighted wedge
        self.armed_sectors = ()      # sectors watched in alerts mode
        self.filter_sector = None    # when set, only this sector is drawn

        self._pulse_ms = 0
        self._trails = {}
        self._trail_ts = 0

    # -- geometry ------------------------------------------------------------

    def screen_bearing(self, compass_bearing):
        """Compass bearing to the bearing drawn on screen, honouring rotation."""
        if not self.rotation:
            return compass_bearing
        return (compass_bearing - self.rotation) % 360.0

    # -- animation -----------------------------------------------------------

    def update(self, delta_ms):
        if self.conf.get("sweep"):
            # One revolution every four seconds.
            self.sweep_deg = (self.sweep_deg + delta_ms * 0.09) % 360.0
        if self.armed_sectors:
            self._pulse_ms = (self._pulse_ms + delta_ms) % 1600

    def _record_trails(self, snapshot):
        """Append the current position of each contact, once per snapshot."""
        if snapshot.ts_ms == self._trail_ts:
            return
        self._trail_ts = snapshot.ts_ms
        seen = set()
        for c in snapshot.contacts:
            seen.add(c.icao)
            hist = self._trails.get(c.icao)
            if hist is None:
                hist = self._trails[c.icao] = []
            hist.append((c.dist_km, c.bearing))
            if len(hist) > TRAIL_MAX:
                del hist[0]
        for icao in [k for k in self._trails if k not in seen]:
            del self._trails[icao]

    def forget_trails(self):
        self._trails.clear()
        self._trail_ts = 0

    # -- drawing -------------------------------------------------------------

    def draw(self, r, snapshot, age_s=0, selected=None):
        conf = self.conf
        unit = conf.get("units", U.AVIATION)
        radius_km = snapshot.radius_km or conf.get("radius_km", 40)

        r.clear(BG)
        # The wedge goes down first so everything else sits on top of it.
        if self.filter_sector is not None:
            self._draw_wedge(r, self.filter_sector, WEDGE_EDGE)
        elif self.active_sector is not None:
            self._draw_wedge(r, self.active_sector, WEDGE)
        self._draw_grid(r, radius_km, unit)
        if self.armed_sectors:
            self._draw_armed(r)

        if conf.get("sweep"):
            self._draw_sweep(r)

        visible = self.visible_contacts(snapshot)
        if snapshot.contacts:
            self._record_trails(snapshot)
            if conf.get("trails"):
                self._draw_trails(r, radius_km)
        if visible:
            self._draw_contacts(r, visible, radius_km, unit, selected)
        elif snapshot.ok:
            self.label_boxes = []
            empty = "SECTOR CLEAR" if self.filter_sector is not None else "NO CONTACTS"
            r.text(empty, 0, -14, LABEL_DIM, 12, "center")

        self._draw_status(r, snapshot, age_s, len(visible))
        r.flush()

    def _draw_grid(self, r, radius_km, unit):
        # Range rings, outermost brightest so the selected radius reads clearly.
        for i, ring in enumerate(RINGS):
            r.circle(0, 0, ring, GRID_BRIGHT if i == len(RINGS) - 1 else GRID)

        # Tick every 30 degrees. Rotation moves where each compass bearing is
        # drawn, so ticks and letters stay consistent with the contacts.
        for deg in range(0, 360, 30):
            ux, uy = geo.bearing_to_unit(self.screen_bearing(deg))
            bright = (deg % 90) == 0
            r.line(
                ux * TICK_INNER, uy * TICK_INNER,
                ux * R_SCREEN, uy * R_SCREEN,
                CARDINAL if bright else GRID,
            )

        # Cardinal letters inside the ticks.
        for deg, letter in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            ux, uy = geo.bearing_to_unit(self.screen_bearing(deg))
            r.text(letter, ux * CARDINAL_R, uy * CARDINAL_R, CARDINAL, 11, "center")

        # Observer marker.
        r.line(-4, 0, 4, 0, CARDINAL)
        r.line(0, -4, 0, 4, CARDINAL)

        header = "R " + U.fmt_radius(radius_km, unit)
        size = 13
        if self.rotation:
            # Course-up: say which way is up, or the scope silently lies. The
            # header sits near the top of a round panel, where there is only
            # about 90px of chord, so it shrinks to make room.
            header += "  ^" + U.fmt_bearing(self.rotation)
            size = 10
        r.text(header, 0, HEADER_Y, STATUS, size, "center")

        # Intermediate ring labels, offset onto the 045 diagonal so they do not
        # sit under the cardinal ticks or the contacts clustered near north.
        dx, dy = geo.bearing_to_unit(45)
        for ring in RINGS[:-1]:
            km = radius_km * ring / float(R_SCREEN)
            r.text(
                U.fmt_radius(km, unit),
                dx * ring, dy * ring - 6,
                GRID_BRIGHT, 8, "center",
            )

    def _draw_sweep(self, r):
        # A short trailing fan approximates the afterglow of a real scope.
        for i in range(8):
            deg = (self.sweep_deg - i * 4.0) % 360.0
            level = (8 - i) / 8.0 * 0.55
            ux, uy = geo.bearing_to_unit(deg)
            r.line(0, 0, ux * R_SCREEN, uy * R_SCREEN, (0.0, level, level * 0.42))

    def _draw_wedge(self, r, sector, colour):
        """Fill the 30 degree slice a touched sector covers."""
        centre = self.screen_bearing(touch.bearing_of(sector))
        half = touch.SECTOR_DEG / 2.0
        steps = 5
        pts = [(0.0, 0.0)]
        for i in range(steps + 1):
            b = centre - half + (touch.SECTOR_DEG * i / steps)
            ux, uy = geo.bearing_to_unit(b)
            pts.append((ux * R_SCREEN, uy * R_SCREEN))
        r.poly(pts, colour, fill=True)

    def _draw_armed(self, r):
        """Thicken the outer ring across each armed sector.

        Drawn on the ring rather than outside it: there is no room out there
        without colliding with the range header, and an armed *arc* reads as
        "watching this slice of the horizon" more directly than tick marks do.
        """
        # Pulse so an armed bearing is obvious without stealing attention.
        phase = self._pulse_ms / 1600.0
        level = 0.45 + 0.55 * abs(1.0 - 2.0 * phase)
        colour = (ARMED[0] * level, ARMED[1] * level, ARMED[2] * level)
        half = touch.SECTOR_DEG / 2.0 - 1.0
        steps = 6
        for sector in self.armed_sectors:
            centre = self.screen_bearing(touch.bearing_of(sector))
            prev = None
            for i in range(steps + 1):
                b = centre - half + (2.0 * half * i / steps)
                ux, uy = geo.bearing_to_unit(b)
                point = (ux * R_SCREEN, uy * R_SCREEN)
                if prev is not None:
                    r.line(prev[0], prev[1], point[0], point[1], colour, 3)
                prev = point

    def _draw_trails(self, r, radius_km):
        for hist in self._trails.values():
            if len(hist) < 2:
                continue
            prev = None
            for i, (dist_km, bearing) in enumerate(hist):
                x, y = geo.polar_to_screen(
                    dist_km, self.screen_bearing(bearing), radius_km, R_SCREEN
                )
                if prev is not None:
                    level = 0.18 + 0.32 * (i / float(len(hist)))
                    r.line(prev[0], prev[1], x, y, (0.0, level, level * 0.45))
                prev = (x, y)

    def _draw_contacts(self, r, drawn, radius_km, unit, selected):
        conf = self.conf
        mode = conf.get("labels", "full")
        label_count = conf.get("label_count", 6) if mode != "off" else 0

        if self.filter_sector is not None:
            # A locked sector holds far fewer aircraft, so labels can expand to
            # cover all of them -- that is the point of filtering.
            label_count = len(drawn) if mode != "off" else 0

        # Glyphs first, so labels always sit on top of them.
        placed = _reserved_boxes()
        positions = []
        for c in drawn:
            screen_b = self.screen_bearing(c.bearing)
            x, y = geo.polar_to_screen(c.dist_km, screen_b, radius_km, R_SCREEN)
            positions.append((x, y))
            is_sel = selected is not None and c.icao == selected
            colour = CONTACT_STALE if c.stale else CONTACT
            if is_sel:
                colour = SELECT
                r.circle(x, y, 9, SELECT)

            # The glyph points along the track as drawn, so it turns with the
            # scope in course-up mode.
            track = self.screen_bearing(c.track_deg) if c.track_deg is not None else 0.0
            pts = []
            for gx, gy in GLYPH:
                rx, ry = geo.rotate(gx, gy, track)
                pts.append((x + rx, y + ry))
            r.poly(pts, colour, fill=True)

            # Heading vector, length scaled by ground speed.
            if c.track_deg is not None and not c.on_ground:
                length = 8.0
                if c.gs_kt:
                    length = 6.0 + min(c.gs_kt, 600.0) / 60.0
                ux, uy = geo.bearing_to_unit(track)
                r.line(x + ux * 5, y + uy * 5, x + ux * length, y + uy * length, colour)

            # Every glyph reserves its own space, so a label never lands on
            # another aircraft.
            placed.append((x - 6, y - 6, x + 6, y + 6))

        # drawn is sorted by distance, so this labels the nearest aircraft first
        # and gives up on any whose block would collide with one already drawn --
        # which is what keeps a busy centre readable.
        self.label_boxes = []
        for i, c in enumerate(drawn):
            if len(self.label_boxes) >= label_count:
                break
            x, y = positions[i]
            box = self._place_label(r, c, x, y, unit, mode, placed, i, selected)
            if box is not None:
                placed.append(box)
                self.label_boxes.append(box)

    def visible_contacts(self, snapshot):
        """Contacts the scope is currently showing, after any sector filter."""
        if self.filter_sector is None:
            return snapshot.contacts
        return [
            c for c in snapshot.contacts
            if touch.sector_of(c.bearing) == self.filter_sector
        ]

    def _place_label(self, r, c, x, y, unit, mode, placed, index, selected):
        lines = [c.label]
        if mode == "full":
            # Altitude and speed share a line: a three-line block is twice as
            # likely to collide with a neighbour, for no extra information.
            lines.append(
                U.fmt_alt(c.alt_ft, unit, c.on_ground)
                + " "
                + U.fmt_speed(c.gs_kt, unit)
            )

        sizes = [10] + [8] * (len(lines) - 1)
        width = max(len(s) * sz * 0.55 for s, sz in zip(lines, sizes))
        height = 4 + 10 * len(lines)
        gap = 8

        # Four candidate positions, tried in order. The inboard side comes
        # first so blocks lean towards the middle of the round panel rather
        # than off its edge.
        beside_in = (x - gap - width, y - height / 2.0, "right", x - gap)
        beside_out = (x + gap, y - height / 2.0, "left", x + gap)
        if x <= 0:
            beside_in, beside_out = beside_out, beside_in
        candidates = (
            beside_in,
            beside_out,
            (x - width / 2.0, y - gap - height, "center", x),
            (x - width / 2.0, y + gap, "center", x),
        )

        for left, top, align, tx in candidates:
            box = (left, top, left + width, top + height)
            if _inside_panel(box) and not _collides(box, placed):
                is_sel = selected is not None and c.icao == selected
                colour = SELECT if is_sel else (LABEL_DIM if c.stale else LABEL)
                for n, text in enumerate(lines):
                    r.text(text, tx, top + 6 + n * 10, colour, sizes[n], align)
                return box
        return None

    def _draw_status(self, r, snapshot, age_s, visible_count=None):
        state = snapshot.state
        if state == model.STATE_ERROR:
            colour, text = ERROR, snapshot.message or "ERROR"
        elif state == model.STATE_CONNECTING:
            colour, text = WARN, snapshot.message or "CONNECTING"
        elif state == model.STATE_UPDATING:
            colour, text = WARN, "UPDATING"
        elif state == model.STATE_OK:
            colour = STATUS
            age = max(0, int(age_s))
            shown = len(snapshot.contacts) if visible_count is None else visible_count
            if self.filter_sector is not None:
                # Say which sector, so a filtered scope is never mistaken for
                # an empty sky.
                colour = WEDGE_EDGE
                text = "%s  %d AC  %ds" % (
                    U.fmt_bearing(touch.bearing_of(self.filter_sector)), shown, age,
                )
            elif snapshot.total > shown:
                text = "%d/%d AC  %ds" % (shown, snapshot.total, age)
            else:
                text = "%d AC  %ds" % (shown, age)
        else:
            colour, text = STATUS, snapshot.message or "READY"

        r.text(text, 0, STATUS_Y, colour, 10, "center")


PANEL_R = 116  # keep label boxes off the bezel of the round panel

_RESERVED = None


def _reserved_boxes():
    """Chrome that aircraft labels must not cover: cardinals, header, status."""
    global _RESERVED
    if _RESERVED is None:
        boxes = [(-34, -120, 34, -103), (-46, 103, 46, 120)]
        for deg in (0, 90, 180, 270):
            ux, uy = geo.bearing_to_unit(deg)
            cx, cy = ux * CARDINAL_R, uy * CARDINAL_R
            boxes.append((cx - 7, cy - 8, cx + 7, cy + 8))
        _RESERVED = tuple(boxes)
    return list(_RESERVED)


def _inside_panel(box):
    x0, y0, x1, y1 = box
    limit = PANEL_R * PANEL_R
    for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        if px * px + py * py > limit:
            return False
    return True


def _collides(box, placed):
    x0, y0, x1, y1 = box
    for px0, py0, px1, py1 in placed:
        if x0 < px1 and px0 < x1 and y0 < py1 and py0 < y1:
            return True
    return False


def status_view(r, title, lines, colour=STATUS):
    """Plain text screen, used for the About page and the hexpansion status view."""
    r.clear(BG)
    r.text(title, 0, -70, CARDINAL, 15, "center")
    for i, line in enumerate(lines):
        r.text(line, 0, -40 + i * 15, colour, 10, "center")
    r.flush()


def detail_view(r, c, unit, obs_name):
    """Full readout for one selected contact."""
    r.clear(BG)
    r.text(c.label, 0, -84, SELECT, 17, "center")
    rows = (
        ("TYPE", c.ac_type or "---"),
        ("REG", c.reg or "---"),
        ("ALT", U.fmt_alt(c.alt_ft, unit, c.on_ground)),
        ("SPD", U.fmt_speed(c.gs_kt, unit)),
        ("V/S", U.fmt_rate(c.baro_rate, unit)),
        ("TRK", U.fmt_bearing(c.track_deg) if c.track_deg is not None else "---"),
        ("RNG", U.fmt_dist(c.dist_km, unit, True)),
        ("BRG", U.fmt_bearing(c.bearing)),
        ("SQK", c.squawk or "---"),
    )
    for i, (k, v) in enumerate(rows):
        y = -58 + i * 15
        r.text(k, -52, y, LABEL_DIM, 10, "left")
        r.text(v, 58, y, LABEL, 11, "right")
    r.text("from " + obs_name, 0, 92, GRID_BRIGHT, 8, "center")
    r.flush()
