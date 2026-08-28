"""The files of a job: their names, their removal, and the text in them.

TWO RULES STAND OVER THE WHOLE MODULE:

    A FILE IS NEVER DELETED OUTRIGHT. It goes to the system recycle bin,
    and when there is none - into a folder beside the job. What somebody
    put there by hand is not ours to destroy.

    A NAME FROM SOMEBODY ELSE IS NOT TRUSTED. It arrives from a browser,
    from a phone, from a rebuild off disk; it can hold a slash, a colon,
    or a name Windows reserves for a device.

The text that comes out of an attachment is here as well, in two shapes:
one for the index (as much as can be afforded) and one for the model
(short, and cut at a block boundary rather than mid-sentence).
"""

import ctypes
import hashlib
import os
import re
import shutil

from . import DELETED_DIR
from . import setupsheet


# Measured on a real six-page setup sheet: the whole report comes to about
# 2 900 characters. The limit is therefore a fuse against an unusual file,
# not a real constraint.
MAX_INDEX_TEXT = 200 * 1024


# The text for the model has a DIFFERENT fuse from the text for the index,
# because a different thing constrains it: not room in the database but how
# much the model sees at once. Measured: one setup sheet is 2 983
# characters, roughly 900-1000 tokens, and a model with an 8K context fits
# four or five jobs at once together with the question.
# 20 KB is about seven such reports - more than we will ever hand a model
# in one go, and at the same time a barrier against a single monster file.
MAX_MODEL_TEXT = 20 * 1024

MAX_ATTACHMENT_BYTES = 2000 * 1024 * 1024   # 2000 MB - a fuse, not a policy


def move_to_recycle_bin(path):
    """Move a directory to the Windows RECYCLE BIN.

    Uses SHFileOperation from the Windows shell through ctypes, which is
    part of Python - no third-party library. The user then recovers the
    folder the way they recover any other file: right-click in the Recycle
    Bin.

    Returns "recycled" when it went to the system bin, or "moved" when the
    fallback path had to be used. NEVER deletes irreversibly.
    """
    path = os.path.abspath(path)
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as w

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", w.HWND),
                    ("wFunc", w.UINT),
                    ("pFrom", w.LPCWSTR),
                    ("pTo", w.LPCWSTR),
                    ("fFlags", ctypes.c_uint16),
                    ("fAnyOperationsAborted", w.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", w.LPCWSTR),
                ]

            FO_DELETE = 3
            FOF_SILENT = 0x0004
            FOF_NOCONFIRMATION = 0x0010
            FOF_ALLOWUNDO = 0x0040          # this is the flag that sends it to the Recycle Bin
            FOF_NOERRORUI = 0x0400

            operation = SHFILEOPSTRUCTW()
            operation.hwnd = None
            operation.wFunc = FO_DELETE
            # pFrom has to end with TWO null characters
            operation.pFrom = path + "\0\0"
            operation.pTo = None
            operation.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION
                               | FOF_SILENT | FOF_NOERRORUI)
            result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
            if result == 0 and not operation.fAnyOperationsAborted:
                return "recycle_bin"
        except Exception:
            pass  # fallback below - the user is never left with nothing
    return None


def archive_instead_of_delete(path, data_dir):
    """Fallback: move the directory to <data_dir>/_deleted/.

    Used when the system recycle bin is unavailable (another operating
    system, a network drive, missing permissions). The rule stays the same:
    nothing is lost.
    """
    target_dir = os.path.join(data_dir, DELETED_DIR)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(path))
    number = 2
    while os.path.exists(target):
        target = os.path.join(target_dir, "%s (%d)" % (os.path.basename(path), number))
        number += 1
    shutil.move(path, target)
    return target


# Names reserved on Windows - a file named this way cannot be created,
# not even with an extension. A trap that does not exist on Linux.
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}


def safe_filename(name):
    """A filename that can definitely be written on Windows.

    We do not guess what the user meant - we only remove what the system
    would reject, or what would allow escaping the job folder.
    """
    name = os.path.basename(str(name or "").replace("\\", "/"))
    name = name.split("/")[-1]
    name = "".join(z for z in name if z >= " " and z not in '<>:"/\\|?*')
    name = name.strip().strip(".")
    if not name:
        name = "file"
    stem = name.split(".")[0].lower()
    if stem in _RESERVED_NAMES:
        name = "_" + name
    if len(name) > 120:
        root, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 12:
            name = root[:120 - len(ext) - 1] + "." + ext
        else:
            name = name[:120]
    return name


