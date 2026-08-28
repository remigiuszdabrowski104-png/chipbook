"""Tests for the chipbook catalog.

Run with:  python -m unittest discover -v

Every test describes ONE promise the program makes.
No libraries from outside the standard library.
"""

import io
import os
import shutil
import tempfile
import unittest

from chipbook import ai
from chipbook import catalog
from chipbook import attachments
import chipbook
from chipbook.ai import grounding
from chipbook import schema
from chipbook import search
from chipbook import setupsheet
from chipbook.setupsheet import render

# THE ONE FIXTURE THAT STAYS POLISH, AND ON PURPOSE. Accent folding exists
# for one language: the person who fills this database writes their notes in
# Polish, and a test that measures the folding has to BE in that language -
# no English word carries the letters the table folds. Everything else in
# these tests is English.
# Non-ASCII is written as escapes, so that the file stays plain ASCII.
NOTES_WITH_ACCENTS = (
    "frez w\u0119glikowy \u015brednica 10, mia\u0142em problem z drganiami, "
    "zszed\u0142em z posuwem i posz\u0142o"
)


class CatalogTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_test_")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def _sample_job(self):
        return self.catalog.add_job(
            name="heart tray", customer="ACME",
            material="titanium",
            notes=NOTES_WITH_ACCENTS,
        )

    # ------------------------------------------ the schema and durability

    def test_a_new_database_has_the_current_schema_version(self):
        version = self.catalog.con.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, schema.SCHEMA_VERSION)

    def test_data_survives_closing_and_opening_again(self):
        record = self._sample_job()
        self.catalog.close()
        self.catalog = catalog.open_catalog(self.directory)
        self.assertEqual(self.catalog.job_count(), 1)
        self.assertEqual(self.catalog.job(record["id"])["material"], "titanium")
        version = self.catalog.con.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, schema.SCHEMA_VERSION)

    def test_a_backup_is_made_on_the_second_opening(self):
        self._sample_job()
        self.catalog.close()
        self.catalog = catalog.open_catalog(self.directory)
        copies = os.listdir(os.path.join(self.directory, chipbook.BACKUPS_DIR))
        self.assertEqual(len(copies), 1, "exactly one backup was expected")
        self.assertTrue(copies[0].startswith("chipbook-"))

    # -------------------------------------------------- saving an entry

    def test_an_entry_saves_with_not_a_single_file(self):
        record = self._sample_job()
        self.assertEqual(self.catalog.job_count(), 1)
        self.assertTrue(record["folder"].endswith("_0001"))

    def test_an_entry_creates_a_readable_text_file(self):
        record = self._sample_job()
        path = os.path.join(self.catalog.job_dir(record),
                               chipbook.METADATA_FILENAME)
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as file:
            content = file.read()
        self.assertIn("material: titanium", content)
        self.assertIn(chipbook.NOTE_SEPARATOR, content)
        self.assertIn(NOTES_WITH_ACCENTS, content)

    def test_the_material_is_obligatory(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.add_job(name="heart tray", customer="ACME", material="   ", notes="anything")

    def test_the_notes_are_NO_LONGER_obligatory(self):
        """REVERSED on the owner's decision. Until then empty notes made the
        program refuse the job - today it may be saved without a single
        sentence.

        THIS TEST USED TO STAND THE OTHER WAY ROUND and that is all right:
        it watches a promise made to the user, and the promise changed.
        The price of that change stands in the comment by add_job."""
        job = self.catalog.add_job(name="heart tray", customer="ACME",
                                    material="titanium", notes="")
        self.assertEqual(job["notes"], "")

    def test_the_other_three_fields_are_still_obligatory(self):
        """The loosening covers the notes ONLY. Name, customer and material
        stay obligatory - without them the entry cannot be found again, and
        that is a different kind of loss than a missing description."""
        for empty in ("name", "customer", "material"):
            fields = {"name": "x", "customer": "y", "material": "z",
                    "notes": ""}
            fields[empty] = "   "
            with self.assertRaises(chipbook.ChipbookError):
                self.catalog.add_job(**fields)

    def test_the_name_is_obligatory(self):
        """Three fields are obligatory, not one."""
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.add_job(name="  ", customer="ACME", material="steel",
                                 notes="anything")

    def test_the_customer_is_obligatory(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.add_job(name="tray", customer="", material="steel",
                                 notes="anything")

    def test_fields_are_saved_without_surrounding_whitespace(self):
        record = self.catalog.add_job(name="  heart tray  ", customer=" ACME ",
                                      material=" steel ", notes="went smoothly")
        self.assertEqual(record["name"], "heart tray")
        self.assertEqual(record["customer"], "ACME")
        self.assertEqual(record["material"], "steel")

    def test_changing_fields_corrects_the_entry_and_the_index(self):
        """We change three fields; the notes stay untouched."""
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        # The date is saved to the second, and the test runs faster than that -
        # so we move it back by hand. Otherwise we would be checking the clock
        # rather than whether the program records the change.
        self.catalog.con.execute(
            "UPDATE job SET updated_at='2020-01-01 00:00:00' WHERE id=?",
            (record["id"],))
        self.catalog.con.commit()

        after = self.catalog.update_fields(record["id"], name="heart tray",
                                  customer="Bosch", material="titanium")
        self.assertEqual(after["name"], "heart tray")
        self.assertEqual(after["customer"], "Bosch")
        self.assertEqual(after["material"], "titanium")
        self.assertEqual(after["notes"], "vibrated")
        self.assertNotEqual(after["updated_at"], "2020-01-01 00:00:00")
        self.assertEqual(len(self.catalog.search("Bosch")), 1)
        self.assertEqual(len(self.catalog.search("ACME")), 0)

    def test_changing_fields_does_not_let_an_empty_one_through(self):
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.update_fields(record["id"], name="tray", customer="  ",
                                 material="steel")
        self.assertEqual(self.catalog.job(record["id"])["customer"], "ACME")

    def test_changing_the_notes_replaces_the_content(self):
        """The notes can be corrected and the old content IS LOST. A decision
        of the owner's, with the price named before it was taken."""
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        after = self.catalog.update_fields(record["id"], name="tray", customer="ACME",
                                  material="steel",
                                  notes="no vibration after all, I measured it wrong")
        self.assertEqual(after["notes"], "no vibration after all, I measured it wrong")
        self.assertEqual(len(self.catalog.search("measured")), 1)
        self.assertEqual(len(self.catalog.search("vibrated")), 1)   # it is in the new content
        self.assertEqual(len(self.catalog.search("wrong")), 1)

    def test_a_change_without_notes_does_not_touch_them(self):
        """No notes in the request means 'do not touch', not 'clear'."""
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        after = self.catalog.update_fields(record["id"], name="heart tray",
                                  customer="ACME", material="steel")
        self.assertEqual(after["notes"], "vibrated")

    def test_the_notes_may_be_cleared_while_editing(self):
        """REVERSED together with the loosening on adding. Until then an
        attempt to clear the notes was refused.

        MIND THE PRICE this change carries and did not carry before: this
        is the only place in the program where a sentence a person wrote by
        hand can be DELETED. The rest of the program never does that - an
        appended note is added on, a scan deletes nothing, a job goes into
        the bin whole. Deleting is possible here because it was asked for,
        but it has to be visible in a test rather than hidden."""
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        self.catalog.update_fields(record["id"], name="tray", customer="ACME",
                             material="steel", notes="   ")
        self.assertEqual(self.catalog.job(record["id"])["notes"], "")

    def test_notes_left_out_while_editing_still_touch_nothing(self):
        """A difference that is easy to miss: EMPTY notes clear them, and
        NOT GIVEN leaves the old ones. That is not the same and must not be."""
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        self.catalog.update_fields(record["id"], name="tray2", customer="ACME",
                             material="steel")
        self.assertEqual(self.catalog.job(record["id"])["notes"], "vibrated")

    def test_an_appended_note_still_overwrites_nothing(self):
        """The part that did NOT change: an appended note is added on and dated."""
        record = self.catalog.add_job(name="tray", customer="ACME",
                                      material="steel", notes="vibrated")
        after = self.catalog.append_note(record["id"], "fine after changing the cutter")
        self.assertIn("vibrated", after["notes"])
        self.assertIn("fine after changing the cutter", after["notes"])

    def test_changing_the_fields_of_an_entry_that_does_not_exist(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.update_fields(999, name="a", customer="b", material="c")

    # -------------------------------------------------- searching

    def test_searching_by_material(self):
        self._sample_job()
        result = self.catalog.search("titanium")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.corrections, [])

    def test_searching_by_an_unaccented_fragment_finds_accented_text(self):
        self._sample_job()
        result = self.catalog.search("eglik")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.corrections, [])

    def test_searching_with_a_typo_finds_and_reports_the_correction(self):
        """A typo AND accents at once, on the Polish fixture. The English typo
        case has a test of its own in FailedTypoFixTest."""
        self._sample_job()
        result = self.catalog.search("weglikowu")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.corrections, [("weglikowu", "weglikowy")])

    def test_searching_for_a_short_word(self):
        """A word shorter than MIN_WORD_LENGTH goes round the index and is
        checked by looking through the entries. The tool has no field of its
        own any more, so it stands where it really stands in use - in the
        notes."""
        self.catalog.add_job(name="heart tray", customer="ACME",
                             material="titanium", notes="carbide endmill op 2, vibrated")
        self.assertEqual(len(self.catalog.search("op")), 1)

    def test_several_words_have_to_match_at_once(self):
        self._sample_job()
        self.catalog.add_job(name="heart tray", customer="ACME", material="steel", notes="problem with vibration at long stickout")
        self.assertEqual(len(self.catalog.search("titanium problem")), 1)
        self.assertEqual(len(self.catalog.search("problem")), 2)

    def test_no_data_is_an_empty_list_and_not_invention(self):
        self._sample_job()
        result = self.catalog.search("inconel")
        self.assertEqual(len(result), 0)
        self.assertEqual(result.jobs, [])

    def test_an_empty_query_returns_nothing(self):
        self._sample_job()
        self.assertEqual(len(self.catalog.search("   ")), 0)

    def test_special_characters_in_a_query_do_not_topple_the_search(self):
        self._sample_job()
        for query in ('titanium"', "titanium*", "-titanium", "titanium OR", "((("):
            self.catalog.search(query)  # it is not to raise an exception

    # ------------------------------------------------- appending a note

    def test_appending_a_note_does_not_delete_the_old_one(self):
        record = self._sample_job()
        after = self.catalog.append_note(record["id"], "the fixture cracked after a week")
        self.assertIn(NOTES_WITH_ACCENTS, after["notes"])
        self.assertIn("the fixture cracked", after["notes"])

    def test_what_was_appended_can_be_found(self):
        record = self._sample_job()
        self.assertEqual(len(self.catalog.search("fixture")), 0)
        self.catalog.append_note(record["id"], "the fixture cracked")
        self.assertEqual(len(self.catalog.search("fixture")), 1)

    def test_what_was_appended_also_reaches_the_text_file(self):
        record = self._sample_job()
        after = self.catalog.append_note(record["id"], "the fixture cracked")
        path = os.path.join(self.catalog.job_dir(after),
                               chipbook.METADATA_FILENAME)
        with open(path, encoding="utf-8") as file:
            content = file.read()
        self.assertIn("the fixture cracked", content)

    def test_an_empty_appended_note_is_refused(self):
        record = self._sample_job()
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.append_note(record["id"], "  ")

    def test_appending_a_note_to_an_entry_that_does_not_exist(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.append_note(999, "anything")


class SentenceQueryTest(unittest.TestCase):
    """A question asked as a whole sentence, rather than as a keyword.

    THE SCENARIO: the user wrote down that on a shaft they had to add a
    hole that was not on the drawing, and months later asks the database in
    a sentence: "what diameter was that extra hole...". Measured before the
    fix: 0 hits, even though the job was in the database - because the
    search demands ALL the words, and a sentence is full of words the entry
    does not hold.
    """

    QUESTION = ("what diameter was that extra hole I had to add "
               "myself, the shaft one")

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_sentence_")
        self.catalog = catalog.open_catalog(self.directory)
        self.shaft = self.catalog.add_job(
            name="Drive shaft 40x180", customer="ACME", material="1.4301",
            notes=("Drilled 6 holes dia 8 in the flange to the drawing. "
                       "I had to add one more hole dia 6.5 on the "
                       "spigot side myself, or the part would have come out wrong. "
                       "Reported it to the foreman."))
        self.catalog.add_job(
            name="Flange dia 120", customer="ACME", material="S355",
            notes="Eight dia 10 holes on a bolt circle, nothing unusual.")

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_a_question_as_a_whole_sentence_finds_the_entry(self):
        result = self.catalog.search(self.QUESTION)
        self.assertEqual([entry["id"] for entry in result.jobs], [self.shaft["id"]])

    def test_skipped_words_are_reported(self):
        """Never quietly - a person is to know what was not searched for."""
        result = self.catalog.search(self.QUESTION)
        self.assertIn("diameter", result.skipped)
        self.assertNotIn("hole", result.skipped)

    def test_one_foreign_word_does_not_zero_the_result(self):
        without = self.catalog.search("spigot hole")
        with_foreign_word = self.catalog.search("spigot hole inconel")
        self.assertTrue(without.jobs)
        self.assertEqual([entry["id"] for entry in without.jobs],
                         [entry["id"] for entry in with_foreign_word.jobs])
        self.assertEqual(with_foreign_word.skipped, ["inconel"])

    def test_skipping_does_not_add_entries_out_of_thin_air(self):
        """Throwing a word out is to UNBLOCK a result, not to hand results out.

        A result with a foreign word in the question has to be EXACTLY the
        same as without it - not one entry more.
        """
        without = self.catalog.search("holes")
        with_foreign_word = self.catalog.search("holes inconel")
        self.assertEqual(len(without.jobs), 2)
        self.assertEqual([entry["id"] for entry in without.jobs],
                         [entry["id"] for entry in with_foreign_word.jobs])

    def test_when_no_word_is_in_the_database_the_result_is_empty(self):
        """No data is emptiness, never guesswork."""
        result = self.catalog.search("inconel tungsten molybdenum")
        self.assertEqual(result.jobs, [])
        self.assertEqual(sorted(result.skipped),
                         ["inconel", "molybdenum", "tungsten"])

    def test_a_fragment_inside_a_word_is_not_skipped(self):
        """WHY NOT A DICTIONARY OF WORDS FROM THE DATABASE: the search matches
        fragments too. "rill" does not stand in the database as a whole
        word, but it sits inside "drilled" - and it is to be searched for,
        not set aside."""
        self.assertEqual(
            self.catalog._words_without_hits(["rill", "inconel"]), ["inconel"])

    def test_a_successful_search_skips_nothing(self):
        result = self.catalog.search("spigot")
        self.assertTrue(result.jobs)
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.corrections, [])
        self.assertEqual(result.forms, [])


