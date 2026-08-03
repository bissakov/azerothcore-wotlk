# Server life observer

`server-life-observer.py` records read-only snapshots of the running realm from the Docker database and keeps them in
`var/server-life/telemetry.sqlite3`. The telemetry database is independent of the game databases, so it can be
retained across realm wipes. Bot death counters are persisted by `mod-playerbots`, while each observer snapshot records
whether an online bot is still a body or has released as a ghost and how long its corpse run has lasted.

Record and inspect a snapshot:

```bash
python apps/server-life-observer.py sample
python apps/server-life-observer.py report
```

Run a sample every 15 minutes:

```bash
python apps/server-life-observer.py watch --interval 900 --stall-hours 6
```

A watcher holds an adjacent `telemetry.sqlite3.watch.lock` file for its lifetime. Starting another watcher for the same
resolved database path exits immediately and reports the current owner's PID and command. The lock file itself remains
on disk after shutdown, but it is only metadata; the operating-system lock is released automatically. One-off
`sample`, `report`, and `trend` commands do not take this lock.

For continuous collection under the user service manager:

```bash
systemctl --user link "$PWD/apps/server-life-observer.service"
systemctl --user enable --now server-life-observer.service
```

Other commands: `trend` prints only the recent-sample table, and `zones` re-reads zone names from the client
`AreaTable.dbc` inside the worldserver container (they are cached on the first sample, so this is only needed after a
client-data change).

## What a sample collects

Per character: identity, level, xp, money, position, online flag, playtime, creation date, quests taken and rewarded,
guild and group, skills and profession skill totals, spells, talents, achievements, equipped slots with average item
level and quality, bag contents, mail, auctions, pets, honorable kills, instance and flight-path state, and the
playerbots account type (human, random bot, addclass bot).

Realm-wide: guilds, arena teams, groups, friend lists, auction listings and their buyout value, mail in flight,
corpses, creature and gameobject respawn queues, live instances and saves, battleground records, LLM chat history and
relationship rows, and the playerbots random-bot event counts. Plus, from outside the database, the worldserver's
rolling update-diff distribution parsed from `Server.log`, container CPU and memory, and container uptime.

## What the report shows

- **Server health** — world update diff (median/mean/p95/p99/max), worldserver CPU, memory and uptime, and the size of
  the random-bot pool.
- **Population** — characters per pool with online counts and login/logout churn, level percentiles, a level-spread
  histogram that adapts its bucket width to the realm's ceiling, and the faction and class split of who is online.
- **Progression** — levels, quests, gold, spells, talents, professions, gear and playtime gained, both since the
  previous sample and since the `--stall-hours` baseline, with the fastest movers named.
- **World activity** — zones occupied with population, level range and median per zone, how many bots are outside
  starter zones, in instances, on flight paths, dead or grouped, and the median and p90 distance moved since the
  previous sample.
- **Character development** — how many bots know a profession and their combined skill, a per-profession census, gear
  depth (slots filled, item level, share in uncommon or better) and spellbook, talent, pet and bag depth.
- **Economy and world state** — gold distribution and realm total, auction and mail volume, creature and node respawn
  queues (the mobs-being-killed proxy), instance and battleground counts.
- **Death lifecycle** — cumulative deaths, releases and resurrections, deaths per 100 bot-hours, corpse versus Spirit
  Healer recovery, and the age of currently active ghosts.
- **Social** — guilds with members and the largest one, groups formed, friend lists, and logged LLM conversations.
- **Organic pace** — where online bots sit relative to the highest human character, and what gold, quests and item
  level that peer band holds.
- **Watchlist** — high-level bots parked in starter zones, bots still level 1 after an hour played, bots with no quest
  after two hours, bots exceeding the two-primary-profession cap, level 10+ bots in fewer than eight gear slots,
  ghosts that have remained unrecovered for at least 15 minutes, frozen bots (saved while online across two samples
  with no xp and under a yard of movement), and long-horizon stalls.
- **Recent samples** — one line per sample with online count, level percentiles, quests, gold, respawn queue, update
  diff and levels per hour, so the last few hours are readable without touching the database.

## Caveats

- Character rows are written by the periodic player save, so position, gold and playtime lag live state by up to one
  save interval. Movement and frozen counts only use characters whose saved playtime advanced; an unchanged row is
  unobserved, not frozen. The check also requires five minutes and skips comparisons spanning a worldserver restart.
- Stall detection needs snapshots separated by the requested `--stall-hours`, and a character must have accumulated at
  least half that interval as actual playtime before it counts.
- `totalKills` is honorable kills, not creature kills, so it stays at zero on a PvE realm; the creature respawn queue
  is the proxy for how much of the world is being farmed.
- Deltas that span a change to the collected column set are suppressed rather than reported as a large fake gain. Bump
  `SCHEMA_VERSION` when adding a column to `CHARACTER_COLUMNS`.
