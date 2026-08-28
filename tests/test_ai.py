"""Tests for talking to the model (chipbook/ai).

NONE OF THESE TESTS NEEDS A RUNNING MODEL. They cover what can break
silently: the shape of the request, the handling of a missing connection,
and the fact that this module has no access to the database.

Why that last one has a test of its own: the whole guarantee that the
model answers only from the catalogue rests on it being handed a finished
piece of text with no way to reach for anything more. That is a property
of the STRUCTURE, not of the model's good will - so it gets guarded.
"""

import json
import os
import shutil
import tempfile
import unittest
import urllib.error

from chipbook import ai
from chipbook.ai import client


class PromptTest(unittest.TestCase):

    def test_prompt_is_plain_ascii(self):
        """The module declares an ASCII coding, so the built-in fallback
        has to survive it. A stray non-ASCII character here would break the
        import on machines with a different default encoding."""
        ai.PROMPT.encode("ascii")

    def test_prompt_is_not_a_wall_of_prohibitions(self):
        """Measured: an instruction with three prohibitions made a small
        model refuse even questions whose answer WAS in the text. This test
        does not judge quality - it stops anyone returning to that shape
        without a fresh measurement."""
        self.assertTrue(len(ai.PROMPT) < 600)
        self.assertNotIn("NEVER SAY", ai.PROMPT)

    def test_prompt_demands_the_second_person(self):
        """Reported after the first working answer: the model wrote "I had
        to add a hole", because that is how the entry was phrased. But the
        entry was written by the programmer about themselves, and the model
        is SPEAKING TO THEM - so it must be "you had to". Without this line
        the model copies the voice out of the note and sounds as though it
        did the work itself."""
        self.assertIn("second person", ai.PROMPT)

    def test_the_package_knows_nothing_about_the_database(self):
        """If chipbook/ai ever started importing the storage layer, the model
        could be handed more than it was given - and "only from the
        catalogue" would stop being a property of the structure.

        EVERY file of the package is checked, not just one. After the package
        was split into prompt.py and client.py, reading a single file would
        have left the other one unwatched - and the guarantee stands or falls
        on the whole package."""
        folder = os.path.dirname(os.path.abspath(ai.__file__))
        checked = 0
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            # encoding="ascii" is a check in itself: this package is to stay
            # readable on any machine, whatever the console codepage.
            with open(os.path.join(folder, name), "r", encoding="ascii") as file:
                source = file.read()
            self.assertNotIn("chipbook.catalog", source, name)
            self.assertNotIn("sqlite3", source, name)
            checked += 1
        self.assertGreaterEqual(checked, 3)


class RequestTest(unittest.TestCase):
    """We check WHAT goes out onto the wire, without standing up a
    server."""

    def setUp(self):
        self.sent = {}

        def fake_call(data, address, wait):
            self.sent["data"] = json.loads(data.decode("utf-8"))
            self.sent["address"] = address
            self.sent["wait"] = wait
            return {"message": {"role": "assistant",
                                "content": "  Feed was 3500 mm/min.  "}}

        self.real = client._call
        client._call = fake_call

    def tearDown(self):
        client._call = self.real

    def test_question_and_text_travel_together(self):
        ai.ask_model("what feed?", "FEEDRATE: 3500")
        content = self.sent["data"]["messages"][-1]["content"]
        self.assertIn("FEEDRATE: 3500", content)
        self.assertIn("what feed?", content)

    def test_instruction_and_question_go_as_SEPARATE_roles(self):
        """Measured: when everything went as one blob through
        /api/generate, the model did not answer - it ECHOED the supplied
        text, because it did not know it was being asked anything. The
        roles have to be separated, because only then does the model
        assemble the conversation with its own chat template."""
        ai.ask_model("what feed?", "FEEDRATE: 3500")
        roles = [m["role"] for m in self.sent["data"]["messages"]]
        self.assertEqual(roles, ["system", "user"])
        self.assertNotIn("prompt", self.sent["data"])

    def test_answer_is_read_from_the_older_format_too(self):
        """The endpoint can be overridden by an environment variable, so
        the code accepts both response shapes rather than breaking on the
        older one."""
        client._call = lambda data, address, wait: {"response": "Old format."}
        self.assertEqual(ai.ask_model("x", "y"), "Old format.")

    def test_answer_is_stripped_of_whitespace(self):
        self.assertEqual(ai.ask_model("x", "y"), "Feed was 3500 mm/min.")

    def test_temperature_zero_so_measurements_repeat(self):
        """Without this, the same question would give different answers and
        there would be no way to tell whether a fix helped or the model
        simply had a better day."""
        ai.ask_model("x", "y")
        self.assertEqual(self.sent["data"]["options"]["temperature"], 0)

    def test_model_name_can_be_overridden(self):
        """One machine fits only a small model, another fits a much better
        one - the name cannot be baked into the code."""
        ai.ask_model("x", "y", model="something-else:Q4")
        self.assertEqual(self.sent["data"]["model"], "something-else:Q4")

    def test_empty_answer_is_an_error_not_an_empty_string(self):
        client._call = lambda data, address, wait: {"response": "   "}
        with self.assertRaises(ai.ModelError):
            ai.ask_model("x", "y")