def _free_filename(directory, name):
    """When a file of that name is already there - append (2), (3)..."""
    if not os.path.exists(os.path.join(directory, name)):
        return name
    root, dot, ext = name.rpartition(".")
    if not dot:
        root, ext = name, ""
    number = 2
    while True:
        candidate = "%s (%d)%s%s" % (root, number, "." if ext else "", ext)
        if not os.path.exists(os.path.join(directory, candidate)):
            return candidate
        number += 1


def _free_job_dir(jobs_dir, name):
    """A folder name for a NEW job - one that is not on disk yet.

    WHY THIS EXISTS AT ALL, given that the folder name contains the job
    number and numbers in the database do not repeat: they do repeat when
    folders sit on disk that this database knows nothing about. That is
    exactly what moving to a new machine looks like - the job folders come
    along, but `chipbook.db` does not. An empty database starts numbering
    from 1, which is the name already lying on disk.

    If a new job entered that folder, it would overwrite its `job.txt` -
    the ONLY way back to that job - and claim its attachments as its own.
    So a new job never enters a folder that already exists; it gets its
    own, suffixed -2, -3 and so on.
    """
    if not os.path.exists(os.path.join(jobs_dir, name)):
        return name
    next_number = 2
    while True:
        candidate = "%s-%d" % (name, next_number)
        if not os.path.exists(os.path.join(jobs_dir, candidate)):
            return candidate
        next_number += 1


def text_for_search(path, name=None):
    """Setup-sheet text, ready to be put into the search index.

    It is extracted by THE SAME code that renders the on-screen view - so
    the screen never shows anything that cannot be found, and the reverse.

    Primary files only (PDF and XML setup sheets). G-code is deliberately
    left out: thousands of nearly identical coordinate lines that would
    bury the results under meaningless numbers.
    DELIBERATELY REJECTED, not forgotten - it comes back when there is a
    real NC program to measure how much of it there actually is.

    An unreadable file yields empty text, not a failure: a job must save
    even when its attachment cannot be broken out.
    """
    name = name or os.path.basename(path)
    if setupsheet.extension(name) not in setupsheet.SETUP_SHEET_EXTENSIONS:
        return ""
    try:
        description = setupsheet.describe(path, name)
    except Exception:                      # noqa: BLE001 - absence is not a failure
        return ""
    parts = []
    for block in description.get("blocks", []):
        parts.append(str(block.get("title") or ""))
        if block.get("kind") == "table":
            parts.extend(str(k) for k in block.get("columns", []))
            for row in block.get("rows", []):
                parts.extend(str(field) for field in row)
        else:
            for pair in block.get("pairs", []):
                parts.append(str(pair[0]))
                parts.append(str(pair[1]))
    text = "\n".join(part for part in parts if part.strip())
    return text[:MAX_INDEX_TEXT]


# FIELD_NOTES - clarifications appended to setup-sheet labels.
#
# WHAT IS NOT HERE AND WHY: the table does not restate labels that speak
# for themselves (FEEDRATE, DIAMETER, DEPTH). It holds ONLY those where the
# label itself misleads - and every one of them arrived after a model
# answered a real question wrongly.
FIELD_NOTES = {
    # abbreviations that cannot be expanded from the name alone
    "FPT": "feed per tooth",
    "SFM": "surface cutting speed",
    "WORK OFFSET": "part zero",

    # A TOOL NAME STARTS WITH THE DIAMETER. These two lines stand bare
    # beside each other in a setup sheet:
    #     TOOL INFO: 17 Chamfer Mill
    #     NUMBER: 25
    # Asked for the tool number, a model answered "17" - because the CAM
    # system starts the name with the diameter and the real number is on
    # the line below.
    "TOOL INFO": "tool name - starts with the DIAMETER, not the tool number",
    "TOOL INFO.NUMBER": "tool number in the magazine - not the figure "
                        "at the start of the tool name",

    # NUMBER means something different in every section, so one shared note
    # would be false in one of those places. Hence a key carrying the section.
    "OFFSET INFO.NUMBER": "offset number",

    # MATERIAL IN A TOOL BLOCK IS THE CUTTING-EDGE MATERIAL. A setup sheet
    # does not contain the part material at all, so asked what the part was
    # made of, a model reached for "Carbide" - and had support for it in the
    # text, so a quote check would not have caught the error.
    # THREE KEYS, NOT ONE: the tool block has three different titles. A PDF
    # from the CAM system gives "TOOL INFO", the same file as XML gives
    # "TOOL".
    "TOOL INFO.MATERIAL": "TOOL material, not part material",
    "TOOL LIST.MATERIAL": "TOOL material, not part material",
    "TOOL.MATERIAL": "TOOL material, not part material",
}


