#!/usr/bin/env python3
"""Sample AzerothCore character progression and report population outliers."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import pathlib
import shlex
import sqlite3
import subprocess
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "var" / "server-life" / "telemetry.sqlite3"
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.override.yml")
STARTER_ZONES = {
    1: "Dun Morogh",
    12: "Elwynn Forest",
    14: "Durotar",
    85: "Tirisfal Glades",
    141: "Teldrassil",
    215: "Mulgore",
    3430: "Eversong Woods",
    3524: "Azuremyst Isle",
}
RACE_NAMES = {
    1: "Human",
    2: "Orc",
    3: "Dwarf",
    4: "Night Elf",
    5: "Undead",
    6: "Tauren",
    7: "Gnome",
    8: "Troll",
    10: "Blood Elf",
    11: "Draenei",
}

CHARACTER_QUERY = """
SELECT
    c.guid, c.account, c.name, c.race, c.class, c.level, c.xp, c.money,
    c.map, c.zone, c.position_x, c.position_y, c.position_z, c.online,
    c.totaltime, c.leveltime, c.logout_time,
    COALESCE(qr.rewarded_quests, 0), COALESCE(qa.active_quests, 0),
    COALESCE(pat.account_type, 0)
FROM acore_characters.characters c
LEFT JOIN (
    SELECT guid, COUNT(*) AS rewarded_quests
    FROM acore_characters.character_queststatus_rewarded
    WHERE active = 1
    GROUP BY guid
) qr ON qr.guid = c.guid
LEFT JOIN (
    SELECT guid, COUNT(*) AS active_quests
    FROM acore_characters.character_queststatus
    GROUP BY guid
) qa ON qa.guid = c.guid
LEFT JOIN acore_playerbots.playerbots_account_type pat ON pat.account_id = c.account
ORDER BY c.guid
"""


def connect(path: pathlib.Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS samples (
            sample_id INTEGER PRIMARY KEY,
            sampled_at INTEGER NOT NULL,
            sampled_at_iso TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            worldserver_started_at TEXT
        );
        CREATE TABLE IF NOT EXISTS characters (
            sample_id INTEGER NOT NULL,
            guid INTEGER NOT NULL,
            account INTEGER NOT NULL,
            name TEXT NOT NULL,
            race INTEGER NOT NULL,
            class INTEGER NOT NULL,
            level INTEGER NOT NULL,
            xp INTEGER NOT NULL,
            money INTEGER NOT NULL,
            map INTEGER NOT NULL,
            zone INTEGER NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            z REAL NOT NULL,
            online INTEGER NOT NULL,
            total_time INTEGER NOT NULL,
            level_time INTEGER NOT NULL,
            logout_time INTEGER NOT NULL,
            rewarded_quests INTEGER NOT NULL,
            active_quests INTEGER NOT NULL,
            account_type INTEGER NOT NULL,
            PRIMARY KEY (sample_id, guid),
            FOREIGN KEY (sample_id) REFERENCES samples(sample_id)
        );
        CREATE INDEX IF NOT EXISTS characters_guid_sample
            ON characters(guid, sample_id);
        CREATE INDEX IF NOT EXISTS characters_sample_bot
            ON characters(sample_id, account_type, online);
        """
    )
    sample_columns = {
        row[1] for row in db.execute("PRAGMA table_info(samples)").fetchall()
    }
    if "worldserver_started_at" not in sample_columns:
        db.execute("ALTER TABLE samples ADD COLUMN worldserver_started_at TEXT")
    return db


def mysql_rows() -> list[list[str]]:
    compose_args: list[str] = []
    for compose_file in COMPOSE_FILES:
        compose_args.extend(("-f", compose_file))
    mysql_command = (
        'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch --raw --skip-column-names '
        f"-e {shlex.quote(CHARACTER_QUERY)}"
    )
    command = [
        "docker",
        "compose",
        *compose_args,
        "exec",
        "-T",
        "ac-database",
        "sh",
        "-lc",
        mysql_command,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"database query failed with exit code {result.returncode}")
    return list(csv.reader(result.stdout.splitlines(), delimiter="\t"))


def worldserver_started_at() -> str | None:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.StartedAt}}",
            "ac-worldserver",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return None
    started_at = result.stdout.strip()
    return started_at or None


