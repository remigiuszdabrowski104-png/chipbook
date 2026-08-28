# -*- coding: ascii -*-
"""Talking to a local language model. Knows nothing about the database or
the UI.

This package does exactly one thing: take an instruction, a block of text
and a question, and return an answer. It does NOT know where the text came
from and has NO access to the catalogue - that is the whole guarantee
behind "the model answers only from what is in the catalogue". It cannot
search for anything, because it is handed a finished piece of text.

    prompt     the instruction, and the file it can be corrected in
    client     the conversation with ollama itself
    grounding  what the answer is checked against before anyone sees it

Answer is defined here because it is the shape this package hands back:
not a string, but a string TOGETHER WITH what stands behind it.
"""

from . import client
from . import grounding
from . import prompt
from .client import (ANSWER_SCHEMA, MAX_ANSWER_TOKENS, MAX_SOURCE_CHARS,
                     MODEL, ModelError, OLLAMA_URL, ask_model,
                     ask_model_fields, available, strip_markup)
from .prompt import NOT_IN_CATALOG, PROMPT, PROMPT_FILE, load_prompt


class Answer:
    """An answer to a question put to the ai.

    `kind` says which of the three cases occurred, and the UI is expected
    to present each differently:
      "none"  - the search found nothing, THE MODEL WAS NOT ASKED;
      "one"   - a single candidate: an answer and one link;
      "many"  - several candidates. THE MODEL IS NOT ASKED AT ALL; the
                sentence is assembled by the UI, and `candidates` carries
                the concrete values by which the jobs differ;
      "error" - the model did not answer (for example Ollama is not
                running).

    `jobs` DO NOT COME from the model - they are the search candidates. The
    model has no way to point at a job it was not given, and no way to
    invent a link.
    """

    def __init__(self, kind, text, result=None, model_name="",
                 differences=(), candidates=(), source="",
                 source_confirmed=False):
        self.kind = kind
        self.text = text
        # WHETHER THE CITED SOURCE WAS FOUND IN THE JOB TEXT.
        # False does NOT mean the answer is wrong - it means we did
        # not confirm where the model saw it. The UI then adds a
        # warning under the answer.
        self.source_confirmed = bool(source_confirmed)
        # THE LINE THE MODEL BASED ITS ANSWER ON.
        # It used to arrive fused into the sentence and the program
        # had no way to check it - fishing it out of the text was
        # guesswork. In a field of its own it can be compared with
        # the job's source text character by character.
        self.source = source
        self.result = result
        self.jobs = list(result.jobs) if result is not None else []
        self.model_name = model_name
        self.differences = list(differences)
        # The concrete distinguishing values - the UI shows them to a person so
        # they have something to pick the right job by.
        self.candidates = list(candidates)

    @property
    def corrections(self):
        return self.result.corrections if self.result is not None else []

    @property
    def forms(self):
        return self.result.forms if self.result is not None else []

    @property
    def skipped(self):
        return self.result.skipped if self.result is not None else []
