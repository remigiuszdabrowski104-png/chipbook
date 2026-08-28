"""Tests for the package description itself.

WHY THESE EXIST: the rest of the suite reads the program through imports,
so it never notices that a file the wheel is supposed to carry was left
out, or that the version in pyproject.toml drifted away from the one the
program reports. Both break only AFTER the program is installed
somewhere else - which is the worst moment to find out.
"""
import os
import re
import unittest

from chipbook import catalog
from chipbook import server
from chipbook.server import routes


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _field(name):
    """One `name = "value"` line out of the [project] table.

    Read with a regular expression rather than tomllib, because tomllib
    arrived in Python 3.11 and the program itself runs on older ones.
    """
    with open(PYPROJECT, encoding="utf-8") as file:
        content = file.read()
    found = re.search(r'^%s\s*=\s*"([^"]*)"' % name, content, re.M)
    return found.group(1) if found else None


class ProjectFileTest(unittest.TestCase):

    def test_the_version_matches_the_one_the_program_reports(self):
        """Two places name the version - and they have to agree.

        If they drift, the window shows one number while the installed
        package carries another, and an update looks as though it did not
        arrive.
        """
        self.assertEqual(_field("version"), str(catalog.PROGRAM_VERSION))

    def test_the_command_points_at_something_that_exists(self):
        """`chipbook = "chipbook.server:main"` in pyproject.

        A typo here is invisible until somebody installs the package and
        types the command.
        """
        with open(PYPROJECT, encoding="utf-8") as file:
            content = file.read()
        self.assertIn('chipbook = "chipbook.server:main"', content)
        self.assertTrue(callable(server.main))

    def test_the_page_and_the_instruction_really_lie_in_the_package(self):
        """The wheel carries them through package-data.

        The server has nothing to serve without the page, so a missing
        file here is not a cosmetic problem.
        """
        package = os.path.join(ROOT, "src", "chipbook")
        for relative in [os.path.join("web", "index.html"),
                         os.path.join("web", "chipbook.ico"),
                         os.path.join("ai", "prompt.txt")]:
            self.assertTrue(os.path.exists(os.path.join(package, relative)),
                            relative)

    def test_the_page_assembles_out_of_its_three_files(self):
        """The page lies in three files and is served as one.

        If a placeholder is ever renamed on one side only, the browser gets
        the word __STYLES__ where the whole look should be - and the page
        still returns 200, so nothing else would notice.
        """
        page = routes.page_source()
        self.assertNotIn(routes.STYLES_PLACEHOLDER, page)
        self.assertNotIn(routes.SCRIPT_PLACEHOLDER, page)
        # something from each of the three files
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("--accent:", page)
        self.assertIn("function drawList(", page)

    def test_a_change_to_the_look_alone_still_reaches_the_phone(self):
        """The phone keeps a copy of the page and refreshes it when the
        file stamp changes. styles.css and app.js have to be on that list -
        otherwise a fix to the look would sit on the laptop while the phone
        went on showing the old page, and the report would read "your fixes
        do not work" rather than "an old copy"."""
        for relative in [os.path.join("web", "styles.css"),
                         os.path.join("web", "app.js")]:
            self.assertIn(relative, routes.CACHE_BUSTED_FILES)

    def test_package_data_lists_every_file_that_is_not_python(self):
        """Whatever is not a .py file reaches the wheel ONLY through
        package-data. This test fails when a new one is added and the
        listing is forgotten - the symptom would otherwise be "it works
        here, it does not work installed"."""
        with open(PYPROJECT, encoding="utf-8") as file:
            content = file.read()
        package = os.path.join(ROOT, "src", "chipbook")
        for base, _, files in os.walk(package):
            if "__pycache__" in base:
                continue
            for name in files:
                if name.endswith(".py"):
                    continue
                folder = os.path.basename(base)
                self.assertTrue(
                    ('%s/*' % folder) in content or ('%s/%s' % (folder, name)) in content,
                    "%s/%s is not covered by package-data" % (folder, name))


if __name__ == "__main__":
    unittest.main()
