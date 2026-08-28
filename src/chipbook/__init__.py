"""chipbook - a local catalogue of CNC jobs.

The files stay where the workshop put them; the catalogue only makes them
findable. Nothing leaves the computer unless the owner says so.

    catalog     the jobs, the attachments and the search over them
    schema      the tables, the migrations, the backup taken first
    search      words, word forms, accents - and what a search returns
    attachments the files of a job, and the text that comes out of them
    setupsheet  reading a setup sheet: XML, PDF, plain text
    ai          asking a local model, and refusing what it cannot support
    server      the browser interface
    web         the page itself

Run it with:  python -m chipbook

WHAT SITS HERE AND WHY: the error type, and the names of the things
chipbook puts on somebody's disk. Every layer agrees on them, and the
LOWEST layer needs them just as much as the highest - the migration reads
the job folders exactly as the catalogue does. Keeping them here is what
stops the modules from having to import one another in a circle.
"""

DB_FILENAME = "chipbook.db"

JOBS_DIR = "jobs"

BACKUPS_DIR = "backups"

METADATA_FILENAME = "job.txt"

FILES_DIR = "files"

DELETED_DIR = "_deleted"

NOTE_SEPARATOR = "--- notes ---"


class ChipbookError(Exception):
    """An error that can be shown to a person without translation."""
