ARG UBUNTU_VERSION=24.04 # lts

# Resolves to the in-file build-deps stage by default. Point it at a tagged
# image instead (built with `apps/docker/build-benchmark.sh prep-base`) to keep
# the toolchain out of `docker builder prune`'s reach: images survive cache
# pruning, cache layers do not, and reinstalling the toolchain is ~5 minutes of
# an otherwise-cold build.
ARG BUILD_BASE=build-deps

# This target lays out the general directory skeleton for AzerothCore,
# This target isn't intended to be directly used
FROM ubuntu:$UBUNTU_VERSION AS skeleton

# Note: ARG instructions defined after FROM are available in this build stage.
# Placing ARG TZ here (after FROM) ensures it is accessible for configuring the timezone below.
ARG TZ=Etc/UTC
ARG DOCKER=1
ARG DEBIAN_FRONTEND=noninteractive

ENV AC_FORCE_CREATE_DB=1

RUN mkdir -pv \
        /azerothcore/bin                   \
        /azerothcore/data                  \
        /azerothcore/deps                  \
        /azerothcore/env/dist/bin          \
        /azerothcore/env/dist/data/Cameras \
        /azerothcore/env/dist/data/dbc     \
        /azerothcore/env/dist/data/maps    \
        /azerothcore/env/dist/data/mmaps   \
        /azerothcore/env/dist/data/vmaps   \
        /azerothcore/env/dist/logs         \
        /azerothcore/env/dist/temp         \
        /azerothcore/env/dist/etc          \
        /azerothcore/modules               \
        /azerothcore/src                   \
        /azerothcore/build

# Every stage below installs packages through a pair of BuildKit cache mounts,
# so touching one package list re-uses the .deb files and index that an earlier
# build already downloaded instead of going back to the mirror. Ubuntu ships a
# docker-clean hook that erases the archives after each apt-get run, which would
# defeat the archive cache, so it is removed once here for all descendants.
# Neither directory is part of the image, so the layers get smaller as well.
RUN rm -f /etc/apt/apt.conf.d/docker-clean

# Configure Timezone
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked              \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked         \
    apt-get update                                                       \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime                  \
    && echo "$TZ" > /etc/timezone                                          \
    && dpkg-reconfigure --frontend noninteractive tzdata

WORKDIR /azerothcore

# Toolchain for the build stage, kept as its own stage so it can be baked into
# a tagged image (`apps/docker/build-benchmark.sh prep-base`) and reused via
# BUILD_BASE above.
FROM skeleton AS build-deps

# The Boost list is spelled out because deps/boost asks for exactly five
# components (filesystem, program_options, iostreams, regex, thread) and the
# only other linked usage in the tree is stacktrace. libboost-all-dev drags in
# MPI, Python and the graph libraries on top of that, which is a few hundred
# megabytes downloaded and unpacked for nothing. rsync is used by the build
# stage below to stage sources with preserved mtimes.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked         \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked     \
    apt-get update                                                     \
    && apt-get install -y --no-install-recommends                         \
        build-essential ccache ninja-build cmake clang lld rsync             \
        default-libmysqlclient-dev libssl-dev                                  \
        libreadline-dev zlib1g-dev libbz2-dev liblzma-dev                   \
        libboost-dev libboost-filesystem-dev libboost-iostreams-dev            \
        libboost-program-options-dev libboost-regex-dev                         \
        libboost-stacktrace-dev libboost-thread-dev

# This target builds the docker image
# This target can be useful to inspect the explicit outputs from the build,
FROM ${BUILD_BASE} AS build

ARG UBUNTU_VERSION
ARG TARGETARCH
ARG CTOOLS_BUILD="db-only"
ARG CTYPE="RelWithDebInfo"
ARG CSCRIPTPCH="ON"
ARG CCOREPCH="ON"
ARG CSCRIPTS="static"
ARG CMODULES="static"
ARG CSCRIPTS_DEFAULT_LINKAGE="static"
ARG CWITH_WARNINGS="ON"
ARG CMAKE_EXTRA_OPTIONS=""
# RelWithDebInfo compiles with `-O2 -g` by default, and full DWARF is a large
# share of both compile time and object size. line-tables-only debug info
# keeps the function+line information that crash backtraces print but drops
# local variables in core dumps; pass "-O2 -g -DNDEBUG" for full debug info.
ARG CFLAGS_RELWITHDEBINFO="-O2 -gline-tables-only -DNDEBUG"
ARG GIT_DISCOVERY_ACROSS_FILESYSTEM=1

