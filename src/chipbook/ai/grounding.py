"""Refusing what the model has nothing to say it from.

A model will state a diameter, a tool number or a material with complete
confidence when the text it was handed holds none of them. The check is
not "does this sound right" - it is arithmetic:

    EVERY NUMBER in the answer has to appear in the text handed over.
    THE SOURCE the model quotes has to be found in that text as well.

Both work on the same word rules the search uses - accents removed, word
forms allowed - because "10.0" and "10,0" are one number to a person, and
so are "hole" and "holes".

Trimming the text to the question and restating the facts belong here too:
both decide WHAT the model gets to see, and that is the same question from
the other side.
"""

import re

from ..search import _words, same_word_family, strip_diacritics
from ..search import MIN_COMMON_STEM


# THE THRESHOLD AT WHICH WE TRIM THE TEXT DOWN TO MATCHING BLOCKS.
# 12 000 characters is about 80% of a 4096-token window at 3.70 characters
# per token.
# WHERE 3.70 COMES FROM - A MEASUREMENT, NOT A CONVERSION: a real setup
# sheet with the instruction is 7 578 characters and 2 050 input tokens
# according to ollama itself (prompt_eval_count).
# THE HISTORY OF THIS NUMBER, SO IT DOES NOT REPEAT: it was first estimated
# at 3.6 (nearly right), then "corrected" to 2.6 by deriving it from an
# earlier measurement - and the threshold stood on that worse number for a
# whole day. At 2.6 a threshold of 8 500 meant 56% of the window instead of
# 80%, so trimming would have kicked in far earlier than the decision said.
# WHY 80% AND NOT 60%: at 60% trimming would already fire on a real file
# today (about 66% of the window with the instruction), and the whole point
# of this design is that nothing changes for today's files. The threshold
# is a fuse, not a new rule.
# NOTE ON MAX_MODEL_TEXT ABOVE: 20 KB sits HIGHER than the whole model
# window, so by itself it protects against nothing - text between it and
# the window is truncated silently by ollama, and there is no telling from
# which end.
TRIM_THRESHOLD_CHARS = 12000


# ------------------------------------------- GROUNDING THE ANSWER
#
# A REFUSAL IS A CORRECT ANSWER and is not checked. If it were, the
# program would punish the model for exactly what the instruction asks of
# it: to admit an absence rather than guess.
REFUSAL_PHRASES = ("not in", "do not have", "don't have", "no such",
                   "not found", "cannot find", "no information",
                   "not stated", "not mentioned", "is not there")


# How long a word has to be to settle anything. "on", "to", "mm" appear in
# every text and grounding on them would be an illusion.
MIN_SUPPORTED_WORD = 4

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _number_variants(amount):
    """1,0 and 1.0 are the same number, and so are 1.0 and 1.

    A model may write a decimal comma while the CAM system writes a point,
    and neither of them is wrong. Without this, the correct answer "1,0 mm"
    would find no support in "STOCK TO LEAVE: 1.0" and the program would
    reject the truth.
    """
    with_dot = str(amount).replace(",", ".")
    variants = {str(amount), with_dot, with_dot.replace(".", ",")}
    if "." in with_dot:
        whole, rest = with_dot.split(".", 1)
        if set(rest) == {"0"}:
            variants.add(whole)
    return variants


def unsupported_numbers(answer, text):
    """What in the answer has NO support in the supplied text.

    An empty list means everything checks out. A non-empty one means the
    program must NOT show the answer and should press the model harder.

    NUMBERS ARE CHECKED STRICTLY, because that is where the measured
    inventions live: one model invented a cycle time of 29:43 that appeared
    nowhere in the file; another reported "14 jobs" when the database held
    five. Every number in an answer has to stand in the text.

    WORDS ARE CHECKED LOOSELY - one significant word is enough. It cannot
    be stricter, because models paraphrase and exact matching would discard
    correct answers. This is a deliberately weaker sieve, not an oversight.
    """
    sanitized = str(answer or "").strip()
    if not sanitized:
        return []
    lowered = strip_diacritics(sanitized).lower()
    if any(word in lowered for word in REFUSAL_PHRASES):
        return []

    lowered_text = strip_diacritics(str(text or "")).lower()
    numbers = _NUMBER_RE.findall(sanitized)
    if numbers:
        return [l for l in numbers
                if not any(w in lowered_text for w in _number_variants(l))]

    words = [s for s in _words(sanitized) if len(s) >= MIN_SUPPORTED_WORD]
    if not words:
        return []
    # INFLECTED FORMS COUNT AS SUPPORT. An entry says "ash", the model
    # answers "Made of ash" - and that is the same thing.
    # The first version of this sieve rejected a correct answer over
    # grammar; caught by our own test before the commit.
    # WE DO NOT BUILD A SECOND SIEVE: same_word_family already exists and
    # is measured on the search.
    text_words = set(_words(str(text or "")))
    for word in words:
        if word in lowered_text:
            return []
        if any(same_word_family(word, other) for other in text_words):
            return []
    return words[:3]


