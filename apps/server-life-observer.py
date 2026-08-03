#!/usr/bin/env python3
"""Sample AzerothCore character progression and report realm health."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import re
import shlex
import sqlite3
import struct
import subprocess
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "var" / "server-life" / "telemetry.sqlite3"
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.override.yml")
WORLDSERVER = "ac-worldserver"
AREA_TABLE = "/azerothcore/env/dist/data/dbc/AreaTable.dbc"
SERVER_LOG = "/azerothcore/env/dist/logs/Server.log"

STARTER_ZONES = frozenset({1, 12, 14, 85, 141, 215, 3430, 3524})
CAPITAL_ZONES = frozenset({1497, 1519, 1537, 1637, 3487, 3557, 4080, 4395})
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
ALLIANCE_RACES = frozenset({1, 3, 4, 7, 11})
CLASS_NAMES = {
    1: "Warrior",
    2: "Paladin",
    3: "Hunter",
    4: "Rogue",
    5: "Priest",
    6: "Death Knight",
    7: "Shaman",
    8: "Mage",
    9: "Warlock",
    11: "Druid",
}
PRIMARY_PROFESSIONS = {
    164: "Blacksmithing",
    165: "Leatherworking",
    171: "Alchemy",
    182: "Herbalism",
    186: "Mining",
    197: "Tailoring",
    202: "Engineering",
    333: "Enchanting",
    393: "Skinning",
    755: "Jewelcrafting",
    773: "Inscription",
}
SECONDARY_PROFESSIONS = {129: "First Aid", 185: "Cooking", 356: "Fishing"}
PROFESSIONS = PRIMARY_PROFESSIONS | SECONDARY_PROFESSIONS

# Bump whenever CHARACTER_COLUMNS gains a field. Samples recorded under an
# older version keep a zero for the new columns, so deltas that cross a version
# boundary are reported as unavailable instead of as a huge fake gain.
SCHEMA_VERSION = 2

# Per-character telemetry columns, in the order the character query returns
# them. Adding one here plus the matching expression in CHARACTER_QUERY is
# enough: the table is created and migrated from this list.
CHARACTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("guid", "INTEGER NOT NULL"),
    ("account", "INTEGER NOT NULL"),
    ("name", "TEXT NOT NULL"),
    ("race", "INTEGER NOT NULL"),
    ("class", "INTEGER NOT NULL"),
    ("gender", "INTEGER NOT NULL DEFAULT 0"),
    ("level", "INTEGER NOT NULL"),
    ("xp", "INTEGER NOT NULL"),
    ("money", "INTEGER NOT NULL"),
    ("map", "INTEGER NOT NULL"),
    ("zone", "INTEGER NOT NULL"),
    ("x", "REAL NOT NULL"),
    ("y", "REAL NOT NULL"),
    ("z", "REAL NOT NULL"),
    ("online", "INTEGER NOT NULL"),
    ("total_time", "INTEGER NOT NULL"),
    ("level_time", "INTEGER NOT NULL"),
    ("logout_time", "INTEGER NOT NULL"),
    ("created_at", "INTEGER NOT NULL DEFAULT 0"),
    ("total_kills", "INTEGER NOT NULL DEFAULT 0"),
    ("honor", "INTEGER NOT NULL DEFAULT 0"),
    ("health", "INTEGER NOT NULL DEFAULT 0"),
    ("instance_id", "INTEGER NOT NULL DEFAULT 0"),
    ("on_taxi", "INTEGER NOT NULL DEFAULT 0"),
    ("rewarded_quests", "INTEGER NOT NULL"),
    ("active_quests", "INTEGER NOT NULL"),
    ("guild_id", "INTEGER NOT NULL DEFAULT 0"),
    ("group_id", "INTEGER NOT NULL DEFAULT 0"),
    ("skills", "INTEGER NOT NULL DEFAULT 0"),
    ("professions", "INTEGER NOT NULL DEFAULT 0"),
    ("profession_skill", "INTEGER NOT NULL DEFAULT 0"),
    ("spells", "INTEGER NOT NULL DEFAULT 0"),
    ("talents", "INTEGER NOT NULL DEFAULT 0"),
    ("achievements", "INTEGER NOT NULL DEFAULT 0"),
    ("equipped_items", "INTEGER NOT NULL DEFAULT 0"),
    ("item_level", "REAL NOT NULL DEFAULT 0"),
    ("item_quality", "REAL NOT NULL DEFAULT 0"),
    ("inventory_items", "INTEGER NOT NULL DEFAULT 0"),
    ("mails", "INTEGER NOT NULL DEFAULT 0"),
    ("auctions", "INTEGER NOT NULL DEFAULT 0"),
    ("pets", "INTEGER NOT NULL DEFAULT 0"),
    ("account_type", "INTEGER NOT NULL"),
)

CHARACTER_QUERY = f"""
SELECT
    c.guid, c.account, c.name, c.race, c.class, c.gender, c.level, c.xp, c.money,
    c.map, c.zone, c.position_x, c.position_y, c.position_z, c.online,
    c.totaltime, c.leveltime, c.logout_time,
    COALESCE(UNIX_TIMESTAMP(c.creation_date), 0),
    c.totalKills, c.totalHonorPoints, c.health, c.instance_id,
    IF(c.taxi_path <> '', 1, 0),
    COALESCE(qr.rewarded_quests, 0), COALESCE(qa.active_quests, 0),
    COALESCE(gm.guildid, 0), COALESCE(grm.group_id, 0),
    COALESCE(sk.skills, 0), COALESCE(sk.professions, 0), COALESCE(sk.profession_skill, 0),
    COALESCE(sp.spells, 0), COALESCE(tal.talents, 0), COALESCE(ach.achievements, 0),
    COALESCE(eq.equipped_items, 0), COALESCE(eq.item_level, 0), COALESCE(eq.item_quality, 0),
    COALESCE(inv.inventory_items, 0),
    COALESCE(ml.mails, 0), COALESCE(au.auctions, 0), COALESCE(pt.pets, 0),
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
LEFT JOIN acore_characters.guild_member gm ON gm.guid = c.guid
LEFT JOIN (
    SELECT memberGuid, MIN(guid) AS group_id
    FROM acore_characters.group_member
    GROUP BY memberGuid
) grm ON grm.memberGuid = c.guid
LEFT JOIN (
    SELECT
        guid,
        COUNT(*) AS skills,
        SUM(skill IN ({','.join(map(str, PRIMARY_PROFESSIONS))})) AS professions,
        SUM(IF(skill IN ({','.join(map(str, PRIMARY_PROFESSIONS))}), value, 0)) AS profession_skill
    FROM acore_characters.character_skills
    GROUP BY guid
) sk ON sk.guid = c.guid
LEFT JOIN (
    SELECT guid, COUNT(*) AS spells FROM acore_characters.character_spell GROUP BY guid
) sp ON sp.guid = c.guid
LEFT JOIN (
    SELECT guid, COUNT(*) AS talents FROM acore_characters.character_talent GROUP BY guid
) tal ON tal.guid = c.guid
LEFT JOIN (
    SELECT guid, COUNT(*) AS achievements FROM acore_characters.character_achievement GROUP BY guid
) ach ON ach.guid = c.guid
LEFT JOIN (
    SELECT
        ci.guid,
        COUNT(*) AS equipped_items,
        ROUND(AVG(it.ItemLevel), 1) AS item_level,
        ROUND(AVG(it.Quality), 2) AS item_quality
    FROM acore_characters.character_inventory ci
    JOIN acore_characters.item_instance ii ON ii.guid = ci.item
    JOIN acore_world.item_template it ON it.entry = ii.itemEntry
    WHERE ci.bag = 0 AND ci.slot < 19
    GROUP BY ci.guid
) eq ON eq.guid = c.guid
LEFT JOIN (
    SELECT guid, COUNT(*) AS inventory_items
    FROM acore_characters.character_inventory
    GROUP BY guid
) inv ON inv.guid = c.guid
LEFT JOIN (
    SELECT receiver, COUNT(*) AS mails FROM acore_characters.mail GROUP BY receiver
) ml ON ml.receiver = c.guid
LEFT JOIN (
    SELECT itemowner, COUNT(*) AS auctions FROM acore_characters.auctionhouse GROUP BY itemowner
) au ON au.itemowner = c.guid
LEFT JOIN (
    SELECT owner, COUNT(*) AS pets FROM acore_characters.character_pet GROUP BY owner
) pt ON pt.owner = c.guid
LEFT JOIN acore_playerbots.playerbots_account_type pat ON pat.account_id = c.account
ORDER BY c.guid
"""

SKILL_QUERY = f"""
SELECT s.skill, COUNT(*), ROUND(AVG(s.value), 1), MAX(s.value)
FROM acore_characters.character_skills s
WHERE s.skill IN ({','.join(map(str, PROFESSIONS))})
GROUP BY s.skill
"""

# Realm-wide scalars. Tables that a module or a wipe may not have created yet
# are skipped rather than failing the whole sample.
REALM_METRICS: tuple[tuple[str, str, str], ...] = (
    ("guilds", "acore_characters.guild", "COUNT(*)"),
    ("guild_members", "acore_characters.guild_member", "COUNT(*)"),
    ("arena_teams", "acore_characters.arena_team", "COUNT(*)"),
    ("groups", "acore_characters.groups", "COUNT(*)"),
    ("group_members", "acore_characters.group_member", "COUNT(*)"),
    ("friends", "acore_characters.character_social", "COUNT(*)"),
    ("auctions", "acore_characters.auctionhouse", "COUNT(*)"),
    ("auction_buyout", "acore_characters.auctionhouse", "COALESCE(SUM(buyoutprice), 0)"),
    ("mails", "acore_characters.mail", "COUNT(*)"),
    ("mail_money", "acore_characters.mail", "COALESCE(SUM(money), 0)"),
    ("corpses", "acore_characters.corpse", "COUNT(*)"),
    ("creature_respawns", "acore_characters.creature_respawn", "COUNT(*)"),
    ("gameobject_respawns", "acore_characters.gameobject_respawn", "COUNT(*)"),
    ("instances", "acore_characters.instance", "COUNT(*)"),
    ("instance_binds", "acore_characters.character_instance", "COUNT(*)"),
    ("battlegrounds", "acore_characters.pvpstats_battlegrounds", "COUNT(*)"),
    ("pets", "acore_characters.character_pet", "COUNT(*)"),
    ("chat_messages", "acore_characters.mod_ollama_chat_history", "COUNT(*)"),
    ("chat_sentiments", "acore_characters.mod_ollama_chat_bot_player_sentiments", "COUNT(*)"),
    ("chat_relationships", "acore_characters.mod_ollama_chat_relationship_memory", "COUNT(*)"),
)
BOT_EVENT_TABLE = "acore_playerbots.playerbots_random_bots"


def is_tty() -> bool:
    return sys.stdout.isatty()


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if is_tty() else text


def dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if is_tty() else text


def heading(title: str) -> None:
    print()
    print(bold(title))


def connect(path: pathlib.Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    columns = ",\n            ".join(
        f"{name} {decl}" for name, decl in CHARACTER_COLUMNS
    )
    db.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS samples (
            sample_id INTEGER PRIMARY KEY,
            sampled_at INTEGER NOT NULL,
            sampled_at_iso TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            worldserver_started_at TEXT,
            schema_version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS characters (
            sample_id INTEGER NOT NULL,
            {columns},
            PRIMARY KEY (sample_id, guid),
            FOREIGN KEY (sample_id) REFERENCES samples(sample_id)
        );
        CREATE INDEX IF NOT EXISTS characters_guid_sample
            ON characters(guid, sample_id);
        CREATE INDEX IF NOT EXISTS characters_sample_bot
            ON characters(sample_id, account_type, online);
        CREATE TABLE IF NOT EXISTS metrics (
            sample_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (sample_id, key)
        );
        CREATE TABLE IF NOT EXISTS skills (
            sample_id INTEGER NOT NULL,
            skill INTEGER NOT NULL,
            characters INTEGER NOT NULL,
            avg_value REAL NOT NULL,
            max_value INTEGER NOT NULL,
            PRIMARY KEY (sample_id, skill)
        );
        CREATE TABLE IF NOT EXISTS zones (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            map INTEGER NOT NULL,
            parent INTEGER NOT NULL
        );
        """
    )
    add_missing_columns(
        db,
        "samples",
        (
            ("worldserver_started_at", "TEXT"),
            ("schema_version", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )
    add_missing_columns(db, "characters", CHARACTER_COLUMNS)
    db.commit()
    return db


def add_missing_columns(
    db: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]
) -> None:
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name in existing:
            continue
        decl = decl.replace("NOT NULL", "NOT NULL DEFAULT 0") if "DEFAULT" not in decl else decl
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def compose_exec(service: str, command: str) -> subprocess.CompletedProcess[str]:
    compose_args: list[str] = []
    for compose_file in COMPOSE_FILES:
        compose_args.extend(("-f", compose_file))
    return subprocess.run(
        ["docker", "compose", *compose_args, "exec", "-T", service, "sh", "-lc", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def compose_exec_binary(service: str, command: str) -> bytes | None:
    compose_args: list[str] = []
    for compose_file in COMPOSE_FILES:
        compose_args.extend(("-f", compose_file))
    result = subprocess.run(
        ["docker", "compose", *compose_args, "exec", "-T", service, "sh", "-lc", command],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def mysql(query: str) -> list[list[str]]:
    command = (
        'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --batch --raw --skip-column-names '
        f"-e {shlex.quote(query)}"
    )
    result = compose_exec("ac-database", command)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"database query failed with exit code {result.returncode}")
    return list(csv.reader(result.stdout.splitlines(), delimiter="\t"))


def existing_tables() -> set[str]:
    wanted = {table for _, table, _ in REALM_METRICS} | {BOT_EVENT_TABLE}
    pairs = ", ".join(
        f"('{table.split('.')[0]}','{table.split('.')[1]}')" for table in sorted(wanted)
    )
    rows = mysql(
        "SELECT CONCAT(table_schema, '.', table_name) FROM information_schema.tables "
        f"WHERE (table_schema, table_name) IN ({pairs})"
    )
    return {row[0] for row in rows if row}


def realm_query(tables: set[str]) -> str:
    parts = [
        f"SELECT '{key}', {expr} FROM {table}"
        for key, table, expr in REALM_METRICS
        if table in tables
    ]
    if BOT_EVENT_TABLE in tables:
        parts.append(
            f"SELECT CONCAT('bot_', event), COUNT(*) FROM {BOT_EVENT_TABLE} GROUP BY event"
        )
    return "\nUNION ALL ".join(parts)


def worldserver_started_at() -> str | None:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.StartedAt}}", WORLDSERVER],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return None
    return result.stdout.strip() or None


def container_usage() -> dict[str, float]:
    """CPU percent and resident memory of the worldserver container."""
    result = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.CPUPerc}}\t{{.MemUsage}}",
            WORLDSERVER,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        return {}
    cpu, _, memory = result.stdout.strip().partition("\t")
    usage: dict[str, float] = {}
    try:
        usage["cpu_percent"] = float(cpu.rstrip("%"))
    except ValueError:
        pass
    match = re.match(r"([\d.]+)\s*([KMGT]?i?B)", memory.strip())
    if match:
        scale = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
        usage["memory_bytes"] = float(match.group(1)) * scale.get(match.group(2), 1)
    return usage


