#!/usr/bin/env bash
# Backup / restore the BuildKit ccache cache mount used by apps/docker/Dockerfile.
#
# The compiler cache lives in a BuildKit cache mount (target=/ccache), which
# `docker builder prune` deletes wholesale - cache mounts cannot be pruned
# selectively. These commands move the cache to/from a tarball so a pruned
# builder or a fresh host can start warm: a fully warm ccache turns the cold
# compile into minutes of cache hits.
#
# Usage:
#   apps/docker/ccache-io.sh backup  [FILE]   # default: ccache-backup.tar
#   apps/docker/ccache-io.sh restore [FILE]
#
# Both operations go through dedicated stages of the main Dockerfile
# (ccache-backup / ccache-import), built with the same file and context as the
# real build, so they resolve exactly the mount the compiler uses. Restoring
# on another host works the same way: run this script there.
#
# Beware: dockerd's build-cache garbage collection can evict the ccache mount
# on its own once total build cache exceeds the daemon's keep-storage
# threshold - observed in practice, with no warning. Periodic backups are the
# insurance; raising the builder GC threshold in /etc/docker/daemon.json is
# the fix.
#
# The tar is uncompressed on purpose - ccache already zstd-compresses objects.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

CMD="${1:-}"
FILE="${2:-ccache-backup.tar}"
# Forces only the tar RUN to re-execute; everything else stays layer-cached.
BUST="$(date +%s)"

case "$CMD" in
    backup)
        TMP=$(mktemp -d)
        trap 'rm -rf "$TMP"' EXIT
        docker buildx build \
            --target ccache-backup \
            --build-arg CCACHE_IO_BUST="$BUST" \
            --output "type=local,dest=$TMP" \
            --file apps/docker/Dockerfile \
            .
        mv "$TMP/ccache-backup.tar" "$FILE"
        echo "ccache saved to $FILE ($(du -h "$FILE" | cut -f1))"
        ;;
    restore)
        [[ -f "$FILE" ]] || { echo "no such file: $FILE" >&2; exit 1; }
        TMP=$(mktemp -d)
        trap 'rm -rf "$TMP"' EXIT
        cp "$FILE" "$TMP/ccache-backup.tar"
        docker buildx build \
            --target ccache-import \
            --build-arg CCACHE_IO_BUST="$BUST" \
            --build-context seed="$TMP" \
            --file apps/docker/Dockerfile \
            .
        echo "ccache restored from $FILE"
        ;;
    *)
        echo "usage: $0 {backup|restore} [FILE]" >&2
        exit 2
        ;;
esac
