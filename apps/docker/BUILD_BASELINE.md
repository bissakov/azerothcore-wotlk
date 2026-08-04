# Docker Build Baseline (before optimization)

Measured on this host (16 cores / 30 GiB RAM, Docker 29.7.1 + Buildx 0.36.0,
default docker driver) using `apps/docker/build-benchmark.sh`, which builds the
`worldserver` target of `apps/docker/Dockerfile` (pulls in the full `build`
stage: core + scripts + modules + dbimport, `RelWithDebInfo`, clang + lld +
ninja, ccache launcher).

Branch: `Playerbot` @ `5c1429d6710c+`

## Before (baseline)

| Scenario                              | Wall time  | Notes |
|---------------------------------------|------------|-------|
| **Cold** (caches pruned, full build)  | **3002 s** (~50 min) | 1655/1655 TUs; tools built (`CTOOLS_BUILD=all`) |
| Warm, no source change                | 1.1 s      | Pure Docker layer-cache hit |
| Incremental, 1 .cpp comment           | 6.1 s      | Build-tree cache mount; ccache not exercised |
| **Incremental, `Common.h` touched**   | **561.8 s** (~9.4 min) | **1655/1655 TUs recompiled** (fresh COPY mtimes + empty ccache) |

ccache after the baseline cold + incremental builds:

```
Cache size (GiB): 0.0 / 5.0
```

## After (ccache wired up + tools scope reduced)

Same harness, same host. Fixes applied: the `CCACHE_*` ARGs are promoted to
ENV and applied via `ccache --set-config` (sloppiness, 10G maxsize, content
compiler-check, compression); `CTOOLS_BUILD` defaults to `db-only` for server /
db-import / client-data images and `all` only for the opt-in `ac-tools`
service; `USE_SCRIPTPCH`/`USE_COREPCH`/`CMAKE_EXTRA_OPTIONS`/`CACHEBUST` wired
through.

| Scenario                              | Wall time  | vs baseline | ccache hits |
|---------------------------------------|------------|-------------|-------------|
| **Cold** (caches pruned, full build)  | **965.5 s** (~16 min) | **-68%** (-2037 s) | 0/1779 (populating) |
| Warm, no source change                | 1.2 s      | same        | n/a (layer cache) |
| **Incremental, `Common.h` touched**   | **190.8 s** (~3.2 min) | **-66%** (-371 s) | **649/1639 (39.6%)** |
| Incremental, 1 .cpp comment           | 47.6 s     | overhead-bound¹ | 1639/1639 (100%) |

ccache after the optimized cold build:

```
Cacheable calls:   1779 / 1779 (100.0%)
  Hits:               0 / 1779 ( 0.00%)   <- cold, populating
  Misses:          1779 / 1779 (100.0%)
Local storage:
  Cache size (GB):  0.5 / 10.0 ( 5.18%)  <- cache now persists
```

¹ Not relink-bound as first assumed — the build log shows lld links worldserver
in ~1 s. The cost is ninja re-running all 1639 compiles against COPY-minted
mtimes (each answered by ccache in ~20 ms, ~30 s total) plus ~15 s of image
export. Round 2 below attacks exactly this.

The `Common.h` incremental is the headline result: ccache's
`include_file_mtime`/`include_file_ctime` sloppiness now lets it serve the
unchanged-content TUs (fresh COPY mtimes no longer force misses). Ninja still
schedules all 1639 TUs, but only the 990 misses in the table — the TUs whose
preprocessed content actually changed — reach the compiler.

## Key finding: ccache was storing nothing (before)

Probed the `/ccache` BuildKit cache mount after the cold + header-change builds:

```
Local storage:
  Cache size (GiB): 0.0 / 5.0 ( 0.00%)
```

- Maxsize was **5.0 GiB** (ccache default), not the **10 GiB** the Dockerfile
  comments documented — the `CCACHE_*` ARGs (Dockerfile lines 59-83) were
  declared but never applied.
- **Zero objects** were retained, so the 561.8 s header-change build recompiled
  all 1655 TUs from scratch. With `include_file_mtime`/`include_file_ctime`
  sloppiness wired up, ccache keys on content instead of COPY-minted mtimes.

This was the highest-impact fix and is now applied.

## Dead ARGs (before) — now wired through

- `CCACHE_MAXSIZE`, `CCACHE_SLOPPINESS`, `CCACHE_COMPRESS`,
  `CCACHE_COMPRESSLEVEL`, `CCACHE_COMPILERCHECK`, `CCACHE_LOGFILE` — declared,
  never applied. Now promoted to ENV / `ccache --set-config`.
- `CCACHE_CPP2` — also declared and never applied; removed instead of wired
  through, since `run_second_cpp` has long been ccache's default.
- `CSCRIPTPCH="OFF"` — never passed to cmake. Now wired as
  `-DUSE_SCRIPTPCH`, with a matching `CCOREPCH`/`-DUSE_COREPCH` (both default ON).
- `CMAKE_EXTRA_OPTIONS=""` — never appended to the cmake invocation. Now appended.
- `CACHEBUST` — never referenced in any `RUN`. Now consumed in the build `RUN`
  so a non-default value forces re-execution.

## Build scope reduction

