"""Turn a sensor_msgs/Image into coloured terminal characters.

The whole trick is the upper half block, U+2580 '▀'. It paints the top half
of a character cell in the foreground colour and leaves the bottom half in the
background colour, so one character carries TWO pixels stacked vertically.
Terminal cells are about twice as tall as they are wide, which means a cell
holding two stacked pixels comes out roughly square. Without this the picture
is squashed to half height and faces stop looking like faces.

Nothing here imports cv_bridge or cv2. A raspberry pi that is already running a
camera and a VIO pipeline does not need another opencv in the loop, and the two
operations we want -- reinterpret a byte buffer, average rectangles -- are a
reshape and a cumulative sum.
"""

import numpy as np

UPPER_HALF_BLOCK = u"▀"
RESET = u"\x1b[0m"

# Encodings we can reinterpret without a colour conversion library.
SUPPORTED = ("mono8", "8UC1", "bgr8", "8UC3", "rgb8", "rgba8", "bgra8",
             "mono16", "16UC1")


def decode_image(msg):
    """sensor_msgs/Image -> (H, W, 3) uint8 RGB.

    `msg.step` is the row stride in bytes and is NOT always width*channels --
    drivers pad rows. Every branch below reshapes by `step` first and slices the
    real pixels out afterwards, which is the one detail that makes this work on
    hardware instead of only on bag files.
    """
    h, w, enc = msg.height, msg.width, msg.encoding

    if enc in ("mono8", "8UC1"):
        rows = np.frombuffer(msg.data, np.uint8).reshape(h, msg.step)
        grey = rows[:, :w]
        return np.repeat(grey[:, :, None], 3, axis=2)

    if enc in ("mono16", "16UC1"):
        dt = np.dtype(np.uint16).newbyteorder('>' if msg.is_bigendian else '<')
        rows = np.frombuffer(msg.data, dt).reshape(h, msg.step // 2)
        # Take the high byte. Depth or 16-bit infra is not meant to be pretty
        # here, it is meant to be visible.
        grey = (rows[:, :w] >> 8).astype(np.uint8)
        return np.repeat(grey[:, :, None], 3, axis=2)

    if enc in ("bgr8", "8UC3"):
        rows = np.frombuffer(msg.data, np.uint8).reshape(h, msg.step)
        return rows[:, :w * 3].reshape(h, w, 3)[:, :, ::-1]

    if enc == "rgb8":
        rows = np.frombuffer(msg.data, np.uint8).reshape(h, msg.step)
        return rows[:, :w * 3].reshape(h, w, 3)

    if enc in ("rgba8", "bgra8"):
        rows = np.frombuffer(msg.data, np.uint8).reshape(h, msg.step)
        img = rows[:, :w * 4].reshape(h, w, 4)[:, :, :3]
        return img if enc == "rgba8" else img[:, :, ::-1]

    raise ValueError(
        "squint cannot decode encoding %r (knows: %s)" % (enc, ", ".join(SUPPORTED)))


# xterm-256 palette: a 6x6x6 colour cube at these levels, then a 24-step grey
# ramp. Cheaper on the wire than truecolour (ESC[38;5;Nm is ~11 bytes against
# ~19) and, more importantly, it collapses near-identical cells onto the same
# index, which is what actually shrinks a frame -- see the run-length note in
# to_ansi. For a mono camera the 24 greys are already more levels than you can
# tell apart in a terminal.
_CUBE_LEVELS = np.array([0, 95, 135, 175, 215, 255], np.int16)


def to_xterm256(rgb):
    """(H, W, 3) uint8 -> (H, W) int16 of xterm-256 colour indices."""
    a = rgb.astype(np.int16)

    cube_idx = np.abs(a[..., None] - _CUBE_LEVELS[None, None, None, :]).argmin(-1)
    cube_err = np.abs(a - _CUBE_LEVELS[cube_idx]).sum(-1)
    cube_code = 16 + 36 * cube_idx[..., 0] + 6 * cube_idx[..., 1] + cube_idx[..., 2]

    grey_step = np.clip(np.rint((a.mean(-1) - 8) / 10.0), 0, 23).astype(np.int16)
    grey_err = np.abs(a - (8 + 10 * grey_step)[..., None]).sum(-1)
    grey_code = 232 + grey_step

    return np.where(grey_err < cube_err, grey_code, cube_code).astype(np.int16)


def block_mean(img, out_h, out_w):
    """Average over out_h x out_w rectangles. Exact, and not a resample.

    An integral image makes every rectangle sum a four-term lookup, so the cost
    does not depend on how big the rectangles are. Bin edges are computed with
    integer division, so a 480-row image into 48 rows gives exactly 10 rows per
    bin, and an awkward ratio spreads the remainder instead of dropping pixels.

    int32 is deliberate: the largest possible sum here is 640*480*255 = 78e6,
    comfortably inside int32, while float32 has only 24 bits of mantissa and
    would start losing whole pixel values around 16.7e6.
    """
    h, w = img.shape[:2]
    out_h = max(1, min(int(out_h), h))
    out_w = max(1, min(int(out_w), w))

    integral = np.zeros((h + 1, w + 1, 3), np.int32)
    np.cumsum(np.cumsum(img.astype(np.int32), axis=0), axis=1,
              out=integral[1:, 1:])

    ys = (np.arange(out_h + 1) * h) // out_h
    xs = (np.arange(out_w + 1) * w) // out_w
    top, bot = ys[:-1], ys[1:]
    left, right = xs[:-1], xs[1:]

    total = (integral[np.ix_(bot, right)] - integral[np.ix_(top, right)]
             - integral[np.ix_(bot, left)] + integral[np.ix_(top, left)])
    area = ((bot - top)[:, None] * (right - left)[None, :])[:, :, None]
    return (total // np.maximum(area, 1)).astype(np.uint8)


def to_ansi(rgb, cols, rows, quantize=0, palette="truecolor"):
    """(H, W, 3) uint8 -> a string of `rows` lines of `cols` characters.

    Two pixels per character, so the image is averaged down to (rows*2, cols).

    A colour escape is only emitted when the colour actually CHANGES along a
    row. A naive renderer spends about 40 bytes on every cell; skipping the
    repeats is the difference between this being a bandwidth fix and being a
    second bandwidth problem. Everything that makes neighbouring cells compare
    equal therefore pays twice: once in the escape it saves, and once in the
    escape the NEXT cell no longer needs.

    `quantize` rounds each channel to a multiple of N before comparing, buying
    repeats at the cost of banding. `palette` of "256" does the same thing by
    snapping to the xterm colour cube, and emits shorter escapes as well.
    """
    cols = max(1, int(cols))
    rows = max(1, int(rows))

    small = block_mean(rgb, rows * 2, cols)

    if quantize and quantize > 1:
        q = int(quantize)
        small = np.clip((small // q) * q + q // 2, 0, 255).astype(np.uint8)

    # Guard the odd case where the source was shorter than the request.
    if small.shape[0] < rows * 2:
        pad = np.zeros((rows * 2 - small.shape[0], small.shape[1], 3), np.uint8)
        small = np.vstack([small, pad])

    if palette == "256":
        codes = to_xterm256(small)
        top_px, bottom_px = codes[0::2], codes[1::2]
        fg_fmt, bg_fmt = u"\x1b[38;5;%dm", u"\x1b[48;5;%dm"
        cell = lambda a, r, c: (int(a[r, c]),)
    else:
        top_px, bottom_px = small[0::2], small[1::2]
        fg_fmt, bg_fmt = u"\x1b[38;2;%d;%d;%dm", u"\x1b[48;2;%d;%d;%dm"
        cell = lambda a, r, c: (int(a[r, c, 0]), int(a[r, c, 1]), int(a[r, c, 2]))

    lines = []
    for r in range(rows):
        parts = []
        last_fg = last_bg = None
        for c in range(cols):
            fg = cell(top_px, r, c)
            bg = cell(bottom_px, r, c)
            if fg != last_fg:
                parts.append(fg_fmt % fg)
                last_fg = fg
            if bg != last_bg:
                parts.append(bg_fmt % bg)
                last_bg = bg
            parts.append(UPPER_HALF_BLOCK)
        parts.append(RESET)
        lines.append(u"".join(parts))

    return u"\n".join(lines)
