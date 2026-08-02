# Server life observer

`server-life-observer.py` records read-only character snapshots from the running Docker database and keeps them in
`var/server-life/telemetry.sqlite3`. The telemetry database is independent of the game databases, so it can be
retained across realm wipes.

Record and inspect a snapshot:

```bash
python apps/server-life-observer.py sample
python apps/server-life-observer.py report
```

Run a sample every 15 minutes:

```bash
python apps/server-life-observer.py watch --interval 900 --stall-hours 6
```

For continuous collection under the user service manager:

```bash
systemctl --user link "$PWD/apps/server-life-observer.service"
systemctl --user enable --now server-life-observer.service
```

The report includes:

- population by pool: random bots (with online count), addclass characters, humans;
- median and 90th-percentile online bot level, plus the highest bot character anywhere,
  online or offline — both overall and for every playable race, so parked outliers are
  never hidden;
- high-level bots found in starter zones, online or offline;
- online bots behind, within ±3 levels of, and ahead of the highest-level organic
  character (three disjoint buckets);
- frozen bots: online across the two most recent samples but with no XP gain and under
  a yard of movement — this works at the sample interval, no multi-hour baseline needed
  (`totaltime` is deliberately not consulted: it only updates on periodic character
  save, so its delta is unreliable over one interval);
- long-horizon stalls: bots that accumulated playtime but either made no level/XP/quest
  progress or moved under 10 yards over the `--stall-hours` window.

Frozen detection needs two samples at least 5 minutes apart. Stall detection needs
snapshots separated by the requested `--stall-hours`, and a character must have
accumulated at least half that interval as actual playtime before it is considered.