def source_is_supported(source, text):
    """Whether the model's cited source can be found in the supplied text.

    THIS IS NOT A CONDITION FOR SHOWING THE ANSWER - see the section
    heading. MEASURED: models cite a source by DESCRIBING it rather than
    quoting, so literal matching would reject correct answers. We therefore
    count how many significant words of the citation appear in the text and
    call it found at half of them.
    """
    cleaned = str(source or "").strip()
    if not cleaned:
        return False
    lowered_text = strip_diacritics(str(text or "")).lower()
    if strip_diacritics(cleaned).lower() in lowered_text:
        return True
    words = [s for s in _words(cleaned) if len(s) >= MIN_SUPPORTED_WORD]
    if not words:
        return False
    hits = sum(1 for s in words if s in lowered_text)
    return hits * 2 >= len(words)


# A block heading in the text for the model: "[OPERATION INFO 2 of 4]".
# It is assembled by the setup-sheet renderer, and it is the only place
# this text can be cut without a number coming loose from its operation.
_BLOCK_HEADING_RE = re.compile(r"^\[[^\]\n]+ \d+ of \d+\]", re.M)


def trim_to_question(text, question):
    """A fuse for a large setup sheet: keep the head of the entry and the
    blocks related to the question. BELOW THE THRESHOLD NOTHING IS TOUCHED.

    WHY A THRESHOLD RATHER THAN ALWAYS TRIMMING. Measured on five real
    setup sheets: the largest is 6 198 characters, about 2 384 tokens - 58%
    of a 4096-token window. The ceiling this was meant to protect against
    does not exist on today's files. Trimming always would mean paying in
    risk for a problem that does not occur - so we pay only once it starts
    occurring.

    WHAT THE RISK IS, STATED PLAINLY: trimming is ELIMINATION, and
    elimination is the search's job, not the program's and not the model's.
    A removed block that happened to hold the answer turns the truth into
    "that is not in this entry" - the same shape of error as a model saying
    it has nothing while looking at the data. Hence three gates:
      - the head (entry heading, the person's own fields, the note, the
        tool list) ALWAYS stays, because that is what a human wrote;
      - when no block matches, NOTHING IS TRIMMED - a cut that removes
        everything is not a cut, it is a blindfold;
      - below the threshold the text comes back byte for byte.

    Inflected forms are matched by same_word_family - the same mechanism
    the search uses and the grounding sieve uses. We do not build a second
    sieve for the same job.
    """
    text = str(text or "")
    if len(text) <= TRIM_THRESHOLD_CHARS:
        return text
    parts = _into_blocks(text)
    if not any(kind == "block" for kind, _ in parts):
        return text

    wanted = [s for s in _words(question or "") if len(s) >= MIN_COMMON_STEM]
    if not wanted:
        return text
    if not any(kind == "block" and _block_matches(lines, wanted)
               for kind, lines in parts):
        return text

    kept = []
    for kind, lines in parts:
        if kind == "outside" or _block_matches(lines, wanted):
            kept.extend(lines)
    return "\n".join(kept)


def _into_blocks(text):
    """Text -> a list of ("block"|"outside", lines), order preserved.

    A BLOCK is a heading like "[OPERATION INFO 2 of 4]" and everything
    indented under it - that is how the setup-sheet renderer assembles
    those tables. Everything else is "outside" and ALWAYS STAYS: the entry
    heading, the person's own fields, the note, the tool list.
    """
    parts = []
    current = None
    for line in text.split("\n"):
        if _BLOCK_HEADING_RE.match(line):
            current = ["block", [line]]
            parts.append(current)
            continue
        indented_or_blank = (not line.strip()) or line[:1] in (" ", "\t")
        if current is not None and indented_or_blank:
            current[1].append(line)
            continue
        current = None
        if parts and parts[-1][0] == "outside":
            parts[-1][1].append(line)
        else:
            parts.append(["outside", [line]])
    return [(kind, lines) for kind, lines in parts]


