"""Exercise the real Git index with synthetic bytes only."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_research_files.py"


class ResearchSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.env = os.environ.copy()
        # Isolate these tests from a user's index/config and global ignore rules.
        for key in list(self.env):
            if key.startswith("GIT_"):
                self.env.pop(key)
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q")
        self.git("config", "user.email", "synthetic@example.test")
        self.git("config", "user.name", "Synthetic test")
        shutil.copy(ROOT / ".gitignore", self.repo / ".gitignore")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "Synthetic baseline")

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              capture_output=True, check=check)

    def write(self, path, data="SYNTHETIC TEST ONLY"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)

    def check(self, expected):
        result = subprocess.run(["python3", str(CHECKER)], cwd=self.repo,
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr.decode())
        self.assertNotIn(b"SYNTHETIC TEST ONLY", result.stdout + result.stderr)

    def test_ignore_coverage_and_force_add(self):
        for path in ["local-research/ISSUER/raw/source.dat", "x/transcript-turns.json",
                     "x/language-analysis.json", "x/report-payload.json",
                     "x/company-earnings-analysis.html", "x/call-transcript.pdf",
                     "x/remarks.vtt", "x/remarks.srt"]:
            with self.subTest(path=path):
                self.write(path)
                self.assertEqual(self.git("check-ignore", "-q", path, check=False).returncode, 0)
                self.git("add", "-f", "--", path)
                self.check(1)
                self.git("reset", "-q")

    def test_all_existing_tracked_files_remain_trackable(self):
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
        for raw in filter(None, paths):
            path = os.fsdecode(raw)
            self.assertNotEqual(self.git("check-ignore", "--no-index", "-q", path,
                                         check=False).returncode, 0, path)

    def test_existing_tracked_tree_passes_checker(self):
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
        for raw in filter(None, paths):
            path = os.fsdecode(raw)
            destination = self.repo / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, destination)
        self.git("add", "-A")
        self.check(0)

    def test_public_assets_and_source_pass(self):
        for path in ["local-research/README.md", "examples/example-earnings-call-report.html",
                     "skills/analyze-earnings-calls/evals/fixtures/transcript-turns.json",
                     "skills/analyze-earnings-calls/evals/fixtures/report-payload.json",
                     "skills/analyze-earnings-calls/assets/report-template.html",
                     "skills/analyze-earnings-calls/references/transcript-input-schema.json",
                     "src/source.py"]:
            self.write(path)
            self.git("add", "--", path)
        self.check(0)

    def test_renamed_json_and_attachments(self):
        for path, data in [("notes.data", '{"company":{},"calls":[]}'),
                           ("notes.json", '{"company":{},"executive_view":{}}'),
                           ("metrics.bin", '{"analysis_scope":{},"sentiment":{},"hedge_analysis":{}}'),
                           ("a report\nwith spaces.html", "SYNTHETIC TEST ONLY"),
                           ("renamed.pdf", "SYNTHETIC TEST ONLY")]:
            with self.subTest(path=path):
                self.write(path, data)
                self.git("add", "--", path)
                self.check(1)
                self.git("reset", "-q")

    def test_index_not_worktree(self):
        self.write("renamed.json", '{"company":{},"calls":[]}')
        self.git("add", "renamed.json")
        self.write("renamed.json", "{}")
        self.check(1)
        self.git("add", "renamed.json")
        self.write("renamed.json", '{"company":{},"calls":[]}')
        self.check(0)

    def test_rename_and_delete(self):
        self.write("safe.md")
        self.git("add", "safe.md")
        self.git("commit", "-qm", "Safe synthetic file")
        self.git("mv", "safe.md", "renamed.html")
        self.check(1)
        # Model removal of a previously tracked risky artifact.
        self.git("commit", "-qm", "Synthetic legacy attachment")
        self.git("rm", "renamed.html")
        self.check(0)

    def test_hook_blocks_commit(self):
        shutil.copytree(ROOT / ".githooks", self.repo / ".githooks")
        (self.repo / "scripts").mkdir()
        shutil.copy(CHECKER, self.repo / "scripts/check_research_files.py")
        self.git("config", "core.hooksPath", ".githooks")
        self.write("local-research/forced.json")
        self.git("add", "-f", "local-research/forced.json")
        result = self.git("commit", "-qm", "Must be blocked", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"commit blocked", result.stderr)


if __name__ == "__main__":
    unittest.main()