ARG CCACHE_DIR="/ccache"
# A full build of the core plus the bundled modules stores ~0.5 GB of objects
# (1800 translation units, RelWithDebInfo). The old 1 GB ceiling therefore held
# barely two builds' worth, so any two branches in rotation evicted each other.
# 10 GB holds roughly twenty and still costs nothing when unused.
ARG CCACHE_MAXSIZE="10G"
# The rsync staging below keeps mtimes stable for unchanged files, but any
# file that still reaches the compiler with a fresh timestamp (touched but
# identical content, a rebuilt staging tree) should hit anyway, so ccache is
# told to ignore mtime/ctime and compare contents. pch_defines is required for
# ccache to handle the precompiled headers at all.
ARG CCACHE_SLOPPINESS="pch_defines,time_macros,include_file_mtime,include_file_ctime"
ARG CCACHE_COMPRESS="true"
# High zstd levels spend substantial CPU for marginal savings, especially while
# a cold build is trying to populate the cache. Level 3 matches the CI builds.
ARG CCACHE_COMPRESSLEVEL="3"
ARG CCACHE_COMPILERCHECK="content"
ARG CCACHE_LOGFILE=""

# The ARGs above only scope values across the build; ccache reads its runtime
# configuration from the environment (CCACHE_*) and its config file. Promote
# them to ENV so the RUN below actually applies them, instead of declaring a
# tuning block that the build never sees. Compression is applied with
# `ccache --set-config` instead because boolean CCACHE_* variables are
# presence-sensitive (any value enables them, even "false" or an empty string),
# so the config file is the only unambiguous way to set them; max_size rides
# along so the limit is recorded in the cache's own config file.
ENV CCACHE_DIR="$CCACHE_DIR" \
    CCACHE_SLOPPINESS="$CCACHE_SLOPPINESS" \
    CCACHE_COMPILERCHECK="$CCACHE_COMPILERCHECK" \
    CCACHE_LOGFILE="$CCACHE_LOGFILE"

# The revision stamped into revision.h, normally read from the repository by
# src/cmake/genrev.cmake. The repository is deliberately kept out of the build
# context (see .dockerignore): it is 1.3 GB of the 1.5 GB context, and because a
# bind-mounted .git is part of this stage's cache key, any git operation at all
# - a fetch, a commit, a branch switch - invalidated the compile below. Passing
# the three values in instead costs nothing and only changes when HEAD does.
#
# Left empty the build still succeeds and reports "unknown ... (Archived branch)"
# like any other repository-less build. `./acore.sh docker build` fills them in
# automatically; see docker-compose.yml if you drive compose directly.
ARG AC_REV_HASH=""
ARG AC_REV_BRANCH=""
ARG AC_REV_DATE=""

# Ordered least to most volatile: a source edit should not invalidate the layers
# holding the vendored dependencies.
COPY deps /azerothcore/deps
COPY CMakeLists.txt /azerothcore/CMakeLists.txt
COPY conf /azerothcore/conf
COPY modules /azerothcore/modules
COPY src /azerothcore/src

# CACHEBUST lets a caller force the build RUN to re-execute even when no
# earlier layer changed. To have any effect it must be consumed inside the RUN
# below; an ARG alone never invalidates a cache key for a step that does not
# reference it.
ARG CACHEBUST=0

WORKDIR /azerothcore/build

