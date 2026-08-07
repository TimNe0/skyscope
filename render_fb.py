"""Radar on an external round GC9A01 panel carried by a hexpansion.

Feature-flagged (conf.FEATURE_HEXPANSION_DISPLAY) and off by default, because it
needs hardware the user has to build. See README.md for the wiring table.

Two pieces:
  * GC9A01 -- a pure-MicroPython SPI driver: init sequence, window, full blit.
  * FbRenderer -- the same renderer interface radar_view.py already draws
    through, backed by a 240x240 RGB565 framebuf.

The framebuffer is 115,200 bytes and is allocated once at open time. A full blit
at 24 MHz takes roughly 40 ms, so the panel refreshes at 1-2 fps -- fine for a
radar that only changes when new data arrives.
"""

import framebuf
import time

SIZE = 240
HALF = SIZE // 2

# Command, arguments. The canonical GC9A01 power-on sequence: most of it is
# undocumented vendor register tuning that the panel does not start without.
_INIT = (
    (0xEF, b""),
    (0xEB, b"\x14"),
    (0xFE, b""),
    (0xEF, b""),
    (0xEB, b"\x14"),
    (0x84, b"\x40"),
    (0x85, b"\xff"),
    (0x86, b"\xff"),
    (0x87, b"\xff"),
    (0x88, b"\x0a"),
    (0x89, b"\x21"),
    (0x8A, b"\x00"),
    (0x8B, b"\x80"),
    (0x8C, b"\x01"),
    (0x8D, b"\x01"),
    (0x8E, b"\xff"),
    (0x8F, b"\xff"),
    (0xB6, b"\x00\x20"),
    (0x36, b"\x08"),          # MADCTL: BGR panel order
    (0x3A, b"\x05"),          # COLMOD: 16 bits per pixel
    (0x90, b"\x08\x08\x08\x08"),
    (0xBD, b"\x06"),
    (0xBC, b"\x00"),
    (0xFF, b"\x60\x01\x04"),
    (0xC3, b"\x13"),
    (0xC4, b"\x13"),
    (0xC9, b"\x22"),
    (0xBE, b"\x11"),
    (0xE1, b"\x10\x0e"),
    (0xDF, b"\x21\x0c\x02"),
    (0xF0, b"\x45\x09\x08\x08\x26\x2a"),
    (0xF1, b"\x43\x70\x72\x36\x37\x6f"),
    (0xF2, b"\x45\x09\x08\x08\x26\x2a"),
    (0xF3, b"\x43\x70\x72\x36\x37\x6f"),
    (0xED, b"\x1b\x0b"),
    (0xAE, b"\x77"),
    (0xCD, b"\x63"),
    (0x70, b"\x07\x07\x04\x0e\x0f\x09\x07\x08\x03"),
    (0xE8, b"\x34"),
    (0x62, b"\x18\x0d\x71\xed\x70\x70\x18\x0f\x71\xef\x70\x70"),
    (0x63, b"\x18\x11\x71\xf1\x70\x70\x18\x13\x71\xf3\x70\x70"),
    (0x64, b"\x28\x29\xf1\x01\xf1\x00\x07"),
    (0x66, b"\x3c\x00\xcd\x67\x45\x45\x10\x00\x00\x00"),
    (0x67, b"\x00\x3c\x00\x00\x00\x01\x54\x10\x32\x98"),
    (0x74, b"\x10\x85\x80\x00\x00\x4e\x00"),
    (0x98, b"\x3e\x07"),
    (0x35, b""),
    (0x21, b""),              # inversion on: this panel family needs it
)

_CMD_CASET = 0x2A
_CMD_RASET = 0x2B
_CMD_RAMWR = 0x2C
_CMD_SLPOUT = 0x11
_CMD_DISPON = 0x29


