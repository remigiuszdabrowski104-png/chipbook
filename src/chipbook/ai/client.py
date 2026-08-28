# -*- coding: ascii -*-
"""Carrying a question to the model and bringing the answer back.

THE CONVERSATION GOES THROUGH OLLAMA on this machine's own loopback
address, using nothing but the standard library. Ollama itself is the only
external piece, and it stays on the user's computer.

THE MODEL NAME IS A SETTING, not a constant in the code. Machines differ:
a laptop with 8 GB fits a small model, a workstation fits a much better
one, and the code should not care. CHIPBOOK_MODEL overrides the default.
"""

import json
import os
import re
import urllib.error
import urllib.request

from .prompt import load_prompt

# /api/chat, NOT /api/generate - and this is not cosmetic.
# MEASURED: against /api/generate, a small model replied by echoing back
# the tail of the instruction and the whole question. That was not a bad
# answer, it was NO answer - the model did not understand it was being
# asked anything, and simply continued the text it had been given.
# /api/chat states the roles explicitly ("this is the system talking, this
# is the human"), so the model assembles them using its own chat template.
# Models with unusual templates have no chance of working without it.
OLLAMA_URL = os.environ.get("CHIPBOOK_MODEL_URL",
                            "http://127.0.0.1:11434/api/chat")

# A DEFAULT FOR DEVELOPMENT, NOT FOR WORK. Any real deployment sets
# CHIPBOOK_MODEL to whatever fits the machine. Two things worth knowing
# before picking one, both measured rather than assumed:
#   - a model whose ollama package carries a broken chat template will
#     echo the question back instead of answering it, and no amount of
#     prompt work fixes that;
#   - small models invent confidently. One produced a part material, a
#     drawing number and a tool that did not exist, all from a file that
#     contained none of them.
MODEL = os.environ.get("CHIPBOOK_MODEL", "mistral:latest")
TIMEOUT_SECONDS = 180

# WHETHER THE MODEL SHOULD THINK OUT LOUD. Off by default - see the
# measurement next to ask_model(). Turn it on with CHIPBOOK_THINKING=1 if
# some future model needs it.
THINKING_ENABLED = os.environ.get(
    "CHIPBOOK_THINKING", "0") not in ("0", "", "no")


def _int_from_env(name, fallback):
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return fallback


# HARD LIMIT ON ANSWER LENGTH.
#
# The original problem: asked "what stock allowance did I leave", the model
# answered correctly and then added a sentence about which section it found
# it in. Asked "have I made a tray like this before", it summarised the
# whole job - material, tools, three operations and the strategy. The
# instruction said "briefly" and said "no markup". The model ignored both.
# A limit is not a request, so it lands where a request does not.
#
# 150 -> 400, AND THE MEASUREMENT INVALIDATED THE ORIGINAL REASONING: once
# answers come back as a filled-in form (see ANSWER_SCHEMA), this limit
# stops SHORTENING answers and starts MUTILATING them. A truncated answer
# is unterminated JSON, which means garbage on screen instead of an answer.
# Three runs of 27 questions each:
#
#   limit 150, source unbounded    -> 10 of 27 truncated
#   limit 400, source unbounded    ->  3 of 27
#   limit 150, source <= 200 chars ->  5 of 27 (this time in the answer)
#   limit 400, source <= 200 chars ->  0 of 27
#
# Timings were indistinguishable within noise. Only the answers that used
# to arrive mangled take longer now; short ones cost not a single extra
# token, because the model ends them by itself.
# BREVITY IS NOW THE INSTRUCTION'S JOB AND THE FORM'S, not the knife's.
MAX_ANSWER_TOKENS = _int_from_env("CHIPBOOK_MAX_ANSWER_TOKENS", 400)