`CTOOLS_BUILD` now defaults to `db-only` in the Dockerfile and in
`docker-compose.yml`'s `x-build-args` (overridable via `DOCKER_CTOOLS_BUILD`).
The opt-in `ac-tools` service overrides it to `all`. This skips compiling the
four map extractors (`map_extractor`, `mmaps_generator`, `vmap4_assembler`,
`vmap4_extractor` — 20 of the 21 tool TUs) on every server / db-import /
client-data image build.

Trade-off: `CTOOLS_BUILD` is part of the build-tree cache-mount id, so the
server images (`db-only`) and `ac-tools` (`all`) keep separate build trees. An
`ac-tools` build re-runs configure and the links (the compiles come from
ccache), and the BuildKit build cache holds two trees' worth of disk. The
`tools` image target also only works with `CTOOLS_BUILD=all`; compose handles
that, but a bare `docker build --target tools` needs the arg passed explicitly.

## Round 2 (structural)

Same host. Four changes on top of Round 1:

1. **Prebaked toolchain image** — the apt toolchain moved to a `build-deps`
   stage selectable via `BUILD_BASE`. `build-benchmark.sh prep-base` tags it as
   `acore-build-base:24.04`; being an image (not cache), it survives
   `docker builder prune`, removing ~390 s of apt work from a cold build.
   Compose: `DOCKER_BUILD_BASE=acore-build-base:24.04`.
2. **Source staging** — the COPY layers are rsynced (`-rlc`, checksum, no `-t`)
   into a persistent `/acore-src` cache mount, so unchanged files keep their
   mtimes. Ninja now rebuilds only true dependents instead of re-running all
   1639 compiles against COPY-minted mtimes (~30 s of ccache hit churn, gone).
3. **`-gline-tables-only`** (`CFLAGS_RELWITHDEBINFO`) — crash backtraces keep
   function+line; core dumps lose local variables. Compile is ~10 % faster,
   objects/binary much smaller: worldserver 172 MB (image 557 MB vs 1.06 GB),
   image export 15.2 s → 4.3 s, full ccache set 0.3 GB (was 0.5 GB).
4. **ccache backup/restore** — `apps/docker/ccache-io.sh backup|restore`
   (411 MB tar for the full set) plus a `cold-seeded` benchmark mode.

| Scenario | Round 1 | Round 2 | Notes |
|---|---|---|---|
| Full recompile, empty ccache | 965.5 s (true cold) | **533.4 s** | all 1779 TUs; base image + `-g1` |
| **Cold-seeded** (prune `--all`, restore ccache) | n/a | **36.8 s** (+ ~1 min restore) | 1639/1779 hits (92 %) |
| Incremental, `Common.h` touched | 190.8 s | **~170 s** (RUN 163.7 s) | see PCH note |
| **Incremental, 1 .cpp** | 47.6 s | **4.5 s** | ninja: genrev + 1 TU + link |
| Forced re-run, no change (`CACHEBUST`) | n/a | 2.7 s | staging+configure+no-op+install |
| Warm, no change | 1.2 s | 0.4 s | layer cache |

PCH note: touching `Common.h` rebuilds the core PCH, and ninja then re-runs
every PCH-consuming TU (all 1639); ccache answers 649, and the 990 whose
preprocessed content really changed recompile. That ~170 s is genuine work —
the only remaining lever there is include-graph surgery.

## ccache is not GC-safe (observed)

dockerd's build-cache garbage collector evicted the entire /ccache mount
mid-session, silently, while total build cache was ~21 GB. Cause: docker's
default GC policy puts cache mounts in a band capped at ~12 GiB with a 48 h
keep-duration. Cache mounts cannot be pinned, so this host now runs an
explicit policy in `/etc/docker/daemon.json` (needs a dockerd restart):

```json
{
  "builder": {
    "gc": {
      "enabled": true,
      "policy": [
        { "keepStorage": "40GB", "filter": ["type=exec.cachemount"] },
        { "keepStorage": "60GB", "all": true }
      ]
    }
  }
}
```

Cache mounts get a dedicated 40 GiB band with no age eviction; everything is
capped at 60 GiB overall. Two syntax traps, both of which broke daemon
startup or the rule when hit: daemon.json uses the CLI filter grammar
(`type=...`, single `=` — `type==...` parses as the value `=...` and matches
nothing), and each rule accepts only **one** filter string (several in one
rule fails with "filters expect only one value" and dockerd refuses to start).
Verify with `docker buildx inspect default`. Do **not** use
`defaultKeepStorage` for this: it shrinks the generated cache-mount band
(measured: 12.23 GiB → 5.53 GiB with `defaultKeepStorage: 40GB`).

`ccache-io.sh backup` remains the insurance for prunes and other hosts —
restore takes ~1 min and turns the next build into the 36.8 s cold-seeded
case.

## Reproducing

```bash
# once: bake the toolchain image (survives pruning)
bash apps/docker/build-benchmark.sh prep-base

# cold — WARNING: prunes ALL BuildKit caches on the host, not just this project's
bash apps/docker/build-benchmark.sh cold

# cold, but with the ccache saved and restored around the prune
bash apps/docker/build-benchmark.sh cold-seeded

# warm / incremental (after a source edit)
bash apps/docker/build-benchmark.sh warm
```

Each run prints a `[BENCH] ...` summary line with wall time and, when the
build RUN executed, the `ccache -s` lines from the build log.