class WordFormsTest(unittest.TestCase):
    """Searching by another form of the same word - a request from the end
    user: they type "vibration" and are to find the job that says
    "vibrated".

    The candidates come EXCLUSIVELY from words standing in the database
    itself - no downloaded dictionary (that variant was rejected by
    measurement).
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_wordforms_")
        self.catalog = catalog.open_catalog(self.directory)
        self.vibrating = self.catalog.add_job(
            name="Heart tray", customer="ACME", material="AlMg3",
            notes="Vibrated at 24000 rpm, backed down to 18000.")
        self.shaft = self.catalog.add_job(
            name="Drive shaft", customer="ACME", material="1.4301",
            notes=("I had to ream one more hole dia 6.5 myself, "
                       "or the part would have come out wrong."))
        self.catalog.add_job(
            name="Flange dia 120", customer="ACME", material="S355",
            notes="Eight dia 10 holes on a bolt circle.")

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_one_word_form_finds_the_entry_holding_another(self):
        result = self.catalog.search("vibration")
        self.assertEqual([entry["id"] for entry in result.jobs], [self.vibrating["id"]])
        self.assertEqual(result.forms, [("vibration", ["vibrated"])])

    def test_a_word_form_hits_its_relative_and_narrows_the_result(self):
        """HOW WE KNOW IT WORKS: before word forms, "reaming hole" gave 2
        jobs (the word was set aside and only "hole" was left); now it has
        to give 1 - the one where somebody reamed something.

        WHY "reaming" AND NOT "drilling": the typo step runs BEFORE this
        one, and "drilling" against "drill" measures 0.769 - above the 0.75
        threshold, so the word would be CORRECTED and this mechanism would
        never be reached. "reaming" against "ream" measures 0.727, below
        the threshold, so only a shared stem can join them. A test that
        passes by the wrong road measures nothing."""
        result = self.catalog.search("reaming hole")
        self.assertEqual([entry["id"] for entry in result.jobs], [self.shaft["id"]])
        self.assertEqual(result.forms, [("reaming", ["ream"])])
        self.assertEqual(result.skipped, [])

    def test_the_word_form_is_reported_and_not_silent(self):
        result = self.catalog.search("vibration")
        self.assertTrue(result.forms)
        word, other = result.forms[0]
        self.assertEqual(word, "vibration")
        self.assertIn("vibrated", other)

    def test_a_word_that_has_hits_is_not_touched(self):
        """A successful search has to work exactly as it did yesterday."""
        result = self.catalog.search("vibrated")
        self.assertEqual(result.forms, [])
        self.assertEqual([entry["id"] for entry in result.jobs], [self.vibrating["id"]])

    def test_a_word_with_no_family_still_goes_to_the_skipped_ones(self):
        result = self.catalog.search("inconel")
        self.assertEqual(result.jobs, [])
        self.assertEqual(result.skipped, ["inconel"])
        self.assertEqual(result.forms, [])

    def test_a_family_is_counted_by_the_common_beginning(self):
        self.assertTrue(search.same_word_family("vibration", "vibrated"))
        self.assertTrue(search.same_word_family("drilling", "drilled"))
        self.assertTrue(search.same_word_family("allowances", "allowance"))
        self.assertFalse(search.same_word_family("shaft", "shape"))
        self.assertFalse(search.same_word_family("plate", "plane"))

    def test_a_short_word_does_not_drag_the_whole_database_behind_it(self):
        """A word shorter than a stem has no family - otherwise "dia" would
        join up with everything that begins with "dia"."""
        self.assertFalse(search.same_word_family("dia", "diameter"))
        self.assertEqual(self.catalog._word_forms("dia", ["diameter", "diagonal"]), [])


class LetterLTypoTest(unittest.TestCase):
    """The letter 'l with a stroke' - a case of its own, see the note in catalog.

    SQLite does NOT fold it down to 'l' by itself, so we normalise the text
    before putting it into the index. This test watches that it stays so.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_typo_")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_searching_without_the_stroke_finds_the_word_with_it(self):
        self.catalog.add_job(
            name="heart tray", customer="ACME",
            material="steel",
            notes="wiercenie g\u0142\u0119bokie w \u0142o\u017cu wrzeciona")
        self.assertEqual(len(self.catalog.search("lebok")), 1)
        self.assertEqual(len(self.catalog.search("lozu")), 1)
        self.assertEqual(len(self.catalog.search("glebokie")), 1)

    def test_migrating_from_schema_1_rebuilds_the_index(self):
        record = self.catalog.add_job(
            name="heart tray", customer="ACME",
            material="steel", notes="wiercenie g\u0142\u0119bokie")
        # we put the base back as it was before the fix: an accented index,
        # schema version 1
        raw_text = "\n".join(str(record[p] or "") for p in schema.DESCRIPTIVE_FIELDS)
        self.catalog.con.execute("DELETE FROM job_fts")
        self.catalog.con.execute(
            "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
            (record["id"], raw_text))
        self.catalog.con.execute("PRAGMA user_version = 1")
        self.catalog.con.commit()
        self.assertEqual(len(self.catalog.search("lebok")), 0)  # the state before the migration

        self.catalog.close()
        self.catalog = catalog.open_catalog(self.directory)

        version = self.catalog.con.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, schema.SCHEMA_VERSION)
        self.assertEqual(len(self.catalog.search("lebok")), 1)
        self.assertEqual(self.catalog.job_count(), 1)

    def test_migrating_to_schema_4_does_not_touch_an_already_migrated_database(self):
        """A version number moved back by hand must not wipe the name and the customer.

        This is not theory: the first version of this migration went through
        a second time without an error and set both fields to empty. Quietly."""
        record = self.catalog.add_job(name="heart tray", customer="ACME",
                                      material="steel", notes="vibrated")
        self.catalog.con.execute("PRAGMA user_version = 3")
        self.catalog.con.commit()
        self.catalog.close()
        self.catalog = catalog.open_catalog(self.directory)

        after = self.catalog.job(record["id"])
        self.assertEqual(after["name"], "heart tray")
        self.assertEqual(after["customer"], "ACME")
        self.assertEqual(len(self.catalog.search("ACME")), 1)


class SuggestionsTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_suggest_")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_the_recent_ones_newest_first(self):
        self.catalog.add_job(name="heart tray", customer="ACME", material="steel", notes="first")
        second = self.catalog.add_job(name="heart tray", customer="ACME", material="titanium", notes="second")
        recent = self.catalog.recent()
        self.assertEqual(recent[0]["id"], second["id"])
        self.assertEqual(len(recent), 2)

    def test_suggestions_skip_empties_and_do_not_repeat(self):
        self.catalog.add_job(name="tray", customer="ACME", material="steel",
                             notes="a")
        self.catalog.add_job(name="wheel", customer="Acme", material="Steel",
                             notes="b")
        hints = self.catalog.suggestions()
        self.assertEqual(len(hints["material"]), 1)
        self.assertEqual(len(hints["customer"]), 1)
        self.assertNotIn("name", hints)   # every job has a different name

    def test_suggestions_start_from_the_last_one_used(self):
        self.catalog.add_job(name="tray", customer="ACME", material="steel",
                             notes="a")
        self.catalog.add_job(name="wheel", customer="Bosch", material="titanium",
                             notes="b")
        self.assertEqual(self.catalog.suggestions()["customer"][0], "Bosch")
        self.assertEqual(self.catalog.suggestions()["material"][0], "titanium")


class AttachmentsTest(unittest.TestCase):
    """Attachments of an entry - copied into the entry folder."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_attach_")
        self.catalog = catalog.open_catalog(self.directory)
        self.job = self.catalog.add_job(name="heart tray", customer="ACME", material="titanium", notes="trial run")

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _add(self, name, content=b"some program bytes"):
        return self.catalog.add_attachment(self.job["id"], name,
                                         io.BytesIO(content), len(content))

    def test_a_file_lands_in_the_entry_folder(self):
        item = self._add("program.nc")
        self.assertTrue(os.path.exists(item["path"]))
        self.assertIn(chipbook.FILES_DIR, item["path"])

    def test_the_saved_checksum_matches_the_content(self):
        import hashlib
        content = b"G0 X0 Y0\nG1 Z-5 F100\n"
        item = self._add("code.nc", content)
        self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(item["size_bytes"], len(content))

    def test_the_list_of_attachments(self):
        self._add("a.nc")
        self._add("b.step")
        names = [att["name"] for att in self.catalog.attachments(self.job["id"])]
        self.assertEqual(names, ["a.nc", "b.step"])

    def test_the_same_name_does_not_overwrite(self):
        first = self._add("setup.xml", b"first")
        second = self._add("setup.xml", b"second")
        self.assertEqual(first["name"], "setup.xml")
        self.assertEqual(second["name"], "setup (2).xml")
        with open(first["path"], "rb") as file:
            self.assertEqual(file.read(), b"first")

    def test_a_name_with_a_path_does_not_escape_the_folder(self):
        item = self._add("..\\..\\..\\Windows\\system32\\evil.dll")
        self.assertEqual(item["name"], "evil.dll")
        self.assertTrue(os.path.abspath(item["path"]).startswith(
            os.path.abspath(self.directory)))

    def test_a_name_reserved_on_windows(self):
        self.assertEqual(attachments.safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(attachments.safe_filename("nul"), "_nul")
        self.assertEqual(attachments.safe_filename("code:1?.nc"), "code1.nc")
        self.assertEqual(attachments.safe_filename("   "), "file")

    def test_a_file_name_can_be_searched_for(self):
        self._add("order_number-2026-114-setup.xml")
        self.assertEqual(len(self.catalog.search("2026-114")), 1)
        self.assertEqual(len(self.catalog.search("setup")), 1)

    def test_an_appended_note_does_not_wipe_file_names_from_the_index(self):
        """A bug found while correcting an entry: appending a note rebuilt the
        index WITHOUT the attachment names, so the job stopped being found by
        its file name. Nothing about it broke on screen."""
        self._add("order_number-2026-114-setup.xml")
        self.catalog.append_note(self.job["id"], "one more sentence")
        self.assertEqual(len(self.catalog.search("2026-114")), 1)

    # --------------------------------- the content of a setup sheet

    SETUP_XML = (b"<?xml version='1.0'?><SETUPSHEET>"
                 b"<DESCRIPTION>Heart tray</DESCRIPTION>"
                 b"<OPERATION><NAME>Contour</NAME>"
                 b"<SPINDLE>24000 RPM</SPINDLE>"
                 b"<TOOLMATERIAL>Carbide</TOOLMATERIAL></OPERATION>"
                 b"<OPERATION><NAME>Dynamic</NAME>"
                 b"<SPINDLE>18000 RPM</SPINDLE>"
                 b"<TOOLMATERIAL>Carbide</TOOLMATERIAL></OPERATION>"
                 b"</SETUPSHEET>")

    def test_the_content_of_a_setup_sheet_can_be_searched_for(self):
        """Until then, typing 24000 or Carbide found nothing, even though the
        preview showed it."""
        self._add("tray.xml", self.SETUP_XML)
        self.assertEqual(len(self.catalog.search("24000")), 1)
        self.assertEqual(len(self.catalog.search("Carbide")), 1)
        self.assertEqual(len(self.catalog.search("Contour")), 1)

    def test_gcode_does_not_enter_the_index(self):
        """We index setup sheets only. G-code is thousands of lines of
        coordinates, which would bury the results."""
        self._add("program.nc", b"G0 X0 Y0\nG1 Z-5 F250\nM30\n")
        self.assertEqual(len(self.catalog.search("F250")), 0)
        # the file name alone still works
        self.assertEqual(len(self.catalog.search("program")), 1)

    def test_the_content_stays_when_the_file_leaves_the_disk(self):
        """The content lives in the database, not in the file - the job can be
        found even after somebody deletes the attachment from the disk."""
        item = self._add("tray.xml", self.SETUP_XML)
        os.remove(os.path.join(self.catalog.files_dir(self.job),
                               item["name"]))
        self.assertEqual(len(self.catalog.search("24000")), 1)

    def test_a_broken_setup_sheet_does_not_block_adding_the_file(self):
        """An unreadable file means no content, not a failed save."""
        item = self._add("bad.xml", b"<SETUPSHEET><A></SETUPSHEET>")
        self.assertEqual(item["name"], "bad.xml")
        self.assertEqual(len(self.catalog.search("bad")), 1)

    def test_an_appended_note_does_not_wipe_setup_sheet_content_from_the_index(self):
        self._add("tray.xml", self.SETUP_XML)
        self.catalog.append_note(self.job["id"], "one more sentence")
        self.assertEqual(len(self.catalog.search("24000")), 1)

    def test_migrating_to_5_reads_content_from_files_already_on_disk(self):
        """Setup sheets dropped in BEFORE this change also have to be findable -
        the migration reads them off the disk instead of waiting for them to
        be dropped in again."""
        self._add("tray.xml", self.SETUP_XML)
        # we put the base back as it was before setup-sheet content was indexed
        self.catalog.con.execute("UPDATE attachment SET content=''")
        self.catalog.con.execute("PRAGMA user_version = 4")
        self.catalog.con.commit()
        self.catalog.close()
        self.catalog = catalog.open_catalog(self.directory)

        version = self.catalog.con.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, schema.SCHEMA_VERSION)
        self.assertEqual(len(self.catalog.search("24000")), 1)

    def test_migrating_to_5_does_not_topple_on_a_missing_file(self):
        item = self._add("tray.xml", self.SETUP_XML)
        os.remove(os.path.join(self.catalog.files_dir(self.job),
                               item["name"]))
        self.catalog.con.execute("UPDATE attachment SET content=''")
        self.catalog.con.execute("PRAGMA user_version = 4")
        self.catalog.con.commit()
        self.catalog.close()
        self.catalog = catalog.open_catalog(self.directory)
        self.assertEqual(self.catalog.job_count(), 1)
        self.assertEqual(len(self.catalog.search("tray")), 1)   # by file name

    def test_changing_fields_does_not_wipe_file_names_from_the_index(self):
        self._add("order_number-2026-114-setup.xml")
        self.catalog.update_fields(self.job["id"], name="other", customer="Bosch",
                             material="titanium")
        self.assertEqual(len(self.catalog.search("2026-114")), 1)

    def test_an_attachment_reaches_the_text_file(self):
        self._add("program.nc", b"12345")
        path = os.path.join(self.catalog.job_dir(self.job),
                               chipbook.METADATA_FILENAME)
        with open(path, encoding="utf-8") as file:
            content = file.read()
        self.assertIn("attachments: 1", content)
        self.assertIn("program.nc", content)
        self.assertIn("5 B", content)

    def test_an_entry_with_no_attachments_still_works(self):
        path = os.path.join(self.catalog.job_dir(self.job),
                               chipbook.METADATA_FILENAME)
        with open(path, encoding="utf-8") as file:
            self.assertIn("attachments: 0", file.read())

    def test_a_truncated_file_does_not_reach_the_database(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.add_attachment(self.job["id"], "truncated.nc",
                                      io.BytesIO(b"just a little"), 999999)
        self.assertEqual(self.catalog.attachments(self.job["id"]), [])

    def test_a_file_that_is_too_large_is_refused_readably(self):
        with self.assertRaises(chipbook.ChipbookError) as caught:
            self.catalog.add_attachment(self.job["id"], "giant.mcam",
                                      io.BytesIO(b""), attachments.MAX_ATTACHMENT_BYTES + 1)
        self.assertIn("GB", str(caught.exception))

    def test_an_attachment_for_an_entry_that_does_not_exist(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.add_attachment(999, "a.nc", io.BytesIO(b"x"), 1)


class DeletionTest(unittest.TestCase):
    """Deleting an entry - checked on THE FALLBACK ROAD, on every system.

    The system Recycle Bin is deliberately switched off here (see setUp):
    the test is to give the same result everywhere and has no right to
    litter the user's Recycle Bin.
    """

    def setUp(self):
        # The test does NOT touch the real system Recycle Bin. Without this,
        # every test run threw temporary folders into the user's Recycle Bin and
        # the result depended on the system. The real Recycle Bin is checked by
        # hand - and it was checked on Windows 11.
        self._real_recycle_bin = attachments.move_to_recycle_bin
        attachments.move_to_recycle_bin = lambda path: None
        self.directory = tempfile.mkdtemp(prefix="chipbook_delete_")
        self.catalog = catalog.open_catalog(self.directory)
        self.job = self.catalog.add_job(name="heart tray", customer="ACME", material="steel", notes="for deletion")
        self.catalog.add_attachment(self.job["id"], "file.nc",
                                  io.BytesIO(b"the nc program"), 14)

    def tearDown(self):
        attachments.move_to_recycle_bin = self._real_recycle_bin
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_the_entry_leaves_the_program(self):
        self.catalog.delete_job(self.job["id"])
        self.assertEqual(self.catalog.job_count(), 0)
        self.assertIsNone(self.catalog.job(self.job["id"]))
        self.assertEqual(len(self.catalog.search("deletion")), 0)
        self.assertEqual(self.catalog.attachments(self.job["id"]), [])

    def test_the_folder_does_not_vanish_from_the_disk(self):
        result = self.catalog.delete_job(self.job["id"])
        self.assertEqual(result["where"], "moved")
        self.assertTrue(os.path.isdir(result["path"]))
        self.assertTrue(os.path.exists(
            os.path.join(result["path"], chipbook.FILES_DIR, "file.nc")))

    def test_the_text_file_gets_the_date_of_deletion(self):
        result = self.catalog.delete_job(self.job["id"])
        with open(os.path.join(result["path"], chipbook.METADATA_FILENAME),
                  encoding="utf-8") as file:
            content = file.read()
        self.assertIn("deleted from chipbook:", content)
        self.assertIn("for deletion", content)

    def test_deleting_an_entry_that_does_not_exist(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.delete_job(4242)

    def test_the_other_entries_stay(self):
        second = self.catalog.add_job(name="heart tray", customer="ACME", material="titanium", notes="this one stays")
        self.catalog.delete_job(self.job["id"])
        self.assertEqual(self.catalog.job_count(), 1)
        self.assertEqual(self.catalog.job(second["id"])["material"], "titanium")
        self.assertEqual(len(self.catalog.search("stays")), 1)


class DiacriticsTest(unittest.TestCase):

    def test_stripping_the_accents(self):
        self.assertEqual(search.strip_diacritics("w\u0119glikowy"), "weglikowy")
        self.assertEqual(search.strip_diacritics("\u0141\u00d3D\u0179"), "LODZ")

    def test_words_are_split_and_normalised(self):
        self.assertEqual(search._words("Frez fi10, w\u0119glik!"),
                         ["frez", "fi10", "weglik"])


class FailedTypoFixTest(unittest.TestCase):
    """A bug caught LIVE, in AI mode.

    The question "what size was that hole I had to add myself" gave zero
    results, even though the job was in the database. The cause: the word
    "size" is not in the database, so difflib corrected it to "side" - a
    word standing in a DIFFERENT entry. A corrected word HAS hits, so the
    step that sets aside words with no hits would never have dropped it,
    and the job in question holds no "side" at all.

    A lesson wider than the bug itself: a typo correction is GUESSWORK, and
    guesswork has the right not to help - but no right to do harm.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_fix_")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_a_correction_into_a_dead_end_does_not_kill_the_question(self):
        # There are TWO jobs in the database: the shaft we are after and a
        # second one that brings into the lexicon a word resembling the typo.
        # Without it difflib would have nothing to substitute and the test would
        # check nothing.
        self.catalog.add_job(name="trial shaft", customer="test",
                             material="steel",
                             notes="I had to add an extra hole dia 10")
        # "side" MUST stand here as a separate word - otherwise difflib has
        # nothing to put in place of "size" and the test passes for the wrong
        # reason, checking nothing. MEASURED on these very words: the 0.75
        # threshold catches "size" -> "side" at exactly 0.75, and catches
        # nothing else in this lexicon. In the real database the word that did
        # the damage came from a setup sheet.
        self.catalog.add_job(name="plate", customer="test", material="steel",
                             notes="slot mill for the grooves, chamfer on far side")

        result = self.catalog.search(
            "what size was that hole I had to add myself")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.jobs[0]["name"], "trial shaft")

    def test_a_correction_that_helped_still_works_and_is_reported(self):
        """We undo only the corrections that FAILED. One that finds the job has
        to work as before - otherwise fixing one thing would break another."""
        self.catalog.add_job(name="tray", customer="ACME", material="titanium",
                             notes="carbide endmill dia 10")
        result = self.catalog.search("carbde")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.corrections, [("carbde", "carbide")])

    def test_a_failed_correction_is_not_shown_to_a_person(self):
        """Since we did not use it, we must not speak of it - otherwise the
        window would be explaining the result by a substitution that never
        happened."""
        self.catalog.add_job(name="trial shaft", customer="test",
                             material="steel",
                             notes="I had to add an extra hole dia 10")
        # "side" MUST stand here as a separate word - otherwise difflib has
        # nothing to put in place of "size" and the test passes for the wrong
        # reason, checking nothing. MEASURED on these very words: the 0.75
        # threshold catches "size" -> "side" at exactly 0.75, and catches
        # nothing else in this lexicon. In the real database the word that did
        # the damage came from a setup sheet.
        self.catalog.add_job(name="plate", customer="test", material="steel",
                             notes="slot mill for the grooves, chamfer on far side")
        result = self.catalog.search(
            "what size was that hole I had to add myself")
        self.assertEqual(result.corrections, [])
        self.assertIn("size", result.skipped)