# THE SHAPE OF THE ANSWER - A FORM, NOT A REQUEST.
#
# Ollama can force the layout of a reply through the "format" field. The
# model is given two fields to fill in and has nowhere to append a note
# about which section it found something in.
#
# WHY THIS IS DIFFERENT IN KIND FROM EVERYTHING TRIED BEFORE: the
# instruction said "briefly" (ignored), "in one or two sentences" (ignored)
# and gave seven worked examples (never measured). Those were REQUESTS. A
# form cannot be ignored - either the model fills it in or it does not
# answer at all.
#
# THE SOURCE IN ITS OWN FIELD matters more here than brevity does: only
# then can the program CHECK whether the quoted line really appears in the
# job text. Fishing it out of a sentence was guesswork.
#
# Forced output shape arrived in ollama 0.5. Older versions reject the
# field - see the fallback path in ask_model().

# HARD LIMIT ON SOURCE LENGTH. Measured over three runs of 27 questions:
#
#   answer limit 150, source unbounded    -> 10 of 27 answers TRUNCATED
#   answer limit 400, source unbounded    ->  3 of 27 (always the same one)
#   answer limit 400, source <= 200 chars ->  0 of 27
#
# WHAT WAS TRUNCATED: never the answer, ALWAYS the source. The model pastes
# whole setup-sheet blocks in there, labels and all. A truncated form is
# unterminated JSON - garbage on screen instead of an answer.
#
# WHY A LIMIT RATHER THAN A REQUEST: requests for brevity had been ignored
# from the start, which is the same conclusion that produced the form.
# 200 characters is enough for "[TOOL INFO 4 of 4] NUMBER ...: 25" and not
# enough to copy out an entire section. Under this limit the model began
# producing shorter, more readable sources on its own.
MAX_SOURCE_CHARS = _int_from_env("CHIPBOOK_MAX_SOURCE_CHARS", 200)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "source": {"type": "string", "maxLength": MAX_SOURCE_CHARS},
    },
    "required": ["answer", "source"],
}


# A STRAY BRACE FROM THE FORM, GLUED TO THE TEXT. Caught on two
# consecutive runs: an answer began with "}In the described machining...".
# The JSON parsed correctly - the model had written that brace INSIDE the
# field. Reproducible at both token limits, so it is not an artefact of
# truncation. A later run showed the same thing at the END: "...four
# different tools.{". The brace sticks to either side.
# WE STRIP ONLY FROM THE EDGES: a brace mid-sentence may be part of a
# designation from the setup sheet, and that stays. We also do not strip a
# trailing quote - an answer may legitimately end with a quoted tool code.
_LEADING_JUNK_RE = re.compile(r'^[\s{}\[\]"\']+')
_TRAILING_JUNK_RE = re.compile(r'[\s{}\[\]]+$')


def _clean_answer(text):
    """The "answer" field ready for the screen: no markup, and no stray
    brace glued on by the model."""
    trimmed = _LEADING_JUNK_RE.sub("", str(text or ""))
    return strip_markup(_TRAILING_JUNK_RE.sub("", trimmed))


def _parse_fields(text):
    """Model reply -> {"answer": ..., "source": ...}.

    NEVER RAISES. If the model replied with an ordinary sentence anyway, we
    return it whole as the answer with an empty source. The program should
    degrade, not stop - the same rule as in load_prompt().
    """
    try:
        data = json.loads(text)
    except Exception:                      # noqa: BLE001 - absence is not failure
        return {"answer": strip_markup(text), "source": ""}
    if not isinstance(data, dict):
        return {"answer": strip_markup(text), "source": ""}
    return {
        "answer": _clean_answer(str(data.get("answer") or "")),
        "source": str(data.get("source") or "").strip(),
    }


def _looks_like_fields(text):
    """Can what came back be read as a form at all?"""
    try:
        return isinstance(json.loads(text), dict)
    except Exception:                      # noqa: BLE001 - absence is not failure
        return False


