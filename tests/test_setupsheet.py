"""Tests for the setup-sheet reader - PDF.

TWO LAYERS:

  LAYER 1 - stand-ins built HERE, in the test code. They run always and
            everywhere, because they need no file on disk.
  LAYER 2 - a real setup sheet from the end user. The file is NOT in the
            repository, so the test SKIPS ITSELF when it does not find it.
            The same mechanism as in the test for the system Recycle Bin.

The figures in layer 2 are WRITTEN DOWN BY HAND from the XML setup sheet of
the same job. The test does not read the XML and does not depend on the XML
lying anywhere - the answer key is in this file.
"""

import hashlib
import os
import shutil
import tempfile
import unittest
import zlib

from chipbook import setupsheet
from chipbook.setupsheet import pdf
from chipbook.setupsheet import render


# ----------------------------------------------------- building stand-ins

def build_pdf(content_stream, compressed=True, header=b"%PDF-1.4\n"):
    """Assembles the smallest PDF our reader has to be able to read."""
    if compressed:
        body = zlib.compress(content_stream)
        filter_entry = b"/Filter /FlateDecode "
    else:
        body = content_stream
        filter_entry = b""
    parts = [header]
    parts.append(b"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n")
    parts.append(b"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n")
    parts.append(b"3 0 obj <</Type /Page /Parent 2 0 R /Contents 4 0 R "
                  b"/MediaBox [0 0 595 842]>> endobj\n")
    parts.append(b"4 0 obj <<" + filter_entry + b"/Length " +
                  str(len(body)).encode("ascii") + b">> stream\n")
    parts.append(body)
    parts.append(b"\nendstream endobj\n")
    parts.append(b"trailer <</Root 1 0 R /Size 5>>\n%%EOF\n")
    return b"".join(parts)


def run(x, y, text):
    return ("1 0 0 1 %s %s Tm (%s) Tj\n" % (x, y, text)).encode("ascii")


def write_file(directory, name, data):
    path = os.path.join(directory, name)
    with open(path, "wb") as file:
        file.write(data)
    return path


# --------------------------------------------- LAYER 1: always has to work

class PdfReaderOnFixturesTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-pdf-")

    def tearDown(self):
        for name in os.listdir(self.directory):
            os.remove(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def _describe(self, content, name="setup.pdf", **kw):
        path = write_file(self.directory, name, build_pdf(content, **kw))
        return setupsheet.describe(path, name)

    def _block(self, result, title_fragment):
        for block in result["blocks"]:
            if title_fragment in block["title"]:
                return block
        self.fail("no block with a title holding %r; there are: %r"
                  % (title_fragment, [b["title"] for b in result["blocks"]]))

    def _pairs(self, result, title_fragment):
        return dict((k, v) for k, v in self._block(result, title_fragment)["pairs"])

    def test_a_pdf_is_a_main_file(self):
        self.assertTrue(setupsheet.can_display("setup.pdf"))
        self.assertIn(".pdf", setupsheet.SETUP_SHEET_EXTENSIONS)
        self.assertIn(".xml", setupsheet.SETUP_SHEET_EXTENSIONS)
        self.assertNotIn(".mcam", setupsheet.SETUP_SHEET_EXTENSIONS)

    def test_a_label_and_a_value_from_one_row(self):
        content = (b"BT\n" + run(73, 700, "GENERAL INFORMATION")
                 + run(77.5, 680, "PROJECT NAME:")
                 + run(181, 680, "Heart tray") + b"ET\n")
        result = self._describe(content)
        self.assertEqual(result["kind"], "pdf")
        self.assertEqual(result["page_count"], 1)
        self.assertEqual(self._pairs(result, "GENERAL INFORMATION"),
                         {"PROJECT NAME": "Heart tray"})

    def test_the_drawing_order_does_not_confuse_the_fields(self):
        """A TRAP. In the stream 'A' stands BEFORE 'REVISION:', but on the
        page it lies to the RIGHT of it - so it belongs to REVISION, and
        DRAWING stays empty. Without reading the coordinates the result was
        'DRAWING: A REVISION:'."""
        content = (b"BT\n" + run(73, 700, "GENERAL INFORMATION")
                 + run(77.5, 574.95, "DRAWING:")
                 + run(433, 574.95, "A")
                 + run(380.43, 574.95, "REVISION:") + b"ET\n")
        result = self._describe(content)
        pairs = self._pairs(result, "GENERAL INFORMATION")
        self.assertEqual(pairs["REVISION"], "A")
        self.assertNotIn("DRAWING", pairs)
        self.assertIn("DRAWING", self._block(result, "GENERAL INFORMATION")["empty_fields"])

    def test_an_empty_field_leaves_the_screen_but_not_the_data(self):
        """A field with no value is not shown. But the program is still to
        KNOW that it was empty - otherwise the evidence would vanish."""
        content = (b"BT\n" + run(73, 700, "OPERATION INFO")
                 + run(82, 680, "COMMENT:")
                 + run(82, 660, "FEEDRATE:")
                 + run(186, 660, "3000.0 mm/min") + b"ET\n")
        result = self._describe(content)
        pairs = self._pairs(result, "OPERATION INFO")
        self.assertEqual(pairs["FEEDRATE"], "3000.0 mm/min")
        self.assertNotIn("COMMENT", pairs)
        self.assertIn("COMMENT", self._block(result, "OPERATION INFO")["empty_fields"])

    def test_a_heading_with_a_description_and_a_pair_beside_it(self):
        content = (b"BT\n" + run(73, 700, "OPERATION INFO")
                 + run(186, 700, "1 - Contour (2D)")
                 + run(73, 660, "TOOL LIST")
                 + run(479, 660, "Sorted:")
                 + run(515, 660, "NO") + b"ET\n")
        result = self._describe(content)
        titles = [b["title"] for b in result["blocks"]]
        self.assertIn("OPERATION INFO: 1 - Contour (2D)", titles)
        self.assertEqual(self._pairs(result, "TOOL LIST"), {"Sorted": "NO"})

    def test_indentation_gives_the_level(self):
        content = (b"BT\n" + run(73, 700, "OPERATION LIST")
                 + run(78, 660, "OPERATION INFO")
                 + run(82, 640, "FEEDRATE:")
                 + run(186, 640, "1.0") + b"ET\n")
        result = self._describe(content)
        levels = dict((b["title"], b["level"]) for b in result["blocks"])
        self.assertEqual(levels["OPERATION LIST"], 1)
        self.assertEqual(levels["OPERATION INFO"], 2)

    def test_uncompressed_text_is_read_too(self):
        content = (b"BT\n" + run(73, 700, "STOCK")
                 + run(86, 680, "SHAPE:") + run(163, 680, "Box") + b"ET\n")
        result = self._describe(content, compressed=False)
        self.assertEqual(self._pairs(result, "STOCK"), {"SHAPE": "Box"})

    def test_a_file_that_is_not_a_pdf(self):
        path = write_file(self.directory, "pretends.pdf", b"this is not a pdf")
        result = setupsheet.describe(path, "pretend.pdf")
        self.assertEqual(result["kind"], "error")
        self.assertIn("is not a PDF", result["notice"])

    def test_a_pdf_with_no_text_says_so_outright(self):
        result = self._describe(b"0 0 1 rg 10 10 100 100 re f\n")
        self.assertEqual(result["kind"], "error")
        self.assertIn("no text to show", result["notice"])

    def test_a_truncated_stream_does_not_topple_the_program(self):
        whole = build_pdf(b"BT\n" + run(73, 700, "STOCK") + b"ET\n")
        where = whole.find(b"stream\n") + len(b"stream\n")
        truncated = whole[:where + 5] + b"\nendstream endobj\n%%EOF\n"
        path = write_file(self.directory, "truncated.pdf", truncated)
        result = setupsheet.describe(path, "truncated.pdf")
        self.assertIn(result["kind"], ("pdf", "error"))

    def test_a_pdf_that_is_too_large_is_not_parsed(self):
        content = b"BT\n" + run(73, 700, "STOCK") + b"ET\n"
        path = write_file(self.directory, "big.pdf", build_pdf(content))
        previous = pdf.MAX_PDF_BYTES
        pdf.MAX_PDF_BYTES = 10
        try:
            result = setupsheet.describe(path, "big.pdf")
        finally:
            pdf.MAX_PDF_BYTES = previous
        self.assertEqual(result["kind"], "too_big")

    def test_an_enormous_number_of_strings_is_truncated(self):
        # The fields have to carry values, otherwise the empty-field rule would
        # remove the whole block and what would be checked here is an empty
        # preview rather than the truncation.
        middle = b"".join(run(80, 700 - i * 0.001, "FIELD%d:" % i)
                          + run(200, 700 - i * 0.001, str(i))
                          for i in range(50))
        previous = pdf.MAX_TEXT_RUNS
        pdf.MAX_TEXT_RUNS = 10
        try:
            result = self._describe(b"BT\n" + middle + b"ET\n", name="many.pdf")
        finally:
            pdf.MAX_TEXT_RUNS = previous
        self.assertEqual(result["kind"], "pdf")
        self.assertIn("showing the first", result["notice"])


# ---------- tables out of repeating sections ----------
#
# The layout of the stand-ins below mirrors what was measured on a real
# setup sheet, but NOT ONE figure in this section comes from the end
# user's file - those are checked by layer 2. Here we check the rule
# itself, including cases that this one file happens not to hold.


def header(x, y, name, value=""):
    """A row starting with text and NO colon = a section heading."""
    out = run(x, y, name)
    if value:
        out += run(x + 120, y, value)
    return out


def field(x, y, label, value=""):
    out = run(x, y, label + ":")
    if value:
        out += run(x + 100, y, value)
    return out


class PdfRepeatedSectionTableTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-tab-")

    def tearDown(self):
        for name in os.listdir(self.directory):
            os.remove(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def _describe(self, content):
        path = write_file(self.directory, "setup.pdf", build_pdf(content))
        return setupsheet.describe(path, "setup.pdf")

    def _tables(self, result, title=None):
        return [b for b in result["blocks"] if b["kind"] == "table"
                and (title is None or b["title"] == title)]

    def _pairs(self, result, fragment):
        for b in result["blocks"]:
            if b["kind"] == "pairs" and fragment in b["title"]:
                return dict((k, v) for k, v in b["pairs"])
        self.fail("no 'pairs' block titled %r; there are: %r"
                  % (fragment, [(b["kind"], b["title"])
                                for b in result["blocks"]]))

    def test_repeated_sections_give_a_table(self):
        """Three sections with the same name = one table, and the file gives
        the columns. The operation name sits EXCLUSIVELY in the section
        title, so it has to come in as the first column - otherwise there is
        no telling whose row this is."""
        content = (b"BT\n"
                 + header(73, 700, "OPERATION LIST")
                 + header(78, 680, "OPERATION INFO", "1 - Contour (2D)")
                 + field(82, 660, "FEEDRATE", "3000.0")
                 + header(78, 640, "OPERATION INFO", "2 - Dynamic")
                 + field(82, 620, "FEEDRATE", "3500.0")
                 + header(78, 600, "OPERATION INFO", "3 - Scallop")
                 + field(82, 580, "FEEDRATE", "4000.0")
                 + b"ET\n")
        result = self._describe(content)
        tables = self._tables(result, "OPERATION INFO")
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["columns"], ["OPERATION INFO", "FEEDRATE"])
        self.assertEqual(len(tables[0]["rows"]), 3)
        self.assertEqual(tables[0]["rows"][0], ["1 - Contour (2D)", "3000.0"])
        self.assertEqual(tables[0]["rows"][2], ["3 - Scallop", "4000.0"])

    def test_a_column_empty_in_every_row_leaves_the_table(self):
        """A column empty in every row is not shown, but its name stays in
        the data - otherwise the evidence would vanish."""
        content = (b"BT\n"
                 + header(73, 700, "OPERATION LIST")
                 + header(78, 680, "OPERATION INFO", "1")
                 + field(82, 660, "FEEDRATE", "3000.0")
                 + field(82, 640, "COMMENT")
                 + header(78, 620, "OPERATION INFO", "2")
                 + field(82, 600, "FEEDRATE", "3500.0")
                 + field(82, 580, "COMMENT")
                 + b"ET\n")
        result = self._describe(content)
        table = self._tables(result, "OPERATION INFO")[0]
        self.assertNotIn("COMMENT", table["columns"])
        self.assertIn("FEEDRATE", table["columns"])
        self.assertIn("COMMENT", table["empty_fields"])

    def test_a_column_empty_in_ONLY_SOME_rows_stays(self):
        """The other side of it: DEPTH has a value only on the contour, and
        that is information rather than a hole. Such a column STAYS."""
        content = (b"BT\n"
                 + header(73, 700, "OPERATION LIST")
                 + header(78, 680, "OPERATION INFO", "1")
                 + field(82, 660, "DEPTH")
                 + header(78, 640, "OPERATION INFO", "2")
                 + field(82, 620, "DEPTH", "-23.0")
                 + b"ET\n")
        table = self._tables(self._describe(content), "OPERATION INFO")[0]
        self.assertIn("DEPTH", table["columns"])
        column = table["columns"].index("DEPTH")
        self.assertEqual([entry[column] for entry in table["rows"]], ["", "-23.0"])

    def test_the_same_names_under_different_headings_are_two_tables(self):
        """Without this, TOOL INFO at an operation and TOOL INFO from the tool
        listing would fall into one table - two different things going by
        the same name."""
        content = (b"BT\n"
                 + header(73, 700, "OPERATION LIST")
                 + header(78, 680, "TOOL INFO", "fi10")
                 + field(82, 660, "DIAMETER", "10.0")
                 + header(78, 640, "TOOL INFO", "fi5")
                 + field(82, 620, "DIAMETER", "5.0")
                 + header(73, 600, "TOOL LIST")
                 + header(78, 580, "TOOL INFO", "fi10")
                 + field(82, 560, "DIAMETER", "10.0")
                 + header(78, 540, "TOOL INFO", "fi5")
                 + field(82, 520, "DIAMETER", "5.0")
                 + b"ET\n")
        tables = self._tables(self._describe(content), "TOOL INFO")
        self.assertEqual(len(tables), 2)
        self.assertEqual([len(t["rows"]) for t in tables], [2, 2])

    def test_a_repeated_label_in_a_section_is_not_lost(self):
        """Measured on a real file: USED BY OPERATION stands twice in one
        section. Assembling the row with a dictionary would eat the first
        value and nobody would find out."""
        content = (b"BT\n"
                 + header(73, 700, "TOOL LIST")
                 + header(78, 680, "TOOL INFO", "fi10")
                 + field(82, 660, "USED BY OPERATION", "# 1")
                 + field(82, 640, "USED BY OPERATION", "# 2")
                 + header(78, 620, "TOOL INFO", "fi5")
                 + field(82, 600, "USED BY OPERATION", "# 3")
                 + b"ET\n")
        table = self._tables(self._describe(content), "TOOL INFO")[0]
        column = table["columns"].index("USED BY OPERATION")
        self.assertEqual(table["rows"][0][column], "# 1; # 2")
        self.assertEqual(table["rows"][1][column], "# 3")

    def test_a_disk_path_goes_but_the_field_under_it_stays(self):
        """The measured part: under a heading that was a disk path sat the
        TOTAL JOB TIME. The heading goes, the figure stays."""
        content = (b"BT\n"
                 + header(73, 700, "GENERAL INFORMATION")
                 + field(77, 680, "PROJECT NAME", "Heart tray")
                 + header(73, 660, "C:\\\\USERS\\\\OPERATOR\\\\DOCUMENTS")
                 + field(77, 640, "CYCLE TIME", "15:37")
                 + b"ET\n")
        result = self._describe(content)
        titles = [b["title"] for b in result["blocks"]]
        self.assertEqual([t for t in titles if t.startswith("C:")], [], titles)
        pairs = self._pairs(result, "GENERAL INFORMATION")
        self.assertEqual(pairs["PROJECT NAME"], "Heart tray")
        self.assertEqual(pairs["CYCLE TIME"], "15:37")

    def test_an_empty_section_goes_along_with_its_title(self):
        """A section in which not one filled field is left is not shown at
        all."""
        content = (b"BT\n"
                 + header(73, 700, "GENERAL INFORMATION")
                 + field(77, 680, "PROJECT NAME", "Heart tray")
                 + header(73, 660, "COMMENTS")
                 + b"ET\n")
        self.assertNotIn("COMMENTS",
                         [b["title"] for b in self._describe(content)["blocks"]])

    def test_the_report_header_stays_because_the_machine_sits_there(self):
        """Lines standing BEFORE the first labelled field stay, because the
        machine name sits there - the user picks it from a profile in the
        CAM system and will never type it by hand. The report title alone is
        hidden by name, and that is the only place in the module carrying
        text from one particular report - deliberately on the HIDING side,
        not the recognising side."""
        content = (b"BT\n"
                 + header(73, 720, "Setup Sheet Report")
                 + header(73, 700, "3 - AXIS VMC")
                 + header(73, 660, "GENERAL INFORMATION")
                 + field(77, 640, "PROJECT NAME", "Heart tray")
                 + header(73, 600, "COMMENTS")
                 + b"ET\n")
        titles = [b["title"] for b in self._describe(content)["blocks"]]
        self.assertIn("3 - AXIS VMC", titles)
        self.assertNotIn("Setup Sheet Report", titles)
        self.assertNotIn("COMMENTS", titles)

    def test_a_single_section_makes_no_table(self):
        content = (b"BT\n"
                 + header(73, 700, "WORK OFFSETS")
                 + header(78, 680, "OFFSET INFO")
                 + field(82, 660, "PLANE", "Top")
                 + b"ET\n")
        result = self._describe(content)
        self.assertEqual(self._tables(result), [])
        self.assertEqual(self._pairs(result, "OFFSET INFO"), {"PLANE": "Top"})


class XmlRepeatedSectionTableTest(unittest.TestCase):
    """The same rule from the XML side. The older rule took the columns
    from ATTRIBUTE names and was dead for a real file from the CAM system -
    measured: 310 elements, ZERO attributes, ZERO tables."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-xml-")

    def tearDown(self):
        for name in os.listdir(self.directory):
            os.remove(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def _describe(self, data):
        return setupsheet.describe(write_file(self.directory, "setup.xml", data),
                             "setup.xml")

    def _tables(self, result):
        return [b for b in result["blocks"] if b["kind"] == "table"]

    def test_repeated_tags_with_no_attributes_give_a_table(self):
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<OPERATION><NAME>op1</NAME><SPINDLE>24000</SPINDLE></OPERATION>"
                b"<OPERATION><NAME>op2</NAME><SPINDLE>24000</SPINDLE></OPERATION>"
                b"<OPERATION><NAME>op3</NAME><SPINDLE>18000</SPINDLE></OPERATION>"
                b"</SETUPSHEET>")
        tables = self._tables(self._describe(data))
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["title"], "OPERATION")
        self.assertEqual(tables[0]["columns"], ["NAME", "SPINDLE"])
        self.assertEqual(tables[0]["rows"][2], ["op3", "18000"])

    def test_the_table_does_not_lose_what_sits_deeper(self):
        """An operation has a tool under it. The table takes the plain fields,
        and the tool goes on separately - otherwise the row alone would be
        left."""
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<OPERATION><NAME>op1</NAME>"
                b"<TOOL><NAME>fi10</NAME></TOOL></OPERATION>"
                b"<OPERATION><NAME>op2</NAME>"
                b"<TOOL><NAME>fi5</NAME></TOOL></OPERATION>"
                b"</SETUPSHEET>")
        result = self._describe(data)
        titles = [b["title"] for b in result["blocks"]]
        self.assertIn("OPERATION 1 > TOOL", titles)
        self.assertIn("OPERATION 2 > TOOL", titles)
        self.assertEqual(len(self._tables(result)), 1)

    def test_utf16_with_a_bom_still_works(self):
        """A real file from the end user is UTF-16 with a BOM. Until now that
        had no test of its own."""
        text = ("<?xml version='1.0' encoding='UTF-16'?><SETUPSHEET>"
                 "<OPERATION><NAME>op1</NAME></OPERATION>"
                 "<OPERATION><NAME>op2</NAME></OPERATION></SETUPSHEET>")
        tables = self._tables(self._describe(text.encode("utf-16")))
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["rows"], [["op1"], ["op2"]])


class AsTextForModelTest(unittest.TestCase):
    """The same blocks, but assembled for THE MODEL, not for the search.

    What these tests watch is one thing: a figure has to stay with ITS OWN
    row. Flat text for the index does not do that, and that is why this
    function came into being at all.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-model-")

    def tearDown(self):
        for name in os.listdir(self.directory):
            os.remove(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def _text(self, data, name="setup.xml"):
        path = write_file(self.directory, name, data)
        return setupsheet.as_text(setupsheet.describe(path, name))

    def test_every_table_row_has_its_own_numbered_heading(self):
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<OPERATION><NAME>op1</NAME><FEED>3000</FEED></OPERATION>"
                b"<OPERATION><NAME>op2</NAME><FEED>3500</FEED></OPERATION>"
                b"</SETUPSHEET>")
        text = self._text(data)
        self.assertIn("[OPERATION 1 of 2]", text)
        self.assertIn("[OPERATION 2 of 2]", text)

    def test_a_figure_stays_with_its_own_row(self):
        """This is the heart of it. Were the text flat as it is for the index,
        3000 and 3500 would stand side by side with no telling whose they
        are."""
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<OPERATION><NAME>op1</NAME><FEED>3000</FEED></OPERATION>"
                b"<OPERATION><NAME>op2</NAME><FEED>3500</FEED></OPERATION>"
                b"</SETUPSHEET>")
        lines = self._text(data).splitlines()
        second = lines.index("[OPERATION 2 of 2]")
        tail = "\n".join(lines[second:])
        self.assertIn("FEED: 3500", tail)
        self.assertNotIn("3000", tail)

    def test_an_empty_field_does_not_enter_the_text(self):
        """The empty-field rule holds just as it does on screen - we do not
        give the model fields a person cannot see."""
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<OPERATION><NAME>op1</NAME><COMMENT></COMMENT></OPERATION>"
                b"<OPERATION><NAME>op2</NAME><COMMENT></COMMENT></OPERATION>"
                b"</SETUPSHEET>")
        self.assertNotIn("COMMENT", self._text(data))

    def test_a_pairs_section_has_labels_beside_the_values(self):
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<GENERAL><PROJECT>Shaft 11</PROJECT></GENERAL>"
                b"</SETUPSHEET>")
        self.assertIn("PROJECT: Shaft 11", self._text(data))

    def test_a_description_with_no_blocks_is_empty_text_not_a_failure(self):
        self.assertEqual(setupsheet.as_text({}), "")
        self.assertEqual(setupsheet.as_text({"kind": "error"}), "")