def text_for_model(path, name=None):
    """Setup-sheet text assembled FOR THE MODEL, not for the search index.

    Same rule as for the search text: it is extracted by THE SAME code that
    renders the on-screen view, so the model sees nothing a person cannot -
    and the reverse.
    The difference is in the ASSEMBLY, not the source.

    An unreadable file yields empty text, not a failure - as with the
    index.
    """
    name = name or os.path.basename(path)
    # THE NC PROGRAM TAKES A DIFFERENT ROUTE. Its text is not supplied -
    # measured on a real file: 8438 lines, about 66 000 tokens against a
    # 4096-token window. We supply computed facts instead, as with the tools.
    if setupsheet.extension(name) in setupsheet.GCODE_EXTENSIONS:
        return describe_gcode(path)
    if setupsheet.extension(name) not in setupsheet.SETUP_SHEET_EXTENSIONS:
        return ""
    try:
        description = setupsheet.describe(path, name)
        # The list is computed from the FULL description while the text is
        # assembled from the trimmed one - the order matters, because trimming
        # removes an entire section.
        listing = describe_tool_list(description)
        stock = describe_stock(description)
        if stock:
            # THE STOCK SENTENCE STANDS TOGETHER WITH THE TOOL LIST, right at the
            # top - for the same reason: if the text gets truncated, what must survive
            # is a finished answer, not the tail of a table.
            listing = (stock + "\n" + listing) if listing else stock
        text = setupsheet.as_text(setupsheet.without_repeated_tools(description),
                                   FIELD_NOTES)
    except Exception:                      # noqa: BLE001 - absence is not a failure
        return ""
    if listing:
        # THE LIST GOES FIRST, and that is a decision rather than an arrangement.
        # If the text is truncated at the size limit, what must survive is the most
        # important thing - and that is a finished answer, not the tail of a table
        # of feed rates.
        text = listing + "\n\n" + text
    return text[:MAX_MODEL_TEXT]


def describe_gcode(path):
    """An NC program described in sentences. To be supplied to the model as
    FACT.

    THIS CLOSES THE ORIGINAL COMPLAINT: "after adding a gcode file the AI
    does not answer questions, for example how many lines the gcode has;
    in general the AI says I have not added any entry". The NC program was
    INVISIBLE to the model - the text builder passed only .pdf and .xml
    through, so the model did not even know the file existed.

    WHY WE DO NOT SUPPLY THE CONTENT: measured on a real file - 8438 lines,
    136 KB, about 66 000 tokens. The window is 4096. Sixteen times too
    much. No fragment of that file would answer "how many lines" either -
    that has to be counted, not read.

    THE LINE COUNT AS A FACT, NOT AS A TASK FOR THE MODEL. Models count
    badly; here the program counts and supplies the result. The same route
    as the job count and the tool list.

    WE ARE CAREFUL ABOUT COOLANT: the program shows that it is TURNED ON
    (M8/M7) but not WHICH coolant. The sentence has to make that
    distinction, or the model will answer "what coolant did I use" with
    something that is not in the file.
    """
    facts = setupsheet.gcode_facts(path)
    if not facts:
        return ""
    lines = ["NC PROGRAM - FIGURES COMPUTED BY THE PROGRAM "
             "(not to be guessed):"]
    lines.append("  %s" % _plural(facts["line_count"], "line", "lines"))
    if facts["program"]:
        lines.append("  program number: %s" % facts["program"])
    if facts["tools"]:
        lines.append("  tools called in the program (tool changes): "
                     + ", ".join("no. " + n for n in facts["tools"]))
    if facts["speeds"]:
        lines.append("  spindle speeds: "
                     + ", ".join("%d" % o for o in facts["speeds"]) + " RPM")
    if facts["feeds"]:
        lines.append("  feeds: " + ", ".join(
            _short_number(p) for p in facts["feeds"]) + " mm/min")
    if facts["coolant"]:
        lines.append("  coolant is TURNED ON in the program (M8/M7). "
                     "Which coolant exactly, the program does not say.")
    return "\n".join(lines)


def _short_number(value):
    """15000.0 -> 15000, but 0.5 stays 0.5. Same rule as for stock size."""
    return (str(int(value)) if float(value) == int(value)
            else str(value))


def describe_stock(description):
    """One finished sentence about the stock, supplied to the model as FACT.

    MEASURED over three runs out of three: asked about the stock, a model
    answered "the information does not contain details" while holding
    SHAPE, SIZE and STOCK in the text. It said "I don't have that" over
    data it did have.

    THE PRICE, STATED: if our reading of the block is ever wrong, the model
    receives a LIE presented as fact instead of making its own mistake.
    That is why the sentence is built ONLY from fields that genuinely stand
    in the file - a missing field means a missing sentence, not a guess.
    """
    facts = setupsheet.stock_facts(description)
    if not facts:
        return ""
    shape = facts.get("shape", "")
    parts = []
    if shape:
        parts.append("shape %s" % shape)
    if facts.get("size"):
        parts.append("size %s" % facts["size"])
    if not parts:
        return ""
    return ("THE STOCK THIS PART WAS MADE FROM "
            "(read by the program, not to be guessed): "
            + ", ".join(parts) + ".")