def _block_matches(lines, wanted):
    """Whether any word of the question stands in this block - inflected
    forms included."""
    words = set(_words("\n".join(lines)))
    for word in wanted:
        if word in words:
            return True
        if any(same_word_family(word, other) for other in words):
            return True
    return False


# RESTATING THE FACTS JUST BEFORE THE QUESTION.
#
# WHY: measured over three runs out of three, a model answered "the
# information does not contain details" to a question about the stock,
# WHILE HOLDING a ready-made sentence with the answer on line 9 of the text
# it had been given. Two attempted fixes moved it not one step: a
# ready-made sentence in the text, and a note in the instruction file -
# both 0 out of 3.
#
# WHAT WE FOLLOW THIS TIME - not a hunch, but described model behaviour
# ("lost in the middle"): attention has the shape of a U, so the beginning
# and the end of the supplied text are read most carefully and the middle
# is lost REGARDLESS of content. The recommended practice is: most
# important at the start, second most important at the end. Here the facts
# stand at the start, and between them and the question sits an entire
# table from the CAM system - about 2000 tokens. So we place those same
# facts ONCE MORE at the end, right before the question.
#
# WHAT IT COSTS, STATED: a few hundred more characters in the window (about
# 3% of a 4096-token one) and a text in which something appears twice.
# The risk: a model may read the repetition as a SECOND job or a second set
# of tools. Hence a heading that says outright it is the same thing, and
# hence a threshold - a long list is not repeated.
#
# THIS IS A HYPOTHESIS TO BE MEASURED, NOT A CERTAINTY. The switch below
# exists so that both states can be measured without touching code.
RESTATE_FACTS = True


# Above this we do not repeat. Repeating twenty tools is no longer a
# reminder but a second text - and then we pay in context window for
# something that may itself do harm.
MAX_RESTATE_CHARS = 2000


# The openings of lines the PROGRAM assembles and supplies as finished
# facts. This tuple MUST match what describe_stock, describe_tool_list and
# describe_gcode write - a separate test guards it, so that changing a
# heading in one place cannot switch the restatement off silently.
FACT_PREFIXES = ("THE STOCK THIS PART WAS MADE FROM",
                 "TOOLS USED ON THIS JOB",
                 "NC PROGRAM")


RESTATE_HEADING = ("THE SAME FACTS ONCE MORE, SO THEY ARE TO HAND "
                     "(nothing new - the same as above):")


def restate_facts(text):
    """Facts computed by the program, lifted out of the text for restating.

    IT ADDS NOTHING OF ITS OWN. Every line of the result (apart from the
    heading) already stands in `text` - and that is the whole guarantee
    this function offers: the model will not see a single piece of
    information it did not already have. A test guards it, because if the
    restatement could add anything, it would become a new source of
    invention rather than a defence against one.

    An empty result means "nothing to restate", and the caller must then
    leave the text BYTE FOR BYTE unchanged - the same rule as for trimming.
    """
    if not RESTATE_FACTS:
        return ""
    collected = []
    taking = False
    for line in str(text or "").split("\n"):
        if line.startswith(FACT_PREFIXES):
            taking = True
            collected.append(line)
            continue
        # The rest of a fact: the indented list entries and the "TOTAL: ..." line,
        # which stands unindented. A blank line ends the fact.
        if taking and (line.startswith("  ") or line.startswith("TOTAL:")):
            collected.append(line)
            continue
        taking = False
    if not collected:
        return ""
    block = RESTATE_HEADING + "\n" + "\n".join(collected)
    if len(block) > MAX_RESTATE_CHARS:
        return ""
    return block


# Appended to the question on a SECOND attempt. We do not change the
# instruction - we change the question, because the instruction file is
# shared by the whole program and its shape is the result of measurement.
FOLLOW_UP_WARNING = ("\n\nNOTE: the previous answer contained data that is"
             " NOT in the supplied text. Answer only with what stands in"
             " it. If it is not there, say that it is not there.")