# The CMake build tree is kept in a cache mount alongside ccache, and the
# sources are staged out of the COPY layers into a third cache mount
# (/acore-src) with `rsync -rlc`: checksum comparison, and deliberately no -t,
# so a file whose content is unchanged is not touched at all and keeps the
# mtime of the build that last changed it. Ninja therefore sees fresh
# timestamps only on files that really changed and rebuilds just their
# dependents, instead of re-running every compile against COPY-minted mtimes
# and leaning on ccache to answer them (~30 s of hit churn per build).
#
# The build-tree mount is keyed on the settings baked into CMakeCache.txt so
# builds with different options don't fight over one directory; the "staged"
# suffix retires trees configured against the pre-staging source path, which
# cmake would reject.
#
# Nothing here reaches the image: `cmake --install` copies the artifacts out to
# /azerothcore/env/dist, which is a real layer. If the tree ever does go stale,
# `docker builder prune --filter type=exec.cachemount` clears it.
#
# CACHEBUST is referenced first so a non-default value forces this step to
# re-execute even when no earlier COPY layer changed; with the default (0) the
# `test` is a no-op. ccache is then configured (max_size/compress come from its
# config file, the rest from the ENV above) and its stats zeroed so the
# post-build `ccache -s` reflects only this build's hits and misses.
RUN --mount=type=cache,target=/ccache,sharing=locked \
    --mount=type=cache,target=/acore-src,sharing=locked \
    --mount=type=cache,target=/azerothcore/build,id=acore-build-$UBUNTU_VERSION-$TARGETARCH-$CTYPE-$CSCRIPTS-$CMODULES-$CTOOLS_BUILD-$CWITH_WARNINGS-$CSCRIPTPCH-$CCOREPCH-staged,sharing=locked \
    test "$CACHEBUST" -eq 0 || echo "cache busted (CACHEBUST=$CACHEBUST)" \
    && rsync -rlc --delete \
        /azerothcore/deps /azerothcore/CMakeLists.txt /azerothcore/conf \
        /azerothcore/modules /azerothcore/src /acore-src/ \
    && ccache --set-config max_size="$CCACHE_MAXSIZE" \
        --set-config compression="$CCACHE_COMPRESS" \
        --set-config compression_level="$CCACHE_COMPRESSLEVEL" \
    && ccache -z \
    && cmake /acore-src \
       -G Ninja \
       -DCMAKE_INSTALL_PREFIX="/azerothcore/env/dist"  \
       -DCMAKE_EXE_LINKER_FLAGS="-fuse-ld=lld"          \
       -DCMAKE_SHARED_LINKER_FLAGS="-fuse-ld=lld"       \
       -DREV_HASH="$AC_REV_HASH"                       \
       -DREV_BRANCH="$AC_REV_BRANCH"                   \
       -DREV_DATE="$AC_REV_DATE"                       \
       -DWITHOUT_GIT=ON                                 \
       -DAPPS_BUILD="all"                              \
       -DTOOLS_BUILD="$CTOOLS_BUILD"                   \
       -DSCRIPTS="$CSCRIPTS"                           \
       -DMODULES="$CMODULES"                           \
       -DUSE_SCRIPTPCH="$CSCRIPTPCH"                   \
       -DUSE_COREPCH="$CCOREPCH"                       \
       -DWITH_WARNINGS="$CWITH_WARNINGS"               \
       -DCMAKE_BUILD_TYPE="$CTYPE"                     \
       -DCMAKE_C_FLAGS_RELWITHDEBINFO="$CFLAGS_RELWITHDEBINFO"   \
       -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="$CFLAGS_RELWITHDEBINFO" \
       -DCMAKE_CXX_COMPILER="clang++"                  \
       -DCMAKE_C_COMPILER="clang"                      \
       -DCMAKE_CXX_COMPILER_LAUNCHER="ccache"          \
       -DCMAKE_C_COMPILER_LAUNCHER="ccache"            \
       -DBoost_USE_STATIC_LIBS="ON"                    \
       $CMAKE_EXTRA_OPTIONS                             \
    && cmake --build . --config "$CTYPE" -j $(($(nproc) + 1)) \
    && ccache -s                                         \
    && cmake --install . --config "$CTYPE"

#############################
# Base runtime for services #
#############################

FROM skeleton AS runtime

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG DOCKER_USER=acore

ENV ACORE_COMPONENT=undefined

# Install base dependencies for azerothcore
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      libmysqlclient21 libreadline8 libicu74 libncurses5-dev \
      gettext-base default-mysql-client \
      adduser

COPY --from=build /azerothcore/env/dist/etc/ /azerothcore/env/ref/etc

VOLUME /azerothcore/env/dist/etc

ENV PATH="/azerothcore/env/dist/bin:$PATH"

# To use GID/UID 1000 in ubuntu > 23.04 the existing user must be deleted
# See https://bugs.launchpad.net/cloud-images/+bug/2005129
RUN userdel --remove ubuntu \
  && addgroup --gid "$GROUP_ID" "$DOCKER_USER" \
  && adduser --disabled-password --gecos '' --uid "$USER_ID" --gid "$GROUP_ID" "$DOCKER_USER" \
  && passwd -d "$DOCKER_USER" \
  && chown -R "$DOCKER_USER:$DOCKER_USER" /azerothcore

COPY --chown=$USER_ID:$GROUP_ID \
     --chmod=755 \
     apps/docker/entrypoint.sh /azerothcore/entrypoint.sh

USER $DOCKER_USER

ENTRYPOINT ["/usr/bin/env", "bash", "/azerothcore/entrypoint.sh"]

###############
# Auth Server #
###############

FROM runtime AS authserver
LABEL description="AzerothCore Auth Server"

ENV ACORE_COMPONENT=authserver
# Don't run database migrations. We can leave that up to the db-import container
ENV AC_UPDATES_ENABLE_DATABASES=0
# This disables user prompts. The console is still active, however
ENV AC_DISABLE_INTERACTIVE=1
ENV AC_CLOSE_IDLE_CONNECTIONS=0

COPY --chown=$DOCKER_USER:$DOCKER_USER \
     --from=build \
     /azerothcore/env/dist/bin/authserver /azerothcore/env/dist/bin/authserver


CMD ["authserver"]

################
# World Server #
################

FROM runtime AS worldserver