def update_diff() -> dict[str, float]:
    """Rolling world-update latency the core prints into Server.log."""
    result = compose_exec(WORLDSERVER, f"tail -n 400 {SERVER_LOG} 2>/dev/null")
    if result.returncode or not result.stdout:
        return {}
    diff: dict[str, float] = {}
    for line in result.stdout.splitlines():
        last = re.search(r"Update time diff: (\d+)ms with (\d+) players online", line)
        if last:
            diff["diff_last_ms"] = float(last.group(1))
            diff["players_online_reported"] = float(last.group(2))
        mean = re.search(r"\|- Mean: (\d+)ms", line)
        if mean:
            diff["diff_mean_ms"] = float(mean.group(1))
        median = re.search(r"\|- Median: (\d+)ms", line)
        if median:
            diff["diff_median_ms"] = float(median.group(1))
        percentiles = re.search(
            r"\|- Percentiles \(95, 99, max\): (\d+)ms, (\d+)ms, (\d+)ms", line
        )
        if percentiles:
            diff["diff_p95_ms"] = float(percentiles.group(1))
            diff["diff_p99_ms"] = float(percentiles.group(2))
            diff["diff_max_ms"] = float(percentiles.group(3))
    return diff


def refresh_zones(db: sqlite3.Connection, force: bool = False) -> int:
    """Cache zone names from the client AreaTable.dbc the worldserver reads."""
    if not force and db.execute("SELECT COUNT(*) FROM zones").fetchone()[0]:
        return 0
    payload = compose_exec_binary(WORLDSERVER, f"cat {AREA_TABLE}")
    if not payload or payload[:4] != b"WDBC":
        print("zones: AreaTable.dbc unavailable; falling back to numeric ids", file=sys.stderr)
        return 0
    _, record_count, field_count, record_size, _ = struct.unpack("<4siiii", payload[:20])
    body = 20
    strings = body + record_count * record_size
    rows = []
    for index in range(record_count):
        offset = body + index * record_size
        record = struct.unpack(f"<{field_count}i", payload[offset : offset + record_size])
        area_id, map_id, parent = record[0], record[1], record[2]
        name_offset = strings + record[11]
        end = payload.index(b"\x00", name_offset)
        name = payload[name_offset:end].decode("utf-8", "replace")
        rows.append((area_id, name, map_id, parent))
    db.executemany("INSERT OR REPLACE INTO zones VALUES (?, ?, ?, ?)", rows)
    db.commit()
    return len(rows)


