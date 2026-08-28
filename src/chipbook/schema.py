"""The tables, the migrations between them, and the backup taken first.

A MIGRATION NEVER RUNS WITHOUT A COPY OF THE DATABASE BESIDE IT. What the
person typed by hand cannot be written again, so the copy is made before
the first statement, not after a check.

The module also holds the text that goes INTO the index, because that text
is part of what the schema promises: change the set of descriptive fields
and the index has to be rebuilt from the same rule.
"""

import datetime
import os
import sqlite3

from . import BACKUPS_DIR, ChipbookError, FILES_DIR, JOBS_DIR
from .attachments import text_for_search
from .search import strip_diacritics, synonyms_for_index

SCHEMA_VERSION = 8

DESCRIPTIVE_FIELDS = ("name", "customer", "material", "order_number", "notes")


# Three fields are mandatory - name, customer and material - plus a note.
# An earlier design required only material and a note, "nothing more, ever".
# The change is a deliberate bet on user behaviour, made with the price
# stated: four things to type before a job can be saved at all. The reason
# it is worth it: a job with no name and no customer cannot be found later,
# and finding it is the entire point.
REQUIRED_FIELDS = ("name", "customer", "material")


SCHEMA_V1 = """
CREATE TABLE job (
    id         INTEGER PRIMARY KEY,
    folder     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    material   TEXT NOT NULL,
    notes  TEXT NOT NULL,
    order_number   TEXT NOT NULL DEFAULT '',
    maszyna    TEXT NOT NULL DEFAULT '',
    tool       TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE job_fts USING fts5(
    content,
    tokenize="trigram remove_diacritics 1"
);
-- NOTE: the text put into job_fts has ALREADY been passed through
-- strip_diacritics(). SQLite's own remove_diacritics setting is not
-- enough: it maps an accented "e" to "e" but leaves a stroked "l" alone,
-- because that is a separate letter rather than a letter with a mark.
-- Measured on real data.
"""