class RepeatedToolSectionTest(unittest.TestCase):
    """The same tool section stands twice in a setup sheet.

    MEASURED on a real file: the needless copy is 2211 characters, that is
    30% of the text and about 1068 tokens. Against a window of 4096, of
    which this file eats 3584, that is the difference between 87% and 61%.
    """

    def _block(self, title, columns, rows):
        return {"kind": "table", "title": title,
                "columns": list(columns),
                "rows": [list(entry) for entry in rows]}

    def _description_with_two(self):
        poorer = self._block("TOOL INFO", ["NUMBER", "TYPE"],
                           [["2", "Drill"], ["25", "Chamfer mill"]])
        richer = self._block("TOOL INFO",
                            ["NUMBER", "TYPE", "USED BY OPERATION"],
                            [["2", "Drill", "# 1 Peck Drill"],
                             ["25", "Chamfer mill", "# 4 Contour"]])
        return {"blocks": [poorer, richer]}

    def test_the_copy_with_the_operation_mapping_stays(self):
        """THE HEART OF IT: we do not delete "the second one", we delete THE
        POORER one. The copy with USED BY OPERATION carries a tool-to-
        operation map that the other one does not have."""
        new_ = setupsheet.without_repeated_tools(self._description_with_two())
        self.assertEqual(len(new_["blocks"]), 1)
        self.assertIn("USED BY OPERATION", new_["blocks"][0]["columns"])

    def test_no_tool_is_lost(self):
        new_ = setupsheet.without_repeated_tools(self._description_with_two())
        text = setupsheet.as_text(new_)
        self.assertIn("25", text)
        self.assertIn("Chamfer mill", text)

    def test_the_order_in_the_file_does_not_decide(self):
        """Were the CAM system ever to print the richer copy first, the same
        one has to stay - the richer one, not "the second one"."""
        description = self._description_with_two()
        description["blocks"].reverse()
        new_ = setupsheet.without_repeated_tools(description)
        self.assertEqual(len(new_["blocks"]), 1)
        self.assertIn("USED BY OPERATION", new_["blocks"][0]["columns"])

    def test_different_tool_sections_both_stay(self):
        """We delete A REPETITION, not everything that looks similar. Two
        sections with DIFFERENT tools are two different pieces of
        information."""
        description = {"blocks": [
            self._block("TOOL INFO", ["NUMBER", "TYPE"], [["2", "Drill"]]),
            self._block("TOOL INFO", ["NUMBER", "TYPE"],
                       [["25", "Chamfer mill"]]),
        ]}
        self.assertEqual(len(setupsheet.without_repeated_tools(description)["blocks"]),
                         2)

    def test_a_single_section_stays_untouched(self):
        description = {"blocks": [self._block("TOOL INFO", ["NUMBER"], [["2"]])]}
        self.assertEqual(setupsheet.without_repeated_tools(description), description)

    def test_the_original_is_not_touched(self):
        """The preview for a person gets the description whole - the slimming
        is for the model alone and has no right to change what is on
        screen."""
        description = self._description_with_two()
        setupsheet.without_repeated_tools(description)
        self.assertEqual(len(description["blocks"]), 2)


