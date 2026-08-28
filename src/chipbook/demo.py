"""A catalogue somebody can look at before they have typed anything.

WHY THIS EXISTS. Started for the first time, chipbook shows an empty list
and an invitation to write the first entry. That is right for the person
it was built for - and wrong for everybody who is only having a look. They
open it, see nothing, and never find out that a word typed into the search
box can be answered out of an attached setup sheet.

WHAT IT PUTS ON DISK. Four jobs and three attachments, in a directory of
their own - `~/chipbook-demo` by default, NEVER the real one. A demo that
could land on top of somebody's own catalogue would be worse than no demo.

WHERE THE DATA COMES FROM. It is invented here, in this file, as text and
bytes. Not one byte of it comes from any real workshop: the customers, the
order numbers and the setup sheets are made up, and there is no file to
copy in from anywhere.

RUN IT WITH:  python -m chipbook --demo
"""

import io
import os

from . import ChipbookError, JOBS_DIR
from . import catalog as catalog_module

DEMO_DIR_NAME = "chipbook-demo"


def default_demo_dir():
    """Beside the real catalogue, never inside it."""
    return os.path.join(os.path.expanduser("~"), DEMO_DIR_NAME)


def _setup_sheet(description, machine, shape, size, operations):
    """A Mastercam-shaped setup sheet, built here as bytes.

    The reader is fed exactly the shape a real sheet has - a description,
    a machine, a stock block and numbered operations with a tool each - so
    that what the demo shows on screen is what the parser really does, and
    not a picture of it.
    """
    parts = [b"<?xml version='1.0'?><SETUPSHEET>",
             ("<DESCRIPTION>%s</DESCRIPTION>" % description).encode(),
             ("<MACHINE><NAME>%s</NAME></MACHINE>" % machine).encode(),
             ("<STOCK><SHAPE>%s</SHAPE><SIZE>%s</SIZE>"
              "<STOCKTOLEAVE>0.3</STOCKTOLEAVE></STOCK>"
              % (shape, size)).encode()]
    for name, spindle, feed, tool in operations:
        parts.append(
            ("<OPERATION><NAME>%s</NAME><SPINDLE>%s RPM</SPINDLE>"
             "<FEEDRATE>%s</FEEDRATE><TOOL><TYPE>%s</TYPE></TOOL>"
             "</OPERATION>" % (name, spindle, feed, tool)).encode())
    parts.append(b"</SETUPSHEET>")
    return b"".join(parts)


NC_PROGRAM = (
    "%\n"
    "O4471 (BEARING HOUSING OP10)\n"
    "G21 G17 G40 G80 G90\n"
    "T1 M6 (FLAT ENDMILL 12)\n"
    "G0 G90 G54 X0 Y0 S12000 M3\n"
    "G43 H1 Z25. M8\n"
    "G1 Z-2.5 F900.\n"
    "G1 X120. F2500.\n"
    "T4 M6 (DRILL 8.5)\n"
    "G0 G90 G54 X30. Y22. S3500 M3\n"
    "G43 H4 Z25. M8\n"
    "G99 G83 Z-46. R2. Q6. F400.\n"
    "G80\n"
    "M5 M9\n"
    "G91 G28 Z0.\n"
    "M30\n"
    "%\n"
)