class GC9A01:
    """Minimal driver: enough to push whole frames at the panel."""

    def __init__(self, spi, dc, cs=None, reset=None, backlight=None):
        self.spi = spi
        self.dc = dc
        self.cs = cs
        self.reset = reset
        self.backlight = backlight
        self._window_set = False
        self.init()

    def _cmd(self, command, data=b""):
        if self.cs is not None:
            self.cs(0)
        self.dc(0)
        self.spi.write(bytes((command,)))
        if data:
            self.dc(1)
            self.spi.write(data)
        if self.cs is not None:
            self.cs(1)

    def init(self):
        if self.reset is not None:
            self.reset(1)
            time.sleep_ms(10)
            self.reset(0)
            time.sleep_ms(20)
            self.reset(1)
            time.sleep_ms(120)
        for command, data in _INIT:
            self._cmd(command, data)
        self._cmd(_CMD_SLPOUT)
        time.sleep_ms(120)
        self._cmd(_CMD_DISPON)
        time.sleep_ms(20)
        if self.backlight is not None:
            self.backlight(1)

    def _set_window(self):
        # The panel is always driven full-frame, so the window is set once and
        # then only RAMWR is re-issued per blit.
        span = bytes((0, 0, 0, SIZE - 1))
        self._cmd(_CMD_CASET, span)
        self._cmd(_CMD_RASET, span)
        self._window_set = True

    def blit(self, buffer):
        if not self._window_set:
            self._set_window()
        if self.cs is not None:
            self.cs(0)
        self.dc(0)
        self.spi.write(bytes((_CMD_RAMWR,)))
        self.dc(1)
        self.spi.write(buffer)
        if self.cs is not None:
            self.cs(1)

    def close(self):
        try:
            if self.backlight is not None:
                self.backlight(0)
            self.spi.deinit()
        except Exception:
            pass


def rgb565(rgb, swap=True):
    """Float RGB triple to a 16-bit colour word.

    MicroPython's framebuf writes RGB565 little-endian while the panel reads it
    big-endian, so the bytes are swapped here unless the caller says otherwise.
    """
    r = int(max(0.0, min(1.0, rgb[0])) * 31)
    g = int(max(0.0, min(1.0, rgb[1])) * 63)
    b = int(max(0.0, min(1.0, rgb[2])) * 31)
    value = (r << 11) | (g << 5) | b
    if swap:
        value = ((value & 0xFF) << 8) | (value >> 8)
    return value