class StockDimensionsTest(unittest.TestCase):
    """A cylinder has two sizes, and a whole number carries no decimal zero.

    WHERE THIS CAME FROM: a request made after a model repeated back what
    we had supplied it with ourselves: "a cylinder measuring 330.0, 15.0,
    0.0". The third value is rubbish, because a cylinder has no third axis.
    """

    def test_a_cylinder_loses_its_zero_third_value(self):
        self.assertEqual(
            render._stock_dimensions("330.0, 15.0, 0.0", "Cylinder"),
            "330, 15")

    def test_a_box_keeps_three_dimensions(self):
        """A box really does have a third dimension and it must not be touched."""
        self.assertEqual(
            render._stock_dimensions("400.0, 250.0, 40.0", "Box"),
            "400, 250, 40")

    def test_a_cylinder_with_a_NON_ZERO_third_value_stays_whole(self):
        """A GATE MORE IMPORTANT THAN THE RULE ITSELF. "A cylinder has two
        sizes" is true in every real file, but were it ever not to be, this
        rule would delete a real figure out of somebody's own file. We
        delete a zero and nothing else."""
        self.assertEqual(
            render._stock_dimensions("90.0, 90.0, 140.0", "Cylinder"),
            "90, 90, 140")

    def test_a_whole_number_without_the_decimal_zero(self):
        self.assertEqual(render._number_without_trailing_zero("330.0"), "330")
        self.assertEqual(render._number_without_trailing_zero("0.0"), "0")

    def test_a_fractional_number_stays_to_the_digit(self):
        """We round nothing - we change the writing, not the value."""
        self.assertEqual(render._number_without_trailing_zero("17.5"), "17.5")
        self.assertEqual(
            render._stock_dimensions("200.0, 170.5, 25.0", "Box"),
            "200, 170.5, 25")

    def test_a_value_that_is_not_a_number_comes_back_unchanged(self):
        self.assertEqual(
            render._stock_dimensions("no data", "Cylinder"),
            "no data")
        self.assertEqual(render._number_without_trailing_zero("Top"), "Top")

    def test_the_size_rule_reaches_an_XML_setup_sheet_TOO(self):
        """THE RULE IS APPLIED TO THE FINISHED STRUCTURE, so that it behaves
        the same for PDF and for XML - that is what _fix_stock says about
        itself. It did not.

        CAUGHT ON A RUNNING PROGRAM, not by this suite: opening a job whose
        XML setup sheet carries a STOCK section ended with
        "'tuple' object does not support item assignment" and the preview
        showed an error instead of the sheet. The tests never saw it,
        because every one of them handed _fix_stock a structure built BY
        HAND out of lists, while the XML reader builds its pairs out of
        tuples. The PDF reader builds lists, so that half worked.
        """
        xml = (b"<?xml version='1.0'?><SETUPSHEET>"
               b"<STOCK><SHAPE>Cylinder</SHAPE>"
               b"<SIZE>330.0, 15.0, 0.0</SIZE></STOCK>"
               b"</SETUPSHEET>")
        folder = tempfile.mkdtemp(prefix="chipbook_stock_")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = os.path.join(folder, "stock.xml")
        with open(path, "wb") as file:
            file.write(xml)
        description = setupsheet.describe(path, "stock.xml")
        text = render.as_text(description)
        self.assertIn("330, 15", text)      # zero dropped, tail trimmed
        self.assertNotIn("330.0, 15.0, 0.0", text)

    def test_an_unknown_shape_deletes_nothing(self):
        """When SHAPE says something we do not know, we leave every value in
        place - we only tidy how the figures are written."""
        self.assertEqual(
            render._stock_dimensions("10.0, 20.0, 0.0", "Sphere"),
            "10, 20, 0")

    def test_the_rule_works_on_a_finished_structure(self):
        description = {"blocks": [{
            "kind": "pairs", "title": "STOCK",
            "pairs": [["STOCK", "YES"], ["SHAPE", "Cylinder"],
                     ["SIZE", "330.0, 15.0, 0.0"]],
        }]}
        render._fix_stock(description)
        self.assertEqual(description["blocks"][0]["pairs"][2][1], "330, 15")

    def test_the_rule_works_in_a_table_too(self):
        description = {"blocks": [{
            "kind": "table", "title": "STOCK",
            "columns": ["SHAPE", "SIZE"],
            "rows": [["Cylinder", "330.0, 15.0, 0.0"],
                        ["Box", "400.0, 250.0, 40.0"]],
        }]}
        render._fix_stock(description)
        self.assertEqual(description["blocks"][0]["rows"][0][1], "330, 15")
        self.assertEqual(description["blocks"][0]["rows"][1][1], "400, 250, 40")

    def test_a_size_with_no_shape_only_tidies_the_writing(self):
        """When the block holds no SHAPE, we do not guess the shape."""
        description = {"blocks": [{
            "kind": "pairs", "title": "STOCK",
            "pairs": [["SIZE", "330.0, 15.0, 0.0"]],
        }]}
        render._fix_stock(description)
        self.assertEqual(description["blocks"][0]["pairs"][0][1], "330, 15, 0")