class PromptFromFileTest(unittest.TestCase):
    """The instruction lives in prompt.txt and can be corrected without
    touching the program. The practical reason: every model wants something
    different. Some accept a "/nothink" directive on the first line
    (measured: 10 minutes against 16 seconds); others do not know the word
    and treat it as noise."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="chipbook-prompt-")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _file(self, content):
        path = os.path.join(self.directory, "prompt.txt")
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)
        return path

    def test_reads_prefix_and_instruction(self):
        path = self._file("# a comment\n"
                          "# PREFIX\n/nothink\n\n"
                          "# INSTRUCTION\nAnswer briefly.\n")
        self.assertEqual(ai.load_prompt(path),
                         ("/nothink", "Answer briefly."))

    def test_comments_do_not_reach_the_model(self):
        path = self._file("# INSTRUCTION\n# this is a comment\nBody.\n")
        self.assertEqual(ai.load_prompt(path)[1], "Body.")

    def test_an_empty_prefix_is_allowed(self):
        """For models that do not know the directive this section must be
        empty - otherwise it is noise inside the question."""
        path = self._file("# PREFIX\n\n# INSTRUCTION\nBody.\n")
        self.assertEqual(ai.load_prompt(path), ("", "Body."))

    def test_a_missing_file_falls_back_instead_of_failing(self):
        prefix, instruction = ai.load_prompt(
            os.path.join(self.directory, "not-here.txt"))
        self.assertEqual(prefix, "")
        self.assertEqual(instruction, ai.PROMPT)

    def test_an_empty_file_does_not_break_the_program_either(self):
        self.assertEqual(ai.load_prompt(self._file(""))[1],
                         ai.PROMPT)

    def test_the_real_file_next_to_the_program_parses(self):
        """Guards against the instruction file disappearing from the
        repository or drifting out of shape."""
        prefix, instruction = ai.load_prompt()
        self.assertTrue(instruction)
        self.assertIn("second person", instruction)


class NoConnectionTest(unittest.TestCase):

    def test_ollama_switched_off_gives_a_readable_message(self):
        """The user should see a sentence in plain language, not a
        traceback."""
        with self.assertRaises(ai.ModelError) as caught:
            ai.ask_model("x", "y",
                            address="http://127.0.0.1:9/api/generate", wait=1)
        self.assertIn("Ollama", str(caught.exception))

    def test_availability_check_does_not_raise_when_nothing_answers(self):
        self.assertFalse(ai.available("http://127.0.0.1:9/api/generate",
                                         wait=1))


class ThinkingTest(unittest.TestCase):
    """The model must NOT think out loud, and that is a measured result
    rather than a preference.

    MEASURED: "what is 2 plus 2" - with thinking, 2552 reasoning tokens and
    2 minutes 18 seconds; without it, 6 tokens and 0.2 seconds. On a real
    setup sheet, 44-183 s against 1.5-2.5 s.
    Thinking did NOT improve resistance to invention: asked about a chamfer
    that was not in the text, the model invented one identically in both
    modes. It costs minutes and buys no safety.

    WHY THIS HAS ITS OWN TEST: without it every other test still passes if
    someone drops the "think" field - and the program does not break so
    much as become unusable, and only at the user's machine, after two
    minutes of waiting for the first answer.
    """

    def setUp(self):
        self.sent = []
        self.real = client._call

    def tearDown(self):
        client._call = self.real

    def _install(self, answer=None, raise_on_think=False):
        def fake_call(data, address, wait):
            request = json.loads(data.decode("utf-8"))
            self.sent.append(request)
            if raise_on_think and "think" in request:
                raise urllib.error.HTTPError(
                    address, 400, "does not support thinking", {}, None)
            return answer or {"message": {"content": "answer"}}
        client._call = fake_call

    def test_thinking_is_switched_off_in_the_request(self):
        self._install()
        ai.ask_model("question", "text")
        self.assertIs(self.sent[0].get("think"), False)

    def test_a_prefix_directive_does_not_replace_the_field(self):
        """A directive inside the text does not switch thinking off - the
        model reads it and starts reasoning about it. The field has to go
        independently of whatever anyone writes in the instruction file."""
        self._install()
        ai.ask_model("question", "text", instruction="anything")
        self.assertIs(self.sent[0].get("think"), False)

    def test_a_model_that_cannot_think_is_asked_again_without_the_field(self):
        """Models without a thinking mode may reject the field outright.
        Then we ask a second time without it, rather than showing the user
        an error they have no way to interpret."""
        self._install(raise_on_think=True)
        answer = ai.ask_model("question", "text")
        self.assertEqual(answer, "answer")
        self.assertEqual(len(self.sent), 2)
        self.assertIn("think", self.sent[0])
        self.assertNotIn("think", self.sent[1])

    def test_the_retry_does_not_lose_the_rest_of_the_request(self):
        """The second attempt has to carry exactly the same question as the
        first - otherwise we would fall back to answering something
        else."""
        self._install(raise_on_think=True)
        ai.ask_model("what feed?", "FEED: 3500")
        self.assertEqual(self.sent[0]["messages"], self.sent[1]["messages"])
        self.assertEqual(self.sent[1]["options"]["temperature"], 0)

    def test_thinking_can_be_switched_back_on(self):
        """Should a future model need it, there has to be a way back
        without touching the code."""
        self.assertFalse(client.THINKING_ENABLED)
        previous = client.THINKING_ENABLED
        try:
            client.THINKING_ENABLED = True
            self._install()
            ai.ask_model("question", "text")
            self.assertNotIn("think", self.sent[0])
        finally:
            client.THINKING_ENABLED = previous


class PromptWithoutNothinkTest(unittest.TestCase):
    """The instruction file shipped with the program must not carry a
    model-specific directive in its prefix."""

    def test_the_real_file_has_no_prefix_directive(self):
        prefix, _ = ai.load_prompt()
        self.assertNotIn("nothink", prefix.lower())

    def test_instruction_covers_false_premises(self):
        """Added after a model answered, three times out of three, a
        question about a chamfer that was not in the text. This test guards
        the SENTENCE, not whether it works - that takes a measurement.

        The sentence is deliberately FUSED into the one about searching
        rather than standing alone at the end: three separate prohibitions
        made the model open with "there is no" even when the answer was
        right there in the text."""
        _, instruction = ai.load_prompt()
        self.assertIn("assumes something that is not there",
                      instruction.lower())


class StripMarkupTest(unittest.TestCase):
    """Markup is removed by the PROGRAM, not by asking the ai.

    Caught in use: the instruction said "no tables and no markup", and the
    model still answered "**Material**: Ash". The UI renders that
    literally, asterisks and all. A request to a model is a request. That
    much is certain.
    """

    def test_bold_disappears_and_the_content_stays(self):
        self.assertEqual(ai.strip_markup("**Material**: Ash"),
                         "Material: Ash")

    def test_underscores_too(self):
        self.assertEqual(ai.strip_markup("__Material__: Ash"),
                         "Material: Ash")

    def test_a_hash_heading_disappears(self):
        self.assertEqual(ai.strip_markup("## Summary\nbody"),
                         "Summary\nbody")

    def test_LIST_HYPHENS_STAY(self):
        """A list of tools one per line reads well without any formatting.
        Removing the hyphens would glue everything into one paragraph,
        which is worse than leaving them."""
        text = "- 10 mm endmill\n- 6 mm drill"
        self.assertEqual(ai.strip_markup(text), text)

    def test_an_asterisk_inside_a_word_stays(self):
        """We remove PAIRS of markers, not every asterisk. A lone one may
        be part of a tool designation or a dimension."""
        self.assertEqual(ai.strip_markup("diameter 10*2 mm"),
                         "diameter 10*2 mm")

    def test_bold_spanning_several_lines(self):
        self.assertEqual(ai.strip_markup("**two\nlines**"), "two\nlines")

    def test_plain_text_is_untouched(self):
        text = "You used four tools: a 6 mm drill and a 5 mm endmill."
        self.assertEqual(ai.strip_markup(text), text)

    def test_instruction_asks_for_a_few_words_not_just_briefly(self):
        """"Briefly" is too soft - models read it and produce four
        paragraphs with lists and parameters nobody asked for. The
        instruction has to name a length and show worked examples."""
        _, instruction = ai.load_prompt()
        lowered = instruction.lower()
        self.assertIn("a few words", lowered)
        self.assertIn("this is how you answer", lowered)

    def test_instruction_forbids_naming_the_source_section(self):
        """Caught in use: asked about a stock allowance, the model answered
        correctly and then added "this information is in section
        [OPERATION INFO 1 of 3] under STOCK TO LEAVE". Nobody asked."""
        _, instruction = ai.load_prompt()
        self.assertIn("which section", instruction.lower())


