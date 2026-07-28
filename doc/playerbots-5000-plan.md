# Playerbots 5,000-bot performance project

## Goal

Run 5,000 simultaneously logged-in random bots on this host while preserving a playable world for the human
client. A bot only counts when it is online and receiving AI updates; a database character is not an active bot.

## Host and initial foundation

- CPU: AMD Ryzen 7 7800X3D, 8 physical cores / 16 threads
- RAM: 30 GiB, with 30 GiB swap available as a safety net
- Core: `mod-playerbots/azerothcore-wotlk`, `Playerbot` branch
- Module: `mod-playerbots`
- Client: WoW 3.3.5a build 12340 through Proton
- Runtime: Docker Compose, MySQL, authserver, and worldserver
- Map workers: 8 (upstream default was 1)
- Initial population: 500 active bots from 1,000 generated characters

The first informal 500-bot observations were 223-265% worldserver CPU and approximately 4.3 GiB RAM after
enabling eight map workers. Initialization fell from 38 seconds to 10 seconds, although warm filesystem and
database caches may explain part of that improvement. These observations are directional, not the formal baseline.

## Benchmark protocol

Normal and instrumented runs are recorded separately:

1. Let the requested bot population finish logging in.
2. Wait two minutes for startup churn to settle.
3. Collect a five-minute normal run:
   `./apps/playerbots-benchmark.sh 300 <bot-count>-<change-name>`
4. Separately enable the built-in Playerbots profiler with `.playerbots pmon toggle`, reset it with
   `.playerbots pmon reset`, collect for 60 seconds, then print `.playerbots pmon tick` and disable it.
5. Record `.playerbots pmon queue` before and after each run. A growing queue, failed operation, or skipped
   operation fails the gate even if CPU and tick latency appear acceptable.
6. During each population gate, log in with the human client, accept a quest, invite a bot, travel, and enter
   combat. Record subjective input and combat delay.

Raw benchmark artifacts live under ignored `var/benchmarks/`. Durable results and decisions are added to the
trend table below.

## Gates

| Metric | Pass | Warning | Fail |
| --- | ---: | ---: | ---: |
| World update p95 | <= 50 ms | <= 100 ms | > 100 ms |
| World update p99 | <= 100 ms | <= 200 ms | > 200 ms |
| World update maximum after warm-up | <= 500 ms | <= 1,000 ms | > 1,000 ms |
| Worldserver resident memory | <= 24 GiB | <= 27 GiB | > 27 GiB or sustained swap growth |
| World-thread operation queue | returns to zero, no failures/skips | bounded nonzero backlog | growing backlog or failures/skips |
| Stability | no crash, assert, or dropped work | isolated recoverable warning | crash, assert, or dropped work |
| Human-client response | no noticeable delay | occasional delay | combat/group/quest interaction impaired |

Population increases only after a pass at the previous gate: 500, 1,000, 2,000, 3,000, 4,000, then 5,000.
If a gate fails, profile and optimize at that population rather than hiding the failure by reducing AI activity.

Set a gate without modifying tracked configuration:

```bash
BOT_COUNT=1000 docker compose up -d ac-worldserver
```

The default `RandomBotAccountCount = 0` automatically creates enough accounts, and the default total-to-online
ratio of 2.0 means the 5,000-online gate needs approximately 10,000 generated characters. `BOT_LOGIN_BATCH` can
override the default 60 bots processed per 20-second manager interval, but must stay constant in A/B comparisons.

## Trend log

| Bots | Revision/change | CPU mean | Memory mean | Tick p95/p99/max | Stability | Trend |
| ---: | --- | ---: | ---: | --- | --- | --- |
| 500 | `ceeb311`, 8 map workers, formal 5-minute idle baseline | 217.86% | 4.84 GiB | 7-9 / 7-9 / 8-10 ms across 10 windows | no matched errors; human test previously passed | formal baseline |
| 500 | Avoid disabled-profiler map label; add queue telemetry | 233.60% | 4.72 GiB | 6-9 / 7-9 / 7-10 ms across 11 windows | queue 0, peak 38, 0 failed/skipped | CPU +7.2%, memory -2.5%; activity variance makes effect inconclusive |
| 1,000 | First scaling gate, 8 map workers | 287.54% | 5.17 GiB | 8-10 / 9-10 / 10-16 ms across 10 windows | queue 0, peak 60, 0 failed/skipped | pass; +24.7% CPU for +100% bots vs optimized 500 run |
| 2,000 | Second scaling gate, login batch 200 | 317.59% | 5.64 GiB | 11-12 / 15-16 / 17-20 ms across 10 windows | queue 0, peak 134, 0 failed/skipped | pass; +10.4% CPU for +100% bots |
| 3,000 | Third scaling gate, login batch 200 | 338.73% | 6.08 GiB | 13-15 / 17-19 / 19-21 ms across 10 windows | queue 0, peak 92, 0 failed/skipped | pass; +6.7% CPU for +50% bots |
| 4,000 | Fourth scaling gate, login batch 200 | 345.04% | 6.55 GiB | 15-18 / 19-21 / 20-30 ms across 10 windows | queue 0, peak 72, 0 failed/skipped | pass; +1.9% CPU for +33% bots |
| 5,000 | Target gate, login batch 300 | 343.48% | 6.94 GiB | 18-20 / 22-24 / 23-28 ms across 10 windows | queue 0, peak 82, 0 failed/skipped; no errors/OOM/restart | machine pass; human-client test pending |

Raw baseline: `var/benchmarks/20260728-110734-500-baseline-prefixed-image`.
Raw optimized run: `var/benchmarks/20260728-111509-500-opt1-no-disabled-perf-label`.
Raw 1,000-bot gate: `var/benchmarks/20260728-112532-1000-gate-opt1`.
Raw 2,000-bot gate: `var/benchmarks/20260728-113752-2000-gate-opt1`.
Raw 3,000-bot gate: `var/benchmarks/20260728-115206-3000-gate-opt1`.
Raw 4,000-bot gate: `var/benchmarks/20260728-120621-4000-gate-opt1`.
Raw 5,000-bot gate: `var/benchmarks/20260728-123018-5000-gate-opt1`.

## Final local operation

The local Compose override defaults to 5,000 active bots, a 300-bot login batch, eight map workers, and 30-second
world-update summaries. A normal restart therefore preserves the target:

```bash
docker compose up -d
```

Inspect health and resource use:

```bash
docker compose ps
docker stats --no-stream ac-worldserver
```

From the worldserver console, `playerbots pmon queue` reports cross-thread queue health. Keep the Playerbots
performance monitor disabled during normal play; it adds locks and allocation overhead when enabled.

## Candidate changes

- Avoid constructing a `WorldPosition` and map label on every bot AI update when the performance monitor is off.
- Use profiler evidence to find repeated value/trigger computation and lock contention.
- Measure the world-thread work queue before changing its batch size or cadence.
- Tune bot AI scheduling only if it preserves independent world activity and human-client responsiveness.

Every code change needs an A/B run at the same bot count. A lower CPU number is not automatically better if world
update latency or bot responsiveness gets worse.
