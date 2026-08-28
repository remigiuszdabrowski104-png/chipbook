# -*- coding: ascii -*-
"""Rebuild the catalogue database from the job folders left on disk.

WHEN THIS IS NEEDED - and when it is NOT:

  NOT for an ordinary move to a new machine. Copy the WHOLE data directory
  (including chipbook.db) and point the program at it with CHIPBOOK_DATA.
  Everything is then in place and there is nothing to rebuild.

  YES when only the JOB FOLDERS survived and the database is gone or
  damaged. This tool reassembles it from the per-job text files and their
  attachments.

WHY THIS IS POSSIBLE AT ALL: from the first day, every job is written to
disk twice - once into the database and once as a plain text file next to
its attachments. At the time that looked like mere caution. It is the way
back.

SAFETY:
  - runs DRY by default and only reports what it would do;
  - never deletes or modifies an existing job;
  - skips folders that are already in the database;
  - takes a database backup before writing anything.

Usage:
    python tools/rebuild.py <data-directory>          (dry run)
    python tools/rebuild.py <data-directory> write    (for real)
"""

import os
import sys

from chipbook import catalog


def main(arguments):
    if not arguments:
        print("Give the data directory, for example:")
        print("  python tools/rebuild.py C:\\Users\\me\\chipbook-data")
        return 1

    directory = arguments[0]
    for_real = len(arguments) > 1 and arguments[1].lower() == "write"

    if not os.path.isdir(os.path.join(directory, catalog.JOBS_DIR)):
        print("STOP: there is no '%s' folder here." % catalog.JOBS_DIR)
        print("Is this really a chipbook data directory?")
        return 1

    catalog = catalog.open_catalog(directory)
    try:
        print("data directory: %s" % catalog.data_dir)
        print("jobs in the database before: %d" % catalog.job_count())
        if for_real:
            copy_ = catalog.backup(catalog.con, catalog.data_dir,
                                "before-rebuild")
            print("database backup taken: %s" % copy_)
        print("")

        result = catalog.rebuild_from_folders(dry_run=not for_real)

        print("WOULD ADD:" if not for_real else "ADDED:")
        for name in result["added"]:
            print("  + %s" % name)
        if not result["added"]:
            print("  (nothing - every folder is already in the database)")

        if result["skipped"]:
            print("")
            print("ALREADY PRESENT, untouched: %d" % len(result["skipped"]))
        if result["errors"]:
            print("")
            print("COULD NOT BE READ (skipped):")
            for name in result["errors"]:
                print("  ! %s" % name)

        print("")
        print("jobs in the database after: %d" % catalog.job_count())
        if not for_real and result["added"]:
            print("")
            print("That was a DRY RUN - nothing was written.")
            print("To rebuild for real, add the word write:")
            print('  python tools/rebuild.py "%s" write' % directory)
    finally:
        catalog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
