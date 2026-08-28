"""Turning what was read out of a file into something a person can read.

One rule for PDF and for XML - they can share it because both formats
leave the reader in the same block shape. Everything here works on that
shape alone and never touches a file.

THE RULE: nothing is guessed. Field names and values come ONLY from the
file. This module does not know what "spindle" means - it shows what the
file says, merely arranged for a human. Any interpretation of names has to
wait until a real file proves what is actually in it.
"""

import re


# ------------------------------------------------ shared table rule
#
# One rule for PDF and for XML - they can share it because both formats
# leave here in the same block shape. In order of importance:
#
#   1. A TABLE IS BUILT ONLY FROM SIBLINGS, that is from sections under the
#      same heading. Grouping by name across the whole document would glue
#      unrelated things together. Measured on a real file: TOOL INFO appears
#      once per operation and once in the tool list, and in XML a section
#      "OP 1" appears once under a tool and once under a coordinate system.
#   2. COLUMNS COME FROM THE FILE: the union of labels in order of first
#      appearance. No list of "important" fields anywhere in the code. This
#      module does not know what "SPINDLE" means.
#   3. A COLUMN EMPTY IN EVERY ROW leaves the table, but its name is printed
#      underneath it. Nothing is lost, and the code still knows no field
#      names - so it survives a different report template. Deleting such
#      columns silently would hide an empty COMMENT field, which is itself
#      evidence about how the source file is filled in.

MIN_TABLE_ROWS = 2


def _drop_empty(pairs):
    """Split fields into filled and empty.

    A field with no value is not displayed at all. The NAMES of empty fields
    are NOT lost - they go to an "empty" key which the UI does not draw but
    which stays in the data. That way the program still KNOWS what was
    absent from the file, and tests guard it: if a field that is always
    empty today starts being filled in, we find out immediately.
    """
    filled = [p for p in pairs if str(p[1]).strip()]
    empty = [str(p[0]) for p in pairs if not str(p[1]).strip()]
    return filled, empty


def _section_name(title):
    """A section's name is its title up to the first colon.

    Measured on a real setup sheet: the title carries a value glued onto the
    name ("OPERATION INFO: 3 - Contour (2D)"), so grouping by the WHOLE
    title would never have grouped three operations together.
    """
    return str(title or "").split(":")[0].strip()


def _title_value(title):
    """Whatever stands AFTER the colon in a title - the operation or tool name.

    This must not be lost when a table is assembled: in a PDF the operation
    name lives ONLY in the section title, in no field at all.
    """
    title = str(title or "")
    return title.split(":", 1)[1].strip() if ":" in title else ""


def _build_table(sections, title, level):
    """Several sibling sections -> a table (plus the list of empty columns).

    `sections` is a list of lists of (label, value) pairs. Returns a list of
    blocks, or None when these sections cannot make a sensible table.
    """
    columns = []
    rows = []
    for pairs in sections:
        row = {}
        for label, value in pairs:
            label = str(label)
            value = str(value)
            if label not in columns:
                columns.append(label)
            # The same label can appear MORE THAN ONCE within one section -
            # measured: USED BY OPERATION twice under a tool and three times
            # under a coordinate system. A dict would keep only the last value
            # and the rest would vanish silently.
            if row.get(label):
                row[label] = row[label] + "; " + value
            else:
                row[label] = value
        rows.append(row)

    if not columns:
        return None

    empty = [k for k in columns
             if not any(w.get(k, "").strip() for w in rows)]
    visible = [k for k in columns if k not in empty]
    if not visible:
        return None

    return [{"kind": "table", "title": title, "level": level,
             "columns": visible, "empty_fields": empty,
             "rows": [[w.get(k, "") for k in visible] for w in rows]}]


# the same name, one of which is noise, are worse than one.
HIDDEN_FIELDS = ("CUSTOMER NAME",)

SHAPE_LABEL = "SHAPE"
SIZE_LABEL = "SIZE"
TWO_DIMENSION_SHAPES = ("CYLINDER",)


def _number_without_trailing_zero(chunk):
    """"330.0" -> "330", but "17.5" stays "17.5".

    This affects the NOTATION only, never the value. A non-integer is left
    untouched to the digit - nothing is rounded.
    Anything that is not a number comes back unchanged.
    """
    text = str(chunk).strip()
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(value)) if value == int(value) else text