class ModelQueryTest(unittest.TestCase):
    """The AI road: question -> search -> model -> answer plus a source.

    NONE OF THESE TESTS RUNS THE MODEL. We inject the conversation, because
    we are checking the ARRANGEMENT and not whether the model is clever. The
    quality of answers is measured on a real model and has its own place -
    not a test suite.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_test_ai_")
        self.catalog = catalog.open_catalog(self.directory)
        self.queries = []

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def _conversation(self, answer_="The model's answer."):
        def conversation(question, text, **rest):
            self.queries.append((question, text, rest.get("prompt")))
            return answer_
        return conversation

    def _raising(self, question, text, **rest):
        raise AssertionError("the model was NOT to be asked")

    def _job(self, name, notes, customer="ACME", material="titanium"):
        return self.catalog.add_job(name=name, customer=customer,
                                    material=material, notes=notes)

    # ------------------------------------- the three cases of AI mode

    def test_zero_candidates_does_not_ask_the_model_at_all(self):
        """The most important test of this set. The guarantee 'I do not have
        this in the database' has to belong to the PROGRAM and not to the
        model's goodwill - so with zero hits the model must not even be
        called."""
        self._job("shaft", "drilling eleven holes")
        answer = self.catalog.ask("a completely different matter xyzabc",
                                   conversation=self._raising)
        self.assertEqual(answer.kind, "none")
        self.assertEqual(answer.jobs, [])
        self.assertEqual(self.queries, [])

    def test_one_candidate_gives_an_answer_and_one_source(self):
        record = self._job("shaft", "I had to add a hole dia 10")
        answer = self.catalog.ask("drilled hole",
                                   conversation=self._conversation("Dia 10."))
        self.assertEqual(answer.kind, "one")
        self.assertEqual(answer.text, "Dia 10.")
        self.assertEqual([entry["id"] for entry in answer.jobs], [record["id"]])

    def test_with_several_jobs_THE_MODEL_IS_NOT_CALLED(self):
        """We tried handing the model the job of composing the follow-up
        question, giving it nothing but the differences - and it WAS NOT
        ENOUGH. Measured: given two jobs to choose from, it asked "does the
        job have a hole of a shape other than round?". No entry holds such a
        notion - the model invented the QUESTION itself. The sentence is now
        composed by the window, and the model is asked nothing at this
        point."""
        self._job("shaft A", "extra hole in the flange")
        self._job("shaft B", "extra hole, tapped M6")
        answer = self.catalog.ask("drilled hole",
                                   conversation=self._raising)
        self.assertEqual(answer.kind, "several")
        self.assertEqual(len(answer.jobs), 2)
        self.assertEqual(answer.text, "")
        self.assertEqual(self.queries, [])

    def test_candidates_carry_THE_ACTUAL_distinguishing_values(self):
        """The sentence "they differ in material" is useless. A person is to
        see BY WHAT exactly, so that they can point at the one right job."""
        self._job("shaft A", "drilled hole", material="steel")
        self._job("shaft B", "drilled hole", material="titanium")
        answer = self.catalog.ask("drilled hole",
                                   conversation=self._raising)
        materials = sorted(k["material"] for k in answer.candidates)
        self.assertEqual(materials, ["steel", "titanium"])
        names = sorted(k["name"] for k in answer.candidates)
        self.assertEqual(names, ["shaft A", "shaft B"])

    # ------------------------------------------------ a conversation, not one question

    def test_a_persons_answer_narrows_through_THE_SEARCH(self):
        """The heart of the conversation: what a person adds is appended to the
        query and we search the WHOLE database ANEW. The narrowing is done by
        the search and not by the model - otherwise the guarantee "only from
        the database" would be a fiction."""
        self._job("shaft A", "drilled hole", material="steel")
        titanium_job = self._job("shaft B", "drilled hole", material="titanium")

        first_answer = self.catalog.ask("drilled hole",
                                        conversation=self._raising)
        self.assertEqual(first_answer.kind, "several")

        second_answer = self.catalog.ask(
            "drilled hole",
            clarifications=[("What of?", "of titanium")],
            conversation=self._conversation("Dia 10."))
        self.assertEqual(second_answer.kind, "one")
        self.assertEqual(second_answer.jobs[0]["id"], titanium_job["id"])

    def test_i_do_not_remember_does_not_lose_the_candidates(self):
        """An answer with no content must not zero the result - a person is to
        go on seeing the same jobs, only with a different question."""
        self._job("shaft A", "drilled hole", material="steel")
        self._job("shaft B", "drilled hole", material="titanium")
        answer = self.catalog.ask(
            "drilled hole",
            clarifications=[("What material?", "I do not remember")],
            conversation=self._raising)
        self.assertEqual(answer.kind, "several")
        self.assertEqual(len(answer.jobs), 2)

    def test_we_answer_the_ORIGINAL_question(self):
        """Clarifications serve to find the job; they do not change what the
        person asked in the first place."""
        self._job("shaft A", "extra hole dia 10", material="steel")
        self.catalog.ask("what diameter was that extra hole",
                             clarifications=[("What of?", "of steel")],
                             conversation=self._conversation("Dia 10."))
        self.assertEqual(self.queries[0][0],
                         "what diameter was that extra hole")

    def test_several_candidates_say_how_they_differ(self):
        """Without this the question "which one do you mean?" is useless - a
        person has to see what tells them apart."""
        self._job("shaft A", "drilled hole", material="steel")
        self._job("shaft B", "drilled hole", material="titanium")
        answer = self.catalog.ask("drilled hole",
                                   conversation=self._raising)
        self.assertIn("name", answer.differences)
        self.assertIn("material", answer.differences)
        self.assertNotIn("customer", answer.differences)   # the same in both

    def test_once_a_job_is_pointed_at_we_ask_about_THAT_ONE(self):
        """A person clicks a job, the program asks the model about it alone -
        and then there is nothing to glue together."""
        first = self._job("shaft A", "extra hole in the flange")
        self._job("shaft B", "extra hole, tapped M6")
        answer = self.catalog.ask("drilled hole",
                                   conversation=self._conversation("In the flange."),
                                   number=first["id"])
        self.assertEqual(answer.kind, "one")
        self.assertEqual([entry["id"] for entry in answer.jobs], [first["id"]])
        self.assertIn("flange", self.queries[0][1])
        self.assertNotIn("tapped", self.queries[0][1])

    def test_pointing_at_an_entry_that_does_not_exist_is_an_absence_not_a_failure(self):
        self._job("shaft A", "drilled hole")
        answer = self.catalog.ask("drilled hole", number=9999,
                                   conversation=self._raising)
        self.assertEqual(answer.kind, "none")
        self.assertEqual(self.queries, [])

    def test_no_answer_from_the_model_is_information_not_a_failure(self):
        self._job("shaft", "extra hole dia 10")

        def falls_over(question, text):
            raise ai.ModelError("Ollama is not running.")

        answer = self.catalog.ask("drilled hole", conversation=falls_over)
        self.assertEqual(answer.kind, "error_message")
        self.assertIn("Ollama", answer.text)
        self.assertEqual(len(answer.jobs), 1)

    # ------------------------------------------ what the model gets

    def test_the_model_gets_THE_REAL_number_of_entries(self):
        """Caught live: asked "how many entries do I have in the database" the
        model invented 14, with five in the database. We do not recognise the
        kind of question - we simply supply a fact we know, so that it has no
        reason to invent one."""
        self._job("shaft A", "drilled hole")
        self._job("shaft B", "a completely different job")
        self.catalog.ask("drilled hole", conversation=self._conversation())
        self.assertIn("The catalogue holds 2 jobs", self.queries[0][1])

    def test_the_model_gets_the_technologists_notes(self):
        self._job("shaft", "I had to add a hole dia 10, the print was wrong")
        self.catalog.ask("drilled hole", conversation=self._conversation())
        text = self.queries[0][1]
        self.assertIn("I had to add a hole dia 10", text)
        self.assertIn("titanium", text)

    def test_the_model_gets_setup_sheet_content_assembled_for_it(self):
        """The whole pipe: the model is to get labels beside the values, not a
        flat list meant for the search."""
        record = self._job("shaft", "drilled")
        xml = (b"<?xml version='1.0'?><SETUPSHEET>"
               b"<OPERATION><NAME>1 - Drill</NAME><FEED>250</FEED></OPERATION>"
               b"<OPERATION><NAME>2 - Contour</NAME><FEED>3500</FEED>"
               b"</OPERATION></SETUPSHEET>")
        self.catalog.add_attachment(record["id"], "setup.xml", io.BytesIO(xml),
                                  len(xml))
        self.catalog.ask("drilled", conversation=self._conversation())
        text = self.queries[0][1]
        self.assertIn("[OPERATION 2 of 2]", text)
        self.assertIn("FEED: 3500", text)

    def test_more_entries_than_the_limit_do_not_flood_the_model(self):
        """A model sees a limited piece of text at a time - giving it
        everything is neither possible nor free."""
        for number in range(6):
            self._job("shaft %d" % number, "extra hole no. %d" % number)
        answer = self.catalog.ask("drilled hole", limit=2,
                                   conversation=self._conversation())
        self.assertEqual(len(answer.jobs), 2)

    # ------------------------------------------------ where a source comes from

    def test_the_source_comes_from_the_search_and_not_from_the_models_answer(self):
        """The model may write anything - the sources are the ones the search
        found all the same. Thanks to that a link to an entry that does not
        exist cannot be invented."""
        record = self._job("shaft", "extra hole dia 10")
        answer = self.catalog.ask(
            "drilled hole",
            conversation=self._conversation("See job no. 9999, the 'Wheel' one."))
        self.assertEqual([entry["id"] for entry in answer.jobs], [record["id"]])

    def test_one_foreign_word_does_not_kill_the_question(self):
        """Reported from use: 'I asked a question for the ai, so it should
        understand from the rest of the sentence'.

        Manual mode demands ALL the words and is to stay that way. AI mode
        counts how many words match and takes the best job - otherwise one
        word standing in somebody else's entry zeroes the whole question."""
        record = self._job("trial shaft",
                            "I had to add an extra hole dia 10")
        # the second job brings into the database the word "speeds", which the shaft does not have
        self._job("plate", "there was vibration so I changed the speeds")

        answer = self.catalog.ask(
            "what speeds did that shaft have where I had to add a hole",
            conversation=self._conversation())
        self.assertTrue(answer.jobs, "the question found nothing")
        self.assertEqual(answer.jobs[0]["id"], record["id"])

    def test_ai_mode_also_searches_by_word_forms(self):
        """Word forms hold in BOTH modes. Caught while writing an independent
        test: the first version of `candidates_for_question` did not have
        this step, so the question "how many holes did I drill in that
        bushing" found nothing - the database holds "hole", not "holes"."""
        record = self._job("bushing LX-88",
                            "I put in one more hole off the print")
        answer = self.catalog.ask("how many holes did I drill in that bushing",
                                   conversation=self._conversation())
        self.assertTrue(answer.jobs, "the question found nothing")
        self.assertEqual(answer.jobs[0]["id"], record["id"])
        self.assertTrue(answer.forms,
                        "the program did not report searching other forms")

    def test_the_threshold_keeps_the_i_do_not_have_it_answer_possible(self):
        """The variant 'any single word is enough' was rejected, because then
        everything matches everything. The threshold is what keeps the answer
        'I do not have this in the database' alive - and that has a test of
        its own.

        A NOTE ON THE EXAMPLE: the first version of this test asked about
        "what coolant for titanium", and the job is precisely about
        titanium. Once
        word forms were added the program started finding it correctly and
        the test failed - rightly. The question has to have NOTHING in common
        with the entry, word families included, otherwise it measures
        something other than it was meant to."""
        self._job("trial shaft", "I had to add an extra hole dia 10")
        answer = self.catalog.ask(
            "what coolant did I use on inconel",
            conversation=self._raising)
        self.assertEqual(answer.kind, "none")

    def test_a_better_matching_entry_goes_first(self):
        """The model gets several candidates and reads them in turn - the order
        is not decoration."""
        weaker = self._job("plate", "drilling holes in the plate")
        better_job = self._job("trial shaft",
                            "I had to add an extra hole in the shaft")
        answer = self.catalog.ask("extra hole in the shaft",
                                   conversation=self._conversation())
        self.assertEqual(answer.jobs[0]["id"], better_job["id"])
        self.assertNotEqual(answer.jobs[0]["id"], weaker["id"])

    def test_when_one_job_matches_better_the_program_DOES_NOT_ASK(self):
        """Reported live: the program asked "which one do you mean?", the
        person answered "I do not remember" - that is, added NO information -
        and the program still managed to choose. Since it could then, it
        could from the start. We ask only on a tie; when one job matches
        better, we simply answer."""
        better_match = self._job("bushing LX-88",
                            "I put in one more hole off the print, "
                            "the drawing was wrong")
        self._job("trial shaft", "extra hole, the drawing was wrong")
        answer = self.catalog.ask(
            "how many holes did I drill in that bushing with the wrong drawing",
            conversation=self._conversation("Eleven."))
        self.assertEqual(answer.kind, "one")
        self.assertEqual(answer.jobs[0]["id"], better_match["id"])

    def test_on_a_tie_we_still_ask(self):
        """The other side of the same rule: when there really is nothing to
        decide by, guessing on a person's behalf would be worse than
        asking."""
        self._job("shaft A", "extra hole in the flange")
        self._job("shaft B", "extra hole in the flange")
        answer = self.catalog.ask("extra hole in the flange",
                                   conversation=self._raising)
        self.assertEqual(answer.kind, "several")
        self.assertEqual(len(answer.jobs), 2)

    def test_manual_search_still_demands_all_the_words(self):
        """The most important safeguard of this change: manual mode has to work
        EXACTLY as it did yesterday. Should anybody ever wire a new road
        under it, this test will fail."""
        self._job("trial shaft", "I had to add an extra hole dia 10")
        self._job("plate", "there was vibration so I changed the speeds")
        self.assertEqual(len(self.catalog.search("speeds shaft")), 0)

    def test_corrections_and_skipped_words_still_reach_the_window(self):
        """Substitutions in a question are never silent - AI mode has to show
        the same as manual mode."""
        self._job("shaft", "I had to add a hole dia 10")
        answer = self.catalog.ask(
            "what diameter was that extra hole I added myself",
            conversation=self._conversation())
        self.assertEqual(answer.kind, "one")
        self.assertTrue(answer.skipped)