# The "answer" field with its contents, escapes included (\" and \n).
_ANSWER_FIELD_RE = re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _salvage_answer(text):
    """Pull the WHOLE "answer" field out of a truncated form, or nothing.

    MEASURED: at a 150-token limit, 10 of 27 replies were cut off halfway -
    but NOT ONCE inside the "answer" field. Truncation always landed in
    "source", where the model pastes chunks of the setup sheet. "answer"
    comes FIRST in the schema, and so was complete and closed every time.

    SO WE DO NOT THROW AWAY A GOOD ANSWER BECAUSE OF MESS AFTER IT. We take
    what the model actually wrote and leave the source empty - the UI then
    shows the answer as UNCONFIRMED, because the grounding check receives
    nothing. That is a worse state than a complete answer, and it should
    look like one.

    A CLOSING QUOTE IS THE CONDITION. If truncation landed mid-sentence we
    do not have an answer, only its beginning - and half a sentence
    presented as an answer from the catalogue is worse than an honest
    error. Then we return nothing and the caller reports a failure.
    """
    match = _ANSWER_FIELD_RE.search(str(text or ""))
    if not match:
        return ""
    try:
        return str(json.loads('"%s"' % match.group(1))).strip()
    except Exception:                      # noqa: BLE001 - absence is not failure
        return ""


class ModelError(Exception):
    """The model did not answer. This is information for the user, not a
    crash."""


def _call(data, address, wait):
    request = urllib.request.Request(
        address, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=wait) as answer:
        return json.loads(answer.read().decode("utf-8"))


