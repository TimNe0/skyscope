"""Checks on the things the Tildagon app store actually validates.

v0.1.1 was rejected by the store with "Failed to parse contents of
tildagon.toml" because a UTF-8 BOM had been written to the front of the file by
a Windows editor. Nothing in the app itself noticed -- Python skips a BOM in
source, and the file looked perfect in every editor -- so it needs a test.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BOM = b"\xef\xbb\xbf"

# Everything the store downloads and parses, plus the sources it runs.
TEXT_SUFFIXES = (".py", ".toml", ".json", ".md")

VALID_CATEGORIES = (
    "Badge", "Music", "Media", "Apps", "Games", "Background", "Pattern",
)


def repo_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames
            if d not in (".git", "__pycache__", ".venv", "docs")
        ]
        for name in filenames:
            if name.endswith(TEXT_SUFFIXES):
                yield os.path.join(dirpath, name)


class TestEncoding(unittest.TestCase):
    def test_no_file_starts_with_a_byte_order_mark(self):
        offenders = []
        for path in repo_files():
            with open(path, "rb") as f:
                if f.read(3) == BOM:
                    offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "BOM found; the app store cannot parse it")

    def test_every_file_is_valid_utf8(self):
        for path in repo_files():
            with open(path, "rb") as f:
                raw = f.read()
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                self.fail("%s is not UTF-8: %s" % (os.path.relpath(path, ROOT), exc))


class TestManifest(unittest.TestCase):
    def setUp(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs Python 3.11+")
        with open(os.path.join(ROOT, "tildagon.toml"), "rb") as f:
            self.manifest = tomllib.load(f)

    def test_required_sections(self):
        for section in ("app", "entry", "metadata"):
            self.assertIn(section, self.manifest)

    def test_category_is_one_the_store_accepts(self):
        self.assertIn(self.manifest["app"]["category"], VALID_CATEGORIES)

    def test_version_is_a_string(self):
        # The store rejects a bare 0.1 as "expected string, received bigint".
        self.assertIsInstance(self.manifest["metadata"]["version"], str)

    def test_description_within_140_characters(self):
        self.assertLessEqual(len(self.manifest["metadata"]["description"]), 140)

    def test_author_within_32_characters(self):
        self.assertLessEqual(len(self.manifest["metadata"]["author"]), 32)

    def test_entry_class_exists_and_is_exported(self):
        entry = self.manifest["entry"]["class"]
        with open(os.path.join(ROOT, "app.py"), "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("class %s(" % entry, source)
        self.assertIn("__app_export__ = %s" % entry, source)

    def test_version_matches_the_app_and_user_agent(self):
        version = self.manifest["metadata"]["version"]
        for name in ("app.py", "adsb.py"):
            with open(os.path.join(ROOT, name), "r", encoding="utf-8") as f:
                self.assertIn(version, f.read(), name)


if __name__ == "__main__":
    unittest.main()