class RebuildFromFoldersTest(unittest.TestCase):
    """The rescue road: a new computer or a broken database, the folders left.

    THE SCENARIO: the user copies the data directory of entries onto a
    portable disk, buys a new laptop, installs the program - and the
    database is empty. Retyping a hundred entries by hand is out.

    THIS WORKS BECAUSE a decision from the first day said to keep a readable
    copy of every entry beside the database. At the time it sounded like
    caution.
    """

    def setUp(self):
        self.old = tempfile.mkdtemp(prefix="chipbook_old_")
        self.new_ = tempfile.mkdtemp(prefix="chipbook_new_")
        store = catalog.open_catalog(self.old)
        self.first = store.add_job(
            name="bushing LX-88", customer="ACME", material="bronze",
            notes="I put in a hole off the print, the drawing was wrong")
        xml = (b"<?xml version='1.0'?><SETUPSHEET>"
               b"<OPERATION><NAME>1 - Drill</NAME>"
               b"<FEED>250</FEED></OPERATION>"
               b"<OPERATION><NAME>2 - Contour</NAME>"
               b"<FEED>3500</FEED></OPERATION></SETUPSHEET>")
        store.add_attachment(self.first["id"], "setup.xml",
                             io.BytesIO(xml), len(xml))
        store.add_job(name="shaft", customer="ACME", material="steel",
                        notes="vibrated at long stickout")
        store.close()
        # moving house: ONLY the entry folders travel, without chipbook.db
        shutil.copytree(os.path.join(self.old, chipbook.JOBS_DIR),
                        os.path.join(self.new_, chipbook.JOBS_DIR))
        self.catalog = catalog.open_catalog(self.new_)

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.old, ignore_errors=True)
        shutil.rmtree(self.new_, ignore_errors=True)

    def test_a_new_database_is_empty_before_we_restore(self):
        """So that it is visible that the test really restores something, and
        that the jobs did not arrive by themselves."""
        self.assertEqual(self.catalog.job_count(), 0)

    def test_a_dry_run_writes_NOTHING(self):
        """The default run is only to say what it would do. When restoring
        somebody else's data that matters more than convenience."""
        result = self.catalog.rebuild_from_folders()
        self.assertEqual(len(result["added"]), 2)
        self.assertEqual(self.catalog.job_count(), 0)

    def test_the_restore_comes_back_with_the_entry_content(self):
        self.catalog.rebuild_from_folders(dry_run=False)
        self.assertEqual(self.catalog.job_count(), 2)
        names = sorted(entry["name"] for entry in self.catalog.recent())
        self.assertEqual(names, ["bushing LX-88", "shaft"])
        bushing = [entry for entry in self.catalog.recent()
                  if entry["name"] == "bushing LX-88"][0]
        self.assertEqual(bushing["customer"], "ACME")
        self.assertEqual(bushing["material"], "bronze")
        self.assertIn("the drawing was wrong", bushing["notes"])

    def test_the_dates_stay_original(self):
        """An entry from a year ago is to stay an entry from a year ago, not today's."""
        self.catalog.rebuild_from_folders(dry_run=False)
        bushing = [entry for entry in self.catalog.recent()
                  if entry["name"] == "bushing LX-88"][0]
        self.assertEqual(bushing["created_at"], self.first["created_at"])

    def test_attachments_come_back_together_with_their_searchable_content(self):
        self.catalog.rebuild_from_folders(dry_run=False)
        bushing = [entry for entry in self.catalog.recent()
                  if entry["name"] == "bushing LX-88"][0]
        files = self.catalog.attachments(bushing["id"])
        self.assertEqual([p["name"] for p in files], ["setup.xml"])
        self.assertTrue(files[0]["present"], "the file is to lie on disk")
        self.assertIn("3500", files[0]["content"])

    def test_after_the_restore_the_search_works(self):
        """A job in the table alone is not enough - without the index the user
        would not find it and the restore would be a pretence."""
        self.catalog.rebuild_from_folders(dry_run=False)
        self.assertEqual(len(self.catalog.search("hole print")), 1)
        self.assertEqual(len(self.catalog.search("3500")), 1)

    def test_a_second_run_duplicates_nothing(self):
        """Somebody will run this twice to be sure. It is to do no harm."""
        self.catalog.rebuild_from_folders(dry_run=False)
        result = self.catalog.rebuild_from_folders(dry_run=False)
        self.assertEqual(result["added"], [])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertEqual(self.catalog.job_count(), 2)

    def test_a_new_entry_does_not_enter_somebody_elses_folder(self):
        """This is the description of a bug that really was here.

        The folder name is the date and the number of the entry. An empty
        database on a new computer numbers from 1 - that is, it gives exactly
        the names that already lie on the disk, brought over from the
        previous computer. An entry added BEFORE the user runs the restore
        went into somebody else's folder and overwrote its job.txt, which is
        the only road back. The test below found it: the count of entries did
        not add up.
        """
        self.catalog.add_job(name="something of my own", customer="X",
                             material="steel", notes="mine")
        directory = os.path.join(self.new_, chipbook.JOBS_DIR)
        self.assertEqual(len(os.listdir(directory)), 3,
                         "a new job was to get ITS OWN folder")
        with open(os.path.join(directory, self.first["folder"], "job.txt"),
                  encoding="utf-8") as file:
            self.assertIn("bushing LX-88", file.read(),
                          "the job.txt brought over was overwritten")

    def test_existing_entries_stay_untouched(self):
        """Restoring into a database that already holds something has no right
        to overwrite anything - the user's data is worth more than the
        convenience of the tool."""
        own_entry = self.catalog.add_job(name="something of my own", customer="X",
                                      material="steel", notes="mine")
        self.catalog.rebuild_from_folders(dry_run=False)
        still_there = self.catalog.job(own_entry["id"])
        self.assertEqual(still_there["name"], "something of my own")
        self.assertEqual(still_there["notes"], "mine")
        self.assertEqual(self.catalog.job_count(), 3)

    def test_a_broken_file_skips_one_entry_and_not_the_whole_job(self):
        """A hundred entries must not be lost over one damaged file."""
        directory = os.path.join(self.new_, chipbook.JOBS_DIR)
        broken = sorted(os.listdir(directory))[0]
        with open(os.path.join(directory, broken, "job.txt"), "w",
                  encoding="utf-8") as file:
            file.write("this is not a chipbook job\n")
        result = self.catalog.rebuild_from_folders(dry_run=False)
        self.assertEqual(result["errors"], [broken])
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(self.catalog.job_count(), 1)


class AllJobsTest(unittest.TestCase):
    """The list is called "All jobs", so it is to show them all. It used to
    break off at 50 and the label would be untrue on a bigger database."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook_alljobs_")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        try:
            self.catalog.close()
        except Exception:
            pass
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_the_recent_ones_with_no_limit_give_back_everything(self):
        for number in range(60):
            self.catalog.add_job(name="job %d" % number, customer="test",
                                 material="steel", notes="something")
        self.assertEqual(len(self.catalog.recent()), 60)
        self.assertEqual(self.catalog.job_count(), 60)

    def test_the_limit_still_works_when_somebody_gives_one(self):
        for number in range(10):
            self.catalog.add_job(name="job %d" % number, customer="test",
                                 material="steel", notes="something")
        self.assertEqual(len(self.catalog.recent(3)), 3)


class ModelTextTest(unittest.TestCase):
    """The core hands back the text of a setup sheet ASSEMBLED FOR THE MODEL.

    These tests watch the boundary, not the assembling - the assembling has
    its own tests in test_setupsheet.py. Here we check that the function takes
    the same road as the index (main files only) and that an absence is
    never a failure.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-model-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _file(self, name, data):
        path = os.path.join(self.directory, name)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def test_a_setup_sheet_gives_text_with_labels(self):
        path = self._file("setup.xml",
                             b"<?xml version='1.0'?><SETUPSHEET>"
                             b"<GENERAL><PROJECT>Shaft 11</PROJECT></GENERAL>"
                             b"</SETUPSHEET>")
        text = attachments.text_for_model(path)
        self.assertIn("PROJECT: Shaft 11", text)

    def test_a_clarification_is_appended_to_a_bare_label(self):
        """Measured: a model asked about the allowance answered about WORK OFFSET
        instead of STOCK TO LEAVE - it did not know which field goes by that
        name. We append that for it instead of asking it to guess."""
        path = self._file("setup.xml",
                             b"<?xml version='1.0'?><SETUPSHEET>"
                             b"<OPERATION><NAME>op1</NAME>"
                             b"<STOCK-TO-LEAVE>1.0</STOCK-TO-LEAVE></OPERATION>"
                             b"<OPERATION><NAME>op2</NAME>"
                             b"<STOCK-TO-LEAVE>0.0</STOCK-TO-LEAVE></OPERATION>"
                             b"</SETUPSHEET>")
        text = attachments.text_for_model(path)
        self.assertIn("STOCK-TO-LEAVE", text)   # the original stays

    def test_a_clarification_does_not_wipe_the_label_from_the_file(self):
        """We do not substitute - we APPEND. The label from the file is the one
        a person sees in the CAM system, so it has to stay in place."""
        self.assertIn("WORK OFFSET", attachments.FIELD_NOTES)
        self.assertEqual(attachments.FIELD_NOTES["WORK OFFSET"], "part zero")
        self.assertEqual(
            render._with_translation("WORK OFFSET", attachments.FIELD_NOTES),
            "WORK OFFSET (part zero)")

    def test_a_label_outside_the_lexicon_stays_untouched(self):
        self.assertEqual(
            render._with_translation("MFG CODE", attachments.FIELD_NOTES), "MFG CODE")

    def test_the_chamfer_mill_stands_in_the_text_for_the_model(self):
        """A REGRESSION ALONG THE WHOLE ROAD, not on the lexicon alone: from
        the tool block right through to the text the model gets."""
        description = {"blocks": [{
            "kind": "table", "title": "TOOL INFO",
            "columns": ["TOOL INFO", "NUMBER", "TYPE", "DIAMETER"],
            "rows": [["17 Chamfer Mill", "25", "Chamfer mill", "17.0"]],
        }]}
        text = setupsheet.as_text(description, attachments.FIELD_NOTES)
        self.assertIn("Chamfer mill", text)
        self.assertIn("Chamfer mill", text)      # the original stays

    def test_tool_material_is_marked_as_THE_TOOLS(self):
        """CAUGHT IN USE. A setup sheet does not hold the PART material at all
        (measured) - the only MATERIAL in the whole file is the material of
        the cutting edge. A model asked "what was that tray made of" reached
        for "Carbide" and had SUPPORT for it in the text, so even the quote
        check would not have caught the error.

        THREE TITLES, NOT ONE: a PDF from the CAM system gives a "TOOL INFO"
        block, the same setup sheet in XML gives "TOOL" (TOOL_BLOCK_TITLES in
        setupsheet/). One key would work on the PDFs and stay silent on the
        XMLs."""
        for title in ("TOOL INFO", "TOOL LIST", "TOOL"):
            description = {"blocks": [{
                "kind": "pairs", "title": title,
                "pairs": [["MATERIAL", "Carbide"]],
            }]}
            text = setupsheet.as_text(description, attachments.FIELD_NOTES)
            self.assertIn("TOOL material", text, title)
            self.assertIn("not part material", text, title)
            self.assertIn("Carbide", text, title)   # the original stays

    def test_material_outside_a_tool_block_stays_ordinary(self):
        """THE OTHER SIDE OF THE SAME THING, and more important than it looks:
        the fix is to work ONLY inside a tool block. Were it to spill over
        the MATERIAL label as a whole, the program would start talking about
        the tool where something else is meant - that is, we would swap one
        false description for another."""
        description = {"blocks": [{
            "kind": "pairs", "title": "STOCK",
            "pairs": [["MATERIAL", "aluminium"]],
        }]}
        text = setupsheet.as_text(description, attachments.FIELD_NOTES)
        self.assertIn("MATERIAL: aluminium", text)
        self.assertNotIn("TOOL material", text)

    def test_the_tool_number_cannot_be_confused_with_the_diameter(self):
        """THE HEART OF THE BUG. A model asked for the tool number answered
        "17" - because the name "17 Chamfer Mill" starts with the DIAMETER,
        and the real number (25) stood lines below under the bare English
        label `NUMBER`, with no word of explanation.

        The test watches TWO things at once: that the name warns what it
        starts with, and that the number is named as the tool number."""
        description = {"blocks": [{
            "kind": "table", "title": "TOOL INFO",
            "columns": ["TOOL INFO", "NUMBER", "DIAMETER"],
            "rows": [["17 Chamfer Mill", "25", "17.0"]],
        }]}
        lines = setupsheet.as_text(description, attachments.FIELD_NOTES).splitlines()
        name = [entry for entry in lines if entry.strip().startswith("TOOL INFO (")][0]
        number = [entry for entry in lines if entry.strip().startswith("NUMBER (")][0]
        self.assertIn("DIAMETER", name.upper())
        self.assertIn("TOOL NUMBER", number.upper())
        self.assertTrue(number.strip().endswith("25"))

    def test_a_number_in_another_section_does_not_lie(self):
        """The reverse of the previous one: `NUMBER` in an offset block must
        NOT be described as a tool number."""
        description = {"blocks": [{
            "kind": "table", "title": "OFFSET INFO",
            "columns": ["NUMBER"], "rows": [["0"]],
        }]}
        text = setupsheet.as_text(description, attachments.FIELD_NOTES)
        self.assertIn("offset number", text)
        self.assertNotIn("magazine", text)

    def test_gcode_goes_to_the_model_AS_NUMBERS(self):
        """CHANGED. Until then this test watched that G-code did NOT go to the
        model at all - and because of that the model did not even know the
        file existed. That was the original complaint.

        THE BOUNDARIES HAVE TO BE SEPARATED HERE, because the old
        description glued them together:
          - G-code still does not enter the SEARCH,
          - what enters the MODEL are NUMBERS about it, never the content.
        The reason for that second boundary is measured: a real file has
        8438 lines, that is about 66 000 tokens against a window of 4096."""
        path = self._file("program.nc",
                             b"O1234\nT2 M6\nG0 X0 Y0 S1000\nG1 Z-1 F100\n")
        text = attachments.text_for_model(path)
        self.assertIn("NC PROGRAM", text)
        self.assertIn("4 lines", text)
        self.assertNotIn("G1 Z-1", text)      # the content does NOT go

    def test_gcode_STILL_does_not_enter_the_search(self):
        """This holds unchanged. We loosened the road to the model, not the
        road to the index - and those two must not be glued together."""
        path = self._file("program.nc", b"G0 X0 Y0\nG1 Z-1 F100\n")
        self.assertEqual(attachments.text_for_search(path), "")

    def test_a_cam_project_file_is_just_kept(self):
        path = self._file("part.mcam", b"\x00\x01binary\x02")
        self.assertEqual(attachments.text_for_model(path), "")

    def test_a_broken_file_is_empty_text_and_not_a_failure(self):
        """The entry has to be saved even when the attachment cannot be broken
        out - the same as with the index."""
        path = self._file("setup.xml", b"<?xml version='1.0'?><SETUP")
        self.assertEqual(attachments.text_for_model(path), "")

    def test_a_file_that_does_not_exist_does_not_topple_it_either(self):
        missing = os.path.join(self.directory, "not-here.pdf")
        self.assertEqual(attachments.text_for_model(missing), "")

    def test_the_text_for_the_model_has_a_fuse_of_its_own(self):
        """Something else limits it than limits the index: not room in the
        database, but how much the model sees at once. Hence a constant of
        its own."""
        self.assertTrue(attachments.MAX_MODEL_TEXT < attachments.MAX_INDEX_TEXT)

    def test_it_is_assembled_differently_from_the_text_for_the_search(self):
        """These are TWO different texts out of one file. The index gets a flat
        list, the model gets labels beside the values."""
        path = self._file("setup.xml",
                             b"<?xml version='1.0'?><SETUPSHEET>"
                             b"<GENERAL><PROJECT>Shaft 11</PROJECT></GENERAL>"
                             b"</SETUPSHEET>")
        for_model = attachments.text_for_model(path)
        for_index = attachments.text_for_search(path)
        self.assertNotEqual(for_model, for_index)
        self.assertIn("PROJECT: Shaft 11", for_model)
        self.assertNotIn("PROJECT: Shaft 11", for_index)


