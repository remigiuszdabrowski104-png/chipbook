"""Reading a setup sheet saved as PDF.

PDFs ARE READ WITH THE STANDARD LIBRARY ALONE (zlib + re), so that nothing
has to be installed on the user's machine.

THE ORDER OF FIELDS IN A PDF does not follow the page layout - it follows
the order in which things were drawn. So we do not read the text
sequentially; we take the COORDINATES the file supplies with every string
and assemble rows by their y value. Measured on a real setup sheet: without
coordinates the output read "DRAWING: A REVISION:"; with them it is clear
that "A" belongs to REVISION and DRAWING is empty.
"""

import re
import zlib

from .render import (MIN_TABLE_ROWS, _build_table, _drop_empty,
                     _section_name, _title_value)

MAX_PDF_BYTES = 30 * 1024 * 1024  # a real setup sheet weighed 2.5 MB
MAX_PAGES = 60                    # how many PDF pages we read
MAX_TEXT_RUNS = 20000             # a fuse against enormous or hostile PDFs
ROW_TOLERANCE = 2.0               # strings this close vertically = one row


# We read PDFs directly, with no third-party library. Only three things are
# needed: find the objects, decompress the streams with zlib, and pull the
# strings out of a stream together with their coordinates.
#
# This module DOES NOT KNOW the layout of any particular setup sheet and is
# not allowed to assume one. It knows only this much:
#   - strings sharing a y coordinate stand on one row,
#   - a string ending in a colon is a label, and whatever is to its right
#     is its value,
#   - a row starting with a string WITHOUT a colon is a section heading,
#     and its indent (x) says how deep that section sits.

_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page[^s]")
_PDF_CONTENT_RE = re.compile(rb"/Contents\s+(\d+)\s+\d+\s+R")
_PDF_OBJECT_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")

_PDF_WHITESPACE_RE = b" \t\r\n\f\x00"
_PDF_WORD_END_RE = _PDF_WHITESPACE_RE + b"()<>[]{}/%"
_PDF_DIGITS_RE = b"+-.0123456789"

_PDF_ESCAPE_RE = {b"n": b"\n", b"r": b"\r", b"t": b"\t",
                 b"b": b"\b", b"f": b"\f"}


def _describe_pdf(path, name):
    try:
        with open(path, "rb") as file:
            raw = file.read()
    except OSError as error:
        return {"kind": "error_message", "name": name,
                "notice": "Could not read the file: " + str(error)}

    if not raw.startswith(b"%PDF"):
        return {"kind": "error_message", "name": name,
                "notice": "This is not a PDF. The attachment is kept untouched "
                         "- open it with the button."}

    state = {"run_count": 0, "truncated": False}
    try:
        pages = _pdf_pages(raw, state)
    except Exception as error:              # a readable message, not a failure
        return {"kind": "error_message", "name": name,
                "notice": "This PDF cannot be broken out (%s). The attachment "
                         "is kept untouched - open it with the button."
                         % error}

    blocks = []
    for runs in pages:
        # Truncation does NOT discard what we already read - we show what we have
        # and say so in the notice.
        blocks.extend(_pdf_blocks(runs, state))
    blocks = _pdf_order(blocks)

    if not blocks:
        return {"kind": "error_message", "name": name,
                "notice": "There is no text to show in this PDF - it may "
                         "be a scan or a drawing only. Open it with the button."}

    result = {"kind": "pdf", "name": name, "page_count": len(pages),
             "blocks": blocks}
    if state["truncated"]:
        result["notice"] = ("This file is very large - showing the first "
                          "%d text runs. Open the whole file with the button."
                          % MAX_TEXT_RUNS)
    return result


def _pdf_objects(data):
    """Returns {number: (heading, stream_or_None)}."""
    result = {}
    for m in _PDF_OBJECT_RE.finditer(data):
        start = m.end()
        end = data.find(b"endobj", start)
        if end < 0:
            continue
        body = data[start:end]
        where = body.find(b"stream")
        if where < 0:
            result[int(m.group(1))] = (body, None)
            continue
        header = body[:where]
        p = where + len(b"stream")
        if body[p:p + 2] == b"\r\n":
            p += 2
        elif body[p:p + 1] in (b"\n", b"\r"):
            p += 1
        e = body.find(b"endstream", p)
        result[int(m.group(1))] = (header,
                                  body[p:e] if e >= 0 else body[p:])
    return result


