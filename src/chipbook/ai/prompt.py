# -*- coding: ascii -*-
"""The instruction handed to the model, and the file it is read from.

THE INSTRUCTION IS DELIBERATELY MILD, and that is a measured result rather
than a matter of taste:
  - a HARSH instruction (three prohibitions plus refusal examples) made one
    model refuse EVERYTHING, including questions whose answer sat in the
    text;
  - a NEUTRAL instruction got another model three out of three on planted
    traps - it worked out by itself that the part material was absent.
Hence the shape below: ask for an answer grounded in the text and for an
honest admission when something is missing, but do not build a wall of
prohibitions.
"""

import os

NOT_IN_CATALOG = "NOT IN THE CATALOGUE"

# The instruction file sits next to the program and is read ON EVERY
# QUESTION. That way it can be corrected without touching code and without
# a restart - which matters, because every model wants something slightly
# different in its prompt.
PROMPT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prompt.txt")

# Built-in fallback, used when the file is missing or empty. The program
# has to keep working even if someone deletes the instruction file.
PROMPT = (
    "You are an assistant to a CNC programmer. You answer from the job "
    "entries given to you in the question.\n"
    "An entry is the programmer's own note together with the setup sheet "
    "attached from the CAM system.\n"
    "If something is not in them, say so plainly instead of guessing.\n"
    "A value belongs to the section it appears in.\n"
    "The entries were written by the programmer about their own work, but "
    "YOU are speaking to them. Use the second person: \"you ran\", "
    "\"you added\" - never the first.\n"
    "Answer in a few words."
)


PROMPT_PREFIX = ""


def load_prompt(path=None):
    """Read the prefix and instruction from the file. Returns both.

    The file format is deliberately simple - a human edits it, not a
    program: lines starting with "#" are comments, and sections begin with
    "# PREFIX" and "# INSTRUCTION".

    NEVER RAISES. A missing file, an empty file, a bad encoding - all end
    with the built-in version. The program must work even if someone
    deletes or mangles this file.
    """
    try:
        with open(path or PROMPT_FILE, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()
    except Exception:                      # noqa: BLE001 - absence is not failure
        return PROMPT_PREFIX, PROMPT

    parts = {"PREFIX": [], "INSTRUCTION": []}
    current_field = None
    for line in lines:
        header = line.strip().lstrip("#").strip().upper()
        if line.lstrip().startswith("#") and header in parts:
            current_field = header
            continue
        if line.lstrip().startswith("#"):
            continue
        if current_field is not None:
            parts[current_field].append(line)

    prefix = "\n".join(parts["PREFIX"]).strip()
    instruction = "\n".join(parts["INSTRUCTION"]).strip()
    return prefix, (instruction or PROMPT)