def take_sample(db: sqlite3.Connection) -> int:
    rows = mysql_rows()
    now = int(time.time())
    iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    cursor = db.execute(
        """
        INSERT INTO samples(
            sampled_at, sampled_at_iso, character_count, worldserver_started_at
        ) VALUES (?, ?, ?, ?)
        """,
        (now, iso, len(rows), worldserver_started_at()),
    )
    sample_id = int(cursor.lastrowid)
    db.executemany(
        """
        INSERT INTO characters VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        ((sample_id, *row) for row in rows),
    )
    db.commit()
    print(f"sample {sample_id}: {len(rows)} characters at {iso}")
    return sample_id


def latest_sample(db: sqlite3.Connection) -> sqlite3.Row:
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM samples ORDER BY sample_id DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("no samples yet; run the sample command first")
    return row


def baseline_sample(db: sqlite3.Connection, latest: sqlite3.Row, hours: float) -> sqlite3.Row | None:
    target = latest["sampled_at"] - int(hours * 3600)
    return db.execute(
        """
        SELECT * FROM samples
        WHERE sampled_at <= ? AND sample_id < ?
        ORDER BY sampled_at DESC LIMIT 1
        """,
        (target, latest["sample_id"]),
    ).fetchone()


def previous_sample(db: sqlite3.Connection, latest: sqlite3.Row) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM samples WHERE sample_id < ? ORDER BY sample_id DESC LIMIT 1",
        (latest["sample_id"],),
    ).fetchone()


def report_frozen(db: sqlite3.Connection, latest: sqlite3.Row, limit: int) -> None:
    previous = previous_sample(db, latest)
    if previous is None:
        print("Frozen: need a previous sample to compare against")
        return

    elapsed = latest["sampled_at"] - previous["sampled_at"]
    if elapsed < 300:
        print(f"Frozen: previous sample is only {elapsed}s old; need at least 300s")
        return
    if (
        latest["worldserver_started_at"]
        and previous["worldserver_started_at"]
        and latest["worldserver_started_at"] != previous["worldserver_started_at"]
    ):
        print(
            "Frozen: worldserver restarted between samples; "
            "skipping the movement comparison"
        )
        return

    frozen = db.execute(
        """
        SELECT
            n.name, n.level, n.zone, n.active_quests,
            SQRT(
                (n.x - o.x) * (n.x - o.x) +
                (n.y - o.y) * (n.y - o.y) +
                (n.z - o.z) * (n.z - o.z)
            ) AS displacement
        FROM characters n
        JOIN characters o ON o.guid = n.guid
        WHERE n.sample_id = ? AND o.sample_id = ?
          AND n.account_type = 1
          AND n.online = 1 AND o.online = 1
          AND n.level = o.level
          AND n.xp = o.xp
          AND SQRT(
                (n.x - o.x) * (n.x - o.x) +
                (n.y - o.y) * (n.y - o.y) +
                (n.z - o.z) * (n.z - o.z)
              ) < 1.0
        """,
        (latest["sample_id"], previous["sample_id"]),
    ).fetchall()
    online_both = db.execute(
        """
        SELECT COUNT(*)
        FROM characters n
        JOIN characters o ON o.guid = n.guid
        WHERE n.sample_id = ? AND o.sample_id = ?
          AND n.account_type = 1
          AND n.online = 1 AND o.online = 1
        """,
        (latest["sample_id"], previous["sample_id"]),
    ).fetchone()[0]
    print(
        f"Frozen bots over the last {elapsed / 60:.0f} min "
        f"(online at both samples, no XP, moved <1 yd): "
        f"{len(frozen):,} of {online_both:,}"
    )
    by_zone: dict[int, int] = {}
    for row in frozen:
        by_zone[row["zone"]] = by_zone.get(row["zone"], 0) + 1
    for zone, count in sorted(by_zone.items(), key=lambda item: item[1], reverse=True):
        zone_name = STARTER_ZONES.get(zone, str(zone))
        print(f"  {zone_name}: {count}")
    for row in frozen[:limit]:
        zone_name = STARTER_ZONES.get(row["zone"], str(row["zone"]))
        print(
            f"  {row['name']} L{row['level']} zone={zone_name} "
            f"quests={row['active_quests']}"
        )


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


def report_bot_levels_by_race(
    online: list[sqlite3.Row], non_humans: list[sqlite3.Row]
) -> None:
    print("Bot levels by race (online random bots; highest includes addclass/offline):")
    observed_races = {row["race"] for row in non_humans}
    race_ids = list(RACE_NAMES)
    race_ids.extend(sorted(observed_races - RACE_NAMES.keys()))
    for race_id in race_ids:
        race_online = [row for row in online if row["race"] == race_id]
        levels = [row["level"] for row in race_online]
        race_characters = [row for row in non_humans if row["race"] == race_id]
        race_name = RACE_NAMES.get(race_id, f"Race {race_id}")
        distribution = (
            f"online={len(levels):,}, p50={percentile(levels, 0.50)}, "
            f"p90={percentile(levels, 0.90)}, max={max(levels, default=0)}"
        )
        if not race_characters:
            print(f"  {race_name}: {distribution}; highest=none")
            continue
        top = max(race_characters, key=lambda row: (row["level"], row["xp"]))
        state = "online" if top["online"] else "offline"
        pool = "addclass" if top["account_type"] == 2 else "random bot"
        print(
            f"  {race_name}: {distribution}; highest={top['name']} "
            f"L{top['level']} ({pool}, {state})"
        )


def report(db: sqlite3.Connection, stall_hours: float, limit: int) -> None:
    latest = latest_sample(db)
    sample_id = latest["sample_id"]
    rows = db.execute(
        "SELECT * FROM characters WHERE sample_id = ?", (sample_id,)
    ).fetchall()
    humans = [row for row in rows if row["account_type"] == 0]
    bots = [row for row in rows if row["account_type"] == 1]
    addclass = [row for row in rows if row["account_type"] == 2]
    online = [row for row in bots if row["online"]]
    online_levels = [row["level"] for row in online]

    print(f"Server life report — {latest['sampled_at_iso']}")
    print(
        f"Characters: {len(bots):,} random bots ({len(online):,} online), "
        f"{len(addclass):,} addclass, {len(humans):,} human"
    )
    print(
        f"Online bot levels: p50={percentile(online_levels, 0.50)}, "
        f"p90={percentile(online_levels, 0.90)}, max={max(online_levels, default=0)}"
    )
    non_humans = bots + addclass
    if non_humans:
        top = max(non_humans, key=lambda row: (row["level"], row["xp"]))
        state = "online" if top["online"] else "offline"
        pool = "addclass" if top["account_type"] == 2 else "random bot"
        print(
            f"Highest bot character overall: {top['name']} "
            f"L{top['level']} ({pool}, {state})"
        )
    report_bot_levels_by_race(online, non_humans)

    high_starters = [
        row
        for row in non_humans
        if row["level"] >= 20 and row["zone"] in STARTER_ZONES
    ]
    print(f"High-level bots in starter zones: {len(high_starters):,}")
    by_zone: dict[int, int] = {}
    for row in high_starters:
        by_zone[row["zone"]] = by_zone.get(row["zone"], 0) + 1
    for zone, count in sorted(by_zone.items(), key=lambda item: item[1], reverse=True):
        print(f"  {STARTER_ZONES[zone]}: {count}")

    if humans:
        highest_human = max(humans, key=lambda row: (row["level"], row["xp"]))
        human_level = highest_human["level"]
        behind = sum(row["level"] < human_level - 3 for row in online)
        peers = sum(abs(row["level"] - human_level) <= 3 for row in online)
        ahead = sum(row["level"] > human_level + 3 for row in online)
        print(
            f"Organic pace: {highest_human['name']} is level {human_level}; "
            f"online bots: {behind:,} behind by >3 levels, "
            f"{peers:,} within ±3, {ahead:,} ahead by >3"
        )

    report_frozen(db, latest, limit)
    baseline = baseline_sample(db, latest, stall_hours)
    if baseline is None:
        print(f"Stalls: need a sample at least {stall_hours:g} hours older")
        return

    elapsed = latest["sampled_at"] - baseline["sampled_at"]
    stalled = db.execute(
        """
        SELECT
            n.name, n.guid, n.level, n.xp, n.zone, n.map, n.online,
            n.rewarded_quests, n.total_time,
            n.total_time - o.total_time AS played,
            SQRT(
                (n.x - o.x) * (n.x - o.x) +
                (n.y - o.y) * (n.y - o.y) +
                (n.z - o.z) * (n.z - o.z)
            ) AS displacement
        FROM characters n
        JOIN characters o ON o.guid = n.guid
        WHERE n.sample_id = ? AND o.sample_id = ?
          AND n.account_type = 1
          AND n.total_time - o.total_time >= ?
          AND (
            (n.level = o.level AND n.xp = o.xp AND n.rewarded_quests = o.rewarded_quests)
            OR SQRT(
                (n.x - o.x) * (n.x - o.x) +
                (n.y - o.y) * (n.y - o.y) +
                (n.z - o.z) * (n.z - o.z)
            ) < 10.0
          )
        ORDER BY played DESC, displacement ASC
        LIMIT ?
        """,
        (sample_id, baseline["sample_id"], int(stall_hours * 3600 * 0.5), limit),
    ).fetchall()
    print(
        f"Potential stalls over {elapsed / 3600:.1f}h "
        f"(no progress, or moved under 10 yd): {len(stalled)} shown"
    )
    for row in stalled:
        zone_name = STARTER_ZONES.get(row["zone"], str(row["zone"]))
        print(
            f"  {row['name']} L{row['level']} zone={zone_name} "
            f"played={row['played'] / 3600:.1f}h moved={row['displacement']:.0f}yd "
            f"quests={row['rewarded_quests']}"
        )


def watch(db: sqlite3.Connection, interval: int, stall_hours: float, limit: int) -> None:
    while True:
        take_sample(db)
        report(db, stall_hours, limit)
        print(f"Next sample in {interval}s", flush=True)
        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=pathlib.Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sample", help="record one database snapshot")
    report_parser = subparsers.add_parser("report", help="report population health and outliers")
    report_parser.add_argument("--stall-hours", type=float, default=6)
    report_parser.add_argument("--limit", type=int, default=25)
    watch_parser = subparsers.add_parser("watch", help="sample and report continuously")
    watch_parser.add_argument("--interval", type=int, default=900)
    watch_parser.add_argument("--stall-hours", type=float, default=6)
    watch_parser.add_argument("--limit", type=int, default=25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = connect(args.database)
    if args.command == "sample":
        take_sample(db)
    elif args.command == "report":
        report(db, args.stall_hours, args.limit)
    elif args.command == "watch":
        watch(db, args.interval, args.stall_hours, args.limit)


if __name__ == "__main__":
    main()