# IF NOT EXISTS on purpose: a migration interrupted halfway (power cut,
# window closed) has to be repeatable, not to lock the database forever.
SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS attachment (
    id       INTEGER PRIMARY KEY,
    job_id   INTEGER NOT NULL,
    name    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    sha256   TEXT NOT NULL,
    added_at   TEXT NOT NULL
);
-- THE INDEX NAME IS HISTORICAL, NOT A LEFTOVER. An index by this name
-- already stands in databases in use. `IF NOT EXISTS` keeps the old one,
-- so renaming here would not rename anything - it would only add a second,
-- duplicate index beside it and leave both to be maintained on every write.
CREATE INDEX IF NOT EXISTS zalacznik_job ON attachment(job_id);
"""


# Schema 4: name and customer come in; order number, machine and tool go
# out. SQLite cannot drop columns, so the table has to be rewritten.
#
# ALL IN ONE TRANSACTION, INCLUDING THE VERSION NUMBER - and that matters
# more here than the content does. Measured while writing it: if the
# version number were written SEPARATELY, an interruption between the two
# would leave the database looking like version 3, and a second run of the
# migration would pass without error and WIPE the name and customer of
# every job. Silently. So the version number changes in the same
# transaction: either the old state or the new one.
SCHEMA_V4 = (
    """CREATE TABLE job_new (
        id         INTEGER PRIMARY KEY,
        folder     TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        name      TEXT NOT NULL DEFAULT '',
        customer     TEXT NOT NULL DEFAULT '',
        material   TEXT NOT NULL,
        notes  TEXT NOT NULL
    )""",
    """INSERT INTO job_new (id, folder, created_at, updated_at, name,
                              customer, material, notes)
        SELECT id, folder, created_at, updated_at, '', '', material, notes
          FROM job""",
    "DROP TABLE job",
    "ALTER TABLE job_new RENAME TO job",
)


# Schema 5: setup-sheet content enters the search index.
# It is held IN THE DATABASE rather than read from the file on every
# search - so a job stays findable even when the file disappears from
# disk, and rebuilding the index opens no files at all.
SCHEMA_V5 = "ALTER TABLE attachment ADD COLUMN content TEXT NOT NULL DEFAULT ''"


def _check_environment(con):
    """Check whether this SQLite has what the search relies on."""
    try:
        con.execute(
            'CREATE VIRTUAL TABLE temp.probe_fts '
            'USING fts5(t, tokenize="trigram remove_diacritics 1")'
        )
        con.execute("DROP TABLE temp.probe_fts")
    except sqlite3.OperationalError as error:
        raise ChipbookError(
            "This Python build lacks the full-text search chipbook needs "
            "(SQLite without FTS5, or without the trigram tokenizer). "
            "Technical detail: " + str(error)
        )


def _migrate(con, data_dir):
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ChipbookError(
            "This database comes from a newer chipbook (schema %d, this "
            "program knows %d). Use the newer version of the program."
            % (version, SCHEMA_VERSION)
        )
    if version == 0:
        con.executescript(SCHEMA_V1)
        con.execute("PRAGMA user_version = 1")
        version = 1
    if version == 1:
        # schema 2: the index holds unaccented text. It is rebuilt from the job
        # table - no user data is lost in the process.
        con.execute("DELETE FROM job_fts")
        for row in con.execute("SELECT * FROM job").fetchall():
            con.execute(
                "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
                (row["id"], _text_for_index(dict(row))),
            )
        con.execute("PRAGMA user_version = 2")
        con.commit()
        version = 2
    if version == 2:
        # schema 3: job attachments. Nothing existing is touched.
        con.executescript(SCHEMA_V3)
        con.execute("PRAGMA user_version = 3")
        con.commit()
        version = 3
    if version == 3:
        # schema 4: name and customer come in; order number, machine and tool go
        # out. The content of the dropped columns IS LOST - a deliberate decision,
        # taken while the database held only two trial jobs.
        old_mode = con.isolation_level
        con.isolation_level = None          # we manage the transaction ourselves
        try:
            con.execute("BEGIN")
            columns = [w[1] for w in con.execute("PRAGMA table_info(job)")]
            # The table is rewritten ONLY when there is something to rewrite.
            # Without this check, running the migration against an already-migrated
            # database would pass without error and wipe the name and customer.
            #
            # THE MARK OF AN OLD DATABASE IS `maszyna`, NOT `order_number`.
            # Until this was corrected we looked at the order number column, but a
            # later schema brought that column back. A freshly migrated database would
            # have it again, this migration would judge it old - and would rewrite the
            # table and DELETE the name and customer. A test caught it, one that
            # existed because of exactly the same mistake made earlier. The machine
            # and tool columns never come back, so they are a safe mark.
            if "maszyna" in columns:
                for instruction in SCHEMA_V4:
                    con.execute(instruction)
            # The index is rebuilt because the set of descriptive fields changed.
            # Attachment names return to it - without that, searching by filename
            # would stop working for jobs created before the migration.
            con.execute("DELETE FROM job_fts")
            for row in con.execute("SELECT * FROM job").fetchall():
                record = dict(row)
                names = [w[0] for w in con.execute(
                    "SELECT name FROM attachment WHERE job_id=? ORDER BY id",
                    (record["id"],)).fetchall()]
                con.execute(
                    "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
                    (record["id"], _text_for_index(record, names)),
                )
            con.execute("PRAGMA user_version = 4")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.isolation_level = old_mode
        version = 4
    if version == 4:
        # schema 5: setup-sheet content enters the search index.
        columns = [w[1] for w in con.execute("PRAGMA table_info(attachment)")]
        if "content" not in columns:
            con.execute(SCHEMA_V5)
        # We pull in content for files already on disk. When a file is missing or
        # unreadable, the content stays empty - that is not a failure but an
        # absence, and the job is still findable by its descriptive fields.
        for row in con.execute(
                "SELECT z.id, z.name, w.folder FROM attachment z"
                " JOIN job w ON w.id = z.job_id").fetchall():
            path = os.path.join(data_dir, JOBS_DIR,
                                   row["folder"], FILES_DIR,
                                   row["name"])
            con.execute("UPDATE attachment SET content=? WHERE id=?",
                        (text_for_search(path, row["name"]),
                         row["id"]))
        con.execute("DELETE FROM job_fts")
        for row in con.execute("SELECT * FROM job").fetchall():
            record = dict(row)
            files = con.execute(
                "SELECT name, content FROM attachment WHERE job_id=? ORDER BY id",
                (record["id"],)).fetchall()
            con.execute(
                "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
                (record["id"],
                 _text_for_index(record, [p["name"] for p in files],
                                   [p["content"] for p in files])),
            )
        con.execute("PRAGMA user_version = 5")
        con.commit()
        version = 5
    if version == 5:
        # schema 6: a job's own idempotency key, for entries brought in from a
        # phone.
        # WHY: measured on a trial run - the same job can arrive TWICE, because a
        # stalled upload completes once the laptop comes back. Without a key there
        # would be two jobs and the user would have to delete one, guessing which.
        # ALL OLDER JOBS HAVE AN EMPTY KEY, as do all created on the laptop - which
        # is why the index only guards keys that are NOT empty.
        columns = [w[1] for w in con.execute("PRAGMA table_info(job)")]
        if "idempotency_key" not in columns:
            con.execute("ALTER TABLE job ADD COLUMN idempotency_key TEXT NOT NULL"
                        " DEFAULT ''")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS job_key"
                    " ON job(idempotency_key) WHERE idempotency_key <> ''")
        con.execute("PRAGMA user_version = 6")
        con.commit()
        version = 6
    if version == 6:
        # schema 7: THE ORDER NUMBER RETURNS.
        # This reverses part of an earlier decision, where the order number column
        # was deliberately dropped along with machine and tool. The owner changed
        # his mind after half a year of use - his call.
        # OPTIONAL and SEARCHABLE: an order number is searched exactly like a
        # material, because that is what it is recorded for.
        # Older jobs get an empty number and that is fine - nobody was typing one
        # back then.
        columns = [w[1] for w in con.execute("PRAGMA table_info(job)")]
        if "order_number" not in columns:
            con.execute("ALTER TABLE job ADD COLUMN order_number TEXT NOT NULL"
                        " DEFAULT ''")
        # The index is rebuilt because a descriptive field was added. Without it,
        # an order number typed into an old job would not be findable.
        con.execute("DELETE FROM job_fts")
        for row in con.execute("SELECT * FROM job").fetchall():
            record = dict(row)
            files = con.execute(
                "SELECT name, content FROM attachment WHERE job_id=? ORDER BY id",
                (record["id"],)).fetchall()
            con.execute(
                "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
                (record["id"],
                 _text_for_index(record, [p["name"] for p in files],
                                   [p["content"] for p in files])),
            )
        con.execute("PRAGMA user_version = 7")
        con.commit()
        version = 7
    if version == 7:
        # schema 8: the lexicon feeds the SEARCH INDEX, not only the model text.
        # Without this rebuild the change would apply ONLY to jobs added from now
        # on, and a real catalogue is full of jobs from before.
        # NOTHING IS READ FROM DISK: attachment content lives in the database, so
        # the migration works even when the files are gone or their drive is
        # unplugged. No user data is lost - only the index is rebuilt, the same
        # route as the previous schema.
        con.execute("DELETE FROM job_fts")
        for row in con.execute("SELECT * FROM job").fetchall():
            record = dict(row)
            files = con.execute(
                "SELECT name, content FROM attachment WHERE job_id=? ORDER BY id",
                (record["id"],)).fetchall()
            con.execute(
                "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
                (record["id"],
                 _text_for_index(record, [p["name"] for p in files],
                                   [p["content"] for p in files])),
            )
        con.execute("PRAGMA user_version = 8")
        con.commit()
        version = 8
    return version


def backup(con, data_dir, stamp=None):
    """Back up the database into <data_dir>/backups/. One per day, deletes
    nothing.

    Returns the path of the backup, or None when today's already exists.
    """
    stamp = stamp or datetime.date.today().strftime("%Y-%m-%d")
    target = os.path.join(data_dir, BACKUPS_DIR, "chipbook-%s.db" % stamp)
    if os.path.exists(target):
        return None
    os.makedirs(os.path.dirname(target), exist_ok=True)
    backup_con = sqlite3.connect(target)
    try:
        con.backup(backup_con)
    finally:
        backup_con.close()
    return target


def _text_for_index(record, names=(), contents=()):
    """Text put into the search index - always unaccented.

    We normalise OURSELVES rather than through an SQLite setting - see the
    note beside the first schema. Queries go through the same
    strip_diacritics function, so both sides speak the same alphabet.
    """
    parts = [str(record.get(field) or "") for field in DESCRIPTIVE_FIELDS]
    parts.extend(str(n) for n in names)
    parts.extend(str(t) for t in contents if t)
    text = "\n".join(parts)
    # CLARIFICATIONS ARE APPENDED, NOT SUBSTITUTED. The label from the file
    # stays in the index, because that is what a person searches for in the
    # CAM system - the same rule as for the text given to the ai.
    translated = synonyms_for_index(text)
    if translated:
        text = text + "\n" + "\n".join(translated)
    return strip_diacritics(text)