def _stock_dimensions(value, shape):
    """Tidy up the stock SIZE. Returns a new string.

    TWO RULES, both added after a model repeated back the noise we had
    handed it ourselves ("a cylinder measuring 330.0, 15.0, 0.0"):

      1. A CYLINDER HAS TWO DIMENSIONS. The CAM system prints three anyway,
         and the third is zero, because a cylinder has no third axis. A box
         has three real ones and those are left alone.
      2. WHOLE NUMBERS LOSE THE TRAILING ZERO. "330.0" reads worse than
         "330" and means exactly the same.

    A GATE THAT WAS NOT IN THE REQUEST AND STAYS ANYWAY: the third value is
    dropped ONLY when it is zero. If it ever were not, the "a cylinder has
    two dimensions" rule would be deleting a real number out of someone's
    file, and that is something we never do. On every real cylinder the
    condition holds, so the rule behaves exactly as intended.
    """
    text = str(value).strip()
    if "," not in text:
        return _number_without_trailing_zero(text)
    chunks = [k.strip() for k in text.split(",")]
    cleaned_parts = [_number_without_trailing_zero(k) for k in chunks]
    if str(shape).strip().upper() in TWO_DIMENSION_SHAPES:
        while len(cleaned_parts) > 2 and cleaned_parts[-1] in ("0", "0.0", ""):
            cleaned_parts.pop()
    return ", ".join(cleaned_parts)


def _hide_fields(description):
    """Remove the fields listed in HIDDEN_FIELDS. Same for PDF and XML.

    We cut the WHOLE LINE, not just the value. An empty field on screen
    would raise the same question a filled one does: "why is there nothing
    here?".
    """
    if not isinstance(description, dict):
        return description
    for block in description.get("blocks", []) or []:
        if block.get("kind") == "table":
            columns = list(block.get("columns", []))
            hidden = [i for i, k in enumerate(columns)
                   if str(k).strip().upper() in HIDDEN_FIELDS]
            if not hidden:
                continue
            block["columns"] = [k for i, k in enumerate(columns)
                               if i not in hidden]
            block["rows"] = [[w for i, w in enumerate(row)
                                if i not in hidden]
                               for row in block.get("rows", [])]
        else:
            pairs = block.get("pairs")
            if not pairs:
                continue
            block["pairs"] = [p for p in pairs
                            if str(p[0]).strip().upper() not in HIDDEN_FIELDS]
    return description


def _fix_stock(description):
    """Apply the size rules everywhere SIZE stands next to SHAPE.

    Done ONCE, on the finished structure, so it behaves identically for PDF
    and XML and nobody has to remember it in two places.
    """
    if not isinstance(description, dict):
        return description
    for block in description.get("blocks", []) or []:
        if block.get("kind") == "table":
            columns = [str(k).strip().upper() for k in block.get("columns", [])]
            if SIZE_LABEL not in columns:
                continue
            where = columns.index(SIZE_LABEL)
            shape_col = (columns.index(SHAPE_LABEL)
                             if SHAPE_LABEL in columns else None)
            for row in block.get("rows", []):
                if where >= len(row):
                    continue
                shape = (row[shape_col]
                           if shape_col is not None
                           and shape_col < len(row) else "")
                row[where] = _stock_dimensions(row[where], shape)
        else:
            pairs = block.get("pairs") or []
            shape = ""
            for label, value in pairs:
                if str(label).strip().upper() == SHAPE_LABEL:
                    shape = value
            for pair in pairs:
                if str(pair[0]).strip().upper() == SIZE_LABEL:
                    pair[1] = _stock_dimensions(pair[1], shape)
    return description


def _with_translation(label, lexicon, section=None):
    """Append a clarifying note to a label from the file, when we have one.

    It does not translate - it APPENDS. The original label from the file
    stays in place, because that is the truth and that is what a person
    searches for in the CAM system.

    THE SECTION MATTERS, and this was not invented at a desk. `NUMBER` in a
    TOOL INFO block is the tool's number in the magazine; in an OFFSET INFO
    block it is something else entirely. One note for both would be false in
    one of those places. So the table may hold a key "TOOL INFO.NUMBER",
    and when it does not, the lookup falls back to plain "NUMBER".
    """
    if not lexicon:
        return label
    sanitized = str(label).strip().upper()
    translated = None
    if section:
        translated = lexicon.get("%s.%s" % (str(section).strip().upper(), sanitized))
    if translated is None:
        translated = lexicon.get(sanitized)
    return "%s (%s)" % (label, translated) if translated else label