class LabelDependsOnSectionTest(unittest.TestCase):
    """`NUMBER` means something different in different blocks.

        In a TOOL INFO block it is the tool number in the magazine, in
        OFFSET INFO the offset number. One description for both would be false
        in one of those places, and it was exactly the vagueness around
        `NUMBER` that a model fell over.
        """

    LEXICON = {
        "NUMBER": "number_of",
        "TOOL INFO.NUMBER": "tool number in the magazine",
        "OFFSET INFO.NUMBER": "offset number",
    }

    def test_a_key_with_a_section_wins_over_the_general_one(self):
        self.assertEqual(
            render._with_translation("NUMBER", self.LEXICON, "TOOL INFO"),
            "NUMBER (tool number in the magazine)")
        self.assertEqual(
            render._with_translation("NUMBER", self.LEXICON, "OFFSET INFO"),
            "NUMBER (offset number)")

    def test_with_no_section_it_falls_back_to_the_general_one(self):
        self.assertEqual(render._with_translation("NUMBER", self.LEXICON),
                         "NUMBER (number_of)")

    def test_a_section_with_no_key_of_its_own_falls_back_to_the_general_one(self):
        self.assertEqual(
            render._with_translation("NUMBER", self.LEXICON, "PLANE INFO"),
            "NUMBER (number_of)")

    def test_as_text_passes_the_section_on(self):
        description = {"blocks": [{
            "kind": "table", "title": "OFFSET INFO",
            "columns": ["NUMBER"], "rows": [["0"]],
        }]}
        self.assertIn("NUMBER (offset number): 0",
                      setupsheet.as_text(description, self.LEXICON))


