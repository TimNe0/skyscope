"""Renderer backed by the badge's built-in ctx vector canvas.

This is the reference implementation of the renderer interface that everything
in radar_view.py draws through. Coordinates are the badge's native centred
space: -120..+120 on both axes, y growing downwards.

    clear(rgb)                          fill the whole panel
    line(x0, y0, x1, y1, rgb, w=1)      straight line
    circle(x, y, r, rgb, fill=False)    ring or disc
    poly(pts, rgb, fill=True)           closed polygon, pts = [(x, y), ...]
    text(s, x, y, rgb, size=12, align="left")
    flush()                             present the frame

`rgb` is a 3-tuple of floats in 0.0..1.0 throughout, so the same view code
drives both this and the RGB565 framebuffer renderer.
"""

SIZE = 240
TWO_PI = 6.283185307179586


class CtxRenderer:
    """Adapts the ctx canvas to the renderer interface. No allocation per frame."""

    size = SIZE
    # ctx draws anti-aliased vectors, so the view can afford thin strokes and
    # small text that would be unreadable on the framebuffer backend.
    vector = True

    def __init__(self, ctx=None):
        self.ctx = ctx
        self._align = None

    def begin(self, ctx):
        """Bind the ctx handed to draw() for this frame."""
        self.ctx = ctx
        if self._align is None:
            # ctx.LEFT and friends live on the canvas object rather than the
            # module, so this table can only be built once a canvas exists --
            # but the values are constants, so build it once and not per frame.
            self._align = {"left": ctx.LEFT, "center": ctx.CENTER, "right": ctx.RIGHT}
        return self

    def clear(self, rgb):
        self.ctx.rgb(*rgb).rectangle(-120, -120, SIZE, SIZE).fill()

    def line(self, x0, y0, x1, y1, rgb, w=1):
        ctx = self.ctx
        ctx.line_width = w
        ctx.rgb(*rgb).begin_path().move_to(x0, y0).line_to(x1, y1).stroke()

    def circle(self, x, y, r, rgb, fill=False, w=1):
        ctx = self.ctx
        ctx.rgb(*rgb).begin_path().arc(x, y, r, 0, TWO_PI, 0)
        if fill:
            ctx.fill()
        else:
            ctx.line_width = w
            ctx.stroke()

    def poly(self, pts, rgb, fill=True, w=1):
        self.polys((pts,), rgb, fill, w)

    def polys(self, shapes, rgb, fill=True, w=1):
        """Draw several shapes of one colour as a single path.

        Thirty aircraft drawn one at a time meant thirty colour changes, thirty
        path setups and thirty fills. As subpaths of one path it is one of each,
        which is most of the per-frame saving on a busy scope.
        """
        ctx = self.ctx
        started = False
        for pts in shapes:
            if len(pts) < 2:
                continue
            if not started:
                ctx.rgb(*rgb).begin_path()
                started = True
            ctx.move_to(pts[0][0], pts[0][1])
            for i in range(1, len(pts)):
                ctx.line_to(pts[i][0], pts[i][1])
            ctx.close_path()
        if not started:
            return
        if fill:
            ctx.fill()
        else:
            ctx.line_width = w
            ctx.stroke()

    def text(self, s, x, y, rgb, size=12, align="left"):
        ctx = self.ctx
        ctx.font_size = size
        ctx.text_align = self._align.get(align, ctx.LEFT)
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(*rgb).move_to(x, y).text(s)

    def text_width(self, s, size=12):
        ctx = self.ctx
        ctx.font_size = size
        return ctx.text_width(s)

    def flush(self):
        # The scheduler's render task presents the ctx frame for us.
        pass