class ForcedAnswerShapeTest(unittest.TestCase):
    """A FORM INSTEAD OF A REQUEST.

    Verbosity had been fought with requests: "briefly", "in one or two
    sentences", seven worked examples in the instruction file. All three
    can be ignored, and the model ignored them.
    Ollama can force the layout of a reply through the "format" field -
    then the model gets two fields to fill in and nowhere to add a lecture.

    THE SOURCE IN ITS OWN FIELD matters more than brevity: only then can
    the program check whether the quoted line really appears in the job
    text. Fishing it out of a sentence was guesswork.
    """

    def setUp(self):
        self.real = client._call

    def tearDown(self):
        client._call = self.real

    def test_without_a_shape_the_request_looks_as_it_always_did(self):
        """THE MOST IMPORTANT OF THESE FOUR. The whole change has to be
        invisible to existing callers - and such a promise without a test
        is only a word. If "format" started going out always, every
        question in the program would change shape with nobody deciding
        so."""
        sent = {}

        def fake_call(data, address, wait):
            sent.update(json.loads(data.decode("utf-8")))
            return {"message": {"content": "an ordinary sentence"}}

        client._call = fake_call
        result = ai.ask_model("question", "text")
        self.assertNotIn("format", sent)
        self.assertEqual(result, "an ordinary sentence")

    def test_the_shape_goes_out_and_the_answer_comes_back_in_two_fields(self):
        """Markup has to be stripped from the answer field as well - the
        model adds asterisks despite being asked not to."""
        sent = {}

        def fake_call(data, address, wait):
            sent.update(json.loads(data.decode("utf-8")))
            return {"message": {"content": json.dumps(
                {"answer": "**1.0 mm**",
                 "source": "[OPERATION 1] STOCK TO LEAVE"})}}

        client._call = fake_call
        result = ai.ask_model_fields("what allowance", "text")
        self.assertIn("format", sent)
        self.assertEqual(sorted(sent["format"]["required"]),
                         ["answer", "source"])
        self.assertEqual(result["answer"], "1.0 mm")
        self.assertEqual(result["source"], "[OPERATION 1] STOCK TO LEAVE")

    def test_a_plain_sentence_instead_of_a_form_does_not_break_anything(self):
        """The model may answer with a sentence anyway. Then we take it
        whole as the answer and leave the source empty - the program should
        degrade, not stop."""
        client._call = lambda data, address, wait: {
            "message": {"content": "The allowance was 1.0 mm."}}
        result = ai.ask_model_fields("question", "text")
        self.assertEqual(result["answer"], "The allowance was 1.0 mm.")
        self.assertEqual(result["source"], "")

    def test_older_ollama_rejects_format_so_we_ask_again_without_it(self):
        """FORCED OUTPUT SHAPE ARRIVED IN OLLAMA 0.5. Older installations
        are still out there, and there is no way to check remotely which
        one a user has. The program has to cope by itself rather than show
        a person an HTTP 400 they cannot interpret. Same rule as for the
        "think" field."""
        attempts = []

        def fake_call(data, address, wait):
            request = json.loads(data.decode("utf-8"))
            attempts.append(request)
            if "format" in request:
                raise urllib.error.HTTPError(
                    address, 400, "format not supported", None, None)
            return {"message": {"content": "Allowance 1.0 mm."}}

        client._call = fake_call
        result = ai.ask_model_fields("question", "text")
        self.assertEqual(len(attempts), 2)
        self.assertIn("format", attempts[0])
        self.assertNotIn("format", attempts[1])
        self.assertEqual(result["answer"], "Allowance 1.0 mm.")


