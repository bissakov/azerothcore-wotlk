# Playerbot AzerothCore Fork

This repository is a C++20 WoW 3.3.5a server fork for a production Playerbot realm. It contains a
Playerbot-compatible core plus the tracked `mod-playerbots` and `mod-llm-chat` modules. Docker
Compose is a supported deployment path.

## Change ownership

- Prefer a module change when it can meet the requirement. Change the core only for a core bug,
  a required hook/interface, or a measured cross-cutting performance/correctness improvement.
  Preserve interfaces and behaviour relied upon by `mod-playerbots`.
- Keep fork-owned, deployable changes tracked. Do **not** put them in
  `src/server/scripts/Custom/`: that directory is gitignored and is for deliberately local-only
  work. Use a tracked module or an explicitly requested core change instead.
- State whether a substantial change is fork-only, intended to remain easy to rebase, or a
  candidate for upstreaming. Do not impose upstream contribution policy on fork-only work.
- Never add attributions to AI agents (or any automated tooling) in commit messages, PR
  descriptions, code comments, changelogs, or any tracked content. Changes are authored by the
  contributor; do not reference the assistant or its involvement anywhere.

## Safety and data rules

- Never edit historical core SQL in `data/sql/base/`, `data/sql/archive/`, or
  `data/sql/updates/db_*/` unless explicitly requested. Put new core migrations in the relevant
  `data/sql/updates/pending_db_{world,auth,characters}/` directory.
- Treat module-owned schemas independently: add module SQL only in that module's documented
  base/update tree. Do not put module tables into a core migration without an explicit reason.
- Migrations must be idempotent where the repository's SQL conventions require it. Do not rewrite
  already-applied migrations to repair a live database; create a corrective migration.
- Never commit credentials, `.env`, database dumps, generated configs, or logs. Keep
  `AC_LLMCHAT_API_KEY` runtime-only.
- Do not reset Docker volumes, expose the MySQL service, or change bot-population/runtime defaults
  without an explicit request. Deployment changes must preserve the container secret and
  read-only module-mount model unless intentionally redesigned.

## Build and validation

Out-of-source builds are required. A standard local build is:

```bash
cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$HOME/azeroth-server" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DSCRIPTS=static -DMODULES=static
cmake --build build -j"$(nproc)"
cmake --install build
```

- C++20 is required. Configure `-DBUILD_TESTING=ON` for unit tests and run
  `ctest --test-dir build` (or the relevant test binary).
- For Docker changes, run `docker compose config`; use `./acore.sh docker build` or the relevant
  Compose target when an image build is warranted.
- Format changed C++ lines with `clang-format-18` using the repository `.clang-format` file; use
  `git-clang-format-18 --binary clang-format-18 --diff HEAD` to verify local changes. Then run the
  relevant build and `cppcheck` check. Validate changed core SQL with
  `apps/ci/check-changed-sql.sh HEAD`; module repositories run their equivalent local checks.
- Validate the smallest relevant surface: compile affected targets; test SQL migrations against a
  disposable database; and test bot, map-update, database, or LLM-path changes in-game or with
  the relevant benchmark. Treat map-update latency and bot throughput as regression-sensitive.

## Long-running commands

- Preserve the active prompt cache during builds, benchmarks, monitoring, and other long-running
  work. Never block a single agent/tool round trip for more than 60 seconds. Configure the command
  to yield within 30 seconds, then poll the same running session at intervals of at most 60 seconds
  until it finishes.
- Do not use a long foreground sleep or chain delayed inspection as `sleep N; <check>` when `N` is
  greater than 60 seconds. For passive waiting, keep the process/session alive and poll it; when no
  process needs to stay attached, run the check separately after short, visible wait intervals.
- An overall command timeout may be longer than 60 seconds when the work genuinely needs it. The
  restriction is on silent blocking time between agent turns, not on total build, test, benchmark,
  or observation duration.