def _block_rows(block):
    """A block -> a list of {label: value} dicts, whatever its kind.

    A setup sheet sometimes prints tools as a table and sometimes as
    separate sections of pairs, depending on how the CAM system laid the
    page out. This function reduces both cases to one shape so that the
    rest of the code does not have to know about it.
    """
    if block.get("kind") == "table":
        columns = [str(k).strip().upper() for k in block.get("columns", [])]
        return [dict(zip(columns, [str(w).strip() for w in row]))
                for row in block.get("rows", [])]
    pairs = block.get("pairs") or []
    if not pairs:
        return []
    return [{str(e).strip().upper(): str(w).strip() for e, w in pairs}]


def stock_facts(description):
    """Stock shape and size, taken from the STOCK block. A dict, or {}.

    WHY THIS EXISTS - MEASURED over three consecutive runs: asked "what was
    the stock and what size was it", the model answered that "the supplied
    information contains no details about the stock" - with this in the text
    it had been given:
        STOCK: YES
        SHAPE: Cylinder
        SIZE: 330, 15
    It said "I DON'T HAVE THAT" while looking straight at it. That is the
    worst kind of error in this project: the user believes the catalogue
    does not hold it and goes looking somewhere else.

    WHY THE PROGRAM COMPUTES THIS ITSELF: those three lines are separate
    fields, and right next to them stand FOUR lines reading "STOCK TO
    LEAVE: 0.0" - where the word STOCK means something different. The model
    has to work out which of them describes the raw material, and it does
    not. Same route as with the tool list and the job count: we do not ask
    it to infer, we hand it the result.
    """
    for block in description.get("blocks", []) or []:
        fields = {}
        for pair in block.get("pairs", []) or []:
            key = str(pair[0]).strip().upper()
            if key in ("SHAPE", "SIZE", "STOCK"):
                fields[key] = str(pair[1]).strip()
        if "SHAPE" in fields or "SIZE" in fields:
            return {"shape": fields.get("SHAPE", ""),
                    "size": fields.get("SIZE", ""),
                    "present": fields.get("STOCK", "")}
    return {}


# Titles of the tool block. THREE, NOT ONE: the same setup sheet gives
# "TOOL INFO" as a PDF and "TOOL" as XML.
TOOL_BLOCK_TITLES = ("TOOL INFO", "TOOL LIST", "TOOL")
OPERATION_TITLE = "OPERATION INFO"
# A row counts as a tool only once it carries something about the tool
# itself. Without this, a heading like "TOOL LIST: Sorted NO" would land on
# the list as a tool with no name.
TOOL_FIELDS = ("TYPE", "DIAMETER")


def tool_list(description):
    """Tools USED on the job, each one ONCE. A list of dicts.

    WHY THIS EXISTS - measured over three consecutive runs: asked "did I use
    a chamfer mill", the model answered "no" while holding TWO rows reading
    `TYPE: Chamfer mill` in the text it had been given. On the last run it
    quoted verbatim the very line it claimed was absent. This is not a gap
    in the text - three rewrites of the instruction changed nothing. So we
    stop relying on the model to infer it and hand it a finished list -
    the same route that already rescued the job count, which the model once
    reported as fourteen when it was five.

    EACH TOOL ONCE, and that is not cosmetic. A setup sheet prints the TOOL
    INFO block for EVERY OPERATION, so one tool used in four operations
    yields four blocks. Measured on a real file: four blocks, all of them
    tool number 1. Without merging by number, the answer to "how many
    tools" would be inflated.
    """
    found_items = []
    seen = {}
    for block in description.get("blocks", []) or []:
        title = str(block.get("title") or "").strip().upper()
        if not any(title.startswith(t) for t in TOOL_BLOCK_TITLES):
            continue
        for row in _block_rows(block):
            if not any(row.get(p) for p in TOOL_FIELDS):
                continue
            number = row.get("NUMBER", "")
            name = (row.get("TOOL INFO")
                     or row.get("TOOL-INFO") or "")
            key = number or name
            if not key:
                continue
            if key in seen:
                # A SECOND COPY ADDS NO TOOL, BUT MAY COMPLETE ONE.
                # Measured on a real file: the setup sheet prints a TOOL INFO block at every
                # operation (without a USED BY OPERATION line), and only the summary list at
                # the end carries that line. Taking the first copy and stopping loses the
                # mapping from tool to operation - which is exactly what used to happen.
                previous = seen[key]
                for field in ("operations", "kind", "diameter", "name"):
                    if not str(previous.get(field) or "").strip():
                        previous[field] = row.get({
                            "operations": "USED BY OPERATION",
                            "kind": "TYPE",
                            "diameter": "DIAMETER",
                            "name": "TOOL INFO"}[field], "")
                continue
            new_tool = {
                "number": number,
                "name": name,
                "kind": row.get("TYPE", ""),
                "diameter": row.get("DIAMETER", ""),
                # WHICH OPERATION THIS TOOL WORKED ON.
                # The raw value from the file, shaped like "# 4 4 - Contour (2D chamfer)".
                # Parsing it belongs to the language layer, not to reading the file, so it
                # is done there.
                # Measured on a real file: this field stands beside EVERY one of the four
                # tools in the block we hand to the model anyway - without_repeated_tools()
                # picks the richer copy precisely because it carries these lines.
                "operations": row.get("USED BY OPERATION", ""),
            }
            seen[key] = new_tool
            found_items.append(new_tool)
    return found_items