class AnswerLengthLimitTest(unittest.TestCase):
    """A hard limit, because asking was not enough.

    The instruction file said "briefly" and said "no markup". The model
    ignored both.
    """

    def setUp(self):
        self.real = client._call

    def tearDown(self):
        client._call = self.real

    def test_the_limit_reaches_ollama(self):
        sent = {}

        def fake_call(data, address, wait):
            sent.update(json.loads(data.decode("utf-8")))
            return {"message": {"content": "short"}}

        client._call = fake_call
        ai.ask_model("question", "text")
        self.assertEqual(sent["options"]["num_predict"],
                         ai.MAX_ANSWER_TOKENS)

    def test_the_limit_fits_a_tool_list_TOGETHER_WITH_ITS_SOURCE(self):
        """RAISED FROM 150 TO 400 AFTER MEASUREMENT.

        This test used to guard 120-200 tokens, back when the limit was a
        net against lectures. Since answers come back as a form, the limit
        no longer shortens them - it MUTILATES them: truncated JSON is
        garbage on screen. Three runs of 27 questions: at 150 with a
        bounded source, 5 of 27 were cut off; at 400, none.
        The limit has to fit the answer AND the source, because there is
        one form and one token counter."""
        self.assertGreaterEqual(ai.MAX_ANSWER_TOKENS, 300)

    def test_the_limit_is_still_a_net_and_not_a_free_hand(self):
        """A boundary still has to exist - with none at all, a model will
        write a lecture for as long as the context lasts."""
        self.assertLessEqual(ai.MAX_ANSWER_TOKENS, 600)

    def test_it_can_be_changed_without_touching_the_code(self):
        self.assertEqual(client._int_from_env("NO_SUCH_VARIABLE", 150), 150)

    def test_a_truncated_form_is_a_FAILURE_not_an_answer(self):
        """When an answer hits the limit, ollama ends it halfway and
        reports `done_reason: length`. Under a forced shape what remains is
        UNTERMINATED JSON, and _parse_fields - true to its promise never to
        raise - would return the whole raw blob as the answer. The user
        would see a brace and a field name presented as an answer from
        their own catalogue.
        A failure should be loud, not silent.
        """
        client._call = lambda data, address, wait: {
            "message": {"content": '{"answer": "The tools used in this'},
            "done_reason": "length"}
        with self.assertRaises(ai.ModelError) as caught:
            ai.ask_model_fields("list the tools", "text")
        self.assertIn(str(ai.MAX_ANSWER_TOKENS), str(caught.exception))

    def test_the_source_has_a_HARD_length_limit(self):
        """MEASURED over three runs of 27 questions:
        limit 150, source unbounded    - 10 answers truncated,
        limit 400, source unbounded    -  3 truncated (always the same one),
        limit 400, source <= 200 chars -  0 truncated.
        Truncation landed NEVER in the answer and ALWAYS in the source -
        the model pastes whole setup-sheet blocks there. This is a limit,
        not a request, because requests for brevity get ignored."""
        self.assertEqual(
            ai.ANSWER_SCHEMA["properties"]["source"]["maxLength"],
            ai.MAX_SOURCE_CHARS)
        self.assertLessEqual(ai.MAX_SOURCE_CHARS, 300)
        self.assertGreaterEqual(ai.MAX_SOURCE_CHARS, 120)

    def test_the_source_limit_reaches_ollama(self):
        sent = {}

        def fake_call(data, address, wait):
            sent.update(json.loads(data.decode("utf-8")))
            return {"message": {"content": json.dumps(
                {"answer": "1.0 mm", "source": "[OPERATION 1]"})}}

        client._call = fake_call
        ai.ask_model_fields("what allowance", "text")
        self.assertEqual(
            sent["format"]["properties"]["source"]["maxLength"],
            ai.MAX_SOURCE_CHARS)

    def test_the_answer_itself_has_NO_length_limit(self):
        """We bound the source, not the answer. The answer to "list the
        tools" is four lines and has to fit whole."""
        self.assertNotIn(
            "maxLength", ai.ANSWER_SCHEMA["properties"]["answer"])

    def test_a_brace_from_the_form_never_reaches_the_screen(self):
        """From a real measured reply: "}In the described machining four
        different tools were used...". The JSON parsed correctly - the
        model wrote that brace INSIDE the field. Reproducible at both token
        limits, so it is not an artefact of truncation and will not go away
        by raising the limit."""
        client._call = lambda data, address, wait: {
            "message": {"content": json.dumps(
                {"answer": "}In the described machining four tools "
                           "were used.",
                 "source": "[TOOL INFO 1 of 4]"})}}
        result = ai.ask_model_fields("how many tools", "text")
        self.assertEqual(result["answer"],
                         "In the described machining four tools were used.")

    def test_a_brace_at_the_end_disappears_too(self):
        """A second run, also a real reply: "...so four different tools
        were used.{". The brace sticks to either side, so both sides have
        to be stripped."""
        self.assertEqual(
            client._clean_answer("Four tools were used.{"),
            "Four tools were used.")

    def test_a_brace_in_the_MIDDLE_of_a_sentence_STAYS(self):
        """We strip only from the edges. A brace in the middle may be part
        of a designation from the setup sheet - removing it would change
        the content."""
        self.assertEqual(
            client._clean_answer("Endmill {special} no. 12"),
            "Endmill {special} no. 12")

    def test_a_truncated_SOURCE_does_not_take_a_good_answer_with_it(self):
        """MEASURED on a real reply: 10 of 27 answers were cut off at the
        150-token limit - but NOT ONCE in the "answer" field. Truncation
        landed in "source", where the model pastes chunks of the setup
        sheet.
        Discarding such a reply would mean the user gets NOTHING for a
        question the model answered correctly."""
        truncated = ('{\n    "answer": "Yes, you used a chamfer mill on '
                     'this job.","source": "Section [TOOL INFO] holds the '
                     'information about the tools used in the operation.')
        client._call = lambda data, address, wait: {
            "message": {"content": truncated}, "done_reason": "length"}
        result = ai.ask_model_fields("did I use a chamfer mill", "text")
        self.assertEqual(result["answer"],
                         "Yes, you used a chamfer mill on this job.")
        self.assertEqual(result["source"], "")

    def test_salvaging_takes_ONLY_what_the_model_wrote(self):
        r"""Salvaging must not become a back door to guessing. JSON escapes
        (\n, \") come back as characters, and nothing more."""
        truncated = ('{"answer": "Drill no. 2\\nEndmill no. 12",'
                     '"source": "[TOOL INFO 1 of 4] TOOL')
        self.assertEqual(client._salvage_answer(truncated),
                         "Drill no. 2\nEndmill no. 12")

    def test_a_form_that_closed_at_the_limit_IS_an_answer(self):
        """We check the RESULT, not the reason. If the fields closed
        despite the limit, the answer is good and there is nothing to
        report - otherwise we would discard correct answers."""
        client._call = lambda data, address, wait: {
            "message": {"content": json.dumps(
                {"answer": "1.0 mm", "source": "[OPERATION 1]"})},
            "done_reason": "length"}
        result = ai.ask_model_fields("what allowance", "text")
        self.assertEqual(result["answer"], "1.0 mm")

    def test_a_truncated_PLAIN_SENTENCE_is_kept(self):
        """Without a form, a truncated answer is ugly but readable - and
        better than no answer at all."""
        client._call = lambda data, address, wait: {
            "message": {"content": "The allowance was 1.0"},
            "done_reason": "length"}
        self.assertEqual(ai.ask_model("question", "text"),
                         "The allowance was 1.0")

    def test_instruction_says_the_setup_sheet_is_part_of_the_entry(self):
        """Added after a measurement, not by eye. Asked "did I use a
        chamfer mill", a model answered "no, the log only says that you
        packed shims underneath" - it had read the programmer's note and
        treated the setup sheet as something separate from the entry.
        This test guards the sentence. Whether it works is a measurement."""
        _, instruction = ai.load_prompt()
        lowered = instruction.lower()
        self.assertIn("setup sheet", lowered)
        self.assertIn("part of the entry", lowered)


if __name__ == "__main__":
    unittest.main()