def _pdf_decompress(header, stream):
    if stream is None:
        return None
    if b"/FlateDecode" in header:
        try:
            return zlib.decompress(stream)
        except zlib.error:
            try:                           # stream cut short - we take what there is
                return zlib.decompressobj().decompress(stream)
            except zlib.error:
                return None
    if b"/Filter" in header:
        return None                        # a different filter - we do not pretend to handle it
    return stream                        # an uncompressed stream


def _pdf_pages(data, state):
    """A list of pages; each page is a list of (x, y, text)."""
    objects = _pdf_objects(data)
    page_numbers = sorted(n for n, (g, _) in objects.items() if _PDF_PAGE_RE.search(g))
    if len(page_numbers) > MAX_PAGES:
        state["truncated"] = True
        page_numbers = page_numbers[:MAX_PAGES]
    pages = []
    for number in page_numbers:
        header = objects[number][0]
        m = _PDF_CONTENT_RE.search(header)
        if not m:
            continue
        target = objects.get(int(m.group(1)))
        if not target:
            continue
        content = _pdf_decompress(*target)
        pages.append(_pdf_strings(content, state) if content else [])
    return pages


def _pdf_read_string(data, i):
    """Read a parenthesised string, starting at the opening parenthesis.

    Parentheses INSIDE a string need no backslash as long as they are
    balanced - that is what the PDF format says. So we count depth rather
    than looking for the first closing parenthesis. Without this,
    "Contour (2D)" was cut off halfway.
    """
    n = len(data)
    i += 1
    depth = 1
    out = bytearray()
    while i < n:
        char = data[i:i + 1]
        if char == b"\\":
            next_byte = data[i + 1:i + 2]
            if next_byte in _PDF_ESCAPE_RE:
                out += _PDF_ESCAPE_RE[next_byte]
                i += 2
            elif next_byte.isdigit():
                j = i + 1
                digits = b""
                while j < n and len(digits) < 3 and data[j:j + 1].isdigit():
                    digits += data[j:j + 1]
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
            elif next_byte in (b"\n", b"\r"):
                i += 2                     # a line break in the source, not in the text
            else:
                out += next_byte
                i += 2
        elif char == b"(":
            depth += 1
            out += char
            i += 1
        elif char == b")":
            depth -= 1
            i += 1
            if depth == 0:
                break
            out += char
        else:
            out += char
            i += 1
    return bytes(out).decode("cp1252", "replace"), i


def _pdf_tokens(data):
    """Split a content stream into tokens: string, array, number, operator.

    Hex-encoded strings (<...>) are NOT read - we have never seen a setup
    sheet using them and do not want to guess. Such a file gets an honest
    "no text to show" message rather than badly assembled fields.
    """
    i, n = 0, len(data)
    while i < n:
        char = data[i:i + 1]
        if char in _PDF_WHITESPACE_RE:
            i += 1
        elif char == b"%":
            end = data.find(b"\n", i)
            i = n if end < 0 else end + 1
        elif char == b"(":
            text, i = _pdf_read_string(data, i)
            yield "string", text
        elif char == b"[":
            i += 1
            parts = []
            while i < n and data[i:i + 1] != b"]":
                if data[i:i + 1] == b"(":
                    text, i = _pdf_read_string(data, i)
                    parts.append(text)
                else:
                    i += 1
            i += 1
            yield "array", "".join(parts)
        elif char == b"<":
            if data[i + 1:i + 2] == b"<":
                i += 2
            else:
                end = data.find(b">", i)
                i = n if end < 0 else end + 1
        elif char == b">":
            i += 2 if data[i + 1:i + 2] == b">" else 1
        elif char == b"/":
            i += 1
            while i < n and data[i:i + 1] not in _PDF_WORD_END_RE:
                i += 1
        elif char in _PDF_DIGITS_RE:
            j = i + 1
            while j < n and data[j:j + 1] in _PDF_DIGITS_RE + b"eE":
                j += 1
            try:
                yield "number", float(data[i:j])
            except ValueError:
                pass
            i = j
        elif char in b"{}":
            i += 1
        else:
            j = i
            while j < n and data[j:j + 1] not in _PDF_WORD_END_RE:
                j += 1
            if j == i:
                j = i + 1
            yield "operator", data[i:j].decode("latin-1")
            i = j


