"""The catalogue: jobs, their attachments, and the search over them.

WHAT THIS MODULE IS: the one door to the data. Everything the window and
the server do to a job goes through the Catalog class, so there is exactly
one place where a rule about jobs can live.

WHAT IT IS NOT: it does not know what a browser is, it does not build
HTML, and it does not talk to the model itself - it hands the model a
finished piece of text and checks what comes back.

THE FILES ON DISK ARE THE ORIGINAL, THE DATABASE IS THE INDEX. Every job
also leaves a readable job.txt beside its files, so that the catalogue can
be rebuilt from the folders alone if the database is ever lost.
"""

import datetime
import difflib
import hashlib
import os
import sqlite3
import threading

from . import (BACKUPS_DIR, ChipbookError, DB_FILENAME, FILES_DIR,
               JOBS_DIR, METADATA_FILENAME, NOTE_SEPARATOR)
from . import ai
from . import setupsheet
from .ai import Answer
from .ai.grounding import (FOLLOW_UP_WARNING, restate_facts,
                           source_is_supported, trim_to_question,
                           unsupported_numbers)
from . import attachments
from .attachments import (MAX_ATTACHMENT_BYTES, archive_instead_of_delete,
                          safe_filename, text_for_model, text_for_search,
                          _file_sha256, _free_filename, _free_job_dir)
from .schema import (REQUIRED_FIELDS, backup, _check_environment,
                     _migrate, _text_for_index)
from .search import (MIN_COMMON_STEM, MIN_WORD_LENGTH, SearchResult,
                     TYPO_THRESHOLD, difference_values, differing_fields,
                     same_word_family, strip_diacritics, _words)


# THE PROGRAM VERSION NUMBER. Raised BY HAND, just before an update
# package is built.
# WHY IT EXISTS: users report problems with a screenshot. Without a number
# there is no way to answer the two questions that come up with EVERY
# report: "are we looking at the same program?" and "did the previous
# update reach them at all?".
# THIS IS NOT SCHEMA_VERSION. That one describes the layout of the
# database and moves only when tables change; this one describes the
# program.
PROGRAM_VERSION = 26

PROGRAM_DATE = "2026-08-13"


# How many search candidates the model gets to read.
# NOT A NUMBER OUT OF THIN AIR: measured - one setup sheet is roughly
# 900-1000 tokens, and a model with an 8K context fits four to five jobs at
# once TOGETHER with the question and the answer. Three leaves room for
# longer notes than we have seen so far.
CANDIDATE_LIMIT = 3


def parse_client_time(when):
    """A timestamp supplied by a phone, normalised to our own format.

    Returns "YYYY-MM-DD HH:MM:SS", or None when the given text cannot be
    read honestly. WE DO NOT GUESS: better to take the laptop's clock than
    to write a date into the database that nobody confirmed.

    WE ALSO REJECT DATES MORE THAN A DAY IN THE FUTURE - a phone with a
    wrong clock would file a job in 2031 and the user would never find it
    where they look for it.
    """
    if not when:
        return None
    text = str(when).strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            moment = datetime.datetime.strptime(text[:len("2026-08-08 12:00:00")],
                                                pattern)
        except ValueError:
            continue
        if moment > datetime.datetime.now() + datetime.timedelta(days=1):
            return None
        return moment.replace(microsecond=0).isoformat(" ")
    return None


def open_catalog(data_dir):
    """Open the database in the given directory, creating it if needed."""
    data_dir = os.path.abspath(data_dir)
    os.makedirs(os.path.join(data_dir, JOBS_DIR), exist_ok=True)
    os.makedirs(os.path.join(data_dir, BACKUPS_DIR), exist_ok=True)

    path = os.path.join(data_dir, DB_FILENAME)
    existed = os.path.exists(path)

    # check_same_thread=False plus a lock on the class: the connection may be
    # used from another thread, but only ever by one at a time
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=wal")

    _check_environment(con)

    # THE BACKUP IS TAKEN BEFORE THE MIGRATION - if a migration breaks
    # something, there is something to go back to
    if existed:
        backup(con, data_dir)

    _migrate(con, data_dir)
    return Catalog(con, data_dir)

FIELD_LABELS = {"name": "Name", "customer": "Customer", "material": "Material"}


# LABELS USED IN THE TEXT SUPPLIED TO THE MODEL.
# SEPARATE FROM THE UI LABELS, and that is not decoration: those also go
# into error messages ("Material is empty"), where the qualifier "part"
# would read oddly. Here the concern is different - the model sees the
# part material and the tool material in ONE text, so both labels have to
# say by themselves what they refer to.
MODEL_FIELD_LABELS = dict(FIELD_LABELS)
MODEL_FIELD_LABELS["material"] = "Part material"


def _check_required(**fields):
    """Check the mandatory fields and return them stripped of whitespace."""
    cleaned = {}
    for key in REQUIRED_FIELDS:
        value = (fields.get(key) or "").strip()
        if not value:
            raise ChipbookError(
                "%s is a required field - without it the job cannot be "
                "found again." % FIELD_LABELS[key])
        cleaned[key] = value
    return cleaned