- While work is still running, give the user a concise progress update at least every 60 seconds.

## Implementation guidance

- Follow `.editorconfig` and the surrounding code's style. Avoid drive-by formatting, legacy-name
  cleanups, and broad refactors unless requested.
- Use semantic/typed APIs rather than raw object-field or flag manipulation when an equivalent
  project helper exists. Do not introduce new `Trinity::`, `TC_LOG_*`, or `sLog` usage.
- Use `LOG_*` with `{}` formatting, `Acore::StringFormat`, project random helpers, and
  `sConfigMgr->GetOption<T>` for new code. Match established local database patterns; use prepared
  statements for new input-bearing or repeatedly executed queries and transactions for atomic
  multi-statement writes.
- Do not retain raw `Player*`, `Creature*`, or `Unit*` across a tick, callback, or deferred task.
  Store an `ObjectGuid` and resolve it when used.
- Use `EventMap` or `TaskScheduler` for timed AI work. For content behavior, use SmartAI when the
  behavior is static and data-driven; use C++ when it needs stateful, concurrent, Playerbot/LLM,
  or cross-system behavior.
- Keep network/LLM work off world-update threads. Bound asynchronous work, preserve object
  lifetimes in callbacks, and avoid per-request thread creation.

## Useful locations

- `src/server/game/` — core gameplay and server systems.
- `modules/mod-playerbots/` — Playerbot implementation and module-owned SQL.
- `modules/mod-llm-chat/` — LLM conversation module, RAG data, and character-database schema.
- `data/sql/updates/pending_db_*/` — new core database migrations.
- `docker-compose.yml` and `docker-compose.override.yml` — deployment and realm runtime settings.
- `apps/playerbots-benchmark.sh` — Playerbot benchmark tooling.

## Issue titles

- When suggesting an issue title, use the same type signifier as commits instead of a plain name:

  ```
  (<type>[+<type>...]): brief description
  ```

  Use the type definitions and single-type preference below. Keep the description imperative,
  lowercase, and without a trailing period.

## End of task

- If — and only if — the task created or modified files that git tracks (or would track, i.e. not ignored), end with a suggested commit message matching the project's convention:

  ```
  (<type>[+<type>...]): brief description
  <type> = feat | fix | refactor | perf | test | docs | style | build | ci | revert | chore
  ```

  Parentheses are mandatory; the description is imperative, lowercase, no trailing period. No attribution of any kind — no `Co-Authored-By`, no "Generated with" trailers, no tool signatures.
- Types, most specific wins — pick the one matching the intent of the change, not the file kind:
  - `(feat)` — new user- or operator-visible capability (endpoint, provider, pack field, CLI flag).
  - `(fix)` — corrects wrong behavior; something was broken before, works after.
  - `(refactor)` — code restructuring with no behavior change.
  - `(perf)` — improves speed or resource use without changing behavior.
  - `(test)` — adds or changes tests only.
  - `(docs)` — documentation only (`README`, `docs/`, docstrings, pack/dataset READMEs).
  - `(style)` — formatting, naming, whitespace; no semantic change.
  - `(build)` — dependencies, `pyproject.toml`/`uv.lock`, Dockerfiles, compose, Helm, Makefile.
  - `(ci)` — `.github/workflows/` only.
  - `(revert)` — undoes a previous commit; reference it in the description.
  - `(chore)` — repo housekeeping that fits none of the above (gitignore, configs, data manifests).
- Prefer a single type. Incidental companions don't earn a union: a feature with its own tests and docs is `(feat)`. Use a union like `(fix+refactor)` only when each part would stand as its own commit, dominant type first; if the parts are unrelated, suggest splitting the commit instead.
- Otherwise end without one. No commit message for: answering questions or analysis, work outside the repository or in git-ignored paths, amendments to work already summarized with a commit message earlier in the session (restate the one message covering the final state instead of adding a second), or tasks that left the working tree unchanged.