def _pdf_strings(content, state):
    """Extract (x, y, text) for every string in a page stream.

    We track the text matrix (Tm/Td/TD/T*), because only that says WHERE a
    string actually stands. Order within the stream is meaningless.
    """
    stack = []
    result = []
    text_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    text_line_matrix = list(text_matrix)
    gap = 0.0

    def attach(text):
        if text:
            result.append((round(text_matrix[4], 2), round(text_matrix[5], 2), text))

    def new_line():
        return [text_line_matrix[0], text_line_matrix[1], text_line_matrix[2], text_line_matrix[3],
                text_line_matrix[4] - gap * text_line_matrix[2], text_line_matrix[5] - gap * text_line_matrix[3]]

    for kind, value in _pdf_tokens(content):
        if kind != "operator":
            stack.append((kind, value))
            if len(stack) > 64:             # we do not collect forever
                del stack[:-16]
            continue

        op = value
        numbers = [w for r, w in stack if r == "number"]

        if op == "BT":
            text_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            text_line_matrix = list(text_matrix)
        elif op == "Tm" and len(numbers) >= 6:
            text_matrix = list(numbers[-6:])
            text_line_matrix = list(text_matrix)
        elif op in ("Td", "TD") and len(numbers) >= 2:
            dx, dy = numbers[-2], numbers[-1]
            if op == "TD":
                gap = -dy
            text_line_matrix = [text_line_matrix[0], text_line_matrix[1], text_line_matrix[2], text_line_matrix[3],
                   text_line_matrix[4] + dx * text_line_matrix[0] + dy * text_line_matrix[2],
                   text_line_matrix[5] + dx * text_line_matrix[1] + dy * text_line_matrix[3]]
            text_matrix = list(text_line_matrix)
        elif op == "TL" and numbers:
            gap = numbers[-1]
        elif op == "T*":
            text_line_matrix = new_line()
            text_matrix = list(text_line_matrix)
        elif op == "Tj" and stack and stack[-1][0] == "string":
            attach(stack[-1][1])
        elif op == "'" and stack and stack[-1][0] == "string":
            text_line_matrix = new_line()
            text_matrix = list(text_line_matrix)
            attach(stack[-1][1])
        elif op == "TJ" and stack and stack[-1][0] == "array":
            attach(stack[-1][1])

        stack = []
        if len(result) >= MAX_TEXT_RUNS:
            state["truncated"] = True         # truncation MUST be visible
            break
    return result


def _pdf_rows(runs):
    """Group strings into rows by y; within a row, order them by x."""
    ordered = sorted(runs, key=lambda n: -n[1])
    rows = []
    for x, y, text in ordered:
        if rows and abs(rows[-1][0] - y) <= ROW_TOLERANCE:
            rows[-1][1].append((x, text))
        else:
            rows.append([y, [(x, text)]])
    return [(y, sorted(entries)) for y, entries in rows]


def _pdf_blocks(runs, state):
    """Turn the strings of one page into the same blocks XML produces."""
    blocks = []
    current = None
    # Indents are collected for the WHOLE document, not for a single page -
    # otherwise the same section would get a different level depending on what
    # else happened to be on its page.
    indents = state.setdefault("indents", [])

    def heading_level(x):
        if x not in indents:
            indents.append(x)
            indents.sort()
        return 1 if x <= indents[0] else 2

    def append_pairs(entries):
        label, chunks = None, []
        for _, text in entries:
            if text.rstrip().endswith(":"):
                if label is not None:
                    current["pairs"].append([label,
                                            " ".join(chunks).strip()])
                label, chunks = text.rstrip()[:-1].strip(), []
            else:
                chunks.append(text)
        if label is not None:
            current["pairs"].append([label, " ".join(chunks).strip()])

    for y, entries in _pdf_rows(runs):
        state["run_count"] += len(entries)
        if state["run_count"] > MAX_TEXT_RUNS:
            state["truncated"] = True
            break

        if not entries[0][1].rstrip().endswith(":"):
            # The row starts with a heading. Everything up to the first label belongs
            # to the title; the rest are ordinary fields.
            where = len(entries)
            for i, (_, text) in enumerate(entries):
                if text.rstrip().endswith(":"):
                    where = i
                    break
            parts = [t for _, t in entries[:where]]
            title = parts[0] + (": " + " ".join(parts[1:]).strip()
                                 if len(parts) > 1 else "")
            current = {"kind": "pairs", "title": title,
                       "level": heading_level(entries[0][0]), "pairs": []}
            blocks.append(current)
            if where < len(entries):
                append_pairs(entries[where:])
            continue

        if current is None:
            current = {"kind": "pairs", "title": "", "level": 1, "pairs": []}
            blocks.append(current)
        append_pairs(entries)

    return blocks


# ------------------------------------------- tidying up the PDF blocks

_PDF_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