def operation_count(description):
    """How many operations this setup sheet has. Zero when it cannot be told."""
    count = 0
    for block in description.get("blocks", []) or []:
        title = str(block.get("title") or "").strip().upper()
        if title.startswith(OPERATION_TITLE):
            count += len(_block_rows(block))
    return count


def gcode_facts(path):
    """Numbers extracted from an NC program. Returns a dict or None.

    WHY FACTS AND NOT THE TEXT - measured, not assumed. A real program file:
    8438 lines, 136 KB, roughly 66 000 tokens. A typical local model's
    context window is 4096. The NC program is SIXTEEN TIMES larger than the
    entire window, and its text can never be supplied - neither whole nor in
    a fragment that would mean anything. So we count it ourselves and hand
    over the result, exactly as with the tool list.

    WHAT WE COUNT AND WHY THESE THINGS: what a person looks for when they
    come back to a job from a year ago. "How many lines" was asked
    literally. The rest is the program number, the tools, speeds, feeds and
    coolant - what a programmer checks before running a program again.

    TOOLS FROM `T.. M6` ARE STRONGER EVIDENCE THAN THE SETUP SHEET: this is
    the program that actually went to the machine, not a plan from the CAM
    system. Measured on a real file: T2, T12, T16, T25 - exactly the four
    that stand in the setup sheet.

    WE READ AS A STREAM, line by line. The file may be tens of megabytes and
    there is no reason to hold it in memory just to count its lines.
    """
    facts = {"line_count": 0, "tools": [], "speeds": [], "feeds": [],
             "program": "", "coolant": False}
    try:
        with open(path, encoding="ascii", errors="replace") as file:
            for line in file:
                facts["line_count"] += 1
                line = line.strip().upper()
                if not line:
                    continue
                if not facts["program"]:
                    match = re.match(r"^O\s*(\d+)", line)
                    if match:
                        facts["program"] = match.group(1)
                # T12 M6 - a real tool change. A bare "T12" on a nearby line is often only
                # an announcement of the next tool and does not mean it entered the
                # spindle.
                match = re.match(r"^T\s*(\d+)\s*M0?6\b", line)
                if match:
                    number = match.group(1).lstrip("0") or "0"
                    if number not in facts["tools"]:
                        facts["tools"].append(number)
                for speeds in re.findall(r"\bS(\d+)", line):
                    if int(speeds) and int(speeds) not in facts["speeds"]:
                        facts["speeds"].append(int(speeds))
                for feed in re.findall(r"\bF([\d.]+)", line):
                    try:
                        value = float(feed)
                    except ValueError:
                        continue
                    if value and value not in facts["feeds"]:
                        facts["feeds"].append(value)
                if re.search(r"\bM0?[78]\b", line):
                    facts["coolant"] = True
    except OSError:
        return None
    if not facts["line_count"]:
        return None
    facts["speeds"].sort()
    facts["feeds"].sort()
    return facts