LABEL description="AzerothCore World Server"

ENV ACORE_COMPONENT=worldserver
# Don't run database migrations. We can leave that up to the db-import container
ENV AC_UPDATES_ENABLE_DATABASES=0
# This disables user prompts. The console is still active, however
ENV AC_DISABLE_INTERACTIVE=1
ENV AC_CLOSE_IDLE_CONNECTIONS=0

COPY --chown=$DOCKER_USER:$DOCKER_USER \
     --from=build \
     /azerothcore/env/dist/bin/worldserver /azerothcore/env/dist/bin/worldserver

VOLUME /azerothcore/env/dist/etc

CMD ["worldserver"]

#############
# DB Import #
#############

FROM runtime AS db-import

LABEL description="AzerothCore Database Import tool"

USER $DOCKER_USER

ENV ACORE_COMPONENT=dbimport

COPY --chown=$DOCKER_USER:$DOCKER_USER \
    data data

COPY --chown=$DOCKER_USER:$DOCKER_USER \
    modules modules

COPY --chown=$DOCKER_USER:$DOCKER_USER\
     --from=build \
     /azerothcore/env/dist/bin/dbimport /azerothcore/env/dist/bin/dbimport

CMD [ "/azerothcore/env/dist/bin/dbimport" ]

###############
# Client Data #
###############

FROM skeleton AS client-data

LABEL description="AzerothCore client-data"

ENV DATAPATH=/azerothcore/env/dist/data

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends curl unzip adduser ca-certificates

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG DOCKER_USER=acore

# To use GID/UID 1000 in ubuntu > 23.04 the existing user must be deleted
# See https://bugs.launchpad.net/cloud-images/+bug/2005129
RUN userdel --remove ubuntu \
  && addgroup --gid "$GROUP_ID" "$DOCKER_USER" \
  && adduser --disabled-password --gecos '' --uid "$USER_ID" --gid "$GROUP_ID" "$DOCKER_USER" \
  && passwd -d "$DOCKER_USER" \
  && chown -R "$DOCKER_USER:$DOCKER_USER" /azerothcore

COPY --chown=$DOCKER_USER:$DOCKER_USER apps apps

VOLUME /azerothcore/env/dist/data

USER $DOCKER_USER

CMD ["bash",  "-c", "source /azerothcore/apps/installer/includes/functions.sh && inst_download_client_data" ]

############################
# ccache backup / restore  #
############################

# Driven by apps/docker/ccache-io.sh. Living in the same Dockerfile as the
# build stage guarantees these stages resolve exactly the /ccache mount the
# compiler uses. CCACHE_IO_BUST forces just these RUNs to re-execute (the
# script passes a timestamp) without a --no-cache that would also rebuild the
# skeleton stage. These stages sit above the tools stage so that `tools`
# remains the Dockerfile's last - and therefore default - target.

FROM skeleton AS ccache-export
ARG CCACHE_IO_BUST=0
RUN --mount=type=cache,target=/ccache,sharing=locked \
    : "$CCACHE_IO_BUST" && tar -C /ccache -cf /ccache-backup.tar .

FROM scratch AS ccache-backup
COPY --from=ccache-export /ccache-backup.tar /

# Placeholder for the `seed` named context the restore path supplies
# (`--build-context seed=DIR` shadows this stage); it exists so the Dockerfile
# resolves - and lints - without one.
FROM scratch AS seed

FROM skeleton AS ccache-import
ARG CCACHE_IO_BUST=0
RUN --mount=type=cache,target=/ccache,sharing=locked \
    --mount=type=bind,from=seed,source=/,target=/seed \
    : "$CCACHE_IO_BUST" && tar -C /ccache -xf /seed/ccache-backup.tar

##################
# Map Extractors #
##################

# Copies the extractor binaries out of the build stage, so this target only
# works when the build ran with CTOOLS_BUILD=all. The Dockerfile default is
# db-only; docker-compose.yml's ac-tools service overrides it to all.
FROM runtime AS tools

LABEL description="AzerothCore Tools"

WORKDIR /azerothcore/env/dist/

RUN mkdir -pv /azerothcore/env/dist/Cameras \
              /azerothcore/env/dist/dbc     \
              /azerothcore/env/dist/maps    \
              /azerothcore/env/dist/mmaps   \
              /azerothcore/env/dist/vmaps

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/map_extractor /azerothcore/env/dist/bin/map_extractor

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/mmaps_generator /azerothcore/env/dist/bin/mmaps_generator

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/vmap4_assembler /azerothcore/env/dist/bin/vmap4_assembler

COPY --chown=$DOCKER_USER:$DOCKER_USER --from=build \
  /azerothcore/env/dist/bin/vmap4_extractor /azerothcore/env/dist/bin/vmap4_extractor