# WHICH OPERATION A TOOL WORKED ON - APPENDED TO THE LIST.
#
# THE REASON, MEASURED THREE TIMES: asked "which position had the chamfer
# mill that was NOT for holes", the model answers with the chamfer tool
# FOR holes instead of the one from the contour operation. Three runs,
# identical every time, including with a longer answer.
# WHY THIS IS HARD FOR A MODEL: the answer requires JOINING the tool block
# with a USED BY OPERATION line. Both are in the text, but in two
# different places - and joining two sections is what this model does not
# do.
# SO WE DO NOT ASK IT TO JOIN. The program joins and supplies the result -
# the same route that has already won TWICE here: the tool list went from
# 0/3 to 3/3, and the stock from 0/3 to 3/3.
# THE PRICE, STATED: this is text that EVERY question sees, so it may harm
# the ones that currently come out 3/3. Hence the switch, and hence
# measuring all nine questions rather than the one about the chamfer.
APPEND_OPERATIONS = True


# "# 4 4 - Contour (2D chamfer)" -> "4 - Contour (2D chamfer)".
# The first figure after the hash is the CAM system's ordinal, the second
# is the operation number. We keep the second together with the name,
# because a person asks about an operation by name ("contour chamfering"),
# not by its ordinal.
_OPERATION_FROM_FIELD = re.compile(r"#\s*\d+\s+([^#]+)")


def _tool_operations(raw):
    """A raw USED BY OPERATION line -> a readable description, or nothing.

    ONE TOOL MAY WORK ON SEVERAL OPERATIONS, and then the field holds
    several of them in a row. We supply all of them - a tool used on two
    operations but listed with one would be a fact computed WRONGLY, and
    that is worse than no fact at all.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    parts = [" ".join(c.split()) for c in _OPERATION_FROM_FIELD.findall(text)]
    parts = [c for c in parts if c]
    if not parts:
        return ""
    return "; ".join(parts)


def describe_tool_list(description):
    """A finished tool list in sentences, supplied to the model as FACT.

    WHY THE PROGRAM COMPUTES THIS ITSELF - measured over three consecutive
    runs at temperature 0. Asked "did I use a chamfer mill", the model
    answered "no" while holding two rows reading `TYPE: Chamfer mill` in
    the text. On the third run it wrote outright that "there is no tool of
    type Chamfer mill" - quoting a string that stands in the supplied text
    twice.
    Three rewrites of the instruction moved it not one step, while the same
    question asked differently ("list the tools") came out correct in EVERY
    run.
    THE CONCLUSION: this is neither missing information nor a bad
    instruction. The model simply does not draw that conclusion from a
    table. So we do not ask it to - we supply the result.
    THIS ROUTE IS PROVEN HERE: the number of jobs in the database has been
    supplied as a stated fact ever since a model reported fourteen when
    there were five.

    THE PRICE, STATED: when our computation is wrong, the model receives a
    LIE delivered with confidence instead of making its own mistake. That
    is why this is covered by tests more heavily than the rest.
    """
    tools = setupsheet.tool_list(description)
    if not tools:
        return ""
    lines = ["TOOLS USED ON THIS JOB "
             "(computed by the program, not to be guessed):"]
    for tool in tools:
        type_text = tool["kind"] or "unknown type"
        parts = []
        if tool["number"]:
            parts.append("no. %s" % tool["number"])
        parts.append(type_text)
        if tool["diameter"]:
            parts.append("diameter %s" % tool["diameter"])
        row = "  " + ", ".join(parts)
        if APPEND_OPERATIONS:
            operations = _tool_operations(tool.get("operations"))
            if operations:
                row += " - used on operation: %s" % operations
        lines.append(row)
    operation_total = setupsheet.operation_count(description)
    summary = "TOTAL: %s" % _plural(
        len(tools), "tool", "different tools")
    if operation_total:
        summary += ", " + _plural(operation_total, "operation", "operations")
    lines.append(summary + ".")
    return "\n".join(lines)


def _plural(count, one, many):
    """Count with the right form: 1 operation, 2 operations.

    The model reads this text the way a person does, and a mismatched form
    reads like a bug and undermines trust in the rest of the sentence - and
    this is a sentence meant to be a FACT for it.
    """
    count = int(count)
    return "%d %s" % (count, one if count == 1 else many)


def _file_sha256(path):
    total = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            total.update(chunk)
    return total.hexdigest()