def without_repeated_tools(description):
    """A copy of the description without the REPEATED tool section.
    FOR THE MODEL, NOT FOR THE SCREEN.

    MEASURED on a real setup sheet: the tool section appears in the file
    TWICE. Once beside the operations, and again under TOOL LIST - the same
    four tools, the same numbers, the same order. There is one difference:
    the second copy carries `USED BY OPERATION`, the map of which tool went
    with which operation.

    WHAT IT COSTS: the redundant copy is 2211 characters, about 30% of the
    whole text and roughly 1068 tokens. Against a 4096-token window from
    which a real file already eats 3584, that is the difference between 87%
    and 61% occupancy.

    WE KEEP THE RICHER COPY, not the first one - because that is the one
    carrying the operation mapping. Not one piece of information is lost; we
    simply stop supplying the same thing twice.

    THE SCREEN IS UNTOUCHED. A person viewing an attachment gets the whole
    description: no context window applies there.
    """
    blocks = description.get("blocks") or []
    tool_blocks = [(i, b) for i, b in enumerate(blocks)
                   if str(b.get("title") or "").strip().upper()
                   .startswith("TOOL INFO")]
    if len(tool_blocks) < 2:
        return description

    def keys_of(block):
        result = []
        for row in _block_rows(block):
            result.append(row.get("NUMBER")
                         or row.get("TOOL INFO")
                         or row.get("TOOL-INFO") or "")
        return tuple(result)

    def field_richness(block):
        fields = set()
        for row in _block_rows(block):
            fields.update(k for k, v in row.items() if str(v).strip())
        return len(fields)

    best = {}
    for number, block in tool_blocks:
        key = keys_of(block)
        if not any(key):
            continue
        old = best.get(key)
        # ">=" rather than ">": on equal richness the LATER copy wins, because in
        # the CAM output it is the one carrying USED BY OPERATION.
        if old is None or field_richness(block) >= field_richness(blocks[old]):
            best[key] = number

    kept = set(best.values())
    to_drop = {number for number, block in tool_blocks
                     if any(keys_of(block)) and number not in kept}
    if not to_drop:
        return description

    trimmed = dict(description)
    trimmed["blocks"] = [b for i, b in enumerate(blocks) if i not in to_drop]
    return trimmed


def as_text(description, lexicon=None):
    """Blocks from describe() -> text for the MODEL to read.

    `lexicon` (optional) appends clarifying notes to labels from the file.
    This module holds none of its own - they arrive from outside, because
    that is knowledge about how people speak, not about reading a PDF.

    WHY THIS IS SEPARATE FROM THE SEARCH TEXT, when the source is the same:
    that one assembles text FLAT - label, value, label, value, and in tables
    all the column names first and then all the cells in one run. The search
    index does not care, because it matches single words. A MODEL cannot
    tell from such a list which number belongs to which operation - and the
    entire point of the AI mode rests on it being able to.

    Here every table row gets its OWN heading with a number
    ("[OPERATION INFO 2 of 3]") and its own fields beneath it. Measured on a
    real setup sheet: the model correctly picked out the feed of the THIRD
    operation (3500) with two others at 3000 - so the number stayed attached
    to its row.

    Empty fields are not shown, just as on screen. Their names stay in the
    data under the "empty" key - this function does not read them, but
    nobody deletes them either.

    Knows nothing about the database or the UI. Never raises: a description
    with no blocks produces empty text, not a failure.
    """
    lines = []
    for block in description.get("blocks", []):
        title = str(block.get("title") or "").strip()
        if block.get("kind") == "table":
            columns = [str(k) for k in block.get("columns", [])]
            rows = block.get("rows", [])
            for number, row in enumerate(rows, 1):
                lines.append("[%s %d of %d]"
                             % (title or "SECTION", number, len(rows)))
                for label, value in zip(columns, row):
                    if str(value).strip():
                        lines.append("  %s: %s" % (
                            _with_translation(label, lexicon, title),
                            value))
                lines.append("")
        else:
            pairs = [p for p in block.get("pairs", []) if str(p[1]).strip()]
            if not title and not pairs:
                continue
            lines.append("[%s]" % (title or "SECTION"))
            for label, value in pairs:
                lines.append("  %s: %s" % (
                    _with_translation(label, lexicon, title),
                    value))
            lines.append("")
    return "\n".join(lines).strip()