# THE ONLY PLACE IN THIS MODULE holding content taken from a specific
# report - and it stands here DELIBERATELY INVERTED from what instinct
# suggests.
#
# The problem: a machine name ("3 - AXIS VMC") appears in a setup sheet as a
# bare heading, with no label and no fields. The machine is chosen from a
# profile in the CAM system, so nobody types it by hand - that name MUST
# survive.
# Instinct says: recognise the machine. We do not. A shop has several
# machines, we do not know the names of the others, and a wrong match would
# delete a name silently. Instead we list the line TO HIDE. The worst a
# changed template can cause is one redundant line on screen.
HIDDEN_HEADINGS = ("setup sheet report",)


def _looks_like_path(title):
    """Whether this heading is a filesystem path rather than a section name.

    Recognised by SHAPE (a drive letter or two slashes), not by content -
    this module still does not know what a setup sheet looks like.
    """
    return bool(_PDF_PATH_RE.match(str(title or "").strip()))


def _pdf_order(blocks):
    if not blocks:
        return blocks
    return _pdf_drop_empty(_pdf_trim(_pdf_tables(_pdf_cleanup(blocks))))


def _pdf_trim(blocks):
    """A field with no value is not shown. Its name stays under "empty"."""
    for block in blocks:
        if block.get("kind") == "pairs":
            block["pairs"], block["empty_fields"] = _drop_empty(block["pairs"])
    return blocks


def _pdf_drop_empty(blocks):
    """A section left with no filled field disappears along with its title.

    The one exception is the report header - see _pdf_cleanup.
    Group headings such as OPERATION LIST do disappear, and nothing is lost
    by it, because the table underneath has its own title. An exception of
    the form "keep it if something sits below" would be guessing: in a real
    file COMMENTS has STOCK below it only because STOCK is indented further,
    not because it belongs to it.
    """
    return [b for b in blocks
            if b.get("kind") != "pairs" or b.get("pairs")
            or b.get("report_heading")]


def _pdf_cleanup(blocks):
    """The part of section cleanup that can be done WITHOUT knowing the
    layout of the file.

    A filesystem path is not a section name, so THE HEADING ITSELF goes -
    but its fields move up into the section above, and that is what matters
    here. Measured: under one such path sat the total cycle time (CYCLE TIME
    0 HOURS, 15 MINUTES, 37 SECONDS). Deleting the whole block would have
    deleted that number along with the heading.

    THE REPORT HEADER: lines standing BEFORE the first labelled field in the
    whole document are kept, even though they have no fields. That is where
    the machine name sits - chosen from a CAM profile and never typed by
    hand. Other empty sections disappear; that is decided by
    _pdf_drop_empty.
    """
    top, top_index = None, len(blocks)
    for i, b in enumerate(blocks):
        if b.get("pairs") and not _looks_like_path(b.get("title")):
            top, top_index = b, i
            break

    result = []
    for i, b in enumerate(blocks):
        title = str(b.get("title") or "").strip()
        if _looks_like_path(title):
            pairs = b.get("pairs") or []
            if pairs and top is not None and b is not top:
                top["pairs"].extend(pairs)
            continue
        if i < top_index and not b.get("pairs"):
            if title.lower() in HIDDEN_HEADINGS:
                continue
            b["report_heading"] = True
        result.append(b)
    return result


def _pdf_tables(blocks):
    """A table is built only from siblings under the same heading."""
    result = []
    i, n = 0, len(blocks)
    while i < n:
        header = blocks[i]
        result.append(header)
        level = header.get("level", 1)
        j = i + 1
        children = []
        while j < n and blocks[j].get("level", 1) > level:
            children.append(blocks[j])
            j += 1
        if children:
            result.extend(_pdf_group(children))
        i = j if children else i + 1
    return result


def _pdf_group(children):
    groups, order = {}, []
    for b in children:
        key = _section_name(b.get("title"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(b)

    result, done = [], set()
    for b in children:
        key = _section_name(b.get("title"))
        items = groups[key]
        if len(items) < MIN_TABLE_ROWS:
            result.append(b)
            continue
        if key in done:
            continue
        done.add(key)
        # The operation name lives ONLY in the section title, in no field at all -
        # so it enters as the first column. That label also comes from the file
        # (the part of the title before the colon).
        sections = []
        for x in items:
            value = _title_value(x.get("title"))
            head = [(key, value)] if value else []
            sections.append(head + [tuple(p) for p in (x.get("pairs") or [])])
        table = _build_table(sections, key, b.get("level", 2))
        result.extend(table if table else items)
    return result
