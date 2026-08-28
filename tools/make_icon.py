# -*- coding: ascii -*-
"""Draw chipbook.ico - the application and shortcut icon.

WHY A SCRIPT AND NOT A CHECKED-IN IMAGE: the icon has to be reproducible.
When the colour or the shape of the mark in the UI changes, one number
changes here and the file is regenerated - instead of hunting for who drew
a binary a year ago and with what.

WE DRAW IT OURSELVES, with no third-party library. The shape is simple - a
rounded tile and the outline of a book - so it is enough to measure, for
each point, its distance from those figures. Edges are smoothed by drawing
on a grid four times denser and averaging.

THE MARK MATCHES THE ONE IN THE UI: the same book, the same colours
(graphite #13161b, orange #f0762e).
"""

import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(os.path.dirname(HERE), "src", "chipbook", "web")
OUTPUT_FILE = os.path.join(WEB_DIR, "chipbook.ico")

SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4                       # grid density multiplier while drawing

BACKGROUND = (0x13, 0x16, 0x1b)       # graphite, as in the UI
MARK_COLOR = (0xf0, 0x76, 0x2e)       # orange of the mark, as in the UI

# The book is drawn in its own 24 x 24 space and then placed on the tile
# with a margin. MARK_RATIO says how much of the tile the mark occupies -
# without a margin the book outline merged with the tile's rounded corner
# and the whole thing read as a plain rectangle with a line in it.
DRAWING_UNITS = 24.0
MARK_RATIO = 0.72
BOOK_BOX = (3.0, 2.0, 21.0, 22.0)     # left, top, right, bottom
CORNER_RADIUS = 2.0
STROKE_WIDTH = 2.4
# The spine, not a horizontal rule: at 16 pixels it is the spine that makes
# the shape read as a book rather than as an empty frame.
SPINE_X = 8.0


def _distance_to_frame(x, y, box, radius):
    """Distance from a point to the OUTLINE of a rounded rectangle."""
    left, top, right, bottom = box
    center_x, center_y = (left + right) / 2.0, (top + bottom) / 2.0
    half_x = (right - left) / 2.0 - radius
    half_y = (bottom - top) / 2.0 - radius
    qx = abs(x - center_x) - half_x
    qy = abs(y - center_y) - half_y
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return abs(outside + inside - radius)


def _distance_to_segment(x, y, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else ((x - x1) * dx + (y - y1) * dy) / length
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def _mark_at(x, y):
    """How much orange there is at this point (0.0 - 1.0)."""
    d = _distance_to_frame(x, y, BOOK_BOX, CORNER_RADIUS)
    d = min(d, _distance_to_segment(
        x, y,
        SPINE_X, BOOK_BOX[1] + STROKE_WIDTH / 2.0,
        SPINE_X, BOOK_BOX[3] - STROKE_WIDTH / 2.0))
    return 1.0 if d <= STROKE_WIDTH / 2.0 else 0.0


def _on_tile(x, y, side):
    """Whether the point lies on the rounded background tile."""
    radius = side * 0.22
    left = top = 0.0
    right = bottom = float(side)
    center_x, center_y = (left + right) / 2.0, (top + bottom) / 2.0
    qx = abs(x - center_x) - ((right - left) / 2.0 - radius)
    qy = abs(y - center_y) - ((bottom - top) / 2.0 - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return (outside + min(max(qx, qy), 0.0) - radius) <= 0.0


def draw(side):
    """Return one RGBA image as bytes, antialiased by supersampling."""
    pixels = bytearray()
    scale = (side * MARK_RATIO) / DRAWING_UNITS
    offset = (side - DRAWING_UNITS * scale) / 2.0
    for row in range(side):
        for column in range(side):
            tile_hits = mark_hits = 0
            for py in range(SUPERSAMPLE):
                for px in range(SUPERSAMPLE):
                    x = column + (px + 0.5) / SUPERSAMPLE
                    y = row + (py + 0.5) / SUPERSAMPLE
                    if not _on_tile(x, y, side):
                        continue
                    tile_hits += 1
                    mark_hits += _mark_at((x - offset) / scale,
                                          (y - offset) / scale)
            samples = float(SUPERSAMPLE * SUPERSAMPLE)
            alpha = tile_hits / samples
            if alpha == 0:
                pixels += b"\x00\x00\x00\x00"
                continue
            share = (mark_hits / tile_hits) if tile_hits else 0.0
            colour = [
                int(round(BACKGROUND[i]
                          + (MARK_COLOR[i] - BACKGROUND[i]) * share))
                for i in range(3)]
            pixels += bytes(colour) + bytes([int(round(alpha * 255))])
    return bytes(pixels)


def png(side, pixels):
    """The simplest valid PNG - every row stored unfiltered."""
    raw = bytearray()
    for row in range(side):
        raw.append(0)
        raw += pixels[row * side * 4:(row + 1) * side * 4]

    def chunk(name, data):
        return (struct.pack(">I", len(data)) + name + data
                + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff))

    header = struct.pack(">IIBBBBB", side, side, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main():
    images = []
    for side in SIZES:
        images.append((side, png(side, draw(side))))
        print("  %3d x %-3d  %6d B" % (side, side, len(images[-1][1])))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory, content = b"", b""
    for side, data in images:
        directory += struct.pack("<BBBBHHII", side % 256, side % 256, 0, 0,
                                 1, 32, len(data), offset)
        content += data
        offset += len(data)

    with open(OUTPUT_FILE, "wb") as file:
        file.write(header + directory + content)
    print("")
    print("WRITTEN: %s" % OUTPUT_FILE)
    print("size: %d B" % os.path.getsize(OUTPUT_FILE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
