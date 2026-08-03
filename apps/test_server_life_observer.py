#!/usr/bin/env python3
"""Tests for the server-life observer process lock."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().with_name("server-life-observer.py")
SPEC = importlib.util.spec_from_file_location("server_life_observer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)

LOCK_HELPER = """
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("server_life_observer", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.watcher_lock(pathlib.Path(sys.argv[2])):
    print("locked")
"""


class WatcherLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = pathlib.Path(self.temporary_directory.name)

    def run_lock_helper(
        self, database: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", LOCK_HELPER, str(SCRIPT), str(database)],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

    def run_observer(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

    def test_second_watcher_for_same_database_fails_with_owner(self) -> None:
        database = self.directory / "telemetry.sqlite3"
        with OBSERVER.watcher_lock(database):
            result = self.run_observer("--database", str(database), "watch")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another watcher holds", result.stderr)
        self.assertIn(str(database.resolve()), result.stderr)
        self.assertIn(f"pid={os.getpid()}", result.stderr)

    def test_watchers_for_different_databases_can_lock_concurrently(self) -> None:
        first = self.directory / "first" / "telemetry.sqlite3"
        second = self.directory / "second" / "telemetry.sqlite3"
        with OBSERVER.watcher_lock(first):
            result = self.run_lock_helper(second)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "locked")

    def test_report_and_trend_are_available_while_watcher_holds_lock(self) -> None:
        database = self.directory / "telemetry.sqlite3"
        db: sqlite3.Connection = OBSERVER.connect(database)
        now = int(time.time())
        db.execute(
            """
            INSERT INTO samples(
                sampled_at, sampled_at_iso, character_count, schema_version
            ) VALUES (?, ?, 0, ?)
            """,
            (now, "2025-01-01T00:00:00+00:00", OBSERVER.SCHEMA_VERSION),
        )
        db.commit()
        db.close()

        with OBSERVER.watcher_lock(database):
            report = self.run_observer(
                "--database", str(database), "report", "--history", "0"
            )
            trend = self.run_observer("--database", str(database), "trend")

        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("Server life", report.stdout)
        self.assertEqual(trend.returncode, 0, trend.stderr)
        self.assertIn("trend needs at least two samples", trend.stdout)


if __name__ == "__main__":
    unittest.main()
