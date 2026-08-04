#!/usr/bin/env bash
# Build-time benchmark harness for the AzerothCore Docker build.
#
# Measures wall-clock time of the `build` stage of apps/docker/Dockerfile,
# with optional control over the ccache and build-tree cache mounts so that
# "cold" (empty caches) and "warm" (populated caches) runs can be compared.
#
# Usage:
#   apps/docker/build-benchmark.sh prep-base
#   apps/docker/build-benchmark.sh cold        [--tag NAME] [--extra-args "..."]
#   apps/docker/build-benchmark.sh cold-seeded [--tag NAME] [--extra-args "..."]
#   apps/docker/build-benchmark.sh warm        [--tag NAME] [--extra-args "..."]
#   apps/docker/build-benchmark.sh nocache-warm
#
# "prep-base"      builds and tags the toolchain stage as an image
#                  (acore-build-base). All later runs detect and use it, which
#                  keeps the toolchain out of cold's prune. Not timed.
# "cold"           prunes ALL BuildKit caches on this host (docker builder
#                  prune --all), including other projects', then builds.
#                  ccache starts empty.
# "cold-seeded"    like cold, but the ccache is tarred out first and restored
#                  after the prune: measures a fresh host with a warm ccache.
# "warm"           keeps existing caches, then builds. Measures incremental.
# "nocache-warm"   keeps ccache but does `docker build --no-cache` (forces full
#                  reconfigure/compile, ccache still hit).
#
# Output: a JSON-ish summary line prefixed with [BENCH] for easy grep.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-warm}"
shift || true

TAG="acore-build-benchmark"
EXTRA_ARGS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --extra-args) EXTRA_ARGS="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Revision args (same logic as apps/docker/docker-cmd.sh).
if git rev-parse --git-dir >/dev/null 2>&1; then
    REV_INFO="$(git describe --long --match 0.1 --dirty=+ --abbrev=12 --always)"
    REV_HASH="$(sed -E 's/^0\.1-//; s/[0-9]+-g//' <<< "$REV_INFO")"
    AC_REV_HASH="${AC_REV_HASH:-$REV_HASH}"
    AC_REV_BRANCH="${AC_REV_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
    AC_REV_DATE="${AC_REV_DATE:-$(git show -s --format=%ci)}"
fi
export AC_REV_HASH AC_REV_BRANCH AC_REV_DATE

BASE_IMAGE="acore-build-base:24.04"

case "$MODE" in
    prep-base)
        docker buildx build \
            --target build-deps \
            --tag "$BASE_IMAGE" \
            --file apps/docker/Dockerfile \
            .
        echo "[BENCH] tagged $BASE_IMAGE; benchmark runs will now use it (compose: DOCKER_BUILD_BASE=$BASE_IMAGE)"
        exit 0
        ;;
    cold)
        echo "[BENCH] pruning BuildKit exec.cachemount + regular build cache..."
        docker builder prune --filter type=exec.cachemount --force >/dev/null 2>&1 || true
        # Also drop regular cache layers to ensure a clean cold start.
        docker builder prune --force --all >/dev/null 2>&1 || true
        BUILD_FLAGS=""
        ;;
    cold-seeded)
        echo "[BENCH] saving ccache, pruning all BuildKit caches, restoring ccache..."
        "$SCRIPT_DIR/ccache-io.sh" backup /tmp/bench-ccache-seed.tar
        docker builder prune --filter type=exec.cachemount --force >/dev/null 2>&1 || true
        docker builder prune --force --all >/dev/null 2>&1 || true
        "$SCRIPT_DIR/ccache-io.sh" restore /tmp/bench-ccache-seed.tar
        rm -f /tmp/bench-ccache-seed.tar
        BUILD_FLAGS=""
        ;;
    warm)
        BUILD_FLAGS=""
        ;;
    nocache-warm)
        # Keep exec.cachemount (ccache + build tree) but force Docker layer cache off.
        BUILD_FLAGS="--no-cache"
        ;;
    *)
        echo "usage: $0 {prep-base|cold|cold-seeded|warm|nocache-warm}" >&2; exit 2 ;;
esac

# Use the prebaked toolchain image when it exists (see prep-base).
BASE_ARGS=()
if docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    echo "[BENCH] using prebuilt toolchain image $BASE_IMAGE"
    BASE_ARGS=(--build-arg BUILD_BASE="$BASE_IMAGE")
fi

START_EPOCH=$(date +%s.%N)
START_ISO=$(date -Iseconds)
LOG="/tmp/bench-build-$MODE.log"

# Build only the worldserver target. It pulls in the full `build` stage and
# is the largest binary, so it dominates total build time. The exit status is
# captured (pipefail makes it the build's, not tee's) so a failed build still
# gets a summary line and the script exits with the build's status.
set +e
docker buildx build \
    --load \
    $BUILD_FLAGS \
    --target worldserver \
    --tag "$TAG:$MODE" \
    --file apps/docker/Dockerfile \
    --build-arg AC_REV_HASH="$AC_REV_HASH" \
    --build-arg AC_REV_BRANCH="$AC_REV_BRANCH" \
    --build-arg AC_REV_DATE="$AC_REV_DATE" \
    "${BASE_ARGS[@]}" \
    $EXTRA_ARGS \
    . 2>&1 | tee "$LOG"
BUILD_EXIT=$?
set -e

END_EPOCH=$(date +%s.%N)
END_ISO=$(date -Iseconds)

WALL=$(awk -v s="$START_EPOCH" -v e="$END_EPOCH" 'BEGIN{printf "%.1f", e - s}')

echo ""
echo "[BENCH] mode=$MODE tag=$TAG:$MODE wall=${WALL}s start=$START_ISO end=$END_ISO exit=$BUILD_EXIT"

# The build stage runs `ccache -s` after compiling; surface those lines from
# the captured log. (The cache mount cannot be probed with `docker run` — cache
# mounts are BuildKit-only — and the runtime image has no ccache binary.)
# A fully layer-cached run never executes the build RUN, so there may be none.
if grep -qE 'Cacheable calls|Cache size' "$LOG"; then
    echo "[BENCH] ccache-stats-begin"
    grep -E 'Cacheable calls|Hits:|Misses:|Uncacheable|Cache size' "$LOG"
    echo "[BENCH] ccache-stats-end"
fi

exit "$BUILD_EXIT"
