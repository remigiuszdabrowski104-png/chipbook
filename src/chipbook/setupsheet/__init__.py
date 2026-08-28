"""chipbook - a readable view of an attachment's contents.

WHY THIS EXISTS: the catalogue is meant to be a readable record of what
was actually machined, not a file dump you have to decipher. So:

    .mcam, .step         -> STORAGE. The program does not read them.
    .xml (setup sheet)   -> PRIMARY FILE. Broken out into a readable view.
    .pdf (setup sheet)   -> PRIMARY FILE. Broken out the same way.
    G-code and other text-> shown as text with line numbers.

THE RULE: nothing is guessed. Field names and values come ONLY from the
file. This package does not know what "spindle" means - it shows what the
file says, merely arranged for a human. Any interpretation of names has to
wait until a real file proves what is actually in it.

The package knows nothing about the UI or the database.

    xml     reading a setup sheet saved as XML
    pdf     the same for PDF, on the standard library alone
    render  the shape they both leave behind, made readable

Plain text needs no reader of its own, so it is handled here: the file is
shown as lines with numbers, and that is the whole of it.
"""

import os

from . import pdf
from . import render
from . import xml
from .render import (as_text, gcode_facts, operation_count, stock_facts,
                     tool_list, without_repeated_tools)

XML_EXTENSIONS = (".xml",)
PDF_EXTENSIONS = (".pdf",)

# Files the catalogue displays BY ITSELF, without anyone clicking.
SETUP_SHEET_EXTENSIONS = XML_EXTENSIONS + PDF_EXTENSIONS

# Files worth showing as plain text with line numbers.
TEXT_EXTENSIONS = (
    ".nc", ".txt", ".tap", ".mpf", ".ptp", ".cnc", ".eia", ".iso",
    ".min", ".prg", ".csv", ".json", ".log", ".md", ".gcode", ".ngc",
)

# NC programs. A SUBSET of TEXT_EXTENSIONS, because counting tools, speeds
# and feeds only makes sense for these. From .txt, .csv, .json, .log or .md
# it would produce empty lists pretending to be a measurement.
GCODE_EXTENSIONS = (
    ".nc", ".tap", ".mpf", ".ptp", ".cnc", ".eia", ".iso",
    ".min", ".prg", ".gcode", ".ngc",
)

MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_LINES = 400                   # how many lines of text we show



def extension(name):
    return os.path.splitext(str(name or ""))[1].lower()


def can_display(name):
    """Whether this file gets a rendered view at all."""
    ext = extension(name)
    return (ext in XML_EXTENSIONS or ext in PDF_EXTENSIONS
            or ext in TEXT_EXTENSIONS)


def describe(path, name=None):
    """Return a structure to show to a person.

    Always returns a dict with a "kind" key. Never raises - an unreadable
    file is information, not a failure.
    """
    name = name or os.path.basename(path)
    ext = extension(name)
    try:
        size_bytes = os.path.getsize(path)
    except OSError as error:
        return {"kind": "error_message", "name": name,
                "notice": "Cannot read this file: " + str(error)}

    if ext in XML_EXTENSIONS:
        if size_bytes > xml.MAX_XML_BYTES:
            return {"kind": "too_big", "name": name, "size_bytes": size_bytes,
                    "notice": "This XML file is larger than %d MB - it is not "
                             "rendered, to keep the program responsive. "
                             "with the button." % (xml.MAX_XML_BYTES // (1024 * 1024))}
        return render._hide_fields(render._fix_stock(xml._describe_xml(path, name)))

    if ext in PDF_EXTENSIONS:
        if size_bytes > pdf.MAX_PDF_BYTES:
            return {"kind": "too_big", "name": name, "size_bytes": size_bytes,
                    "notice": "This PDF is larger than %d MB - it is not "
                             "rendered, to keep the program responsive. "
                             "with the button." % (pdf.MAX_PDF_BYTES // (1024 * 1024))}
        return render._hide_fields(render._fix_stock(pdf._describe_pdf(path, name)))

    if ext in TEXT_EXTENSIONS:
        if size_bytes > MAX_TEXT_BYTES:
            return {"kind": "too_big", "name": name, "size_bytes": size_bytes,
                    "notice": "This text file is larger than %d MB - only its "
                             "name is shown." % (MAX_TEXT_BYTES // (1024 * 1024))}
        return _describe_text(path, name)

    return {"kind": "stored", "name": name, "size_bytes": size_bytes}


# Setup-sheet fields we do NOT show - neither to a person nor to the
# model. The list is explicit and short; every entry needs a reason.
#
# CUSTOMER NAME: the customer is entered in the catalogue itself, and that
# is the value that counts. The field from the PDF sat right beside it,
# looked identical and carried something else - one screen showed


# ----------------------------------------------------------------- text

def _describe_text(path, name):
    try:
        with open(path, "rb") as file:
            raw = file.read()
    except OSError as error:
        return {"kind": "error_message", "name": name,
                "notice": "Could not read the file: " + str(error)}

    content = raw.decode("utf-8", "replace")
    everything = content.splitlines()
    lines = [[number, text] for number, text
             in enumerate(everything[:MAX_LINES], 1)]
    result = {"kind": "text", "name": name, "text_lines": lines,
             "total_lines": len(everything)}
    if len(everything) > MAX_LINES:
        result["notice"] = ("Showing the first %d of %d lines. Open the whole "
                          "file with the button."
                          % (MAX_LINES, len(everything)))
    return result
