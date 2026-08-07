"""The 12-point capacitive touch ring, as a bearing input.

A radar is a polar display and the touch ring is a polar input, so the two map
onto each other exactly: twelve touch points, twelve LEDs, twelve 30 degree
compass sectors, matching the tick marks already drawn on the scope.

**2026 Spaceagon hardware only.** The 2024 Tildagon frontboard has six buttons
and no touch ring, and the simulator stubs the touch inputs out entirely and
never fires them, so `available` is False in both cases and every mode quietly
becomes a no-op.

One gesture vocabulary across all modes:

* touching a sector makes it *active* -- the scope highlights that wedge and
  selects the nearest aircraft in it. The selection is sticky, so CONFIRM can
  act on it after you lift your finger.
* holding a sector for `HOLD_MS` does the mode-specific thing (arm an alert,
  lock a filter, rotate the scope).

No firmware imports at module scope, so the sector maths is testable off-badge.
"""

SECTORS = 12
SECTOR_DEG = 360.0 / SECTORS
HOLD_MS = 800

MODE_OFF = "off"
MODE_SCRUB = "scrub"
MODE_ALERTS = "alerts"
MODE_FILTER = "filter"
MODE_COURSE = "course"

MODES = (MODE_OFF, MODE_SCRUB, MODE_ALERTS, MODE_FILTER, MODE_COURSE)

MODE_LABELS = {
    MODE_OFF: "Off",
    MODE_SCRUB: "Scrub bearings",
    MODE_ALERTS: "Hold to arm alert",
    MODE_FILTER: "Hold to filter",
    MODE_COURSE: "Hold for course-up",
}

MODE_HINTS = {
    MODE_OFF: "Touch ring ignored",
    MODE_SCRUB: "Touch a bearing to pick the nearest aircraft",
    MODE_ALERTS: "Hold a bearing to be told when traffic enters it",
    MODE_FILTER: "Hold a bearing to show only that sector",
    MODE_COURSE: "Hold a bearing to turn the scope that way up",
}

# TOUCH01 sits level with LED 1, which the firmware's own pattern code treats as
# the top of the badge. If a board revision rotates the pads, this is the single
# constant to change.
NORTH_INDEX = 0


def bearing_of(sector):
    """Compass bearing at the centre of a sector."""
    return ((sector - NORTH_INDEX) % SECTORS) * SECTOR_DEG


def sector_of(bearing):
    """Sector containing a compass bearing."""
    return int((bearing % 360.0 + SECTOR_DEG / 2.0) // SECTOR_DEG + NORTH_INDEX) % SECTORS


def in_sector(bearing, sector):
    return sector_of(bearing) == sector


class TouchRing:
    """Polls the frontboard's touch state and turns it into sector gestures."""

    def __init__(self):
        self.available = False
        self.sector = None          # sector under a finger right now
        self._states = None
        self._keys = ()
        self._held_ms = 0
        self._hold_done = False
        self._load()

    def _load(self):
        try:
            from frontboards.utils import detect_frontboard

            if (detect_frontboard() & 0xFF00) != 0x2600:
                return
            from frontboards.twentysix import TwentyTwentySix

            self._states = TwentyTwentySix.touch_states
            self._keys = tuple(
                "TOUCH%02d" % (i + 1) for i in range(SECTORS)
            )
            # A board that reports 2026 but exposes fewer pads would index out
            # of range later, so check the keys really are there.
            for key in self._keys:
                if key not in self._states:
                    return
            self.available = True
        except Exception as exc:  # no touch hardware, or a firmware without it
            print("[skyscope] touch ring unavailable:", exc)

    def _touched_sector(self):
        states = self._states
        # Keep the current sector while it stays down, so resting a finger
        # across two pads does not flicker between them.
        if self.sector is not None and states[self._keys[self.sector]][0]:
            return self.sector
        for i, key in enumerate(self._keys):
            if states[key][0]:
                return i
        return None

    def update(self, delta_ms):
        """Refresh from hardware.

        Returns the sector a hold has just completed on, or None. Holds fire
        once per touch, not repeatedly.
        """
        if not self.available:
            return None
        sector = self._touched_sector()
        if sector != self.sector:
            self.sector = sector
            self._held_ms = 0
            self._hold_done = False
            return None
        if sector is None:
            return None
        self._held_ms += delta_ms
        if not self._hold_done and self._held_ms >= HOLD_MS:
            self._hold_done = True
            return sector
        return None

    def release(self):
        """Forget the current touch, so a mode change starts from a clean slate."""
        self.sector = None
        self._held_ms = 0
        self._hold_done = False