# THE FOUR JOBS. Each one is here to show a DIFFERENT thing, and that is
# why there are four and not one:
#   1. an entry that is nothing but a note - the cheapest thing to write;
#   2. a plain entry whose value is the fixture drawing kept with it;
#   3. an entry with a setup sheet, so the search can find a word that
#      stands in no field of the entry at all;
#   4. an entry with a setup sheet AND an NC program, so the tool list and
#      the line count are computed rather than typed.
JOBS = [
    {
        "name": "Adapter flange",
        "customer": "Halbrook Machine",
        "material": "1.4301",
        "order_number": "SO-4318",
        "notes": ("Stainless, so the feed came down and the coolant went "
                  "through the tool. Finished bore measured 0.02 over on "
                  "the first one - the second setup runs 0.01 smaller on "
                  "the boring head and it comes out on size."),
        "files": [],
    },
    {
        "name": "Cover plate",
        "customer": "Meridian Tooling",
        "material": "S355",
        "order_number": "SO-4402",
        "notes": ("Plain plate, four holes. Kept because the fixture "
                  "drawing is in here and the next one will need it."),
        "files": [],
    },
    {
        "name": "Spindle shaft",
        "customer": "Northgate Engineering",
        "material": "42CrMo4",
        "order_number": "SO-4455",
        "notes": ("Chattered on the long overhang until the tailstock went "
                  "in. Second setup runs at 1400 rpm, not 2200 - written "
                  "down so nobody tries 2200 again."),
        "files": [("spindle-shaft-op20.xml",
                   _setup_sheet("Spindle shaft - OP20", "Okuma LB-3000",
                                "CYLINDER", "60, 340",
                                [("1 - Face", "1400", "180",
                                  "CNMG turning insert"),
                                 ("2 - Rough turn", "1400", "220",
                                  "CNMG turning insert"),
                                 ("3 - Finish turn", "2000", "90",
                                  "DNMG turning insert"),
                                 ("4 - Keyway", "3000", "120",
                                  "Woodruff cutter")]))],
    },
    {
        "name": "Bearing housing",
        "customer": "Meridian Tooling",
        "material": "EN-AW 7075",
        "order_number": "SO-4471",
        "notes": ("Clamped on soft jaws, second op from the finished bore. "
                  "The 8.5 hole went one pass deeper than the drawing - the "
                  "customer asked for it on the phone and it stayed that "
                  "way on the next batch."),
        "files": [("bearing-housing-op10.xml",
                   _setup_sheet("Bearing housing - OP10", "DMU 50",
                                "BLOCK", "120, 90, 45",
                                [("1 - Face", "12000", "2500",
                                  "Flat endmill"),
                                 ("2 - Contour", "10000", "1800",
                                  "Flat endmill"),
                                 ("3 - Chamfer", "14000", "1200",
                                  "Chamfer mill"),
                                 ("4 - Drill 8.5", "3500", "400", "Drill"),
                                 ("5 - Bore", "2600", "160",
                                  "Boring head")])),
                  ("bearing-housing-op10.nc", NC_PROGRAM.encode("ascii"))],
    },
]


def looks_like_a_real_catalogue(data_dir):
    """Whether somebody's own jobs already sit in this directory.

    THE ONE THING THIS MODULE MUST NEVER DO is add invented jobs to a
    catalogue a person is actually using. A directory with a jobs folder
    already in it is treated as theirs, and the demo refuses to touch it.
    """
    return os.path.isdir(os.path.join(data_dir, JOBS_DIR))


def fill(data_dir=None):
    """Puts the demo jobs in place and hands back the directory used.

    RUNNING IT TWICE ADDS NOTHING. The second run finds the jobs already
    there and leaves them alone - somebody who has been clicking around in
    the demo does not lose what they changed.
    """
    data_dir = data_dir or default_demo_dir()
    fresh = not looks_like_a_real_catalogue(data_dir)

    store = catalog_module.open_catalog(data_dir)
    try:
        if store.job_count():
            return data_dir, 0
        if not fresh:
            # A jobs folder with an empty database is the shape a person is
            # left with after a broken database - tools/rebuild.py is for
            # that, and the demo has no business writing into it.
            raise ChipbookError(
                "%s already holds a jobs folder. The demo will not write "
                "into a catalogue that is not its own - give it a "
                "directory of its own instead." % data_dir)
        added = 0
        for job in JOBS:
            fields = dict(job)
            files = fields.pop("files")
            record = store.add_job(**fields)
            for name, content in files:
                store.add_attachment(record["id"], name,
                                     io.BytesIO(content), len(content))
            added += 1
        return data_dir, added
    finally:
        store.close()