class GcodeFactsTest(unittest.TestCase):
    """An NC program described by numbers rather than handed over as content.

    THIS CLOSES THE ORIGINAL COMPLAINT: "after adding a gcode file the ai
    does not answer questions, for example how many lines the gcode has".
    Until then `text_for_model` let through .pdf and .xml only, so the NC
    program was INVISIBLE to the model.

    MEASURED ON A REAL FILE: 8438 lines, 136 KB, about 66 000 tokens against
    a window of 4096. The content can never be handed over.
    """

    PROGRAM = (
        "%\n"
        "O1234\n"
        "G21\n"
        "T2 M6\n"
        "G0 G90 G54 X0 Y0 S1150 M3\n"
        "G43 H2 Z10. M8\n"
        "G99 G83 Z-15. R10. Q3.5 F75.\n"
        "T12 M6\n"
        "S7500 M3\n"
        "G1 X10. F550.\n"
        "T12\n"                       # an announcement, NOT a tool change
        "M5 M9\n"
        "M30\n"
        "%\n"
    )

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-gcode-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _file(self, name="program.nc", content=None):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="ascii") as file:
            file.write(self.PROGRAM if content is None else content)
        return path

    def test_the_line_count_is_counted_and_not_guessed(self):
        """The question word for word. Models count badly - the program counts."""
        text = attachments.describe_gcode(self._file())
        self.assertIn("14 lines", text)

    def test_THE_PROGRAM_CONTENT_DOES_NOT_GO_TO_THE_MODEL(self):
        """THE HEART OF IT. Were it to go, one file would eat sixteen windows."""
        text = attachments.describe_gcode(self._file())
        self.assertNotIn("G43", text)
        self.assertNotIn("X10.", text)
        self.assertLess(len(text), 600)

    def test_tools_only_from_a_real_tool_change(self):
        """`T12 M6` is a tool change, `T12` alone announces the next one.
                Counting both would give tools that never entered the spindle."""
        text = attachments.describe_gcode(self._file())
        self.assertIn("no. 2", text)
        self.assertIn("no. 12", text)
        self.assertEqual(text.count("no. 12"), 1)

    def test_speeds_and_feeds_without_trailing_zeros(self):
        text = attachments.describe_gcode(self._file())
        self.assertIn("1150", text)
        self.assertIn("75, 550", text)
        self.assertNotIn("75.0", text)

    def test_we_speak_about_coolant_CAREFULLY(self):
        """The program shows that coolant IS SWITCHED ON (M8), but not WHICH.
        The sentence has to tell those apart, otherwise the model will answer
        "what coolant did I use" with something the file does not hold."""
        text = attachments.describe_gcode(self._file())
        self.assertIn("TURNED ON", text)
        self.assertIn("Which coolant exactly", text)

    def test_no_coolant_adds_no_sentence(self):
        without = self.PROGRAM.replace(" M8", "").replace(" M9", "")
        text = attachments.describe_gcode(self._file(content=without))
        self.assertNotIn("coolant", text.lower())

    def test_gcode_enters_the_text_for_the_model(self):
        """Only .pdf and .xml used to be let through - that is the line the
        model did not know the file existed because of."""
        text = attachments.text_for_model(self._file())
        self.assertIn("NC PROGRAM", text)

    def test_an_ordinary_txt_is_NOT_treated_as_a_program(self):
        """A .txt or .csv would give empty lists pretending to be a measurement."""
        path = os.path.join(self.directory, "note.txt")
        with open(path, "w", encoding="ascii") as file:
            file.write("anything\n")
        self.assertEqual(attachments.text_for_model(path), "")

    def test_an_unreadable_file_is_an_empty_description_not_a_failure(self):
        self.assertEqual(
            attachments.describe_gcode(os.path.join(self.directory, "no such file")),
            "")

    def test_an_empty_program_does_not_pretend_to_be_a_measurement(self):
        self.assertEqual(attachments.describe_gcode(self._file(content="")), "")