def ask_model(question, text, model=None, address=None, wait=None,
              instruction=None, shape=None):
    """Ask the model a question about the GIVEN text. Returns the answer.

    Raises ModelError when no conversation is possible - the caller is
    expected to show that to a person, not crash.
    """
    from_file, instruction_text = load_prompt()
    if instruction is not None:
        instruction_text = instruction
    # The prefix goes at the START OF THE HUMAN MESSAGE, because that is
    # where it worked when measured: some models take a directive on the
    # first line there and nowhere else.
    start = (from_file + "\n") if from_file else ""

    request_json = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": instruction_text},
            {"role": "user",
             "content": start + "JOB ENTRIES:\n" + text
                        + "\n\nQUESTION: " + question},
        ],
        "stream": False,
        # Zero, so that measurements repeat: the same question against the
        # same text must give the same answer. Otherwise there is no way to
        # tell whether a fix helped or the model simply had a better day.
        "options": {"temperature": 0, "num_predict": MAX_ANSWER_TOKENS},
    }
    # THE FORM IS OPT-IN. We attach it only when the caller asked for it;
    # without it ask_model() behaves exactly as it did before and returns
    # plain text.
    if shape is not None:
        request_json["format"] = shape
    # THINKING OFF. MEASURED: "what is 2 plus 2" cost 2552 reasoning tokens
    # and 2 minutes 18 seconds with thinking on, and 6 tokens and 0.2
    # seconds with it off. On a real setup sheet, 44-183 s against
    # 1.5-2.5 s - and resistance to invention was UNCHANGED: asked about a
    # chamfer that was not in the text, the model invented one identically
    # in both modes. Thinking costs minutes and buys no safety.
    if not THINKING_ENABLED:
        request_json["think"] = False
    data = json.dumps(request_json).encode("utf-8")
    try:
        result = _call(data, address or OLLAMA_URL, wait or TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        # A model that cannot think at all may reject the "think" field
        # outright. Then we ask again without it, rather than showing the
        # user a message they have no way to interpret.
        if "think" not in request_json and "format" not in request_json:
            raise ModelError("The model did not answer: %s" % error)
        request_json.pop("think", None)
        # FALLBACK FOR OLDER OLLAMA. Forced output shape arrived in 0.5.
        # When the "format" field is rejected we ask again without it and
        # the answer comes back as plain text - worse, but working.
        # _parse_fields() copes with that.
        request_json.pop("format", None)
        try:
            result = _call(json.dumps(request_json).encode("utf-8"),
                           address or OLLAMA_URL, wait or TIMEOUT_SECONDS)
        except Exception as second_error:  # noqa: BLE001
            raise ModelError("The model did not answer: %s" % second_error)
    except urllib.error.URLError as error:
        raise ModelError(
            "Cannot reach the program that runs the model. Check whether "
            "Ollama is running. (%s)" % error)
    except Exception as error:             # noqa: BLE001 - every failure gets words
        raise ModelError("The model did not answer: %s" % error)
    # /api/chat returns the content under "message", the older
    # /api/generate under "response". We read both, so that pointing
    # CHIPBOOK_MODEL_URL elsewhere does not require a code change.
    message = result.get("message") or {}
    answer_text = str(message.get("content")
                      or result.get("response", "")).strip()
    if not answer_text:
        raise ModelError("The model returned an empty message.")

    # A TRUNCATED FORM IS A FAILURE, NOT A SHORTER ANSWER.
    #
    # Ollama reports why generation stopped in `done_reason`. "length"
    # means the model did NOT finish - it hit `num_predict`. With plain
    # text that is a clipped sentence: ugly but readable, and it stays.
    # With a FORCED SHAPE it is unterminated JSON: _parse_fields() cannot
    # parse it and - true to its promise never to raise - would return the
    # whole raw blob as the answer. The user would see
    # {"answer": "Tools used... on screen, garbage presented as an answer
    # from the catalogue.
    #
    # WE CHECK THE RESULT, NOT THE REASON: if the form closed on complete
    # fields despite the limit, the answer is good and there is nothing to
    # report. A failure should be loud, never silent.
    forced_shape = "format" in request_json
    if (forced_shape and str(result.get("done_reason") or "") == "length"
            and not _looks_like_fields(answer_text)):
        # SALVAGE THE ANSWER ITSELF if the model managed to close it.
        salvaged = _salvage_answer(answer_text)
        if salvaged:
            return {"answer": _clean_answer(salvaged), "source": ""}
        raise ModelError(
            "The model's answer was cut off at the %d token limit and "
            "cannot be read. Ask a narrower question, or raise "
            "CHIPBOOK_MAX_ANSWER_TOKENS." % MAX_ANSWER_TOKENS)

    if shape is not None:
        return _parse_fields(answer_text)
    return strip_markup(answer_text)


def ask_model_fields(question, text, **rest):
    """Same as ask_model(), but the reply comes back in two fields.

    Returns {"answer": ..., "source": ...}. It exists so that the rest of
    the program does not need to know what the schema looks like, and so
    that a schema change has one place to be made rather than one per
    caller.
    """
    rest.setdefault("shape", ANSWER_SCHEMA)
    return ask_model(question, text, **rest)


def strip_markup(text):
    """Remove formatting markup from a model's answer.

    WHY, when the instruction already says "no tables and no markup":
    because the model does not obey it. Measured - answers came back as
    "**Material**: Ash", and the UI renders that literally, asterisks and
    all, because it does not interpret markdown. A request to a model is a
    request. That much is certain.

    WE STRIP MARKUP, NOT CONTENT. An asterisk inside a word stays - it may
    be part of a tool designation or a dimension. We remove paired ** and
    __ and leading hashes, which mean nothing in this UI and look like
    debris.

    LEADING HYPHENS ARE LEFT ALONE. A list of tools one per line reads well
    without any formatting, and removing them would glue everything into a
    single paragraph.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    return text.strip()


def available(address=None, wait=3):
    """Whether the model can be reached at all. Never raises."""
    address = (address or OLLAMA_URL).replace("/api/chat", "/api/tags")
    address = address.replace("/api/generate", "/api/tags")
    try:
        with urllib.request.urlopen(address, timeout=wait) as answer:
            return answer.status == 200
    except Exception:                      # noqa: BLE001
        return False
