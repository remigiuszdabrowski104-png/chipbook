"""Tests for the demo catalogue (`python -m chipbook --demo`).

WHAT IS BEING PROTECTED HERE. The demo writes invented jobs to disk, and
that is the only place in the program that writes something a person did
not type. Two promises therefore matter more than the rest:

  - it NEVER writes into a catalogue that is not its own;
  - running it twice adds nothing.

The rest of the tests watch that the demo shows what it is there to show:
a job whose word can only be found inside an attached setup sheet.
"""

import os
import shutil
import tempfile
import unittest

import chipbook
from chipbook import catalog
from chipbook import demo


class DemoCatalogueTest(unittest.TestCase):

    def setUp(self):
        self.directory = os.path.join(tempfile.mkdtemp(prefix="chipbook_demo_"),
                                      "demo")

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.directory), ignore_errors=True)

    def test_it_writes_the_jobs_and_says_how_many(self):
        used, added = demo.fill(self.directory)
        self.assertEqual(used, self.directory)
        self.assertEqual(added, len(demo.JOBS))
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        self.assertEqual(store.job_count(), len(demo.JOBS))

    def test_RUNNING_IT_TWICE_ADDS_NOTHING(self):
        """Somebody will run it again to be sure. It is to do no harm, and
        above all not to double every job."""
        demo.fill(self.directory)
        used, added = demo.fill(self.directory)
        self.assertEqual(added, 0)
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        self.assertEqual(store.job_count(), len(demo.JOBS))

    def test_IT_REFUSES_A_CATALOGUE_THAT_IS_NOT_ITS_OWN(self):
        """THE MOST IMPORTANT TEST HERE. A jobs folder with an empty
        database is what a person is left with after a broken database -
        `tools/rebuild.py` is for that. Invented jobs landing in there
        would mix made-up work with somebody's own."""
        os.makedirs(os.path.join(self.directory, chipbook.JOBS_DIR))
        with self.assertRaises(chipbook.ChipbookError) as caught:
            demo.fill(self.directory)
        self.assertIn("not its own", str(caught.exception))

    def test_the_default_directory_is_NOT_the_real_one(self):
        """The demo lives beside the catalogue, never inside it. Were the
        two ever to become the same path, this test is what says so."""
        from chipbook.server import app
        self.assertNotEqual(os.path.normcase(demo.default_demo_dir()),
                            os.path.normcase(app.DEFAULT_DATA_DIR))

    def test_a_word_that_stands_ONLY_IN_A_SETUP_SHEET_is_found(self):
        """This is what the demo exists to show. "Woodruff" appears in no
        field a person typed - it is inside an attached setup sheet, which
        chipbook has read and put into the index."""
        demo.fill(self.directory)
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        hits = store.search("woodruff")
        self.assertEqual(len(hits), 1, "the setup sheet was not indexed")
        job = hits.jobs[0]
        self.assertNotIn("woodruff", job["notes"].lower())
        self.assertNotIn("woodruff", job["name"].lower())

    def test_the_demo_carries_an_NC_program_and_a_setup_sheet(self):
        """Two different readers, so both roads can be seen working: the
        setup-sheet parser and the one that describes an NC program by
        counting rather than by handing its content over."""
        demo.fill(self.directory)
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        names = [att["name"]
                 for job in store.recent()
                 for att in store.attachments(job["id"])]
        self.assertTrue(any(n.endswith(".xml") for n in names), names)
        self.assertTrue(any(n.endswith(".nc") for n in names), names)

    def test_every_demo_job_has_the_obligatory_fields(self):
        """A demo that cannot be saved would fail in front of the very
        person we wanted to show the program to."""
        for job in demo.JOBS:
            for field in ("name", "customer", "material"):
                self.assertTrue(str(job.get(field, "")).strip(), job["name"])


if __name__ == "__main__":
    unittest.main()