def zone_names(db: sqlite3.Connection) -> dict[int, str]:
    return {row["id"]: row["name"] for row in db.execute("SELECT id, name FROM zones")}


def take_sample(db: sqlite3.Connection) -> int:
    refresh_zones(db)
    tables = existing_tables()
    rows = mysql(CHARACTER_QUERY)
    realm_rows = mysql(realm_query(tables)) if tables else []
    skill_rows = mysql(SKILL_QUERY)
    now = int(time.time())
    iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    cursor = db.execute(
        """
        INSERT INTO samples(
            sampled_at, sampled_at_iso, character_count, worldserver_started_at,
            schema_version
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (now, iso, len(rows), worldserver_started_at(), SCHEMA_VERSION),
    )
    sample_id = int(cursor.lastrowid)
    names = ", ".join(name for name, _ in CHARACTER_COLUMNS)
    placeholders = ", ".join("?" * (len(CHARACTER_COLUMNS) + 1))
    db.executemany(
        f"INSERT INTO characters(sample_id, {names}) VALUES ({placeholders})",
        ((sample_id, *row) for row in rows),
    )
    metrics = {key: float(value) for key, value in realm_rows}
    metrics.update(update_diff())
    metrics.update(container_usage())
    db.executemany(
        "INSERT OR REPLACE INTO metrics VALUES (?, ?, ?)",
        ((sample_id, key, value) for key, value in metrics.items()),
    )
    db.executemany(
        "INSERT OR REPLACE INTO skills VALUES (?, ?, ?, ?, ?)",
        ((sample_id, *row) for row in skill_rows),
    )
    db.commit()
    print(f"sample {sample_id}: {len(rows)} characters, {len(metrics)} metrics at {iso}")
    return sample_id


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


def median(values: list[float]) -> float:
    return percentile(values, 0.50)


def duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def gold(copper: float) -> str:
    copper = int(copper)
    sign = "-" if copper < 0 else ""
    copper = abs(copper)
    if copper >= 10000:
        return f"{sign}{copper // 10000:,}g {copper % 10000 // 100:02d}s"
    if copper >= 100:
        return f"{sign}{copper // 100}s {copper % 100:02d}c"
    return f"{sign}{copper}c"


def signed(value: float, unit: str = "") -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:+,.1f}{unit}"
    return f"{int(value):+,}{unit}"


def bar(value: float, peak: float, width: int = 28) -> str:
    if peak <= 0:
        return ""
    filled = int(round(width * value / peak))
    return "█" * max(filled, 1 if value else 0)


def per_hour(delta: float, seconds: float) -> float:
    return delta * 3600 / seconds if seconds > 0 else 0.0


def displacement(new: sqlite3.Row, old: sqlite3.Row) -> float:
    return (
        (new["x"] - old["x"]) ** 2
        + (new["y"] - old["y"]) ** 2
        + (new["z"] - old["z"]) ** 2
    ) ** 0.5


class Snapshot:
    """One sample, with the lookups the report sections need."""

    def __init__(self, db: sqlite3.Connection, sample: sqlite3.Row):
        self.sample = sample
        self.sample_id = int(sample["sample_id"])
        self.rows = db.execute(
            "SELECT * FROM characters WHERE sample_id = ?", (self.sample_id,)
        ).fetchall()
        self.by_guid = {row["guid"]: row for row in self.rows}
        self.metrics = {
            row["key"]: row["value"]
            for row in db.execute(
                "SELECT key, value FROM metrics WHERE sample_id = ?", (self.sample_id,)
            )
        }
        self.skills = db.execute(
            "SELECT * FROM skills WHERE sample_id = ? ORDER BY characters DESC",
            (self.sample_id,),
        ).fetchall()
        self.humans = [row for row in self.rows if row["account_type"] == 0]
        self.bots = [row for row in self.rows if row["account_type"] == 1]
        self.addclass = [row for row in self.rows if row["account_type"] == 2]
        self.non_humans = self.bots + self.addclass
        self.online = [row for row in self.bots if row["online"]]

    @property
    def at(self) -> int:
        return int(self.sample["sampled_at"])

    @property
    def version(self) -> int:
        return int(self.sample["schema_version"] or 0)


def compare(new: Snapshot, old: Snapshot) -> dict[str, float]:
    """Aggregate bot progress between two samples."""
    elapsed = max(new.at - old.at, 1)
    both = [
        (row, old.by_guid[row["guid"]])
        for row in new.bots
        if row["guid"] in old.by_guid
    ]
    online_both = [pair for pair in both if pair[0]["online"] and pair[1]["online"]]
    moved = [displacement(*pair) for pair in online_both]
    result = {
        "elapsed": elapsed,
        "extended": float(new.version == old.version),
        "tracked": len(both),
        "online_both": len(online_both),
        "logins": sum(1 for row, was in both if row["online"] and not was["online"]),
        "logouts": sum(1 for row, was in both if not row["online"] and was["online"]),
        "new_characters": len(new.bots) - len(both),
        "median_move": median(moved),
        "p90_move": percentile(moved, 0.90),
        "frozen": sum(
            1
            for (row, was), moved_yd in zip(online_both, moved)
            if row["level"] == was["level"] and row["xp"] == was["xp"] and moved_yd < 1.0
        ),
    }
    for field in (
        "rewarded_quests",
        "money",
        "total_kills",
        "total_time",
        "professions",
        "spells",
        "equipped_items",
        "talents",
    ):
        result[field] = sum(row[field] - was[field] for row, was in both)
    # XP resets on level-up, so levels are counted on their own.
    result["levels"] = sum(max(row["level"] - was["level"], 0) for row, was in both)
    result["played"] = max(result["total_time"], 0)
    return result


def leaders(new: Snapshot, old: Snapshot, limit: int) -> list[tuple[sqlite3.Row, int, int]]:
    scored = []
    for row in new.bots:
        was = old.by_guid.get(row["guid"])
        if was is None:
            continue
        gained = row["level"] - was["level"]
        quests = row["rewarded_quests"] - was["rewarded_quests"]
        if gained > 0 or quests > 0:
            scored.append((row, gained, quests))
    scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return scored[:limit]


def report_population(snap: Snapshot, change: dict[str, float] | None) -> None:
    heading("Population")
    online = snap.online
    total = len(snap.bots)
    share = 100 * len(online) / total if total else 0
    churn = ""
    if change:
        churn = (
            f"   {signed(change['logins'])} in / {signed(-change['logouts'])} out"
            f"   {signed(change['new_characters'])} new characters"
        )
    print(f"  random bots   {total:>6,} created   {len(online):>5,} online ({share:.0f}%){churn}")
    print(
        f"  addclass      {len(snap.addclass):>6,} created   "
        f"{sum(1 for row in snap.addclass if row['online']):>5,} online"
    )
    print(
        f"  humans        {len(snap.humans):>6,} created   "
        f"{sum(1 for row in snap.humans if row['online']):>5,} online"
    )

    levels = [row["level"] for row in online]
    all_levels = [row["level"] for row in snap.non_humans]
    print(
        f"  online levels   p50 {percentile(levels, 0.5):.0f}"
        f"   p90 {percentile(levels, 0.9):.0f}"
        f"   max {max(levels, default=0)}"
        f"        every bot   p50 {percentile(all_levels, 0.5):.0f}"
        f"   p90 {percentile(all_levels, 0.9):.0f}"
        f"   max {max(all_levels, default=0)}"
    )

    ceiling = max(all_levels, default=1)
    width = 1 if ceiling <= 12 else 5 if ceiling <= 40 else 10
    buckets: dict[int, int] = {}
    for level in all_levels:
        buckets[(level - 1) // width * width + 1] = buckets.get(
            (level - 1) // width * width + 1, 0
        ) + 1
    peak = max(buckets.values(), default=0)
    print(dim("  level spread (every bot character)"))
    for floor in sorted(buckets):
        label = f"{floor}" if width == 1 else f"{floor}-{floor + width - 1}"
        print(f"    {label:>7}  {bar(buckets[floor], peak):<28} {buckets[floor]:>6,}")

    alliance = sum(1 for row in online if row["race"] in ALLIANCE_RACES)
    print(f"  factions online   Alliance {alliance:,}   Horde {len(online) - alliance:,}")
    counts: dict[int, int] = {}
    for row in online:
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    classes = "   ".join(
        f"{CLASS_NAMES.get(class_id, class_id)} {count}" for class_id, count in ranked
    )
    if classes:
        print(f"  classes online    {classes}")


def report_progression(
    snap: Snapshot, before: Snapshot, change: dict[str, float], label: str
) -> None:
    elapsed = change["elapsed"]
    tracked = max(change["tracked"], 1)
    heading(f"Progression — {label} ({duration(elapsed)})")
    played = change["played"]
    print(
        f"  levels gained     {signed(change['levels']):>10}"
        f"   {per_hour(change['levels'], elapsed):.1f}/h realm"
        f"   {per_hour(change['levels'] / tracked, elapsed) * 24:.2f}/bot/day"
    )
    print(
        f"  quests completed  {signed(change['rewarded_quests']):>10}"
        f"   {per_hour(change['rewarded_quests'], elapsed):.1f}/h realm"
    )
    print(f"  gold earned       {gold(change['money']):>10}")
    if not change["extended"]:
        print(dim("  gear, spell and kill deltas need two samples of the same schema"))
    else:
        print(
            f"  spells learned    {signed(change['spells']):>10}"
            f"   talents {signed(change['talents'])}"
            f"   professions {signed(change['professions'])}"
            f"   gear equipped {signed(change['equipped_items'])}"
        )
        if change["total_kills"]:
            print(f"  honorable kills   {signed(change['total_kills']):>10}")
    if played:
        print(
            f"  bot playtime      {duration(played):>10}"
            f"   {played / elapsed:.0f} bot-hours per wall hour"
            + dim("   (accrues at character save)")
        )
    for row, gained, quests in leaders(snap, before, limit=5):
        print(
            f"    {row['name']:<14} L{row['level']:<3} "
            f"{signed(gained)} levels, {signed(quests)} quests"
        )


def report_world(snap: Snapshot, change: dict[str, float] | None, names: dict[int, str]) -> None:
    heading("World activity")
    online = snap.online
    zones: dict[int, list[int]] = {}
    for row in online:
        zones.setdefault(row["zone"], []).append(row["level"])
    outside_starter = sum(1 for row in online if row["zone"] not in STARTER_ZONES)
    print(
        f"  zones occupied {len(zones)}"
        f"   outside starter zones {outside_starter:,} of {len(online):,} online"
        f"   maps {len({row['map'] for row in online})}"
    )
    peak = max((len(levels) for levels in zones.values()), default=0)
    for zone, levels in sorted(zones.items(), key=lambda item: len(item[1]), reverse=True)[:12]:
        name = names.get(zone, f"zone {zone}")
        tag = " (starter)" if zone in STARTER_ZONES else " (capital)" if zone in CAPITAL_ZONES else ""
        print(
            f"    {name + tag:<26} {bar(len(levels), peak, 20):<20} {len(levels):>5,}"
            f"   L{min(levels)}-{max(levels)} p50 {median(levels):.0f}"
        )
    in_instance = sum(1 for row in online if row["instance_id"])
    on_taxi = sum(1 for row in online if row["on_taxi"])
    dead = sum(1 for row in online if row["health"] == 0)
    print(
        f"  in instances {in_instance:,}   on flight paths {on_taxi:,}"
        f"   dead or ghost {dead:,}   grouped {sum(1 for row in online if row['group_id']):,}"
    )
    if change:
        print(
            f"  movement since previous sample   median {change['median_move']:,.0f} yd"
            f"   p90 {change['p90_move']:,.0f} yd"
            f"   frozen {change['frozen']:,} of {change['online_both']:,}"
        )


def report_development(snap: Snapshot, names: dict[int, str]) -> None:
    heading("Character development (online bots)")
    online = snap.online
    if not online:
        print("  no bots online")
        return
    with_profession = [row for row in online if row["professions"]]
    geared = [row for row in online if row["equipped_items"]]
    print(
        f"  professions   {len(with_profession):,} of {len(online):,} know one"
        f"   p50 combined skill "
        f"{median([row['profession_skill'] for row in with_profession]):.0f}"
    )
    primary = [row for row in snap.skills if row["skill"] in PRIMARY_PROFESSIONS]
    peak = max((row["characters"] for row in primary), default=0)
    print(dim("  primary profession census (every character, not just online)"))
    for row in primary[:8]:
        name = PRIMARY_PROFESSIONS[row["skill"]]
        print(
            f"    {name:<16} {bar(row['characters'], peak, 18):<18} {row['characters']:>6,}"
            f"   avg {row['avg_value']:.0f} max {row['max_value']}"
        )
    secondary = "   ".join(
        f"{SECONDARY_PROFESSIONS[row['skill']]} {row['characters']:,} (avg {row['avg_value']:.0f})"
        for row in snap.skills
        if row["skill"] in SECONDARY_PROFESSIONS
    )
    if secondary:
        print(f"    secondary     {secondary}")
    print(
        f"  gear          p50 {median([row['equipped_items'] for row in online]):.0f} slots filled"
        f"   p50 item level {median([row['item_level'] for row in geared]):.0f}"
        f"   uncommon or better {sum(1 for row in geared if row['item_quality'] >= 2) / max(len(geared), 1) * 100:.0f}%"
    )
    print(
        f"  spellbook     p50 {median([row['spells'] for row in online]):.0f} spells"
        f"   talents p50 {median([row['talents'] for row in online]):.0f}"
        f"   pets {sum(row['pets'] for row in online):,}"
        f"   bags p50 {median([row['inventory_items'] for row in online]):.0f} items"
    )
    quests = [row["rewarded_quests"] for row in online]
    print(
        f"  quests        p50 {median(quests):.0f} completed"
        f"   p90 {percentile(quests, 0.9):.0f}"
        f"   max {max(quests, default=0)}"
        f"   active p50 {median([row['active_quests'] for row in online]):.0f}"
    )


def report_economy(snap: Snapshot, previous: Snapshot | None) -> None:
    heading("Economy and world state")
    purses = [row["money"] for row in snap.non_humans]
    print(
        f"  gold held     p50 {gold(median(purses))}"
        f"   p90 {gold(percentile(purses, 0.9))}"
        f"   max {gold(max(purses, default=0))}"
        f"   realm total {gold(sum(purses))}"
    )

    def metric(key: str) -> float:
        return snap.metrics.get(key, 0.0)

    def change(key: str) -> str:
        if previous is None or key not in previous.metrics or key not in snap.metrics:
            return ""
        delta = snap.metrics[key] - previous.metrics[key]
        return f" ({signed(delta)})" if delta else ""

    print(
        f"  auction house {metric('auctions'):,.0f} listings{change('auctions')}"
        f"   worth {gold(metric('auction_buyout'))}"
        f"   mail {metric('mails'):,.0f} in flight{change('mails')}"
    )
    print(
        f"  world churn   {metric('creature_respawns'):,.0f} creatures awaiting respawn"
        f"{change('creature_respawns')}"
        f"   {metric('gameobject_respawns'):,.0f} nodes looted{change('gameobject_respawns')}"
        f"   {metric('corpses'):,.0f} corpses"
    )
    print(
        f"  instances     {metric('instances'):,.0f} live"
        f"   {metric('instance_binds'):,.0f} saves"
        f"   battlegrounds {metric('battlegrounds'):,.0f}"
    )


def report_social(snap: Snapshot, previous: Snapshot | None) -> None:
    heading("Social")
    guilds: dict[int, int] = {}
    for row in snap.non_humans:
        if row["guild_id"]:
            guilds[row["guild_id"]] = guilds.get(row["guild_id"], 0) + 1
    largest = max(guilds.values(), default=0)
    unguilded = sum(1 for row in snap.bots if not row["guild_id"])
    print(
        f"  guilds        {len(guilds):,} with members"
        f"   {sum(guilds.values()):,} members"
        f"   largest {largest}"
        f"   unguilded {unguilded:,}"
    )
    groups: dict[int, int] = {}
    for row in snap.rows:
        if row["group_id"]:
            groups[row["group_id"]] = groups.get(row["group_id"], 0) + 1
    print(
        f"  groups        {len(groups):,} formed"
        f"   {sum(groups.values()):,} members"
        f"   largest {max(groups.values(), default=0)}"
        f"   friends listed {snap.metrics.get('friends', 0):,.0f}"
    )
    messages = snap.metrics.get("chat_messages")
    if messages is not None:
        delta = ""
        if previous and "chat_messages" in previous.metrics:
            gained = messages - previous.metrics["chat_messages"]
            delta = f" ({signed(gained)} since previous sample)" if gained else ""
        print(
            f"  chat          {messages:,.0f} logged conversations{delta}"
            f"   relationships {snap.metrics.get('chat_relationships', 0):,.0f}"
        )


def report_health(snap: Snapshot) -> None:
    heading("Server health")
    metrics = snap.metrics
    started = snap.sample["worldserver_started_at"]
    uptime = ""
    if started:
        try:
            begin = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
            uptime = f"   uptime {duration(snap.at - begin.timestamp())}"
        except ValueError:
            uptime = ""
    if "diff_median_ms" in metrics:
        print(
            f"  update diff   median {metrics['diff_median_ms']:.0f}ms"
            f"   mean {metrics.get('diff_mean_ms', 0):.0f}ms"
            f"   p95 {metrics.get('diff_p95_ms', 0):.0f}ms"
            f"   p99 {metrics.get('diff_p99_ms', 0):.0f}ms"
            f"   max {metrics.get('diff_max_ms', 0):.0f}ms"
        )
    if "cpu_percent" in metrics:
        memory = metrics.get("memory_bytes", 0) / 1024**3
        print(
            f"  worldserver   cpu {metrics['cpu_percent']:.0f}%"
            f"   memory {memory:.1f} GiB{uptime}"
        )
    elif uptime:
        print(f"  worldserver  {uptime.strip()}")
    pool = metrics.get("bot_add")
    if pool:
        print(
            f"  bot pool      {pool:,.0f} registered"
            f"   randomized {metrics.get('bot_randomize', 0):,.0f}"
            f"   teleport slots {metrics.get('bot_teleport', 0):,.0f}"
            f"   flagged dead {metrics.get('bot_dead', 0):,.0f}"
        )


def report_watchlist(
    snap: Snapshot,
    previous: Snapshot | None,
    change: dict[str, float] | None,
    baseline: Snapshot | None,
    stall_hours: float,
    limit: int,
    names: dict[int, str],
) -> None:
    heading("Watchlist")

    def show(title: str, rows: list[sqlite3.Row], detail) -> None:
        print(f"  {title}: {len(rows):,}")
        for row in rows[:limit]:
            print(f"    {row['name']:<14} {detail(row)}")

    online = snap.online
    high_starters = [
        row for row in snap.non_humans if row["level"] >= 20 and row["zone"] in STARTER_ZONES
    ]
    show(
        "high-level bots parked in starter zones",
        sorted(high_starters, key=lambda row: -row["level"]),
        lambda row: f"L{row['level']:<3} {names.get(row['zone'], row['zone'])}",
    )
    idle_level_one = [
        row for row in online if row["level"] == 1 and row["total_time"] >= 3600
    ]
    show(
        "still level 1 after an hour played",
        sorted(idle_level_one, key=lambda row: -row["total_time"]),
        lambda row: f"played {duration(row['total_time'])} quests {row['rewarded_quests']}",
    )
    questless = [
        row
        for row in online
        if row["total_time"] >= 7200 and row["rewarded_quests"] == 0
    ]
    show(
        "no quest ever completed after two hours played",
        sorted(questless, key=lambda row: -row["total_time"]),
        lambda row: f"L{row['level']:<3} played {duration(row['total_time'])} "
        f"in {names.get(row['zone'], row['zone'])}",
    )
    naked = [row for row in online if row["level"] >= 10 and row["equipped_items"] < 8]
    show(
        "level 10+ wearing fewer than eight items",
        sorted(naked, key=lambda row: row["equipped_items"]),
        lambda row: f"L{row['level']:<3} {row['equipped_items']} slots "
        f"ilvl {row['item_level']:.0f}",
    )

    if previous is not None and change is not None:
        if change["elapsed"] < 300:
            print(dim("  frozen check skipped: samples less than five minutes apart"))
        elif (
            snap.sample["worldserver_started_at"]
            and previous.sample["worldserver_started_at"]
            and snap.sample["worldserver_started_at"] != previous.sample["worldserver_started_at"]
        ):
            print(dim("  frozen check skipped: worldserver restarted between samples"))
        else:
            frozen = [
                row
                for row in snap.bots
                if row["online"]
                and row["guid"] in previous.by_guid
                and previous.by_guid[row["guid"]]["online"]
                and row["level"] == previous.by_guid[row["guid"]]["level"]
                and row["xp"] == previous.by_guid[row["guid"]]["xp"]
                and displacement(row, previous.by_guid[row["guid"]]) < 1.0
            ]
            print(
                f"  frozen over the last {duration(change['elapsed'])}"
                f" (online at both samples, no xp, moved under a yard):"
                f" {len(frozen):,} of {change['online_both']:,}"
            )
            by_zone: dict[int, int] = {}
            for row in frozen:
                by_zone[row["zone"]] = by_zone.get(row["zone"], 0) + 1
            for zone, count in sorted(by_zone.items(), key=lambda item: -item[1])[:8]:
                print(f"    {names.get(zone, f'zone {zone}'):<26} {count:,}")

    if baseline is None:
        print(dim(f"  stall check needs a sample at least {stall_hours:g}h older"))
        return
    elapsed = snap.at - baseline.at
    minimum_played = int(stall_hours * 3600 * 0.5)
    stalled = []
    for row in snap.bots:
        was = baseline.by_guid.get(row["guid"])
        if was is None or row["total_time"] - was["total_time"] < minimum_played:
            continue
        moved = displacement(row, was)
        no_progress = (
            row["level"] == was["level"]
            and row["xp"] == was["xp"]
            and row["rewarded_quests"] == was["rewarded_quests"]
        )
        if no_progress or moved < 10.0:
            stalled.append((row, was, moved))
    stalled.sort(key=lambda item: (item[0]["total_time"] - item[1]["total_time"]), reverse=True)
    print(
        f"  stalled over {duration(elapsed)}"
        f" (played but made no progress, or moved under ten yards): {len(stalled):,}"
    )
    for row, was, moved in stalled[:limit]:
        print(
            f"    {row['name']:<14} L{row['level']:<3} "
            f"{names.get(row['zone'], row['zone']):<20} "
            f"played {duration(row['total_time'] - was['total_time'])} "
            f"moved {moved:,.0f} yd quests {row['rewarded_quests']}"
        )


def report_organic_pace(snap: Snapshot) -> None:
    if not snap.humans or not snap.online:
        return
    heading("Organic pace")
    highest = max(snap.humans, key=lambda row: (row["level"], row["xp"]))
    level = highest["level"]
    behind = sum(1 for row in snap.online if row["level"] < level - 3)
    peers = sum(1 for row in snap.online if abs(row["level"] - level) <= 3)
    ahead = sum(1 for row in snap.online if row["level"] > level + 3)
    print(
        f"  {highest['name']} is level {level}; online bots: "
        f"{behind:,} behind by more than three levels, "
        f"{peers:,} within three, {ahead:,} ahead by more than three"
    )
    pool = [row for row in snap.online if abs(row["level"] - level) <= 3]
    if pool:
        print(
            f"  peers hold p50 {gold(median([row['money'] for row in pool]))}"
            f"   p50 {median([row['rewarded_quests'] for row in pool]):.0f} quests"
            f"   p50 item level {median([row['item_level'] for row in pool]):.0f}"
        )


def report_trend(db: sqlite3.Connection, limit: int) -> None:
    samples = db.execute(
        "SELECT * FROM samples ORDER BY sample_id DESC LIMIT ?", (limit,)
    ).fetchall()
    if len(samples) < 2:
        print(dim("\n  trend needs at least two samples"))
        return
    heading(f"Recent samples (last {len(samples)})")
    print(
        dim(
            "    time       online    p50   max    quests       gold"
            "   respawn queue   diff   levels/h"
        )
    )
    rows = []
    for sample in reversed(samples):
        aggregate = db.execute(
            """
            SELECT
                SUM(online) AS online,
                SUM(rewarded_quests) AS quests,
                SUM(money) AS money,
                SUM(level) AS levels
            FROM characters
            WHERE sample_id = ? AND account_type = 1
            """,
            (sample["sample_id"],),
        ).fetchone()
        levels = [
            row["level"]
            for row in db.execute(
                "SELECT level FROM characters "
                "WHERE sample_id = ? AND account_type = 1 AND online = 1",
                (sample["sample_id"],),
            )
        ]
        metrics = {
            row["key"]: row["value"]
            for row in db.execute(
                "SELECT key, value FROM metrics WHERE sample_id = ? "
                "AND key IN ('creature_respawns', 'diff_median_ms')",
                (sample["sample_id"],),
            )
        }
        rows.append((sample, aggregate, levels, metrics))
    for index, (sample, aggregate, levels, metrics) in enumerate(rows):
        rate = ""
        if index:
            before_sample, before, _, _ = rows[index - 1]
            elapsed = sample["sampled_at"] - before_sample["sampled_at"]
            gained = (aggregate["levels"] or 0) - (before["levels"] or 0)
            rate = f"{per_hour(gained, elapsed):+,.0f}"
        print(
            f"    {sample['sampled_at_iso'][11:16]:<9} {aggregate['online'] or 0:>6,} "
            f"{percentile(levels, 0.5):>6.0f} {max(levels, default=0):>5}"
            f" {aggregate['quests'] or 0:>9,} {gold(aggregate['money'] or 0):>10}"
            f" {metrics.get('creature_respawns', 0):>15,.0f}"
            f" {metrics.get('diff_median_ms', 0):>5.0f}ms {rate:>10}"
        )


def latest_sample(db: sqlite3.Connection) -> sqlite3.Row:
    row = db.execute("SELECT * FROM samples ORDER BY sample_id DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("no samples yet; run the sample command first")
    return row


def previous_sample(db: sqlite3.Connection, latest: sqlite3.Row) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM samples WHERE sample_id < ? ORDER BY sample_id DESC LIMIT 1",
        (latest["sample_id"],),
    ).fetchone()


def baseline_sample(
    db: sqlite3.Connection, latest: sqlite3.Row, hours: float
) -> sqlite3.Row | None:
    target = latest["sampled_at"] - int(hours * 3600)
    return db.execute(
        """
        SELECT * FROM samples
        WHERE sampled_at <= ? AND sample_id < ?
        ORDER BY sampled_at DESC LIMIT 1
        """,
        (target, latest["sample_id"]),
    ).fetchone()


def report(db: sqlite3.Connection, stall_hours: float, limit: int, history: int) -> None:
    latest = latest_sample(db)
    snap = Snapshot(db, latest)
    previous_row = previous_sample(db, latest)
    previous = Snapshot(db, previous_row) if previous_row else None
    baseline_row = baseline_sample(db, latest, stall_hours)
    baseline = Snapshot(db, baseline_row) if baseline_row else None
    names = zone_names(db)

    short = compare(snap, previous) if previous else None
    long_run = compare(snap, baseline) if baseline else None

    print(
        bold(f"Server life — {latest['sampled_at_iso']}")
        + dim(f"   sample {latest['sample_id']}, {len(snap.rows):,} characters")
    )
    report_health(snap)
    report_population(snap, short)
    if previous and short:
        report_progression(snap, previous, short, "since previous sample")
    if baseline and long_run:
        report_progression(snap, baseline, long_run, f"since {stall_hours:g}h baseline")
    report_world(snap, short, names)
    report_development(snap, names)
    report_economy(snap, previous)
    report_social(snap, previous)
    report_organic_pace(snap)
    report_watchlist(snap, previous, short, baseline, stall_hours, limit, names)
    if history:
        report_trend(db, history)
    print("\n\n")


def watch(
    db: sqlite3.Connection, interval: int, stall_hours: float, limit: int, history: int
) -> None:
    while True:
        take_sample(db)
        report(db, stall_hours, limit, history)
        print(f"Next sample in {interval}s", flush=True)
        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=pathlib.Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sample", help="record one database snapshot")
    for name, help_text in (
        ("report", "report realm health and outliers"),
        ("watch", "sample and report continuously"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--stall-hours", type=float, default=6)
        sub.add_argument("--limit", type=int, default=10)
        sub.add_argument("--history", type=int, default=8, help="samples in the trend table")
        if name == "watch":
            sub.add_argument("--interval", type=int, default=900)
    subparsers.add_parser("trend", help="print the recent-sample table only").add_argument(
        "--history", type=int, default=24
    )
    subparsers.add_parser("zones", help="re-read zone names from the client AreaTable.dbc")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = connect(args.database)
    if args.command == "sample":
        take_sample(db)
    elif args.command == "report":
        report(db, args.stall_hours, args.limit, args.history)
    elif args.command == "watch":
        watch(db, args.interval, args.stall_hours, args.limit, args.history)
    elif args.command == "trend":
        report_trend(db, args.history)
        print()
    elif args.command == "zones":
        print(f"cached {refresh_zones(db, force=True):,} zone names")


if __name__ == "__main__":
    main()
