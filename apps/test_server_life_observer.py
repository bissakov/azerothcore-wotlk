#!/usr/bin/env python3
"""Tests for the server-life observer process lock and reported metrics."""

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


class ReportMetricsTest(unittest.TestCase):
    """The report must describe what it counts, and count something that moves."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        directory = pathlib.Path(self.temporary_directory.name)
        self.database = directory / "telemetry.sqlite3"
        self.columns = [name for name, _ in OBSERVER.CHARACTER_COLUMNS]

    def character(self, **fields: object) -> tuple[object, ...]:
        row: dict[str, object] = dict.fromkeys(self.columns, 0)
        row.update(name="Bot", race=1, level=10, online=1, account_type=1)
        row.update(fields)
        return tuple(row[name] for name in self.columns)

    def write_sample(
        self,
        db: sqlite3.Connection,
        sample_id: int,
        sampled_at: int,
        characters: list[tuple[object, ...]],
        metrics: dict[str, float] | None = None,
        version: int = OBSERVER.SCHEMA_VERSION,
    ) -> None:
        db.execute(
            """
            INSERT INTO samples(
                sample_id, sampled_at, sampled_at_iso, character_count, schema_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (sample_id, sampled_at, f"sample-{sample_id}", len(characters), version),
        )
        names = ", ".join(self.columns)
        placeholders = ", ".join("?" * (len(self.columns) + 1))
        db.executemany(
            f"INSERT INTO characters(sample_id, {names}) VALUES ({placeholders})",
            ((sample_id, *row) for row in characters),
        )
        db.executemany(
            "INSERT INTO metrics VALUES (?, ?, ?)",
            ((sample_id, key, value) for key, value in (metrics or {}).items()),
        )

    def report(self) -> str:
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT), "--database", str(self.database),
                "report", "--history", "0",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def line(self, report: str, prefix: str) -> str:
        for line in report.splitlines():
            if line.strip().startswith(prefix):
                return line
        self.fail(f"no {prefix!r} line in report:\n{report}")

    def population(self, gathering_skill: int) -> list[tuple[object, ...]]:
        # Two greens in nine slots: an average quality well under uncommon, which
        # is what the share used to be thresholded on.
        return [
            self.character(
                guid=guid,
                account=guid,
                equipped_items=9,
                item_level=4.0,
                uncommon_items=2 if guid < 3 else 0,
                gathering_professions=2,
                gathering_skill=gathering_skill,
            )
            for guid in range(1, 5)
        ]

    def build(self, version: int = OBSERVER.SCHEMA_VERSION) -> str:
        db: sqlite3.Connection = OBSERVER.connect(self.database)
        now = int(time.time())
        self.write_sample(db, 1, now - 600, self.population(40), version=version)
        self.write_sample(
            db,
            2,
            now,
            self.population(55),
            {"creature_respawns": 2264, "gameobject_respawns": 0, "corpses": 60},
            version=version,
        )
        db.commit()
        db.close()
        return self.report()

    def test_uncommon_share_counts_characters_and_slots(self) -> None:
        gear = self.line(self.build(), "gear ")
        # Two of four bots carry a green; 4 of 36 equipped slots are green.
        self.assertIn("uncommon or better 50% of bots, 11% of slots", gear)

    def test_gathering_figure_moves_when_gathering_skill_rises(self) -> None:
        report = self.build()
        self.assertIn("+60", self.line(report, "gathering skill "))
        census = self.line(report, "gathering     ")
        self.assertIn("4 of 4 gather", census)
        self.assertIn("+60 skill since previous sample", census)

    def test_samples_predating_the_counts_report_no_share_at_all(self) -> None:
        # A zero here would read as "no bot owns a green", which is the reading
        # the old average produced; say the sample lacks the data instead.
        report = self.build(version=OBSERVER.GATHERING_AND_QUALITY_SCHEMA - 1)
        self.assertIn("need a sample of the current schema", report)
        self.assertNotIn("uncommon or better", report)

    def test_world_churn_labels_the_gameobject_respawn_gauge(self) -> None:
        churn = self.line(self.build(), "world churn")
        self.assertNotIn("nodes looted", churn)
        self.assertIn("awaiting respawn: creatures 2,264", churn)
        self.assertIn("gameobjects 0", churn)


if __name__ == "__main__":
    unittest.main()
