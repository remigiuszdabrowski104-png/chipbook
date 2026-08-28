"""Finding a job the way a person remembers it, not the way it was typed.

Three things at once, and each was needed by a real question that came up
in the workshop:

    accents      a word typed flat finds text written with accents,
                 and the other way round
    word forms   one word finds its relative, without a dictionary
    typos        a word close enough to a real one still finds it

The person who fills this database writes their notes in Polish, so the
accent table is Polish and so are some of the examples below. Nothing
else in the mechanism is: it works off shared prefixes and letter
distance, not off a list of words in any one language.

What a search gives back is here too - a result knows not only WHICH jobs
matched but what tells them apart, which is what lets the window ask a
useful follow-up question instead of listing nine identical rows.
"""

import re

from .attachments import FIELD_NOTES

MIN_WORD_LENGTH = 3      # below this the trigram index cannot search

TYPO_THRESHOLD = 0.75      # how far a word may differ and still count as a typo


# How many leading letters two words must share to count as forms of the
# same word ("vibrated" and "vibration" share "vibrat").
# A DIAL TO BE MEASURED, not a constant out of thin air.
MIN_COMMON_STEM = 4


# Written as \uXXXX escapes so the source file stays pure ASCII.
_DIACRITICS = {
    "\u0105": "a",  # a with a tail
    "\u0107": "c",  # c with an acute
    "\u0119": "e",  # e with a tail
    "\u0142": "l",  # l with a stroke
    "\u0144": "n",  # n with an acute
    "\u00f3": "o",  # o with an acute
    "\u015b": "s",  # s with an acute
    "\u017a": "z",  # z with an acute
    "\u017c": "z",  # z with a dot
}


def strip_diacritics(text):
    """Replace accented letters with their unaccented equivalents."""
    result = []
    for char in text:
        plain = _DIACRITICS.get(char.lower())
        if plain is None:
            result.append(char)
        elif char.isupper():
            result.append(plain.upper())
        else:
            result.append(plain)
    return "".join(result)


def _words(text):
    """Text -> searchable words: lower case, unaccented, no punctuation."""
    return re.findall(r"[0-9a-z]+", strip_diacritics(text).lower())


def common_prefix_length(a, b):
    """How many leading letters two words have in common."""
    count = 0
    for char_a, char_b in zip(a, b):
        if char_a != char_b:
            break
        count += 1
    return count


def same_word_family(a, b, minimum=MIN_COMMON_STEM):
    """Whether these are forms of the same word - judged by a common prefix.

    WHY THE PREFIX AND NOT SUFFIXES: inflected languages change the ending,
    so what the forms share sits at the front. "vibration" and "vibrated"
    share "vibrat"; "drilling" and "drilled" share "drill"; in Polish, the
    language these notes are written in, "wiercenie" and "wiertlo" share
    "wier". No list of endings to maintain, no dictionary to download
    - and stemming dictionaries were measured and failed (3 out of 15).

    BOTH words must be long enough: otherwise every short word would join
    everything that starts the same way.
    """
    if len(a) < minimum or len(b) < minimum:
        return False
    return common_prefix_length(a, b) >= minimum


class SearchResult:
    """A search result.

    `corrections` is a list of (what_was_typed, what_was_searched_for).
    `forms` is a list of (what_was_typed, [other forms found in the base]).
    `skipped` are words from the question that appear in NO job.
    They are set aside so that a question asked as a whole sentence finds
    anything at all - but never silently: the UI shows both.
    """

    def __init__(self, jobs, corrections, words, skipped=(), forms=()):
        self.jobs = jobs
        self.corrections = corrections
        self.words = words
        self.skipped = list(skipped)
        self.forms = [(word, list(other)) for word, other in forms]

    def __len__(self):
        return len(self.jobs)

COMPARISON_FIELDS = ("name", "customer", "material")


def differing_fields(jobs):
    """Which fields hold DIFFERENT values across the candidates.

    Feeds the sentence "I have several jobs like this, they differ in this
    and that". Only what genuinely distinguishes them is listed - showing a
    field that is identical everywhere would explain nothing.
    Field names stay as keys; the sentence is assembled by the UI.
    """
    differing = []
    for field in COMPARISON_FIELDS:
        values = set()
        for job in jobs:
            values.add(str(job[field] or "").strip().lower())
        if len(values) > 1:
            differing.append(field)
    return differing


def difference_values(jobs, differences):
    """The concrete values by which the jobs differ from one another.

    For "they differ in material" to be of any use, a person has to see
    WHICH material - "CuSn12 bronze" against "steel", not the bare word
    "material". They need something concrete in order to pick the one job
    they actually meant.
    """
    description = []
    for job in jobs:
        item = {"id": job["id"], "name": str(job["name"] or "")}
        for field in differences:
            item[field] = str(job[field] or "")
        description.append(item)
    return description


def _lexicon_for_index():
    """{LABEL FROM THE FILE: its clarification} - for the search index.

    WHY THE INDEX AND NOT ONLY THE MODEL TEXT: until this existed, the
    clarifications reached only the text handed to the model, while the
    search index was built separately and without them. The measured
    consequence: a person asks the AI a question, gets an answer, types the
    same word into the search box and finds NOTHING. Two fields in one
    window, two different answers.

    TWO RULES, EACH WITH A REASON:

    1. KEYS CARRYING A SECTION ("TOOL INFO.NUMBER") ARE SKIPPED. They exist
       because `NUMBER` means one thing in a tool block and another in an
       offset block. In the text prepared for the index there is no way to
       see which section a line came from - so we do not guess.
    2. WE TAKE THE PART BEFORE A DASH OR A COMMA. Measured before this code
       was written: some entries are not names but whole sentences for the
       model ("tool name - starts with the DIAMETER, not the tool number").
       Taken whole they would add words like "starts" and "not" to EVERY
       job.
    """
    lexicon = {}
    for label, note in FIELD_NOTES.items():
        if "." in label:
            continue
        short = re.split(r" - |,", note)[0].strip()
        if short:
            lexicon[label.strip().upper()] = short
    return lexicon

INDEX_LEXICON = _lexicon_for_index()


def synonyms_for_index(text):
    """Clarifications for what stands in the text - to append to the index.

    MATCHED ON A WHOLE LINE ONLY, and that is a rule rather than caution:
    the search text is assembled so that EVERY line is one complete label
    or one complete value (measured on a real file: 197 lines, 70 of them
    matching a key whole). That way "Drill" cannot land inside an operation
    name like "1 - Drill (Peck)" and attach a clarification to a job that
    used no drill.

    The output order is fixed, and every word appears ONCE - a setup sheet
    prints TOOL INFO at every operation, and without this the index would
    swell for no gain.
    """
    found_items = []
    for line in str(text or "").split("\n"):
        translated = INDEX_LEXICON.get(line.strip().upper())
        if translated and translated not in found_items:
            found_items.append(translated)
    return found_items