class FbRenderer:
    """Renderer interface over a 240x240 RGB565 framebuffer.

    Accepts the same centred coordinates as CtxRenderer (-120..+120) and shifts
    them into the buffer's 0..239 space.
    """

    size = SIZE
    vector = False

    def __init__(self, panel, swap_bytes=True):
        self.panel = panel
        self.swap = swap_bytes
        self.buffer = bytearray(SIZE * SIZE * 2)
        self.fb = framebuf.FrameBuffer(self.buffer, SIZE, SIZE, framebuf.RGB565)
        # framebuf's font is 8x8 with no scaling, so a scratch mono buffer is
        # used to pixel-double headings.
        self._scratch = None

    # -- primitives ----------------------------------------------------------

    def clear(self, rgb):
        self.fb.fill(rgb565(rgb, self.swap))

    def line(self, x0, y0, x1, y1, rgb, w=1):
        colour = rgb565(rgb, self.swap)
        self.fb.line(int(x0) + HALF, int(y0) + HALF, int(x1) + HALF, int(y1) + HALF, colour)

    def circle(self, x, y, r, rgb, fill=False, w=1):
        colour = rgb565(rgb, self.swap)
        cx, cy, radius = int(x) + HALF, int(y) + HALF, int(r)
        try:
            self.fb.ellipse(cx, cy, radius, radius, colour, fill)
            return
        except AttributeError:
            pass
        _midpoint_circle(self.fb, cx, cy, radius, colour, fill)

    def poly(self, pts, rgb, fill=True, w=1):
        colour = rgb565(rgb, self.swap)
        try:
            from array import array

            coords = array("h")
            for px, py in pts:
                coords.append(int(px) + HALF)
                coords.append(int(py) + HALF)
            self.fb.poly(0, 0, coords, colour, fill)
            return
        except (AttributeError, ImportError):
            pass
        # Outline fallback: aircraft glyphs are five pixels across, so losing
        # the fill costs very little legibility.
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            self.fb.line(int(x0) + HALF, int(y0) + HALF, int(x1) + HALF, int(y1) + HALF, colour)

    def text(self, s, x, y, rgb, size=12, align="left"):
        colour = rgb565(rgb, self.swap)
        scale = 2 if size >= 13 else 1
        width = len(s) * 8 * scale
        px = int(x) + HALF
        if align == "center":
            px -= width // 2
        elif align == "right":
            px -= width
        py = int(y) + HALF - 4 * scale
        if scale == 1:
            self.fb.text(s, px, py, colour)
        else:
            self._text_2x(s, px, py, colour)

    def text_width(self, s, size=12):
        return len(s) * 8 * (2 if size >= 13 else 1)

    def _text_2x(self, s, px, py, colour):
        width = len(s) * 8
        need = width * 8
        if self._scratch is None or len(self._scratch) < (need + 7) // 8:
            self._scratch = bytearray((need + 7) // 8)
        mono = framebuf.FrameBuffer(self._scratch, width, 8, framebuf.MONO_HLSB)
        mono.fill(0)
        mono.text(s, 0, 0, 1)
        fb = self.fb
        for gy in range(8):
            for gx in range(width):
                if mono.pixel(gx, gy):
                    fb.fill_rect(px + gx * 2, py + gy * 2, 2, 2, colour)

    def flush(self):
        self.panel.blit(self.buffer)

    def close(self):
        self.panel.close()


def _midpoint_circle(fb, cx, cy, radius, colour, fill):
    if radius <= 0:
        return
    x = radius
    y = 0
    err = 1 - radius
    while x >= y:
        if fill:
            fb.hline(cx - x, cy + y, 2 * x + 1, colour)
            fb.hline(cx - x, cy - y, 2 * x + 1, colour)
            fb.hline(cx - y, cy + x, 2 * y + 1, colour)
            fb.hline(cx - y, cy - x, 2 * y + 1, colour)
        else:
            for sx, sy in ((x, y), (y, x), (-x, y), (-y, x),
                           (-x, -y), (-y, -x), (x, -y), (y, -x)):
                fb.pixel(cx + sx, cy + sy, colour)
        y += 1
        if err < 0:
            err += 2 * y + 1
        else:
            x -= 1
            err += 2 * (y - x) + 1


# -- hexpansion wiring -------------------------------------------------------

_HS_INDEX = {"HS1": 0, "HS2": 1, "HS3": 2, "HS4": 3}
# SPI host to try first. The badge's own panel occupies the other one; if the
# radar panel stays dark, swap the order here (documented in README).
_SPI_HOSTS = (1, 2)
BAUDRATE = 24_000_000


def open_hexpansion_panel(slot, pin_roles, baudrate=BAUDRATE):
    """Build a FbRenderer driving a GC9A01 on the given hexpansion slot.

    Pin numbers come from the firmware's HexpansionConfig rather than being
    hard-coded, so the same wiring works in any of the six slots.
    """
    from machine import Pin, SPI
    from system.hexpansion.config import HexpansionConfig

    hexpansion = HexpansionConfig(slot)
    hs_pins = hexpansion.pin

    def role_pin(role):
        name = pin_roles.get(role)
        index = _HS_INDEX.get(name)
        if index is None:
            return None  # tied to GND or 3V3 in hardware
        return hs_pins[index]

    sck = role_pin("sck")
    mosi = role_pin("mosi")
    dc = role_pin("dc")
    cs = role_pin("cs")
    if sck is None or mosi is None or dc is None:
        raise ValueError("SCK, MOSI and DC must map to HS pins")

    dc.init(Pin.OUT)
    if cs is not None:
        cs.init(Pin.OUT)
        cs(1)

    # RST goes to the first low-speed pin. It is driven through the I2C GPIO
    # expander, which is slow -- fine for a one-off reset.
    reset = None
    try:
        reset = hexpansion.ls_pin[0]
        reset.init(Pin.OUT)
    except Exception:
        reset = None

    spi = None
    last_error = None
    for host in _SPI_HOSTS:
        try:
            spi = SPI(
                host,
                baudrate=baudrate,
                polarity=0,
                phase=0,
                sck=sck,
                mosi=mosi,
            )
            break
        except Exception as exc:
            last_error = exc
    if spi is None:
        raise RuntimeError("no free SPI host: %s" % last_error)

    panel = GC9A01(spi, dc, cs, reset)
    return FbRenderer(panel)