# ------------------- LAYER 2: a real file, skipped when it is not there

def sample_path():
    """A REAL setup sheet, if this machine happens to have one.

    The file itself never enters the repository and never will: it is
    somebody's actual job, with a customer name, disk paths and a CAM
    licence number inside it. So this layer looks for a copy on the
    machine and skips itself, out loud, when there is none.

    Point CHIPBOOK_SAMPLE at a PDF to run it.
    """
    named = os.environ.get("CHIPBOOK_SAMPLE")
    if named:
        return named
    return os.path.join(os.path.expanduser("~"), "chipbook-data",
                        "samples", "setup-sheet.pdf")


SAMPLE = sample_path()
SAMPLE_SHA = "9d4223fcc632f277"

# The answer key WRITTEN DOWN FROM THE XML of the same job. The test
# does not read the XML - these values are to hold even when the XML has
# long been off the disk.
OPERATIONS_KEY = [
    ("1 - 3D High Speed (Dynamic OptiRough)", {
        "SPINDLE SPEED": "24000 RPM",
        "FEEDRATE": "3000.0 mm/min",
        "STOCK TO LEAVE": "1.0",
        "CLEARANCE PLANE": "5.0",
        "RETRACT PLANE": "2.0",
        "FEED PLANE": "0.5",
        "CYCLE TIME": "0 HOURS, 2 MINUTES, 10 SECONDS",
        "COMMENT": "",
    }),
    ("2 - 3D High Speed (Equal Scallop)", {
        "SPINDLE SPEED": "24000 RPM",
        "FEEDRATE": "3000.0 mm/min",
        "STOCK TO LEAVE": "0.0",
        "CLEARANCE PLANE": "5.0",
        "RETRACT PLANE": "2.0",
        "FEED PLANE": "0.5",
        "CYCLE TIME": "0 HOURS, 12 MINUTES, 29 SECONDS",
        "COMMENT": "",
    }),
    ("3 - Contour (2D)", {
        "SPINDLE SPEED": "24000 RPM",
        "FEEDRATE": "3500.0 mm/min",
        "STOCK TO LEAVE": "0.0",
        "DEPTH": "-23.0",
        "CLEARANCE PLANE": "50.0",
        "RETRACT PLANE": "5.0",
        "FEED PLANE": "5.0",
        "CYCLE TIME": "0 HOURS, 0 MINUTES, 57 SECONDS",
        "COMMENT": "",
    }),
]