class Catalog:

    def __init__(self, con, data_dir):
        self.con = con
        self.data_dir = data_dir
        # SQLite dislikes two things at once on one connection; the lock is
        # re-entrant, so methods may call one another
        self.lock = threading.RLock()

    def close(self):
        with self.lock:
            self.con.close()

    # ------------------------------------------------------- saving

    def job_by_key(self, idempotency_key):
        """The job brought in from a phone under this key, or None.

        The key is issued by THE PHONE when the entry is created and does
        not change on a resend. That is how the laptop recognises the same
        job rather than a second identical one.
        """
        idempotency_key = (idempotency_key or "").strip()
        if not idempotency_key:
            return None
        with self.lock:
            row = self.con.execute(
                "SELECT id FROM job WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            return self._record(row["id"]) if row else None

    def add_job(self, name, customer, material, notes,
                   idempotency_key=None, when=None, order_number=None):
        """Add a job. Mandatory: name, customer, material.

        No file is required - and that does not change.

        TWO FIELDS FOR AN ENTRY FROM A PHONE, both optional:
          idempotency_key - issued by the phone. When a job with that key
                  ALREADY EXISTS, a second one is not created - that one
                  comes back. It is the only defence against a repeated
                  upload, because the phone has no way of knowing whether
                  the previous one arrived.
          when  - the moment the job was created ON THE PHONE, at the
                  machine. Without it the job would take the timestamp of
                  the evening sync and lose the whole point of being
                  written at the machine.
        """
        with self.lock:
            idempotency_key = (idempotency_key or "").strip()
            if idempotency_key:
                existing = self.con.execute(
                    "SELECT id FROM job WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if existing:
                    # A SECOND UPLOAD OF THE SAME ENTRY. This is not an error and
                    # there is no reason to shout about it - the phone gets that job
                    # back and can add files to it.
                    return self._record(existing["id"])
            return self._add_job(name, customer, material, notes,
                                    idempotency_key, when, order_number)

    def _add_job(self, name, customer, material, notes, idempotency_key, when,
                    order_number=None):
        # The lock is re-entrant, so taking it again is allowed - and taking
        # it explicitly here protects this method even if someone ever calls
        # it from somewhere else.
        with self.lock:
            fields = _check_required(name=name, customer=customer,
                                        material=material)
            # THE NOTE IS NO LONGER MANDATORY.
            # This reverses part of the earlier rule. The reason: better that a job
            # can be saved immediately than that a person hits a wall here. The
            # price, stated plainly: a job with no note is just a name, a customer
            # and a material - which is not what anyone searches for years later.
            # The value of this catalogue sits in that one sentence.
            # Encouragement stays in the UI (a hint in the field); compulsion does
            # not.
            notes = (notes or "").strip()

            now = datetime.datetime.now().replace(microsecond=0).isoformat(" ")
            # THE MOMENT OF CREATION COMES FROM THE PHONE when the phone supplies
            # it and it can be read. When it supplies garbage, we take the laptop's
            # clock and the job IS STILL CREATED. A broken date is not a reason to
            # lose work somebody typed in at the machine.
            created_at = parse_client_time(when) or now

            with self.con:
                cursor = self.con.execute(
                    "INSERT INTO job (created_at, updated_at, name, customer,"
                    " material, notes, idempotency_key, order_number)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (created_at, now, fields["name"], fields["customer"],
                     fields["material"], notes, idempotency_key or "",
                     (order_number or "").strip()),
                )
                number = cursor.lastrowid
                # The folder name takes THE JOB'S DATE, not the date it was let into
                # the database - a job from yesterday afternoon belongs under
                # yesterday's date. The free-folder helper makes sure a new job never
                # enters somebody else's folder.
                folder = _free_job_dir(
                    os.path.join(self.data_dir, JOBS_DIR),
                    "%s_%04d" % (created_at[:10], number))
                self.con.execute("UPDATE job SET folder=? WHERE id=?",
                                 (folder, number))
                record = self._record(number)
                self.con.execute(
                    "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
                    (number, _text_for_index(record)),
                )
                # the file is written INSIDE the transaction: if the write fails, the
                # database returns to the state before the entry
                self._write_metadata(record)

            return record

    def rebuild_from_folders(self, dry_run=True):
        """Rebuild jobs from the folders lying on disk.

        WHY: when someone moves to a new machine, or the database is
        damaged and only THE JOB FOLDERS remain, this tool reassembles the
        database from them. Retyping a hundred jobs by hand is not an
        option.

        THIS IS AN EMERGENCY ROUTE, NOT AN EVERYDAY ONE. A normal move
        means copying the WHOLE data directory including chipbook.db and
        pointing the program at it - then there is nothing to rebuild.
        This function is for when the database is gone.

        WHY IT IS POSSIBLE AT ALL: from the first day, every job is written
        beside the database as a readable copy (`job.txt`). At the time
        that sounded like caution - it turns out to be the way back.

        WHAT IT DELIBERATELY DOES NOT DO:
          - it never deletes or alters ANY existing job;
          - it skips folders already in the database (recognised by folder
            NAME, not by number - numbers can drift apart);
          - a broken `job.txt` is skipped and reported, rather than
            aborting the whole run.

        `dry_run=True` (the default) WRITES NOTHING and only reports what
        it would do. Rebuilding for real has to be asked for explicitly.

        Returns a dict: added, skipped, errors (lists of folder names).
        """
        with self.lock:
            directory = os.path.join(self.data_dir, JOBS_DIR)
            result = {"added": [], "skipped": [], "errors": []}
            if not os.path.isdir(directory):
                return result

            known = {w[0] for w in self.con.execute(
                "SELECT folder FROM job WHERE folder IS NOT NULL")}

            for folder_name in sorted(os.listdir(directory)):
                path = os.path.join(directory, folder_name)
                if not os.path.isdir(path):
                    continue
                if folder_name in known:
                    result["skipped"].append(folder_name)
                    continue
                data = _read_metadata(os.path.join(path, "job.txt"))
                if data is None:
                    result["errors"].append(folder_name)
                    continue
                if not dry_run:
                    self._insert_restored(folder_name, path, data)
                result["added"].append(folder_name)

            return result

    def _insert_restored(self, folder_name, path, data):
        """Insert one restored job together with its attachments.

        The number is issued afresh by the database - the old one from the
        file is ignored, because on a new machine it could collide with an
        existing one. What stays constant is the FOLDER NAME, and that is
        how we recognise a job already present.
        """
        with self.con:
            cursor = self.con.execute(
                "INSERT INTO job (folder, created_at, updated_at, name,"
                " customer, material, notes) VALUES (?,?,?,?,?,?,?)",
                (folder_name, data["created_at"], data["updated_at"],
                 data["name"], data["customer"], data["material"],
                 data["notes"]))
            number = cursor.lastrowid

            files_dir = os.path.join(path, FILES_DIR)
            if os.path.isdir(files_dir):
                for file in sorted(os.listdir(files_dir)):
                    full_text = os.path.join(files_dir, file)
                    if not os.path.isfile(full_text):
                        continue
                    self.con.execute(
                        "INSERT INTO attachment (job_id, name, size_bytes, sha256,"
                        " added_at, content) VALUES (?,?,?,?,?,?)",
                        (number, file, os.path.getsize(full_text),
                         _file_sha256(full_text), data["created_at"],
                         text_for_search(full_text, file)))

            record = self._record(number)
            self._reindex(number, record)

    def append_note(self, number, text):
        """Append to an existing job. Overwrites nothing."""
        with self.lock:
            text = (text or "").strip()
            if not text:
                raise ChipbookError("The note is empty - there is nothing to save.")
            record = self._record(number)
            if record is None:
                raise ChipbookError("There is no job numbered %r." % (number,))

            now = datetime.datetime.now().replace(microsecond=0).isoformat(" ")
            merged = record["notes"] + "\n\n[appended " + now + "]\n" + text

            with self.con:
                self.con.execute(
                    "UPDATE job SET notes=?, updated_at=? WHERE id=?",
                    (merged, now, number),
                )
                record = self._record(number)
                # _reindex, not by hand: it re-adds the attachment names.
                # Appending a note used to wipe them from the index and the job
                # stopped being findable by filename - silently.
                self._reindex(number, record)
                self._write_metadata(record)

            return record

    def update_fields(self, number, name, customer, material, notes=None,
                   order_number=None):
        """Amend a job that has already been saved.

        THE NOTE: when supplied, it REPLACES the existing one. The old
        content is lost, and this is the only place in the program where
        something a person typed by hand can be lost. The price was stated
        before the decision was taken.
        The net that remains: the daily database backup.
        When the note is None the field is left alone - and appending a
        note works as before and still overwrites nothing.
        """
        with self.lock:
            record = self._record(number)
            if record is None:
                raise ChipbookError("There is no job numbered %r." % (number,))
            fields = _check_required(name=name, customer=customer,
                                        material=material)
            # The order number. When not supplied it is left alone, exactly like
            # the note. Correcting a typo in the number has to be possible, or a
            # wrong number stays forever.
            if order_number is not None:
                fields["order_number"] = (order_number or "").strip()
            if notes is not None:
                # See add_job: the note is no longer mandatory. When amending a job
                # that also means the note may be CLEARED - and that is a change to
                # data a person typed by hand, so it has to be possible deliberately
                # and not by accident.
                fields["notes"] = (notes or "").strip()

            now = datetime.datetime.now().replace(microsecond=0).isoformat(" ")
            # column names come from our own code, never from the user
            assignments = ", ".join("%s=?" % k for k in fields)
            with self.con:
                self.con.execute(
                    "UPDATE job SET " + assignments + ", updated_at=? WHERE id=?",
                    tuple(fields.values()) + (now, number),
                )
                record = self._record(number)
                # _reindex, not by hand: it re-adds the attachment names.
                # Appending a note used to wipe them from the index and the job
                # stopped being findable by filename - silently.
                self._reindex(number, record)
                self._write_metadata(record)

            return record

    def delete_job(self, number):
        """Remove a job from the program and hand its folder to the SYSTEM
        RECYCLE BIN.

        No recycle bin of our own inside the program - we use the one the
        operating system already has and the user already knows.

        The order: first a clean write to the database, then the folder. If
        the folder cannot be handed to the bin, the job has still gone from
        the program and the folder stays on disk - and we say so plainly
        rather than pretending everything worked.

        Returns a dict describing what actually happened.
        """
        with self.lock:
            record = self._record(number)
            if record is None:
                raise ChipbookError("There is no job numbered %r." % (number,))

            directory = self.job_dir(record)
            now = datetime.datetime.now().replace(microsecond=0).isoformat(" ")

            # before the folder leaves, we leave the deletion date inside it - so
            # that after recovery from the bin it is clear what it is
            if os.path.isdir(directory):
                try:
                    files = self.con.execute(
                        "SELECT name, size_bytes, sha256 FROM attachment WHERE job_id=?"
                        " ORDER BY id", (number,)).fetchall()
                    text = _metadata_text(record, [dict(p) for p in files])
                    text += "\ndeleted from chipbook: " + now + "\n"
                    with open(os.path.join(directory, METADATA_FILENAME), "w",
                              encoding="utf-8", newline="\n") as file:
                        file.write(text)
                except OSError:
                    pass  # a metadata file must never block a deletion

            with self.con:
                self.con.execute("DELETE FROM job_fts WHERE rowid=?", (number,))
                self.con.execute("DELETE FROM attachment WHERE job_id=?", (number,))
                self.con.execute("DELETE FROM job WHERE id=?", (number,))

            result = {"id": number, "folder": record["folder"],
                     "name": record["name"],
                     "material": record["material"], "where": None,
                     "path": directory}
            if not os.path.isdir(directory):
                result["where"] = "no_folder"
                return result
            if attachments.move_to_recycle_bin(directory) == "recycle_bin":
                result["where"] = "recycle_bin"
                return result
            try:
                result["path"] = archive_instead_of_delete(directory,
                                                         self.data_dir)
                result["where"] = "moved"
            except OSError:
                result["where"] = "left_in_place"
            return result

    def delete_attachment(self, attachment_number):
        """Remove ONE file from a job and hand the file to the SYSTEM RECYCLE
        BIN.

        Until this existed you could delete a whole job or nothing. To get
        rid of one wrongly attached file you had to delete the job together
        with its note.

        IT TAKES THE SAME ROUTE AS DELETING A JOB, AND THAT IS NOT
        LAZINESS: first a clean write to the database, then the file, and
        when the bin will not take it we say plainly that it stayed on
        disk rather than pretending. No recycle bin of our own.

        THE JOB REMAINS, even when its last file disappears. A job without
        a file has been legal since the first day - the value is in the
        note, not in the attachment.

        THE CONTENT ALSO LEAVES THE SEARCH INDEX. If it stayed, a person
        would find a job by words from a file that is no longer there,
        with no way to work out where those words came from.

        Returns a dict describing what actually happened.
        """
        with self.lock:
            row = self.con.execute(
                "SELECT * FROM attachment WHERE id=?",
                (attachment_number,)).fetchone()
            if row is None:
                raise ChipbookError("There is no file numbered %r."
                                   % (attachment_number,))
            attachment = dict(row)
            record = self._record(attachment["job_id"])
            if record is None:
                raise ChipbookError("The job this file belongs to is missing.")

            path = os.path.join(self.files_dir(record),
                                   attachment["name"])
            now = datetime.datetime.now().replace(
                microsecond=0).isoformat(" ")
            with self.con:
                self.con.execute("DELETE FROM attachment WHERE id=?",
                                 (attachment_number,))
                self.con.execute("UPDATE job SET updated_at=? WHERE id=?",
                                 (now, attachment["job_id"]))
                record = self._record(attachment["job_id"])
                self._reindex(attachment["job_id"], record)
                self._write_metadata(record)

            result = {"id": attachment_number, "job_id": attachment["job_id"],
                     "name": attachment["name"], "where": None,
                     "path": path}
            if not os.path.exists(path):
                result["where"] = "no_file"
                return result
            if attachments.move_to_recycle_bin(path) == "recycle_bin":
                result["where"] = "recycle_bin"
                return result
            # THE FALLBACK, the same one as for deleting a job. Without it the
            # file would stay in the job folder: the database would say "gone"
            # while the file sat there beside the others and came back on every
            # rebuild from folders. A system recycle bin does not always exist.
            try:
                result["path"] = archive_instead_of_delete(path,
                                                         self.data_dir)
                result["where"] = "moved"
            except OSError:
                result["where"] = "left_in_place"
            return result

    # ------------------------------------------------------ reading

    def job(self, number):
        with self.lock:
            return self._record(number)

    def job_count(self):
        with self.lock:
            return self.con.execute("SELECT count(*) FROM job").fetchone()[0]

    def job_dir(self, record):
        return os.path.join(self.data_dir, JOBS_DIR,
                            record["folder"])

    def recent(self, limit=None):
        """Jobs newest first - what is on screen when the program opens.

        NO LIMIT BY DEFAULT. The list is called "All jobs" and is supposed
        to show all of them, with the counter beside it giving the real
        number, not the number displayed. Cutting off at the first fifty
        made that label untrue.
        """
        with self.lock:
            if limit is None:
                job_numbers = [w[0] for w in self.con.execute(
                    "SELECT id FROM job ORDER BY id DESC")]
            else:
                job_numbers = [w[0] for w in self.con.execute(
                    "SELECT id FROM job ORDER BY id DESC LIMIT ?", (limit,))]
            return [self._record(n) for n in job_numbers]

    def customers(self):
        """Customers from the database, alphabetically, each with a job count.

        THE LIST IS BUILT FROM THE DATABASE, NOT FROM ANY LOOKUP TABLE.
        There is no customers table and there will not be one - a customer
        is a text field on a job and nothing more. That way the list cannot
        drift out of step with reality, and it is impossible to have a
        customer with no jobs at all.

        THE JOB COUNT IS THERE ON PURPOSE: without it, a customer typed
        once with a typo looks exactly like one you have worked for all
        year. "ACME 41" next to "ACEM 1" says plainly where the mistake is.
        WE DO NOT CORRECT TYPOS - this is data a person typed by hand, and
        merging it silently would be guessing.
        """
        with self.lock:
            # CASE DOES NOT MAKE A SECOND CUSTOMER. "ACME" and "acme" are one
            # thing to a person, and the per-customer view shows both anyway.
            # CAUGHT ON THE FIRST TRY: without this the list said "ACME 2" and
            # "acme 1", and clicking either gave THREE jobs. The number beside the
            # name would contradict what appears after the click - and that is
            # worse than having no number.
            # WE DO NOT CORRECT THE DATA, only group it for display. In the
            # database both spellings stay exactly as they were typed.
            groups = {}
            for customer, count in self.con.execute(
                    "SELECT customer, COUNT(*) FROM job"
                    " WHERE TRIM(customer) <> '' GROUP BY customer"):
                key = customer.strip().lower()
                group = groups.setdefault(key, {"customer": customer.strip(),
                                                 "count": 0, "most": 0})
                group["count"] += count
                # We display the spelling USED MOST OFTEN - because that is the one
                # the person treats as correct.
                if count > group["most"]:
                    group["most"] = count
                    group["customer"] = customer.strip()
            return sorted(({"customer": g["customer"], "count": g["count"]}
                           for g in groups.values()),
                          key=lambda g: g["customer"].lower())

    def jobs_for_customer(self, customer):
        """One customer's jobs, newest first.

        COMPARED CASE-INSENSITIVELY, because "ACME" and "Acme" are one
        customer to a person, while the customer list prints them
        separately. We do not merge them in the list (that would be
        correcting someone's data), but when one is clicked we show both -
        otherwise half the jobs would vanish without trace.
        """
        customer = (customer or "").strip()
        if not customer:
            return []
        with self.lock:
            job_numbers = [w[0] for w in self.con.execute(
                "SELECT id FROM job WHERE LOWER(TRIM(customer))=LOWER(?)"
                " ORDER BY id DESC", (customer,))]
            return [self._record(n) for n in job_numbers]

    def suggestions(self, limit=40):
        """Values used before, most recently used first.

        Nothing is imposed - these are suggestions, not a closed list.
        The name field has none, because every job is called something
        different.
        """
        with self.lock:
            result = {}
            for field in ("customer", "material"):
                # the field name comes from this tuple, never from the user
                rows = self.con.execute(
                    "SELECT %s AS value, max(id) AS last_id FROM job"
                    " WHERE %s <> '' GROUP BY lower(%s)"
                    " ORDER BY last_id DESC LIMIT ?" % (field, field, field),
                    (limit,),
                ).fetchall()
                result[field] = [w["value"] for w in rows]
            return result

    # -------------------------------------------------------- attachments

    def files_dir(self, record):
        return os.path.join(self.job_dir(record), FILES_DIR)

    def attachments(self, number):
        """A job's attachments, oldest first."""
        with self.lock:
            record = self._record(number)
            if record is None:
                return []
            directory = self.files_dir(record)
            result = []
            for row in self.con.execute(
                    "SELECT * FROM attachment WHERE job_id=? ORDER BY id",
                    (number,)).fetchall():
                item = dict(row)
                item["path"] = os.path.join(directory, item["name"])
                item["present"] = os.path.exists(item["path"])
                result.append(item)
            return result

    def attachment(self, attachment_number):
        with self.lock:
            row = self.con.execute(
                "SELECT * FROM attachment WHERE id=?",
                (attachment_number,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            record = self._record(item["job_id"])
            if record is None:
                return None
            item["path"] = os.path.join(self.files_dir(record),
                                              item["name"])
            item["present"] = os.path.exists(item["path"])
            return item

    def add_attachment(self, number, name, source, size_bytes):
        """Copy a file into the job folder.

        `source` is anything with a read(n) method - a file or a stream
        from the browser. The original is NOT touched: we take a copy.

        The order matters: first the file is written cleanly to disk under
        a temporary name, and only then the record goes into the database.
        If anything fails along the way, the database returns to its
        earlier state - and at worst a .partial file is left on disk, which
        nobody will delete behind the user's back.
        """
        if size_bytes is None or size_bytes < 0:
            raise ChipbookError("The size of this file is unknown.")
        if size_bytes > MAX_ATTACHMENT_BYTES:
            raise ChipbookError(
                "This file is %.1f GB and chipbook accepts up to %.1f GB. "
                "Save the job without it and put the path in the note."
                % (size_bytes / 1073741824.0, MAX_ATTACHMENT_BYTES / 1073741824.0))

        with self.lock:
            record = self._record(number)
            if record is None:
                raise ChipbookError("There is no job numbered %r." % (number,))

            directory = self.files_dir(record)
            os.makedirs(directory, exist_ok=True)
            name = _free_filename(directory, safe_filename(name))
            target = os.path.join(directory, name)
            temporary = target + ".partial"

            total = hashlib.sha256()
            written = 0
            with open(temporary, "wb") as file:
                while written < size_bytes:
                    chunk = source.read(min(1024 * 1024, size_bytes - written))
                    if not chunk:
                        break
                    file.write(chunk)
                    total.update(chunk)
                    written += len(chunk)
            if written != size_bytes:
                raise ChipbookError(
                    "File %s was cut off halfway (%d of %d bytes). "
                    "Nothing was saved." % (name, written, size_bytes))

            # The content is read from the TEMPORARY file, before we enter the
            # transaction - it is already complete, and the reader recognises the
            # format by the target name, not by the .partial suffix.
            content = text_for_search(temporary, name)

            now = datetime.datetime.now().replace(microsecond=0).isoformat(" ")
            with self.con:
                self.con.execute(
                    "INSERT INTO attachment (job_id, name, size_bytes, sha256,"
                    " added_at, content) VALUES (?,?,?,?,?,?)",
                    (number, name, written, total.hexdigest(), now, content))
                os.replace(temporary, target)
                self.con.execute("UPDATE job SET updated_at=? WHERE id=?",
                                 (now, number))
                record = self._record(number)
                self._reindex(number, record)
                self._write_metadata(record)

            return self.attachments(number)[-1]

    # ------------------------------------------------------------ search

    def search(self, query, limit=50):
        """Search across all fields and notes.

        The attempts, each one only after the previous returned ZERO:
          1. exactly what was typed;
          2. typo correction;
          3. other forms of the same word, taken from the base itself;
          4. setting aside words that appear in no job at all.

        Step 3 exists so that a whole sentence can be used as a question.
        We look for jobs containing ALL the words, so a single word that
        appears nowhere would zero the entire result - and an ordinary
        spoken question is full of exactly such words.
        Measured: the question "what diameter was that extra hole I had to
        add myself, the shaft one" returned 0 hits although the job was in
        the database.

        Corrections, forms and omissions are REPORTED, never silent.
        """
        with self.lock:
            words = _words(query or "")
            if not words:
                return SearchResult([], [], words)

            jobs = self._search_word(words, limit)
            if jobs:
                return SearchResult(jobs, [], words)

            lexicon = self._lexicon()
            corrected, corrections = self._fix_typos(words, lexicon)
            if corrections:
                jobs = self._search_word(corrected, limit)
                if jobs:
                    return SearchResult(jobs, corrections, words)
                # A CORRECTION THAT DID NOT HELP IS ROLLED BACK - because it can do
                # HARM. The matcher replaces a word that is absent with a SIMILAR word
                # that IS present - and that new word becomes required from then on,
                # although the person never typed it.
                # The step below sets aside words with no hits, but a substituted word
                # HAS hits, so it would never be dropped.
                # MEASURED live: a question asked as a whole sentence returned
                # nothing, because one ordinary word of it had been corrected into a
                # word from a cutter name in somebody else's setup sheet. The answer
                # was sitting in the database.
                corrected, corrections = words, []

            missing = self._words_without_hits(corrected)
            if not missing:
                return SearchResult([], corrections, words)

            # Step 3: a word that is absent may have another of its forms in the
            # base. We look for it ONLY for words with no hits - a successful
            # search has to behave exactly as it did yesterday.
            groups = []
            forms = []
            still_missing = []
            for word in corrected:
                if word not in missing:
                    groups.append([word])
                    continue
                other = self._word_forms(word, lexicon)
                if other:
                    groups.append(other)
                    forms.append((word, other))
                else:
                    still_missing.append(word)

            groups = [g for g in groups if g]
            if not groups:
                # nothing from this question is in the base - we do not invent
                return SearchResult([], corrections, words, still_missing, forms)
            return SearchResult(self._search_group(groups, limit), corrections, words,
                         still_missing, forms)

    # --------------------------------------------- question to the model

    def candidates_for_question(self, question, limit=CANDIDATE_LIMIT):
        """The jobs that best match a question - different from a manual search.

        WHY A SEPARATE ROUTE rather than the same one as search(): a manual
        search demands that a job contain ALL the typed words, and there
        that is a good rule - a person types two words and wants exactly
        those. For a question asked as a sentence the same rule does harm:
        one word that happens to appear in a DIFFERENT job is enough to
        sink the whole question.

        WHAT WE DO INSTEAD: we count HOW MANY of the question's words stand
        in each job, and take the best. That is ordering, not filtering.

        WHAT REMAINS OF THE "NOT IN THE CATALOGUE" GUARANTEE - and this is
        the most important sentence here. The variant "any single word will
        do" was rejected, because then everything matches everything and an
        honest "not here" becomes impossible. Hence a THRESHOLD: a job must
        carry at least half of the words that exist in the base at all.
        Below the threshold there are no candidates, the model is not
        called, and the user is told it is not in the catalogue.

        THE THRESHOLD IS A DIAL TO BE MEASURED, not a constant out of thin
        air - like the common-stem length. Half for now, because a small
        base gives nothing to measure on. It comes back when there are real
        jobs to measure against.

        Words present in NO job are set aside exactly as in a manual
        search, and reported exactly the same way.
        """
        with self.lock:
            everything = _words(question or "")
            words = [s for s in everything if len(s) >= MIN_WORD_LENGTH]
            if not words:
                return SearchResult([], [], everything)

            missing = self._words_without_hits(words)
            lexicon = self._lexicon() if missing else []

            # A word with no hits may have ANOTHER OF ITS FORMS in the base -
            # exactly as in a manual search. Without this, "how many holes did I
            # drill in that bushing" would find nothing, because the base holds
            # "hole" and "bushing" rather than the inflected forms.
            groups = []
            skipped = []
            forms = []
            for word in words:
                if word not in missing:
                    groups.append([word])
                    continue
                other = self._word_forms(word, lexicon)
                if other:
                    groups.append(other)
                    forms.append((word, other))
                else:
                    skipped.append(word)

            if not groups:
                return SearchResult([], [], everything, skipped, forms)

            # A group counts as hit when ANY of its forms is present - but each
            # group is still one word from the question, so the threshold behaves
            # the same.
            threshold = (len(groups) + 1) // 2
            fts_query = " OR ".join('"%s"' % s for g in groups for s in g)
            rows = self.con.execute(
                "SELECT rowid, content FROM job_fts WHERE job_fts MATCH ?"
                " LIMIT ?", (fts_query, max(limit * 20, 100))).fetchall()

            points = []
            for row in rows:
                record = self._record(row[0])
                if record is None:
                    continue
                # We count against THE SAME text that sits in the index - that is,
                # including attachment names and setup-sheet content.
                content = str(row[1] or "").lower()
                count = sum(1 for g in groups if any(s in content for s in g))
                if count >= threshold:
                    points.append((count, record))

            # ONLY THE BEST-MATCHING JOBS SURVIVE. The threshold lets through
            # anyone holding half the words - but when one job matches four words
            # and another three, they are not equal candidates.
            #
            # WHY THIS MATTERS AND IS NOT COSMETIC (reported live): without it the
            # program asked "which job do you mean?", the person answered "I don't
            # remember" - adding NO information at all - and the program still
            # managed to pick one. If it could pick then, it could have picked from
            # the start. The question was empty, and an empty question is worse
            # than none.
            #
            # THE PRICE, STATED: when the difference is one incidental word, the
            # program chooses by itself instead of asking. That is visible on
            # screen though - the card of the job the answer came from stands right
            # beneath it.
            # SO WE ASK ONLY ON A TIE, that is when there really is nothing to
            # decide by.
            if points:
                best = max(count for count, _ in points)
                points = [p for p in points if p[0] == best]

            points.sort(key=lambda pair: -pair[0])
            return SearchResult([r for _, r in points[:limit]], [], everything,
                         skipped, forms)

    def job_text_for_model(self, record):
        """One job assembled into the text the model will receive.

        It consists of what a person wrote (name, customer, material, note)
        AND of the setup-sheet content. The job number is in the heading so
        that it is possible to check which job the model is talking about -
        but the link itself still comes from the search, never from the
        model's answer.

        ATTACHMENT CONTENT IS COMPUTED FROM THE FILE rather than taken from
        the database column. The reason: the database holds FLAT text
        assembled for the search index - a model could not tell from it
        which number belongs to which operation.
        THE PRICE, STATED: when a file disappears from disk, the model gets
        the job without its setup sheet. The search still finds it, because
        THAT reads from the database. Chosen deliberately as cheaper than
        another database schema - to be revisited if files start
        disappearing or if the assembly turns out slow (measured: 0.098 s
        for a six-page PDF).
        """
        lines = ["=== JOB %s: %s ===" % (record["id"], record["name"] or "")]
        for field in ("customer", "material"):
            value = str(record[field] or "").strip()
            if value:
                lines.append("%s: %s" % (MODEL_FIELD_LABELS[field], value))
        notes = str(record["notes"] or "").strip()
        if notes:
            lines.append("Technologist's notes:")
            lines.append(notes)

        for attachment in self.attachments(record["id"]):
            if not attachment.get("present"):
                continue
            text = text_for_model(attachment["path"], attachment["name"])
            if text:
                # THE HEADING SAYS THIS IS STILL THE SAME ENTRY - a correction
                # that came out of a measurement.
                # With a heading reading "--- setup sheet: X ---" the model
                # treated the setup sheet as something SEPARATE from the entry.
                # Asked "did I use a chamfer mill" it answered "no, the log only
                # says you packed shims underneath" - it read the person's own
                # note and stopped there, with two chamfer mills listed a dozen
                # lines below.
                # That it had seen that text is clear from the same run: a harder
                # question ("the chamfer mill not for holes") it answered
                # correctly, straight from operation 5.
                # WE CALL A FILE WHAT IT IS. Every attachment used to be labelled
                # "setup sheet", including NC programs - two different things the
                # model had no way to tell apart.
                what_kind = ("program NC"
                        if setupsheet.extension(attachment["name"])
                        in setupsheet.GCODE_EXTENSIONS else "setup sheet")
                lines.append("--- JOB %s CONTINUED: %s %s ---"
                             % (record["id"], what_kind, attachment["name"]))
                lines.append(text)
        return "\n".join(lines)

    def ask(self, question, clarifications=(), limit=CANDIDATE_LIMIT,
                   conversation=None, model_name=None, number=None):
        """A question in plain words -> search -> model -> answer plus links.

        THE ORDER IS NOT A MATTER OF TASTE: the model NEVER searches the
        database, it only reads what it is given, and the link comes from
        the search rather than from its answer.

        WHY THIS IS NOT MERE ELEGANCE: measured - one setup sheet is about
        900-1000 tokens, and a model sees 8K at once. With a dozen jobs the
        whole catalogue physically does not fit. The search is not a stage
        BEFORE the model - it is the model's first half.

        THREE CASES:
          no candidates  -> "not in the catalogue", THE MODEL IS NOT
                            CALLED;
          one candidate  -> an answer plus a link;
          several        -> "I have a few jobs like this, they differ in
                            this and that - which one?", and AGAIN THE
                            MODEL IS NOT CALLED.

        WHY THE MODEL IS NOT ASKED WHEN THERE ARE SEVERAL - a correction
        from measurement, not caution for its own sake. The model was given
        two jobs with very similar histories (both: a wrong drawing and an
        added hole) and MERGED THEM: it named the bushing correctly but
        pushed the hole diameter from the other job into the answer. With
        two similar entries there is no guessing which is meant - and that
        must not depend on a ai. A person points at the job, and then we
        ask about THAT ONE.

        A CONVERSATION, NOT A SINGLE QUESTION: `clarifications` is a list of
        (model's question, person's answer). Every answer from the person
        IS APPENDED TO THE QUERY and the search runs again across the WHOLE
        catalogue. So narrowing is still done by the search, not by the
        model - the "only from the catalogue" guarantee holds.
        We always answer the ORIGINAL question; the clarifications serve
        only to find the right job.

        `number` - when given, the search is skipped and we ask about that
        one job.
        The conversation callback is injected in tests, so that this whole
        mechanism can be checked WITHOUT a running ai.
        """
        pairs = [(str(p or ""), str(o or "")) for p, o in clarifications]
        if number is not None:
            record = self._record(number)
            if record is None:
                return Answer("none", ai.NOT_IN_CATALOG)
            result = SearchResult([record], [], _words(question or ""))
        else:
            phrase = " ".join([question or ""] + [o for _, o in pairs]).strip()
            result = self.candidates_for_question(phrase, limit=limit)

        if not result.jobs:
            return Answer("none", ai.NOT_IN_CATALOG, result=result)

        if conversation is None:
            # A FORM, NOT A REQUEST. The fields helper makes ollama enforce
            # the answer layout; requests ("briefly", "in one sentence", seven
            # worked examples) were ignored by the model from the start.
            conversation = ai.ask_model_fields

        if len(result.jobs) > 1:
            # THE MODEL IS NOT ASKED AT ALL. We tried handing it the job of
            # composing the follow-up question, giving it nothing but the
            # differences between the entries - and that WAS NOT ENOUGH.
            # Measured: given two jobs to choose between, it asked "does the
            # job have a hole of a shape other than round?". No such notion
            # exists in any entry or any file - the model invented the
            # QUESTION itself. Handing it that job opened a new channel for
            # invention, so the channel was closed.
            # The sentence is assembled by the UI from the differences, and
            # the links stand underneath anyway - a person sees the jobs and
            # either picks one or adds a word that tells them apart.
            differences = differing_fields(result.jobs)
            return Answer("several", "", result=result, differences=differences,
                               candidates=difference_values(result.jobs, differences))

        # THE REAL NUMBER OF JOBS AS A FACT, not as the model's guess.
        # Caught live: asked "how many jobs do I have in the catalogue",
        # the model was handed one arbitrary job, which said nothing about
        # any count - and it invented "14" when there were five.
        # We do not guess what the person is asking and we do not build
        # question recognition. We state a fact we know anyway - then the
        # model has no reason to invent it, and when the question is about
        # something else, that line does not get in the way.
        parts = ["The catalogue holds %d jobs. Below are the ones that "
                  "match the question." % self.job_count()]
        parts += [self.job_text_for_model(r) for r in result.jobs]

        # THE FUSE FOR A LARGE SETUP SHEET. Below the threshold the trimmer
        # returns the text BYTE FOR BYTE, so for the files on disk today this
        # line changes nothing. We assemble the supplied text ONCE and that is
        # what goes to the model AND to the grounding check - otherwise we
        # would be checking the answer against text the model never saw, and
        # punishing it for a lack of support in a fragment we removed.
        given = "\n\n".join(trim_to_question(c, question) for c in parts)

        # THE FACTS ONCE MORE AT THE END. They are assembled from the text
        # ALREADY trimmed - so the restatement shows what the model really got,
        # not what it would have got without the fuse.
        # Appended BEFORE the grounding check and before sending, because it has
        # to be one and the same text; otherwise we would be checking the answer
        # against something other than the model read.
        restatement = restate_facts(given)
        if restatement:
            given = given + "\n\n" + restatement
        try:
            answer = conversation(question, given)
        except ai.ModelError as error:
            return Answer("error", str(error), result=result)

        # TWO ANSWER SHAPES, ONE CODE PATH. Asked with a form, the
        # model returns a dict with two fields. Test doubles and the
        # fallback path for older ollama return plain text. We accept
        # both - so not a single existing test needed changing, and the
        # program works where forced output shape does not exist.
        if not isinstance(answer, dict):
            # THE OLD PATH: plain text, no fields and no checking.
            # That is how test doubles answer. From ollama the answer comes
            # back as a dict ALWAYS, including on the fallback path.
            return Answer("one", str(answer), result=result,
                               model_name=model_name or ai.MODEL)

        text = str(answer.get("answer") or "").strip()
        source = str(answer.get("source") or "").strip()

        # THE GROUNDING CHECK. A number that is not in the supplied
        # text means invention - and it has no business reaching a
        # person's eyes.
        unsupported = unsupported_numbers(text, given)
        if unsupported:
            # A SECOND, STRICTER ATTEMPT. Only when that one has no
            # support either do we say "not here". One stumble is not
            # enough to refuse someone an answer.
            try:
                second = conversation(question + FOLLOW_UP_WARNING, given)
            except ai.ModelError as error:
                return Answer("error", str(error), result=result)
            if isinstance(second, dict):
                text = str(second.get("answer") or "").strip()
                source = str(second.get("source") or "").strip()
            else:
                text = str(second)
                source = ""
            if unsupported_numbers(text, given):
                return Answer("none", ai.NOT_IN_CATALOG, result=result)

        return Answer("one", text, result=result,
                           model_name=model_name or ai.MODEL,
                           source=source,
                           source_confirmed=source_is_supported(
                               source, given))

    def _word_forms(self, word, lexicon):
        """Words FROM THE BASE ITSELF that are forms of the same word.

        The candidates come only from words somebody actually wrote - not
        from any downloaded dictionary. That way the rule does not fall
        apart as the base grows: there is nowhere to get a word from that
        nobody has used.
        """
        if len(word) < MIN_COMMON_STEM:
            return []
        return [other for other in lexicon
                if other != word and same_word_family(word, other)]

    def _words_without_hits(self, words):
        """Words that appear in NOT A SINGLE job.

        WHY REMOVING THEM IS SAFE: we look for jobs containing ALL the
        words at once. A word that stands nowhere cannot add a single job
        to the result - it can only zero it. Dropping such a word therefore
        changes NOT ONE hit that would have appeared anyway. It changes
        only this: the result stops being empty.

        Each word is checked separately by the same mechanism we search
        with, rather than against a list of words in the base. The reason:
        the search matches fragments inside words too, so a word may not
        stand in the base whole and still have hits ("hole" inside
        "holes").

        Computed only once an ordinary search has returned zero anyway - so
        a successful search pays not one millisecond for it.
        """
        missing = []
        everything_text = None
        for word in words:
            if len(word) >= MIN_WORD_LENGTH:
                hit = self.con.execute(
                    "SELECT rowid FROM job_fts WHERE job_fts MATCH ? LIMIT 1",
                    ('"%s"' % word,)).fetchone()
                if hit is None:
                    missing.append(word)
                continue
            # words shorter than a trigram: one pass over the whole content,
            # not one pass per word
            if everything_text is None:
                everything_text = "\n".join(
                    strip_diacritics(content).lower() for (content,) in
                    self.con.execute("SELECT content FROM job_fts"))
            if word not in everything_text:
                missing.append(word)
        return missing

    def _search_word(self, words, limit):
        """Every word must appear - one word, one group."""
        return self._search_group([[s] for s in words], limit)

    def _search_group(self, groups, limit):
        """Every GROUP must appear; within a group ANY form will do.

        A single-element group is an ordinary word - which is why the old
        search runs through this same path and behaves identically.
        """
        long_groups = [g for g in groups
                  if all(len(s) >= MIN_WORD_LENGTH for s in g)]
        short = [g for g in groups
                   if any(len(s) < MIN_WORD_LENGTH for s in g)]

        if long_groups:
            # quotes: FTS5 has its own query syntax, and without them a hyphen or
            # an asterisk typed by the user breaks the query
            parts = []
            for group in long_groups:
                if len(group) == 1:
                    parts.append('"%s"' % group[0])
                else:
                    parts.append(
                        "(" + " OR ".join('"%s"' % s for s in group) + ")")
            question = " ".join(parts)
            rows = self.con.execute(
                "SELECT rowid FROM job_fts WHERE job_fts MATCH ?"
                " ORDER BY bm25(job_fts) LIMIT ?",
                (question, limit * 4),
            ).fetchall()
            job_numbers = [w[0] for w in rows]
        else:
            # only short words - the trigram index does not handle them, so we scan
            # everything. At this size of base that is free.
            job_numbers = [w[0] for w in self.con.execute(
                "SELECT id FROM job ORDER BY id DESC").fetchall()]

        result = []
        for number in job_numbers:
            record = self._record(number)
            if record is None:
                continue
            if short:
                content = strip_diacritics(_text_for_index(record)).lower()
                if not all(any(s in content for s in g) for g in short):
                    continue
            result.append(record)
            if len(result) >= limit:
                break
        return result

    def _fix_typos(self, words, lexicon=None):
        if lexicon is None:
            lexicon = self._lexicon()
        if not lexicon:
            return words, []
        corrected = []
        corrections = []
        for word in words:
            if word in lexicon or len(word) < MIN_WORD_LENGTH:
                corrected.append(word)
                continue
            closest = difflib.get_close_matches(word, lexicon, n=1,
                                               cutoff=TYPO_THRESHOLD)
            if closest:
                corrected.append(closest[0])
                corrections.append((word, closest[0]))
            else:
                corrected.append(word)
        return corrected, corrections

    def _lexicon(self):
        words = set()
        for (content,) in self.con.execute("SELECT content FROM job_fts"):
            for word in _words(content):
                if len(word) >= MIN_WORD_LENGTH:
                    words.add(word)
        return sorted(words)

    # ---------------------------------------------------------- internal

    def _reindex(self, number, record):
        files = self.con.execute(
            "SELECT name, content FROM attachment WHERE job_id=? ORDER BY id",
            (number,)).fetchall()
        self.con.execute("DELETE FROM job_fts WHERE rowid=?", (number,))
        self.con.execute(
            "INSERT INTO job_fts (rowid, content) VALUES (?,?)",
            (number, _text_for_index(record, [p["name"] for p in files],
                                      [p["content"] for p in files])))

    def _record(self, number):
        row = self.con.execute(
            "SELECT * FROM job WHERE id=?", (number,)).fetchone()
        return dict(row) if row is not None else None

    def _write_metadata(self, record):
        files = self.con.execute(
            "SELECT name, size_bytes, sha256 FROM attachment WHERE job_id=?"
            " ORDER BY id", (record["id"],)).fetchall()
        directory = self.job_dir(record)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, METADATA_FILENAME)
        temporary = path + ".new"
        with open(temporary, "w", encoding="utf-8", newline="\n") as file:
            file.write(_metadata_text(record, [dict(p) for p in files]))
        os.replace(temporary, path)
        return path


def _read_metadata(path):
    """Read `job.txt` back into a dict. Returns None when it cannot be done.

    NOTE THE INVERTED RULE: normally `job.txt` is an EXPORT and the program
    NEVER reads it (see the header of this file). Here we read it
    deliberately, and only here - while rebuilding a database that no
    longer exists. If this file were a source day to day, one hand-broken
    text would break the database; while rebuilding, the worst that
    happens is one skipped job and a message saying so.
    """
    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read()
    except Exception:                      # noqa: BLE001 - absence is not a failure
        return None

    header, _, notes = content.partition(NOTE_SEPARATOR)
    data = {"notes": notes.strip("\n")}
    for line in header.splitlines():
        if line.startswith("#") or ":" not in line or line.startswith("  "):
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()

    for required in ("name", "created_at", "updated_at"):
        if required not in data:
            return None
    data.setdefault("customer", "")
    data.setdefault("material", "")
    return data


def _metadata_text(record, files=()):
    lines = [
        "# chipbook - job",
        "# A copy a human can read. The source of truth is chipbook.db;",
        "# this file is written by the program and never read by it.",
        "id: %s" % record["id"],
        "folder: %s" % record["folder"],
        "created_at: %s" % record["created_at"],
        "updated_at: %s" % record["updated_at"],
        "name: %s" % record["name"],
        "customer: %s" % record["customer"],
        "material: %s" % record["material"],
        "attachments: %d" % len(files),
    ]
    for item in files:
        lines.append("  %s  %d B  sha %s"
                     % (item["name"], item["size_bytes"],
                        item["sha256"][:16]))
    lines += [
        NOTE_SEPARATOR,
        record["notes"],
    ]
    return "\n".join(lines) + "\n"
