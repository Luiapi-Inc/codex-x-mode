import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge import service


class CountingScandir:
    def __init__(self, iterator):
        self.iterator = iterator
        self.items_requested = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.iterator.close()

    def __iter__(self):
        return self

    def __next__(self):
        self.items_requested += 1
        return next(self.iterator)


class ProjectReadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.outside = self.base / "outside"
        (self.project / "sub").mkdir(parents=True)
        self.outside.mkdir()
        (self.project / "sub" / "data.txt").write_text("project data")
        (self.outside / "data.txt").write_text("outside secret")
        (self.outside / "secret.txt").write_text("outside filename")
        self.config = {
            "projects": {
                "demo": {"cwd": str(self.project.resolve()), "allow_write": False}
            }
        }

    def tearDown(self):
        self.temp.cleanup()

    def _race_after_open(self, *, directory):
        original = service._open_project_path
        swapped = [False]

        def open_then_swap(config, project_id, path, *, directory):
            opened = original(config, project_id, path, directory=directory)
            if not swapped[0]:
                subdir = self.project / "sub"
                subdir.rename(self.project / "sub.original")
                subdir.symlink_to(self.outside, target_is_directory=True)
                swapped[0] = True
            return opened

        return patch.object(service, "_open_project_path", side_effect=open_then_swap)

    def test_file_read_stays_on_opened_directory_during_symlink_swap(self):
        with self._race_after_open(directory=False):
            value = service.read_project_file(self.config, "demo", "sub/data.txt")
        self.assertEqual(value["content"], "project data")

    def test_directory_listing_stays_on_opened_directory_during_symlink_swap(self):
        with self._race_after_open(directory=True):
            value = service.list_project_directory(self.config, "demo", "sub")
        names = {entry["name"] for entry in value["entries"]}
        self.assertIn("data.txt", names)
        self.assertNotIn("secret.txt", names)

    def test_directory_limit_bounds_entries_scanned(self):
        listing = self.project / "many"
        listing.mkdir()
        for index in range(30):
            (listing / f"file-{index:02}.txt").touch()

        actual_scandir = os.scandir
        wrappers = []

        def counted_scandir(path):
            wrapper = CountingScandir(actual_scandir(path))
            wrappers.append(wrapper)
            return wrapper

        with patch.object(service.os, "scandir", side_effect=counted_scandir):
            value = service.list_project_directory(self.config, "demo", "many", limit=5)

        self.assertEqual(len(value["entries"]), 5)
        self.assertTrue(value["truncated"])
        self.assertEqual(wrappers[0].items_requested, 6)


if __name__ == "__main__":
    unittest.main()