class CustomersTest(unittest.TestCase):
    """The Customers tab.

    A list of every customer; picking one shows their jobs only.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-customers-")
        self.catalog = catalog.open_catalog(self.directory)
        for name, customer in (("strainer", "Delta"), ("tray", "ACME"),
                              ("bushing", "ACME"), ("shaft", "acme"),
                              ("wheel", "Zeta"), ("bracket", "ACEM")):
            self.catalog.add_job(name=name, customer=customer,
                                 material="steel", notes="x")

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _count_for(self, name):
        for item in self.catalog.customers():
            if item["customer"] == name:
                return item["count"]
        return None

    def test_the_list_is_alphabetical(self):
        names = [p["customer"] for p in self.catalog.customers()]
        self.assertEqual(names, sorted(names, key=str.lower))

    def test_letter_case_does_NOT_make_a_second_customer(self):
        """CAUGHT AT THE FIRST CHECK: the list said "ACME 2" and "acme 1", and
        clicking either gave THREE jobs. The number beside the name would be
        lying against what is seen after the click - and that is worse than
        no number."""
        names = [p["customer"] for p in self.catalog.customers()]
        self.assertEqual(sum(1 for n in names if n.lower() == "acme"), 1)
        self.assertEqual(self._count_for("ACME"), 3)

    def test_the_number_matches_what_the_click_gives(self):
        """The only promise this list makes: the number beside the name is how
        many you will see after clicking it."""
        for item in self.catalog.customers():
            self.assertEqual(item["count"],
                             len(self.catalog.jobs_for_customer(item["customer"])),
                             item["customer"])

    def test_A_TYPO_STAYS_SEPARATE(self):
        """"ACEM" is not "ACME" and must not be merged with it. This is data a
                person typed by hand; correcting it quietly would be guesswork. The
                list is to SHOW them side by side - "ACME 3" next to "ACEM 1" says
                outright where the mistake is."""
        self.assertEqual(self._count_for("ACEM"), 1)
        self.assertEqual(self._count_for("ACME"), 3)

    def test_a_customers_entries_newest_first(self):
        jobs = self.catalog.jobs_for_customer("ACME")
        self.assertEqual([entry["name"] for entry in jobs],
                         ["shaft", "bushing", "tray"])

    def test_an_empty_customer_does_not_return_the_whole_database(self):
        """A gate against a mistake on the window side: no choice is to mean
        "nothing", not "everything"."""
        self.assertEqual(self.catalog.jobs_for_customer(""), [])
        self.assertEqual(self.catalog.jobs_for_customer(None), [])
        self.assertEqual(self.catalog.jobs_for_customer("   "), [])

    def test_an_unknown_customer_is_an_empty_list_and_not_a_failure(self):
        self.assertEqual(self.catalog.jobs_for_customer("no such customer"), [])

    def test_an_entry_with_no_customer_makes_no_empty_item(self):
        """The customer is obligatory, but the database may come from an older
        version or from a restore out of the folders."""
        changed = self.catalog.con.execute(
            "UPDATE job SET customer='' WHERE name='wheel'").rowcount
        self.catalog.con.commit()
        self.assertEqual(changed, 1, "the test cleared no customer at all")
        self.assertNotIn("", [p["customer"] for p in self.catalog.customers()])


class AttachmentDeletionTest(unittest.TestCase):
    """Deleting ONE file from an entry.

    Until now the whole job could be removed, or nothing - to throw out one
    wrongly added file, the job had to be deleted along with its notes.

    THERE ARE MANY TESTS HERE, BECAUSE THIS IS A ROAD THAT TOUCHES SOMEBODY
    ELSE'S FILES.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-attach-delete-")
        self.catalog = catalog.open_catalog(self.directory)
        self.job = self.catalog.add_job(
            name="tray", customer="ACME", material="steel",
            notes="vibrated at 3000")

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _add(self, name="setup.xml", data=None):
        data = data or (b"<?xml version='1.0'?><SETUPSHEET>"
                        b"<TOOL><TYPE>Chamfer mill</TYPE>"
                        b"<NUMBER>25</NUMBER></TOOL></SETUPSHEET>")
        return self.catalog.add_attachment(self.job["id"], name,
                                         io.BytesIO(data), len(data))

    def test_the_file_leaves_the_entry(self):
        attached = self._add()
        self.catalog.delete_attachment(attached["id"])
        self.assertEqual(self.catalog.attachments(self.job["id"]), [])

    def test_THE_ENTRY_AND_THE_NOTES_STAY(self):
        """An entry with no file has been legal since the first day. The value
        is the notes, not the attachment - and deleting a file has no right
        to touch a sentence a person wrote."""
        attached = self._add()
        self.catalog.delete_attachment(attached["id"])
        job = self.catalog.job(self.job["id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["notes"], "vibrated at 3000")

    def test_the_file_IS_NOT_DELETED_from_the_disk(self):
        """WE DELETE NOTHING FOR GOOD. The system Recycle Bin, and where there
        is none - a _deleted directory. The file has to be recoverable."""
        attached = self._add()
        result = self.catalog.delete_attachment(attached["id"])
        self.assertIn(result["where"], ("recycle_bin", "moved"))
        if result["where"] == "moved":
            self.assertTrue(os.path.exists(result["path"]))

    def test_the_content_leaves_the_search(self):
        """Were it to stay, the user would be finding a job by words from a
        file that is no longer there - with no way to check where they came
        from."""
        attached = self._add()
        self.assertTrue(self.catalog.search("chamfer").jobs)
        self.catalog.delete_attachment(attached["id"])
        self.assertFalse(self.catalog.search("chamfer").jobs)

    def test_the_other_files_stay_untouched(self):
        first = self._add("one.xml")
        second = self._add("two.xml")
        self.catalog.delete_attachment(first["id"])
        remaining = self.catalog.attachments(self.job["id"])
        self.assertEqual([att["id"] for att in remaining], [second["id"]])
        self.assertTrue(os.path.exists(remaining[0]["path"]))

    def test_a_nonexistent_file_is_a_readable_error_not_a_crash(self):
        with self.assertRaises(chipbook.ChipbookError):
            self.catalog.delete_attachment(999999)

    def test_a_missing_file_on_disk_does_not_topple_the_deletion(self):
        """The file may have vanished earlier - it still has to leave the database."""
        attached = self._add()
        os.remove(attached["path"])
        result = self.catalog.delete_attachment(attached["id"])
        self.assertEqual(result["where"], "no_file")
        self.assertEqual(self.catalog.attachments(self.job["id"]), [])


class ToolListForModelTest(unittest.TestCase):
    """A ready tool list handed to the model as a FACT.

    WHY THIS EXISTS: one model answered "no" three runs in a row to the
    question "did I use a chamfer mill", with two rows in the text reading
    `TYPE: Chamfer mill`. On the third run it quoted that very line while
    claiming it was not there. Three corrections to the instruction changed
    nothing.

    WHY THERE ARE MANY TESTS HERE: this is the only place where the program
    tells the model "this is so" instead of showing it the raw file. A
    mistake in the count is not a model's slip but a LIE delivered with
    conviction.
    """

    def _description(self, rows, columns=("TOOL INFO", "NUMBER", "TYPE",
                                      "DIAMETER"), operations_line=0):
        blocks = [{"kind": "table", "title": "TOOL INFO",
                  "columns": list(columns), "rows": [list(entry)
                                                        for entry in rows]}]
        if operations_line:
            blocks.append({"kind": "table", "title": "OPERATION INFO",
                          "columns": ["OPERATION INFO"],
                          "rows": [["%d - op" % (i + 1)]
                                      for i in range(operations_line)]})
        return {"blocks": blocks}

    def test_a_second_copy_FILLS_IN_the_tool_instead_of_losing_it(self):
        """CAUGHT BY OUR OWN CHECK, before it went to measurement. A setup
        sheet prints a TOOL INFO block at EVERY operation (with no USED BY
        OPERATION line), and only the summary list at the end carries those
        lines. The first version took the first copy and stopped - and the
        assignment to an operation did not appear at all, though it stood in
        the file. A tool is still to be counted ONCE, but the richer copy is
        to fill it in."""
        description = {"blocks": [
            {"kind": "table", "title": "TOOL INFO",
             "columns": ["TOOL INFO", "NUMBER", "TYPE", "DIAMETER"],
             "rows": [["17 Chamfer Mill", "25", "Chamfer mill", "17.0"]]},
            {"kind": "table", "title": "TOOL INFO",
             "columns": ["TOOL INFO", "NUMBER", "TYPE", "DIAMETER",
                         "USED BY OPERATION"],
             "rows": [["17 Chamfer Mill", "25", "Chamfer mill", "17.0",
                          "# 4 4 - Contour (2D chamfer)"]]}]}
        text = attachments.describe_tool_list(description)
        self.assertIn("4 - Contour (2D chamfer)", text)
        self.assertIn("TOTAL: 1 tool", text)

    def test_the_tool_type_stands_in_the_listing_as_in_the_file(self):
        text = attachments.describe_tool_list(self._description(
            [["17 Chamfer Mill", "25", "Chamfer mill", "17.0"]]))
        self.assertIn("Chamfer mill", text)
        self.assertIn("no. 25", text)
        self.assertIn("17.0", text)

    def test_every_tool_counted_ONCE(self):
        """THE HEART OF IT. A setup sheet prints a TOOL INFO block at EVERY
        operation, so one tool used in four operations gives four blocks.
        Measured on a real report: four blocks, all of them tool no. 1.
        Without merging, the answer would be too high."""
        four_times_the_same = [["10 Ball", "1", "Ball endmill", "10.0"]] * 4
        text = attachments.describe_tool_list(
            self._description(four_times_the_same, operations_line=4))
        self.assertIn("TOTAL: 1 tool", text)
        self.assertIn("4 operations", text)

    def test_different_tools_are_not_merged(self):
        text = attachments.describe_tool_list(self._description([
            ["6 Drill", "2", "Drill", "6.0"],
            ["17 Chamfer Mill", "25", "Chamfer mill", "17.0"],
        ]))
        self.assertIn("no. 2", text)
        self.assertIn("no. 25", text)
        self.assertIn("TOTAL: 2 different tools", text)

    def test_a_type_outside_the_lexicon_stays_as_it_is(self):
        """We do not invent a name we do not know."""
        text = attachments.describe_tool_list(self._description(
            [["8 Woodruff", "3", "Woodruff cutter", "8.0"]]))
        self.assertIn("Woodruff cutter", text)

    def test_no_tools_is_an_empty_listing_and_not_an_invented_one(self):
        self.assertEqual(attachments.describe_tool_list({"blocks": []}), "")
        self.assertEqual(attachments.describe_tool_list({}), "")

    def test_at_which_operation_the_tool_worked(self):
        """Asked "at which position did I have the chamfer mill NOT FOR HOLES"
        a model pointed three runs in a row at no. 16 - that is, the one FOR
        HOLES. The answer needs the tool block joined with the USED BY
        OPERATION line, and joining two sections is something this model does
        not do. So the program joins them."""
        text = attachments.describe_tool_list(self._description(
            [["10 Chamfer Mill", "16", "Chamfer mill", "10.0",
              "# 3 3 - Chamfer Drill"],
             ["17 Chamfer Mill", "25", "Chamfer mill", "17.0",
              "# 4 4 - Contour (2D chamfer)"]],
            columns=("TOOL INFO", "NUMBER", "TYPE", "DIAMETER",
                     "USED BY OPERATION")))
        self.assertIn("no. 16, Chamfer mill, diameter 10.0 - used on "
                      "operation: 3 - Chamfer Drill", text)
        self.assertIn("no. 25, Chamfer mill, diameter 17.0 - used on "
                      "operation: 4 - Contour (2D chamfer)", text)

    def test_a_tool_at_TWO_operations_carries_both(self):
        """A tool used twice with one operation written out would be a fact
        counted WRONG - and that is worse than no fact."""
        text = attachments.describe_tool_list(self._description(
            [["10 Ball", "1", "Ball endmill", "10.0",
              "# 1 1 - Peck Drill # 2 2 - Contour (2D)"]],
            columns=("TOOL INFO", "NUMBER", "TYPE", "DIAMETER",
                     "USED BY OPERATION")))
        self.assertIn("1 - Peck Drill; 2 - Contour (2D)", text)

    def test_a_missing_field_appends_no_tail(self):
        """A setup sheet without that line is to give a listing exactly as it
        gave yesterday - otherwise the change would touch files it does not
        concern."""
        text = attachments.describe_tool_list(self._description(
            [["6 Drill", "2", "Drill", "6.0"]]))
        self.assertIn("  no. 2, Drill, diameter 6.0\n", text)
        self.assertNotIn("used on operation", text.split("TOTAL")[0])

    def test_the_switch_switches_it_off(self):
        """The way back is to be free: one constant, and the text goes back
        to what it was before the operation line was added."""
        description = self._description(
            [["17 Chamfer Mill", "25", "Chamfer mill", "17.0",
              "# 4 4 - Contour (2D chamfer)"]],
            columns=("TOOL INFO", "NUMBER", "TYPE", "DIAMETER",
                     "USED BY OPERATION"))
        previous = attachments.APPEND_OPERATIONS
        attachments.APPEND_OPERATIONS = False
        try:
            text = attachments.describe_tool_list(description)
        finally:
            attachments.APPEND_OPERATIONS = previous
        self.assertNotIn("used on operation", text)
        self.assertIn("no. 25, Chamfer mill, diameter 17.0", text)

    def test_the_running_number_does_not_pose_as_the_operation_number(self):
        """The field holds "# 4 4 - ...": the first figure is the CAM system's
        running number, the second is the operation number. Giving the first
        would be a quiet mistake in every file where those two differ."""
        self.assertEqual(attachments._tool_operations("# 7 4 - Contour (2D)"),
                         "4 - Contour (2D)")
        self.assertEqual(attachments._tool_operations(""), "")
        self.assertEqual(attachments._tool_operations("no hash"), "")

    def test_count_uses_the_right_form(self):
        """"4 operation" reads like a bug and undermines trust in a
        sentence meant to be a fact for the model."""
        plural = attachments._plural
        self.assertEqual(plural(1, "operation", "operations"), "1 operation")
        self.assertEqual(plural(4, "operation", "operations"), "4 operations")
        self.assertEqual(plural(0, "operation", "operations"), "0 operations")

    def test_the_listing_goes_AT_THE_START_of_the_text_for_the_model(self):
        """When the text is cut at MAX_MODEL_TEXT, what has to survive is a
        finished answer, not the tail of a table of feed rates."""
        directory = tempfile.mkdtemp(prefix="chipbook-test-toollist-")
        try:
            path = os.path.join(directory, "setup.xml")
            with open(path, "wb") as file:
                file.write(b"<?xml version='1.0'?><SETUPSHEET>"
                           b"<TOOL><TOOL-INFO>17 Chamfer Mill</TOOL-INFO>"
                           b"<NUMBER>25</NUMBER><TYPE>Chamfer mill</TYPE>"
                           b"</TOOL></SETUPSHEET>")
            text = attachments.text_for_model(path)
            self.assertTrue(text.startswith("TOOLS USED"), text[:80])
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class AnswerGroundingTest(unittest.TestCase):
    """A SIEVE AGAINST INVENTION.

    The program checks the model's answer BEFORE showing it. A number that
    is not in the text supplied means invention - and has no right to reach
    a person's eyes.

    THE MEASURED INVENTIONS THIS WAS BUILT FOR: one model invented a time of
    29:43 that was nowhere in the file, and gave it TWICE identically at
    temperature zero. Another invented "14 entries" with five in the
    database.
    """

    def test_a_number_from_outside_the_text_has_no_support(self):
        self.assertEqual(grounding.unsupported_numbers("12 holes", "allowance 1.0 mm"),
                         ["12"])

    def test_a_comma_and_a_full_stop_are_the_same_number(self):
        """A model writes with a comma, the CAM system with a full stop, and
        neither of them is wrong. Without this the program would be rejecting
        THE TRUTH."""
        self.assertEqual(grounding.unsupported_numbers("1,0 mm", "STOCK TO LEAVE: 1.0"),
                         [])
        self.assertEqual(grounding.unsupported_numbers("1.0 mm", "allowance 1,0"), [])

    def test_a_refusal_always_passes(self):
        """Were a refusal subject to checking, the program would be punishing
        the model for exactly what we ask of it in prompt.txt: to admit an
        absence instead of guessing."""
        self.assertEqual(grounding.unsupported_numbers("That is not in the entry.", "x"), [])
        self.assertEqual(grounding.unsupported_numbers("I do not have that in this entry.", "x"),
                         [])

    def test_an_answer_with_no_numbers_is_checked_by_word(self):
        self.assertEqual(grounding.unsupported_numbers("Of birchwood", "material birch"),
                         [])
        self.assertEqual(grounding.unsupported_numbers("Of birchwood", "material aluminium"),
                         ["birchwood"])


class StockFactsTest(unittest.TestCase):
    """MEASURED ON A RENTED CARD, 3 runs out of 3.

    The question "what was the stock and what size was it" got the answer
    "the information provided does not contain details of the stock" - with
    a text holding SHAPE: Cylinder, SIZE: 330, 15 and STOCK: YES.

    THIS IS THE WORST KIND OF ERROR IN THIS PROJECT: the model says "I do
    not have it" with the data in front of it. The user will believe it and
    go looking elsewhere. So the program supplies a finished sentence
    instead of counting on the model to connect three separate fields - the
    same road as with the tool list and the number of entries in the
    database.
    """

    def description(self, pairs):
        return {"blocks": [{"title": "STOCK", "kind": "pairs", "pairs": pairs}]}

    def test_the_shape_and_the_size_go_in_one_sentence(self):
        sentence = attachments.describe_stock(self.description(
            [["STOCK", "YES"], ["SHAPE", "Cylinder"], ["SIZE", "330, 15"]]))
        self.assertIn("THE STOCK", sentence)
        self.assertIn("Cylinder", sentence)
        self.assertIn("Cylinder", sentence)       # the value exactly as in the file
        self.assertIn("330, 15", sentence)

    def test_no_block_means_no_sentence_and_not_guesswork(self):
        """When the file cannot be read or the block is not there, the program
        SAYS NOTHING. An invented sentence would be worse than its absence -
        the model would be handed a lie presented as a fact."""
        self.assertEqual(attachments.describe_stock({"blocks": []}), "")
        self.assertEqual(attachments.describe_stock(self.description(
            [["AXIS", "Z"]])), "")

    def test_the_shape_alone_without_the_size_also_goes_in(self):
        sentence = attachments.describe_stock(self.description([["SHAPE", "Box"]]))
        self.assertIn("Box", sentence)
        self.assertNotIn("size", sentence)

    def test_stock_to_leave_is_NOT_the_stock(self):
        """The trap this went wrong through in the first place: next to the
        STOCK block stand four STOCK TO LEAVE lines. Those are two different
        things and the program has no right to confuse them."""
        sentence = attachments.describe_stock(
            {"blocks": [{"title": "OPERATION INFO", "kind": "pairs",
                        "pairs": [["STOCK TO LEAVE", "0.0"]]}]})
        self.assertEqual(sentence, "")

    def test_the_sentence_stands_AT_THE_START_of_the_text_for_the_model(self):
        """When the text is cut at MAX_MODEL_TEXT, what has to survive is a
        finished answer, not the tail of a table of feed rates."""
        record = {"name": "x", "customer": "y", "material": "z",
                  "notes": "", "order_number": ""}
        del record                              # for readability only
        description = self.description([["SHAPE", "Cylinder"], ["SIZE", "330, 15"]])
        sentence = attachments.describe_stock(description)
        self.assertTrue(sentence.startswith("THE STOCK"))


class TrimToQuestionTest(unittest.TestCase):
    """A FUSE FOR A LARGE SETUP SHEET.

    The choice made: we cut ONLY after a threshold is crossed. Below the
    threshold the text goes whole, as it went before - thanks to that not
    ONE byte changes on the files that lie on disk today, and no new road
    opens to a false "I do not have this in this entry".

    WHY A THRESHOLD AND NOT ALWAYS CUTTING - CONFIRMED BY MEASUREMENT on a
    rented card: a real setup sheet together with the instruction is 2 050
    tokens, that is EXACTLY 50% of a 4096 window. The ceiling this was meant
    to save us from IS NOT THERE on these files. Always cutting would
    therefore be paying with risk for a problem that does not arise today.

    CUTTING IS ELIMINATION, and elimination is the search's job. Hence two
    gates in the test: the head of the entry always stays, and no hits CUT
    NOTHING.
    """

    def assemble(self, how_many_blocks, length=900):
        """Text of a known length, built like a real one - a head and numbered
        blocks [OPERATION INFO n of m]."""
        lines = ["=== JOB 7: tray ===",
                 "Part material: aluminium",
                 "Technologist's notes:",
                 "packed it up on parallels, the vice jaws were too low",
                 "TOOLS USED ON THIS JOB (computed by the program):",
                 "  no. 1 - end mill dia 10"]
        for number in range(1, how_many_blocks + 1):
            lines.append("[OPERATION INFO %d of %d]" % (number, how_many_blocks))
            lines.append("  operation name: operation no. %d" % number)
            if number == 1:
                lines.append("  STOCK TO LEAVE (allowance): 1.0")
            else:
                lines.append("  feedrate: %d00 mm/min" % (number + 20))
            lines.append("  filler: " + "x" * length)
            lines.append("")
        return "\n".join(lines)

    def test_below_the_threshold_the_text_comes_back_byte_for_byte(self):
        """THE MOST IMPORTANT TEST IN THIS CLASS. The whole promise reads: on
        today's files nothing changes."""
        text = self.assemble(4, length=200)
        self.assertLess(len(text), grounding.TRIM_THRESHOLD_CHARS)
        self.assertEqual(grounding.trim_to_question(text, "what allowance"),
                         text)

    def test_above_the_threshold_the_head_of_the_entry_always_stays(self):
        text = self.assemble(16)
        self.assertGreater(len(text), grounding.TRIM_THRESHOLD_CHARS)
        result = grounding.trim_to_question(text, "what allowance")
        self.assertIn("=== JOB 7: tray ===", result)
        self.assertIn("Part material: aluminium", result)
        self.assertIn("packed it up on parallels", result)
        self.assertIn("TOOLS USED ON THIS JOB", result)

    def test_above_the_threshold_a_block_without_a_word_from_the_question_drops_out(self):
        text = self.assemble(16)
        result = grounding.trim_to_question(text, "what allowance")
        self.assertIn("STOCK TO LEAVE (allowance): 1.0", result)
        self.assertIn("[OPERATION INFO 1 of 16]", result)
        self.assertNotIn("[OPERATION INFO 7 of 16]", result)
        self.assertLess(len(result), len(text))

    def test_another_form_of_a_word_catches_the_block(self):
        """A question carries a word in whatever form the person happened to
        use; the CAM system prints one fixed form. Without this the program
        would be punishing the user for asking in a sentence - the same bug
        caught by the test on quote checking."""
        text = self.assemble(16)
        result = grounding.trim_to_question(text, "what were the allowances")
        self.assertIn("STOCK TO LEAVE (allowance): 1.0", result)

    def test_no_matching_block_cuts_nothing(self):
        """A cut that removes everything is not a cut but a blindfold. Then we
        hand back the whole text and let the model decide."""
        text = self.assemble(16)
        self.assertEqual(
            grounding.trim_to_question(text, "was there any coolant"), text)

    def test_an_empty_question_cuts_nothing(self):
        text = self.assemble(16)
        self.assertEqual(grounding.trim_to_question(text, ""), text)

    def test_text_with_no_blocks_comes_back_unchanged(self):
        """An entry with no attachment - the notes alone. There is nothing to cut."""
        text = "=== JOB 3: shaft ===\n" + "a" * (grounding.TRIM_THRESHOLD_CHARS + 10)
        self.assertEqual(grounding.trim_to_question(text, "allowance"), text)

    def test_the_second_attachment_does_not_vanish_with_the_block(self):
        """CAUGHT BY OUR OWN CHECK BEFORE THE COMMIT. The first version cut on
        block boundaries alone - and the line "--- JOB n CONTINUED ---"
        with the name of the second file and its tool listing vanished
        together with the preceding block. The model stopped knowing that the
        second file existed. A fact counted by the program has to survive
        every cut."""
        text = (self.assemble(8)
                 + "\n--- JOB 7 CONTINUED: setup sheet second.pdf ---\n"
                 + "TOOLS USED IN THE SECOND FILE:\n  no. 9 - drill dia 5\n"
                 + self.assemble(8))
        self.assertGreater(len(text), grounding.TRIM_THRESHOLD_CHARS)
        result = grounding.trim_to_question(text, "what allowance")
        self.assertIn("JOB 7 CONTINUED: setup sheet second.pdf", result)
        self.assertIn("TOOLS USED IN THE SECOND FILE", result)
        self.assertIn("no. 9 - drill dia 5", result)
        self.assertLess(len(result), len(text))

    def test_the_threshold_stands_below_the_model_window(self):
        """The fuse is to come on BEFORE the window, not after it. Today
        MAX_MODEL_TEXT (20 KB) sits ABOVE a window of 4096 tokens - that is,
        the text between the two is cut off quietly by ollama, not by us."""
        self.assertLess(grounding.TRIM_THRESHOLD_CHARS, attachments.MAX_MODEL_TEXT)


class RestateFactsTest(unittest.TestCase):
    """THE FACTS REPEATED JUST BEFORE THE QUESTION.

    WHERE THIS CAME FROM: a model on a rented card, three runs out of three,
    answered "the information does not contain details" to a question about
    the stock, with the answer ready in line 9 of the text supplied. Two
    attempts at a fix - a finished sentence, and an addition to
    prompt.txt - got 0/3 each.

    THE THIRD ATTEMPT IS NOT A GUESS: the described behaviour of models
    ("lost in the middle") says that attention has the shape of a U - the
    beginning and the end of the text are read most closely, and the middle
    is lost regardless of content. Here the facts stand at the beginning,
    and about 2000 tokens of table separate them from the question. So we
    put the same facts once more at the end.

    WHAT THESE TESTS WATCH - not that it helped (measurement will show
    that), but that the repetition CANNOT DO HARM:
      - it adds NOT ONE piece of information the model did not have;
      - with no facts it does not touch the text by a byte;
      - it does not repeat a long listing;
      - it can be switched off without touching the code.
    """

    HEAD = ("The catalogue holds 1 jobs. Below are the ones that "
             "match the question.\n\n=== JOB 4: tray ===\n"
             "Part material: aluminium\n"
             "Technologist's notes:\n"
             "packed it up on parallels, the vice jaws were too low\n")

    FACTS = ("THE STOCK THIS PART WAS MADE FROM (read by the program, "
             "not to be guessed): shape Cylinder, size 330, 15.\n"
             "TOOLS USED ON THIS JOB (computed by the program, "
             "not to be guessed):\n"
             "  no. 1, Flat endmill, diameter 10\n"
             "  no. 25, Chamfer mill, diameter 6\n"
             "TOTAL: 2 different tools, 5 operations.\n")

    TABLE = ("\n[OPERATION INFO 1 of 5]\n"
              "  STOCK TO LEAVE (allowance): 1.0\n"
              "  FEEDRATE (feed): 2500\n")

    def assemble(self):
        return self.HEAD + self.FACTS + self.TABLE

    def test_the_repetition_carries_the_stock_and_the_tool_listing(self):
        """These are the two facts the model stumbled over in the measurements."""
        restatement = grounding.restate_facts(self.assemble())
        self.assertIn("THE STOCK", restatement)
        self.assertIn("size 330, 15", restatement)
        self.assertIn("Chamfer mill", restatement)
        self.assertIn("TOTAL: 2 different tools", restatement)

    def test_it_ADDS_NOT_ONE_NEW_LINE(self):
        """THE MOST IMPORTANT TEST IN THIS CLASS. Could the repetition add
        anything of its own, it would be a new source of invention rather
        than a safeguard against one. Every line of it apart from the heading
        must stand in the text above."""
        text = self.assemble()
        restatement = grounding.restate_facts(text)
        lines = restatement.split("\n")
        self.assertEqual(lines[0], grounding.RESTATE_HEADING)
        for line in lines[1:]:
            self.assertIn(line, text)

    def test_the_heading_says_outright_that_it_is_the_same(self):
        """Without this the model may take the repetition for a SECOND job or a
        second tool - and count them twice. That is the price of this change,
        so it has to be paid with something."""
        restatement = grounding.restate_facts(self.assemble())
        self.assertIn("nothing new", restatement)
        self.assertIn("the same as above", restatement)

    def test_the_table_does_not_enter_the_repetition(self):
        """We repeat FACTS COUNTED BY THE PROGRAM, not the tail of a table -
        otherwise the repetition would be a second text rather than a
        reminder."""
        restatement = grounding.restate_facts(self.assemble())
        self.assertNotIn("STOCK TO LEAVE", restatement)
        self.assertNotIn("FEEDRATE", restatement)

    def test_text_with_no_facts_gets_nothing(self):
        """The technologist's notes alone, with no attachment. There is nothing
        to repeat - and then the text is to stay byte for byte."""
        self.assertEqual(
            grounding.restate_facts("=== JOB 1: shaft ===\nnothing here"), "")

    def test_a_long_listing_is_NOT_repeated(self):
        """Repeating twenty tools is no longer a reminder but a second text -
        and then we pay with the window for something that may do harm in
        itself."""
        lines = ["TOOLS USED ON THIS JOB (computed by the program):"]
        lines += ["  no. %d, square endmill, diameter %d, %s"
                  % (n, n, "x" * 40) for n in range(1, 41)]
        restatement = grounding.restate_facts("\n".join(lines))
        self.assertEqual(restatement, "")

    def test_the_switch_switches_it_off(self):
        """Both must be measurable without touching the code."""
        previous = grounding.RESTATE_FACTS
        grounding.RESTATE_FACTS = False
        try:
            self.assertEqual(grounding.restate_facts(self.assemble()), "")
        finally:
            grounding.RESTATE_FACTS = previous

    def test_the_headings_match_WHAT_THE_PROGRAM_ASSEMBLES(self):
        """A FUSE AGAINST A QUIET DRIFT. Were somebody to correct the heading
        in `describe_tool_list` and not here, the repetition would stop
        working QUIETLY - and nobody would notice, because the program would
        still be answering."""
        description = {"blocks": [{"title": "TOOL", "kind": "pairs",
                           "pairs": [["TYPE", "Chamfer mill"],
                                    ["NUMBER", "25"]]},
                          {"title": "STOCK", "kind": "pairs",
                           "pairs": [["SHAPE", "Cylinder"],
                                    ["SIZE", "330, 15"]]}]}
        listing = attachments.describe_tool_list(description)
        stock_sentence = attachments.describe_stock(description)
        self.assertTrue(listing.startswith(grounding.FACT_PREFIXES), listing[:60])
        self.assertTrue(stock_sentence.startswith(grounding.FACT_PREFIXES),
                        stock_sentence[:60])

        directory = tempfile.mkdtemp(prefix="chipbook-test-restate-")
        try:
            path = os.path.join(directory, "program.nc")
            with open(path, "w", encoding="ascii") as file:
                file.write("%\nO1234\nT2 M6\nM30\n%\n")
            gcode = attachments.describe_gcode(path)
            self.assertTrue(gcode.startswith(grounding.FACT_PREFIXES),
                            gcode[:60])
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class RestateAtEndOfTextTest(unittest.TestCase):
    """The whole road: database -> ask -> the text that really went out.

    The sense of this lies in the PLACE, not in the content. A test without
    a check that the repetition stands at the VERY END would check nothing.
    """

    XML = (b"<?xml version='1.0'?><SETUPSHEET>"
           b"<TOOL><NAME>17 Chamfer Mill</NAME><TYPE>Chamfer mill</TYPE>"
           b"<NUMBER>25</NUMBER></TOOL></SETUPSHEET>")

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-tools-")
        self.catalog = catalog.open_catalog(self.directory)
        self.number = self.catalog.add_job(
            name="Heart tray", customer="ACME", material="aluminium",
            notes="parallels packed underneath")["id"]
        path = os.path.join(self.directory, "setup.xml")
        with open(path, "wb") as file:
            file.write(self.XML)
        with open(path, "rb") as file:
            self.catalog.add_attachment(self.number, "setup.xml", file,
                                      len(self.XML))
        self.texts_given = []

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _catcher(self, question, text):
        self.texts_given.append(text)
        return "With a 6 mm chamfer mill."

    def _ask(self):
        self.catalog.ask("what tools did I use", number=self.number,
                             conversation=self._catcher)
        return self.texts_given[0]

    def test_the_repetition_stands_AT_THE_VERY_END(self):
        """Right before the question - because the question is appended behind
        the text only in model.ask_model."""
        text = self._ask()
        self.assertTrue(text.rstrip().endswith("."), text[-80:])
        self.assertIn(grounding.RESTATE_HEADING, text)
        self.assertGreater(text.index(grounding.RESTATE_HEADING),
                           text.index("=== JOB"))
        tail = text[text.index(grounding.RESTATE_HEADING):]
        self.assertIn("Chamfer mill", tail)
        self.assertNotIn("[OPERATION INFO", tail)

    def test_the_facts_stand_at_the_beginning_TOO(self):
        """The repetition ADDS a second place, it does not move the first. The
        recommendation reads: the most important at the beginning AND at the
        end."""
        text = self._ask()
        first_pos = text.index("TOOLS USED ON THIS JOB")
        self.assertLess(first_pos, text.index(grounding.RESTATE_HEADING))

    def test_the_repetition_switched_off_does_not_change_the_text_by_a_byte(self):
        """The way back is to be free: the switch set to False and the program
        hands the model exactly what it handed before the repetition
        existed."""
        previous = grounding.RESTATE_FACTS
        grounding.RESTATE_FACTS = False
        try:
            text = self._ask()
        finally:
            grounding.RESTATE_FACTS = previous
        self.assertNotIn(grounding.RESTATE_HEADING, text)
        self.assertIn("TOOLS USED ON THIS JOB", text)


class ModelAnswerCheckTest(unittest.TestCase):
    """The whole road: model -> sieve -> screen."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-grounding-")
        self.catalog = catalog.open_catalog(self.directory)
        self.catalog.add_job(name="Heart tray", customer="ACME",
                             material="aluminium",
                             notes="allowance 1.0 mm after roughing")

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _model(self, *answers):
        """A stand-in handing back answers in turn. The last one repeats."""
        counter = {"count": 0}

        def conversation(question, text):
            number = min(counter["count"], len(answers) - 1)
            counter["count"] += 1
            return answers[number]

        conversation.counter = counter
        return conversation

    def test_an_invented_number_does_not_reach_the_screen(self):
        """THE HEART OF THE CHECK. The model insists on a number that is not in
        the text - a person gets an honest "I do not have it", not an
        invention."""
        conversation = self._model({"answer": "12 holes", "source": "[OP 3]"})
        answer = self.catalog.ask("tray allowance", conversation=conversation)
        self.assertEqual(answer.kind, "none")
        self.assertNotIn("12", answer.text)
        # TWO ATTEMPTS, NOT ONE - a single stumble is too little to refuse a
        # person an answer.
        self.assertEqual(conversation.counter["count"], 2)

    def test_a_second_attempt_saves_the_answer(self):
        """The model corrects itself - and then the answer is to come through normally."""
        conversation = self._model(
            {"answer": "12 holes", "source": "[OP 3]"},
            {"answer": "1.0 mm", "source": "allowance 1.0 mm after roughing"})
        answer = self.catalog.ask("tray allowance", conversation=conversation)
        self.assertEqual(answer.kind, "one")
        self.assertEqual(answer.text, "1.0 mm")

    def test_an_unconfirmed_source_does_NOT_wipe_the_answer(self):
        """MEASURED on a real ollama: the model answers correctly ("1.0 mm")
        and gives as its source A DESCRIPTION OF THE PLACE in its own words.
        Were an unconfirmed source to reject the answer, that correct answer
        would be lost."""
        conversation = self._model({"answer": "1.0 mm",
                               "source": "somewhere in the operation description"})
        answer = self.catalog.ask("tray allowance", conversation=conversation)
        self.assertEqual(answer.kind, "one")
        self.assertEqual(answer.text, "1.0 mm")
        self.assertFalse(answer.source_confirmed)
        # The model asked ONCE - the answer had support in the figure.
        self.assertEqual(conversation.counter["count"], 1)

    def test_a_source_that_stands_in_the_text_is_confirmed(self):
        conversation = self._model({"answer": "1.0 mm",
                               "source": "allowance 1.0 mm after roughing"})
        answer = self.catalog.ask("tray allowance", conversation=conversation)
        self.assertTrue(answer.source_confirmed)

    def test_plain_text_from_a_stand_in_is_not_subject_to_checking(self):
        """WHY SO: the checking needs a `source` field, and that arrives by the
        form only. From ollama an answer comes back as a dict ALWAYS, on the
        fallback road too - so in the program the sieve always works. The
        stand-ins in the tests hand back text, and thanks to that not one of
        today's tests needed a fix."""
        answer = self.catalog.ask(
            "tray allowance", conversation=lambda p, t: "anything 999")
        self.assertEqual(answer.kind, "one")
        self.assertEqual(answer.text, "anything 999")


class WholeJobTextTest(unittest.TestCase):
    """What the model gets as ONE job: the notes plus the setup sheet.

    This function assembles everything the model sees, and for a long time
    it had not one test. The hole came to light when a measurement showed
    the model reading the notes alone and not looking into the attachment.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-job-model-")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _job_with_attachment(self):
        number = self.catalog.add_job(
            name="Housing 7", customer="Customer A", material="s355",
            notes="parallels packed underneath")["id"]
        data = (b"<?xml version='1.0'?><SETUPSHEET>"
                b"<TOOL><NAME>17 Chamfer Mill</NAME><TYPE>Chamfer mill</TYPE>"
                b"<NUMBER>25</NUMBER></TOOL></SETUPSHEET>")
        path = os.path.join(self.directory, "setup.xml")
        with open(path, "wb") as file:
            file.write(data)
        with open(path, "rb") as file:
            self.catalog.add_attachment(number, "setup.xml", file, len(data))
        return self.catalog.job_text_for_model(self.catalog._record(number))

    def test_the_setup_sheet_is_marked_as_PART_OF_THE_ENTRY(self):
        """CAUGHT BY MEASUREMENT. The heading read "--- setup sheet: X ---" and
        the model took the attachment for a separate document. Asked "did I
        use a chamfer mill" it answered "no, the log says ONLY that you put
        blocks underneath" - with the chamfer mill written out a dozen lines
        below.
        That it did see the text is known from the same run: it answered a
        harder question from that very attachment faultlessly."""
        text = self._job_with_attachment()
        header = [entry for entry in text.splitlines() if "setup.xml" in entry][0]
        self.assertIn("JOB", header.upper())
        self.assertNotIn("--- setup sheet:", text)

    def test_the_notes_and_the_attachment_are_in_one_piece(self):
        """The model is to get one source, not two separate ones."""
        text = self._job_with_attachment()
        self.assertIn("parallels packed underneath", text)
        self.assertIn("Chamfer mill", text)
        self.assertLess(text.index("parallels packed"), text.index("Chamfer mill"))

    def test_the_entry_material_is_called_the_PART_material(self):
        """THE OTHER HALF OF IT. The model sees two materials in ONE text: the
        one from the setup sheet (the cutting edge) and the one from the
        obligatory chipbook field (the part). Telling them apart on the setup
        sheet side gives nothing until the other label says by itself what it
        concerns.

        A separate MODEL_FIELD_LABELS map, and not FIELD_LABELS - the latter
        go into error messages too, where "The part material is empty" would
        sound odd."""
        text = self._job_with_attachment()
        self.assertIn("Part material: s355", text)
        self.assertNotIn("\nMaterial: s355", text)

    def test_the_lexicon_works_by_this_road_too(self):
        """A regression along the whole road: database -> attachment -> model text."""
        self.assertIn("Chamfer mill", self._job_with_attachment())


# ------------------------------------------- a job from the phone

class JobFromPhoneTest(unittest.TestCase):
    """An entry created at the machine, let into the database in the evening.

    Both promises watched here came out of MEASUREMENT and not out of
    caution:
      - the same job can arrive twice (a stalled send gets through when the
        laptop comes back) - and no twin is to appear;
      - the job is to carry the date of the moment at the machine, not of
        the moment it was let into the database.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-phone-")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _job(self, idempotency_key=None, when=None, name="Tray"):
        return self.catalog.add_job(name=name, customer="Jacobs",
                                    material="steel", notes="at the machine",
                                    idempotency_key=idempotency_key, when=when)

    def test_THE_SAME_MARK_DOES_NOT_MAKE_A_SECOND_ENTRY(self):
        """THE HEART OF IT. In a trial the same job arrived twice and only
        chance (an identical folder name) meant that the second overwrote the
        first instead of standing beside it."""
        first = self._job(idempotency_key="phone-abc")
        second = self._job(idempotency_key="phone-abc")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.catalog.job_count(), 1)

    def test_different_marks_are_different_entries(self):
        self._job(idempotency_key="one")
        self._job(idempotency_key="two")
        self.assertEqual(self.catalog.job_count(), 2)

    def test_entries_from_the_laptop_do_not_get_in_each_others_way(self):
        """Entries created on the laptop carry no mark. Were an empty
        idempotency_key to count like any other, a second job from the laptop
        would never come into being."""
        self._job(name="first")
        self._job(name="second")
        self._job(name="third")
        self.assertEqual(self.catalog.job_count(), 3)

    def test_the_date_comes_from_the_phone(self):
        job = self._job(idempotency_key="with-date", when="2026-08-07T15:30:00")
        self.assertEqual(job["created_at"], "2026-08-07 15:30:00")

    def test_the_folder_takes_the_entry_date_and_not_the_date_it_was_let_in(self):
        """An entry made yesterday is to lie under yesterday's date - otherwise
        the user would look for it in the wrong place when recovering from
        the folders."""
        job = self._job(idempotency_key="with-folder", when="2026-08-07 15:30:00")
        self.assertTrue(job["folder"].startswith("2026-08-07_"), job["folder"])

    def test_a_broken_date_does_NOT_lose_the_entry(self):
        """A broken clock in the phone is no reason to lose a job written down
        at the machine. We take the laptop's clock then."""
        job = self._job(idempotency_key="bad-date", when="yesterday after lunch")
        self.assertTrue(job["created_at"])
        self.assertEqual(self.catalog.job_count(), 1)

    def test_a_date_from_the_future_is_refused(self):
        """A phone with a clock set wrong would write a job into the year 2031
        and nobody would look for it there."""
        job = self._job(idempotency_key="future", when="2031-01-01 10:00:00")
        self.assertFalse(job["created_at"].startswith("2031"))

    def test_looking_an_entry_up_by_its_mark_finds_it_and_invents_nothing(self):
        job = self._job(idempotency_key="wanted")
        self.assertEqual(self.catalog.job_by_key("wanted")["id"], job["id"])
        self.assertIsNone(self.catalog.job_by_key("no-such-thing"))
        self.assertIsNone(self.catalog.job_by_key(""))
        self.assertIsNone(self.catalog.job_by_key(None))

    def test_an_entry_with_no_mark_works_as_before(self):
        """The whole rest of the program calls add_job without those two fields
        and has to work exactly as it did before."""
        job = self.catalog.add_job(name="Plain", customer="K",
                                    material="alu", notes="")
        self.assertTrue(job["created_at"])
        self.assertTrue(job["folder"])


class PhoneTimestampTest(unittest.TestCase):
    """Reading the moment given by the phone. We do not guess dates."""

    def test_a_timestamp_with_the_letter_T_and_without_it(self):
        self.assertEqual(catalog.parse_client_time("2026-08-07T15:30:00"),
                         "2026-08-07 15:30:00")
        self.assertEqual(catalog.parse_client_time("2026-08-07 15:30:00"),
                         "2026-08-07 15:30:00")

    def test_a_bare_date_is_allowed_too(self):
        self.assertEqual(catalog.parse_client_time("2026-08-07"),
                         "2026-08-07 00:00:00")

    def test_rubbish_gives_None_and_not_an_invented_date(self):
        for bad in ("", None, "yesterday", "07.08.2026", "2026-13-45 99:99:99"):
            self.assertIsNone(catalog.parse_client_time(bad), bad)


class OrderNumberTest(unittest.TestCase):
    """The order number comes back to the entry.

    It REVERSES an earlier decision, where this column was deliberately
    deleted. It is OPTIONAL and SEARCHABLE - that is the point of saving it.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-order_number-")
        self.catalog = catalog.open_catalog(self.directory)

    def tearDown(self):
        self.catalog.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_the_order_number_is_saved(self):
        job = self.catalog.add_job(name="Tray", customer="Jacobs",
                                    material="steel", notes="",
                                    order_number="ZL-2026/118")
        self.assertEqual(self.catalog.job(job["id"])["order_number"], "ZL-2026/118")

    def test_searching_by_the_order_number_finds_the_entry(self):
        """Were the number not to enter the search, typing it would make no
        sense - and that is the only reason it came back."""
        self.catalog.add_job(name="Tray", customer="Jacobs", material="steel",
                             notes="", order_number="ZL-2026/118")
        self.catalog.add_job(name="Other", customer="Smith", material="alu",
                             notes="")
        result = self.catalog.search("ZL-2026/118")
        self.assertEqual(len(result.jobs), 1, result.jobs)
        self.assertEqual(result.jobs[0]["name"], "Tray")

    def test_the_order_number_is_NOT_obligatory(self):
        """The user does not always have the number to hand at the machine."""
        job = self.catalog.add_job(name="No number", customer="K",
                                    material="steel", notes="")
        self.assertEqual(self.catalog.job(job["id"])["order_number"], "")

    def test_an_older_database_gets_an_empty_order_and_loses_nothing(self):
        """Entries from before today had nowhere to write the number."""
        self.catalog.add_job(name="Old", customer="K", material="steel",
                             notes="from before the change")
        self.catalog.con.execute("PRAGMA user_version = 6")
        self.catalog.con.commit()
        self.catalog.close()
        again = catalog.open_catalog(self.directory)
        self.addCleanup(again.close)
        self.catalog = again
        self.assertEqual(again.job_count(), 1)
        self.assertEqual(again.job(1)["notes"], "from before the change")
        self.assertEqual(again.job(1)["order_number"], "")

    def test_migrating_to_schema_4_is_NOT_confused_by_the_order_column_returning(self):
        """CAUGHT by an older test immediately after the column was added. The
        migration to schema 4 recognised an old database by the presence of
        the `order_number` column. Since that column came back, a
        freshly-migrated database would look old - and the migration WOULD
        DELETE its name and its customer. The mark of an old database is now
        `maszyna`, which does not come back."""
        self.catalog.add_job(name="Heart tray", customer="ACME",
                             material="steel", notes="x",
                             order_number="ZL-1")
        self.catalog.con.execute("PRAGMA user_version = 3")
        self.catalog.con.commit()
        self.catalog.close()
        again = catalog.open_catalog(self.directory)
        self.addCleanup(again.close)
        self.catalog = again
        job = again.job(1)
        self.assertEqual(job["name"], "Heart tray")
        self.assertEqual(job["customer"], "ACME")
        self.assertEqual(job["order_number"], "ZL-1")


class MigrationToSchema6Test(unittest.TestCase):
    """An older database is to move to schema 6 without loss.

    The user has real jobs in the database. A migration that loses even one
    of them is worse than not having this feature at all.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-migration6-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_an_older_database_migrates_and_nothing_is_lost(self):
        store = catalog.open_catalog(self.directory)
        store.add_job(name="Old", customer="Customer", material="steel",
                        notes="from before the change")
        # we move the database back to schema 5, as it looked in use
        store.con.execute("DROP INDEX IF EXISTS job_key")
        store.con.execute("PRAGMA user_version = 5")
        store.con.commit()
        store.close()

        again = catalog.open_catalog(self.directory)
        self.addCleanup(again.close)
        self.assertEqual(
            again.con.execute("PRAGMA user_version").fetchone()[0],
            schema.SCHEMA_VERSION)
        self.assertEqual(again.job_count(), 1)
        job = again.job(1)
        self.assertEqual(job["name"], "Old")
        self.assertEqual(job["notes"], "from before the change")
        # a job from before the change carries no mark, and that is all right
        self.assertIsNone(again.job_by_key("anything"))

    def test_a_migration_run_a_second_time_breaks_nothing(self):
        """A migration broken off half way has to be repeatable - the same rule
        that stands at schema 3."""
        store = catalog.open_catalog(self.directory)
        store.add_job(name="One", customer="K", material="steel", notes="")
        store.con.execute("PRAGMA user_version = 5")
        store.con.commit()
        store.close()
        for _ in range(2):
            again = catalog.open_catalog(self.directory)
            self.assertEqual(again.job_count(), 1)
            again.con.execute("PRAGMA user_version = 5")
            again.con.commit()
            again.close()


# A stand-in setup sheet with a chamfer mill. It is built in the test
# code as bytes, not as a file on disk - real user files do not go into
# the repository.
XML_WITH_CHAMFER_MILL = (
    b"<?xml version='1.0'?><SETUPSHEET>"
    b"<OPERATION><NAME>1 - 2D Chamfer</NAME>"
    b"<TOOL><TYPE>Chamfer mill</TYPE><DIAMETER>6.</DIAMETER>"
    b"<FPT>0.05</FPT></TOOL>"
    b"</OPERATION></SETUPSHEET>")


class LexiconInSearchTest(unittest.TestCase):
    """Clarifications go INTO THE INDEX, not only to the model.

    MEASURED, and that is the whole reason for this: a person asks the AI
    "was there a chamfer mill", gets an answer, types the same word into the
    search field and FINDS NOTHING. Two fields in one window, two different
    answers to the same word.

    WHY FROM THE INDEX SIDE AND NOT FROM THE QUESTION SIDE: expanding a word
    in a question works only while the word has NOT ONE hit in the database.
    The first note in which somebody writes that word would switch the
    expansion off - and jobs with CHAMFER MILL in the setup sheet would drop
    out of the results QUIETLY, the more so the bigger the database.

    WHAT IS APPENDED are the clarifications from FIELD_NOTES - what a label
    or a value in a setup sheet means. The one-to-one translation dictionary
    was dropped; what is left are the notes that came out of measurement.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-test-lexicon-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    # ---------------------------------------------- the lexicon alone, without a database

    def test_a_word_INSIDE_a_name_does_NOT_count(self):
        """A match on a WHOLE value only. Otherwise "Drill" would land inside
        the operation name "1 - Drill (Peck)" and a clarification would
        attach itself to jobs where there was no drill."""
        text = "OPERATION INFO\n1 - TOOL INFO taken from the operation name"
        self.assertNotIn("tool name", search.synonyms_for_index(text))

    def test_the_label_goes_in_too(self):
        """BOTH lexicons go into the index: the values and the labels."""
        self.assertIn("part zero",
                      search.synonyms_for_index("WORK OFFSET\nG54"))

    def test_a_sentence_meant_for_the_model_does_NOT_go_in_whole(self):
        """MEASURED BEFORE THE CODE WAS WRITTEN: two of the entries are not
        names but sentences written for the model ("tool name - starts with
        the DIAMETER, not the tool number"). Whole, they would add the words
        "starts", "not", "number" to EVERY entry. We take the part before the
        dash or the comma."""
        result = search.synonyms_for_index("TOOL INFO\n10. FLAT ENDMILL")
        self.assertIn("tool name", result)
        self.assertNotIn("starts", " ".join(result))
        self.assertNotIn("DIAMETER", " ".join(result))

    def test_a_label_that_depends_on_its_section_is_SKIPPED(self):
        """`NUMBER` in a tool block is the number in the magazine, and in an
                offset block something else. The text meant for the index does not
                show which section a line came from - so WE DO NOT GUESS. Entries
                with a full stop in the key do not go into the index at all."""
        result = " ".join(search.synonyms_for_index("NUMBER\n12"))
        self.assertNotIn("magazine", result)
        self.assertNotIn("offset number", result)

    def test_letter_case_and_spaces_do_not_get_in_the_way(self):
        self.assertIn("part zero",
                      search.synonyms_for_index("  work offset  "))

    def test_a_word_outside_the_lexicon_appends_nothing(self):
        self.assertEqual(search.synonyms_for_index("Tray 3 pockets"), [])

    def test_every_word_ONCE_despite_repetitions(self):
        """A setup sheet prints TOOL INFO at every operation. Were every
        repetition to append the word separately, the index would swell for
        no gain."""
        text = "WORK OFFSET\nWORK OFFSET\nWORK OFFSET"
        self.assertEqual(
            search.synonyms_for_index(text).count("part zero"), 1)

    # ------------------------------------------------ the whole road, with a database

    def test_A_CLARIFYING_WORD_FINDS_THE_ENTRY(self):
        """The heart of it, end to end."""
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        number = store.add_job(name="Tray", customer="ACME",
                                material="aluminium",
                                notes="no remarks")["id"]
        store.add_attachment(number, "setup.xml", io.BytesIO(XML_WITH_CHAMFER_MILL),
                            len(XML_WITH_CHAMFER_MILL))
        self.assertEqual([entry["id"] for entry in store.search("tooth").jobs],
                         [number])

    def test_THE_WORD_FROM_THE_FILE_WORKS_AS_BEFORE(self):
        """We do not translate - we APPEND. Searching in the CAM system's own
        English is a road that has to stay untouched."""
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        number = store.add_job(name="Tray", customer="ACME",
                                material="aluminium",
                                notes="no remarks")["id"]
        store.add_attachment(number, "setup.xml", io.BytesIO(XML_WITH_CHAMFER_MILL),
                            len(XML_WITH_CHAMFER_MILL))
        self.assertEqual([entry["id"] for entry in store.search("chamfer").jobs],
                         [number])

    def test_a_clarifying_word_does_NOT_attach_to_an_unrelated_entry(self):
        """The other side of the same rule: the word is to find the jobs in
        which that tool WAS used, not all of them."""
        store = catalog.open_catalog(self.directory)
        self.addCleanup(store.close)
        with_chamfer = store.add_job(name="Tray", customer="ACME",
                                        material="aluminium",
                                        notes="x")["id"]
        store.add_attachment(with_chamfer, "setup.xml", io.BytesIO(XML_WITH_CHAMFER_MILL),
                            len(XML_WITH_CHAMFER_MILL))
        store.add_job(name="Shaft", customer="ACME", material="steel",
                        notes="no attachment")
        self.assertEqual([entry["id"] for entry in store.search("tooth").jobs],
                         [with_chamfer])

    def test_AN_OLDER_DATABASE_FINDS_BY_CLARIFICATION_AFTER_MIGRATING(self):
        """THE MOST IMPORTANT TEST OF THIS CLASS. The database holds jobs
        created BEFORE this change - those too have to be findable this way,
        otherwise the whole change concerns only jobs that do not exist
        yet."""
        store = catalog.open_catalog(self.directory)
        number = store.add_job(name="Old tray", customer="ACME",
                                material="aluminium",
                                notes="from before")["id"]
        store.add_attachment(number, "setup.xml", io.BytesIO(XML_WITH_CHAMFER_MILL),
                            len(XML_WITH_CHAMFER_MILL))
        # we put the index and the version back as they were before the change
        store.con.execute("DELETE FROM job_fts")
        store.con.execute(
            "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
            (number, search.strip_diacritics("Old tray\nACME\naluminium\nfrom before")))
        store.con.execute("PRAGMA user_version = 7")
        store.con.commit()
        store.close()

        again = catalog.open_catalog(self.directory)
        self.addCleanup(again.close)
        self.assertEqual(
            again.con.execute("PRAGMA user_version").fetchone()[0],
            schema.SCHEMA_VERSION)
        self.assertEqual([entry["id"] for entry in again.search("tooth").jobs],
                         [number])

    def test_the_migration_LOSES_NOTHING(self):
        """A migration that loses even one of the user's jobs is worse than not
        having this feature at all."""
        store = catalog.open_catalog(self.directory)
        number = store.add_job(name="Old tray", customer="ACME",
                                material="aluminium",
                                notes="parallels packed underneath")["id"]
        store.add_attachment(number, "setup.xml", io.BytesIO(XML_WITH_CHAMFER_MILL),
                            len(XML_WITH_CHAMFER_MILL))
        store.con.execute("PRAGMA user_version = 7")
        store.con.commit()
        store.close()

        again = catalog.open_catalog(self.directory)
        self.addCleanup(again.close)
        self.assertEqual(again.job_count(), 1)
        job = again.job(number)
        self.assertEqual(job["name"], "Old tray")
        self.assertEqual(job["notes"], "parallels packed underneath")
        self.assertEqual(len(again.attachments(number)), 1)
        # what worked yesterday is to work the same
        self.assertEqual([entry["id"] for entry in again.search("parallels").jobs],
                         [number])

    def test_a_migration_run_a_second_time_breaks_nothing(self):
        store = catalog.open_catalog(self.directory)
        number = store.add_job(name="One", customer="K", material="steel",
                                notes="")["id"]
        store.add_attachment(number, "setup.xml", io.BytesIO(XML_WITH_CHAMFER_MILL),
                            len(XML_WITH_CHAMFER_MILL))
        store.con.execute("PRAGMA user_version = 7")
        store.con.commit()
        store.close()
        for _ in range(2):
            again = catalog.open_catalog(self.directory)
            self.assertEqual(again.job_count(), 1)
            self.assertEqual([entry["id"] for entry in again.search("tooth").jobs],
                             [number])
            again.con.execute("PRAGMA user_version = 7")
            again.con.commit()
            again.close()


if __name__ == "__main__":
    unittest.main()