@unittest.skipUnless(os.path.exists(SAMPLE),
                     "no real setup sheet at %s - test skipped "
                     "(this file must not lie in the repository)" % SAMPLE)
class PdfReaderOnRealFileTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.result = setupsheet.describe(SAMPLE, "heart tray.pdf")

    def _pairs(self, title_fragment):
        for block in self.result["blocks"]:
            if block["kind"] == "pairs" and title_fragment in block["title"]:
                return dict((k, v) for k, v in block["pairs"])
        self.fail("no 'pairs' block with a title holding %r; there are: %r"
                  % (title_fragment, [(b["kind"], b["title"])
                                       for b in self.result["blocks"]]))

    def _tables(self, title):
        return [b for b in self.result["blocks"]
                if b["kind"] == "table" and b["title"] == title]

    def _rows(self, title, which=0):
        tables = self._tables(title)
        self.assertTrue(len(tables) > which,
                        "no table %r no. %d; there are: %r"
                        % (title, which, [(b["kind"], b["title"])
                                          for b in self.result["blocks"]]))
        table = tables[which]
        return [dict(zip(table["columns"], entry)) for entry in table["rows"]]

    def test_this_is_the_same_file_that_was_measured(self):
        with open(SAMPLE, "rb") as file:
            data = file.read()
        self.assertEqual(len(data), 2515888)
        self.assertEqual(hashlib.sha256(data).hexdigest()[:16], SAMPLE_SHA)

    def test_it_is_read_whole(self):
        self.assertEqual(self.result["kind"], "pdf")
        self.assertEqual(self.result["page_count"], 6)
        self.assertNotIn("notice", self.result)

    def test_the_stock(self):
        """THE SIZE WAS CORRECTED on request: a whole number with no decimal
        zero. The file still holds "200.0, 170.0, 25.0" - what changes is
        THE WRITING we show, not the value.

        THIS TEST FAILED ON THE OWNER'S MACHINE AND IT IS GOOD that it did.
        Here it is skipped, because the sample is not in the repository -
        that is, the only place where this change is seen on a REAL file is
        on his machine. That is why a full test run there is part of the
        work and not a formality.

        A box keeps THREE sizes - cutting down to two concerns the cylinder
        alone, which has no third."""
        pairs = self._pairs("STOCK")
        self.assertEqual(pairs["SHAPE"], "Box")
        self.assertEqual(pairs["SIZE"], "200, 170, 25")

    def test_the_operations_match_the_key_from_the_xml(self):
        """The same key out of the XML as before, only the operations now
        stand side by side in a table rather than one under another."""
        rows = self._rows("OPERATION INFO")
        self.assertEqual(len(rows), len(OPERATIONS_KEY))
        for row, (name, expected) in zip(rows, OPERATIONS_KEY):
            self.assertEqual(row["OPERATION INFO"], name)
            for field, value in expected.items():
                if value == "":
                    # A field empty in EVERY row comes out from under the table.
                    # That it is visible beneath it is checked by a separate test.
                    self.assertNotIn(field, row)
                    continue
                self.assertEqual(row.get(field), value,
                                 "operation %r, field %r" % (name, field))

    def test_the_total_job_time_was_not_lost(self):
        """A heading that is a disk path goes, but the field from under it
        stays. The whole job time was once hidden under exactly such a
        path."""
        self.assertEqual(self._pairs("GENERAL INFORMATION")["CYCLE TIME"],
                         "0 HOURS, 15 MINUTES, 37 SECONDS")
        paths = [b["title"] for b in self.result["blocks"]
                   if pdf._looks_like_path(b["title"])]
        self.assertEqual(paths, [])

    def test_tools_at_operations_and_the_listing_are_two_tables(self):
        """On a real file: TOOL INFO appears five times, but in two roles -
        three times at operations and twice in the listing."""
        tables = self._tables("TOOL INFO")
        self.assertEqual([len(t["rows"]) for t in tables], [3, 2])
        self.assertNotIn("USED BY OPERATION", tables[0]["columns"])
        self.assertIn("USED BY OPERATION", tables[1]["columns"])

    def test_a_tool_used_twice_does_not_lose_a_reference(self):
        """The dia 10 end mill works at operations 1 and 2, so USED BY
        OPERATION stands twice in its section. Both values have to
        survive."""
        row = self._rows("TOOL INFO", 1)[0]
        self.assertIn(";", row["USED BY OPERATION"])

    def test_the_drawing_order_did_not_confuse_the_fields(self):
        """The same trap on a real file, not on a stand-in. DRAWING is empty,
        so it is not shown - but the program still knows that it was empty,
        and that is the same trap: were it reading in text order, DRAWING
        would get the 'A' that belongs to REVISION."""
        block = None
        for b in self.result["blocks"]:
            if b["kind"] == "pairs" and "GENERAL INFORMATION" in b["title"]:
                block = b
        self.assertIsNotNone(block)
        pairs = dict((k, v) for k, v in block["pairs"])
        self.assertEqual(pairs["REVISION"], "A")
        self.assertNotIn("DRAWING", pairs)
        self.assertIn("DRAWING", block["empty_fields"])

    def test_the_machine_name_was_not_lost(self):
        """On a real file. The user picks the machine from a profile in the
        CAM system, so they will not type it by hand - this line MUST
        survive the tidying."""
        titles = [b["title"] for b in self.result["blocks"]]
        self.assertIn("3 - AXIS VMC", titles)
        self.assertNotIn("Setup Sheet Report", titles)

    def test_the_part_material_is_still_not_there(self):
        """A setup sheet does not carry the part material. The only MATERIAL
        in the file concerns the cutter. Were that ever to change, this test
        is to say so."""
        materials = set()
        for block in self.result["blocks"]:
            if block["kind"] == "table":
                if "MATERIAL" in block["columns"]:
                    where = block["columns"].index("MATERIAL")
                    materials.update(entry[where] for entry in block["rows"])
                continue
            for label, value in block["pairs"]:
                if label == "MATERIAL":
                    materials.add(value)
        self.assertEqual(materials, {"Carbide"})

    def test_the_users_comments_are_empty(self):
        """On a real file. COMMENT is empty in ALL THREE operations, so the
        empty-column rule takes it out from under the table - but it is to
        STAY VISIBLE, because it is the evidence that the end user does not
        fill comments in.
        Should this test ever fail, that is GOOD news: they have started
        filling them in."""
        table = self._tables("OPERATION INFO")[0]
        self.assertNotIn("COMMENT", table["columns"])
        self.assertIn("COMMENT", table["empty_fields"])

    # ------------------------------------------------ the text for the model

    def _text_sections(self):
        """The text for the model -> a dict {heading: the content under it}."""
        sections = {}
        current = None
        for line in setupsheet.as_text(self.result).splitlines():
            if line.startswith("["):
                current = line.strip()
                sections[current] = []
            elif current and line.strip():
                sections[current].append(line.strip())
        return dict((k, "\n".join(v)) for k, v in sections.items())

    def test_the_text_for_the_model_is_a_size_that_fits_a_model(self):
        """Measured on this file: 2983 characters, that is about 900-1000
        tokens. We watch the order of magnitude and not the character - the
        point is that several jobs at once fit into an 8K context."""
        text = setupsheet.as_text(self.result)
        self.assertTrue(2000 < len(text) < 4000,
                        "the text for the model is %d characters" % len(text))

    def test_the_feed_stays_with_its_own_operation(self):
        """THE HEART OF IT on a real file. Three operations carry 3000, 3000
        and 3500 - and a model's answer is worth exactly as much as the
        figure being attached to the right row."""
        sections = self._text_sections()
        self.assertIn("FEEDRATE: 3000.0 mm/min", sections["[OPERATION INFO 1 of 3]"])
        self.assertIn("FEEDRATE: 3000.0 mm/min", sections["[OPERATION INFO 2 of 3]"])
        self.assertIn("FEEDRATE: 3500.0 mm/min", sections["[OPERATION INFO 3 of 3]"])
        self.assertNotIn("3500", sections["[OPERATION INFO 1 of 3]"])

    def test_the_operation_name_is_in_the_text_for_the_model(self):
        """Without it the model has no way of saying WHICH operation an answer
        concerns - and in a PDF the name sits in the section title alone."""
        sections = self._text_sections()
        self.assertIn("3 - Contour (2D)", sections["[OPERATION INFO 3 of 3]"])

    def test_the_model_does_not_get_an_empty_drawing(self):
        """The same trap from the model's side: DRAWING is empty, so it is not
        in the text. Were it to land there, the model would have a label
        with no value - and that is exactly when it would most gladly write
        in the 'A' from the neighbouring field."""
        text = setupsheet.as_text(self.result)
        self.assertNotIn("DRAWING", text)
        self.assertIn("REVISION: A", text)

    def test_material_reaches_the_model_only_beside_a_tool(self):
        """This is the trap a small model lied about twice. The program cannot
        remove it - it can only see to it that Carbide never stands outside
        the tool section."""
        for header, content in self._text_sections().items():
            if "MATERIAL: Carbide" in content:
                self.assertIn("TOOL INFO", header)


if __name__ == "__main__":
    unittest.main()
