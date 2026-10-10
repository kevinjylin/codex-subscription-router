"""Exercise real Git merges without network or installed-app writes."""
from pathlib import Path
import subprocess
import sys
import types
import tempfile
import unittest
from unittest.mock import patch

import update


class CustomizationUpdateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = self.root / "custom"
        self.source.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@localhost")
        (self.source / "VERSION").write_text("1.0.0\n")
        (self.source / "feature").write_text("base\n")
        self.commit("base")
        self.git("branch", "upstream")
        (self.source / "feature").write_text("my customization\n")
        self.commit("customization")
        self.revision = self.git("rev-parse", "HEAD")
        self.cache = patch.object(update, "MERGED", self.root / "merged")
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
             "-C", str(self.source), *args], text=True, stderr=subprocess.DEVNULL,
        ).strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)

    def release(self, conflict=False):
        self.git("checkout", "upstream")
        (self.source / "VERSION").write_text("2.0.0\n")
        (self.source / "compatibility").write_text("new ChatGPT build\n")
        if conflict:
            (self.source / "feature").write_text("upstream changed the same line\n")
        self.commit("new release")
        self.git("tag", "v2.0.0")
        self.git("checkout", "main")

    def test_combines_customization_and_upstream_without_mutating_checkout(self):
        self.release()
        merged = update.merge_release(self.source, self.revision, "2.0.0", str(self.source))
        self.assertEqual((merged / "feature").read_text(), "my customization\n")
        self.assertEqual((merged / "compatibility").read_text(), "new ChatGPT build\n")
        self.assertEqual((merged / "VERSION").read_text(), "2.0.0\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.revision)
        self.assertEqual((self.source / "VERSION").read_text(), "1.0.0\n")

    def test_conflict_reports_files_without_replacing_source_or_installation(self):
        self.release(conflict=True)
        app = self.root / "installed.app"
        app.mkdir()
        (app / "marker").write_text("working app\n")
        with self.assertRaisesRegex(RuntimeError, "Conflicts: feature"):
            update.merge_release(self.source, self.revision, "2.0.0", str(self.source))
        self.assertEqual(self.git("rev-parse", "HEAD"), self.revision)
        self.assertEqual((app / "marker").read_text(), "working app\n")
        self.assertEqual(list((self.root / "merged").iterdir()), [])

    def test_dirty_customization_checkout_fails_before_merge(self):
        (self.source / "feature").write_text("uncommitted change\n")
        with self.assertRaisesRegex(RuntimeError, "uncommitted changes"):
            update.customization_revision(self.source)

    def test_changelog_conflict_keeps_both_custom_and_upstream_notes(self):
        self.git("checkout", "upstream")
        (self.source / "CHANGELOG.md").write_text("## Unreleased\n\nbase notes\n")
        self.commit("shared changelog")
        base = self.git("rev-parse", "HEAD")
        self.git("checkout", "main")
        self.git("merge", "--no-edit", base)
        (self.source / "CHANGELOG.md").write_text("## Unreleased\n\ncustom fixes\n\nbase notes\n")
        self.commit("custom notes")
        self.revision = self.git("rev-parse", "HEAD")
        self.git("checkout", "upstream")
        (self.source / "VERSION").write_text("2.0.0\n")
        (self.source / "CHANGELOG.md").write_text("## Unreleased\n\n## 2.0.0\n\nnew build\n\nbase notes\n")
        self.commit("release notes")
        self.git("tag", "v2.0.0")
        self.git("checkout", "main")
        merged = update.merge_release(self.source, self.revision, "2.0.0", str(self.source))
        notes = (merged / "CHANGELOG.md").read_text()
        self.assertIn("custom fixes", notes)
        self.assertIn("new build", notes)
        self.assertNotIn("<<<<<<<", notes)

    def test_new_customization_can_rebuild_same_release_and_build(self):
        settings = {"app": "/unused/app", "customizations_source": str(self.source),
                    "installed_customizations_revision": "old"}
        info = {"CodexMuxVersion": "2.0.0", "CFBundleVersion": "13232"}
        with patch.object(update, "installed", return_value=info), \
             patch.object(update, "latest_release", return_value=("2.0.0", "unused")), \
             patch.object(update, "merge_release", return_value=self.source), \
             patch.object(update, "choose_build", return_value=("13232", None)), \
             patch.object(update, "read_json", return_value=None), \
             patch.object(update, "discard_stage"), \
             patch.object(update, "set_state"), \
             patch.object(update, "build_stage") as build:
            self.assertEqual(update.check(settings), 0)
            self.assertEqual(build.call_args.args[-1], self.revision)



class ReleaseImportTests(unittest.TestCase):
    def test_sibling_imports_are_release_local_and_do_not_poison_next_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            roots = [Path(temporary) / name for name in ("one", "two")]
            previous = sys.modules.get("release_sibling")
            sentinel = types.ModuleType("release_sibling")
            sentinel.VALUE = "wrong checkout"
            sys.modules["release_sibling"] = sentinel
            search_path = sys.path[:]
            try:
                modules = []
                for root in roots:
                    (root / "scripts").mkdir(parents=True)
                    (root / "scripts/release_sibling.py").write_text(f"VALUE = {root.name!r}\n")
                    (root / "scripts/entry.py").write_text("from release_sibling import VALUE\n")
                    modules.append(update.load_module(root, "entry"))
                    self.assertIs(sys.modules["release_sibling"], sentinel)
                    self.assertEqual(sys.path, search_path)
                self.assertEqual([m.VALUE for m in modules], ["one", "two"])
            finally:
                if previous is None:
                    sys.modules.pop("release_sibling", None)
                else:
                    sys.modules["release_sibling"] = previous

    def test_copied_updater_imports_real_release_without_scripts_on_pythonpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "update.py"
            copied.write_text(Path(update.__file__).read_text())
            source = Path(update.__file__).resolve().parent.parent
            code = "import update; from pathlib import Path; print(update.load_module(Path(" + repr(str(source)) + "), 'patch_app').PROJECT_VERSION)"
            result = subprocess.run([sys.executable, "-c", code], cwd=temporary, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), (source / "VERSION").read_text().strip())


if __name__ == "__main__":
    unittest.main()
