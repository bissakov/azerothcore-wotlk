ARG UBUNTU_VERSION=24.04 # lts

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

# This target builds the docker image
# This target can be useful to inspect the explicit outputs from the build,
FROM skeleton AS build

ARG UBUNTU_VERSION
ARG TARGETARCH
ARG CTOOLS_BUILD="all"
ARG CTYPE="RelWithDebInfo"
ARG CCACHE_CPP2="true"
ARG CSCRIPTPCH="OFF"
ARG CSCRIPTS="static"
ARG CMODULES="static"
ARG CSCRIPTS_DEFAULT_LINKAGE="static"
ARG CWITH_WARNINGS="ON"
ARG CMAKE_EXTRA_OPTIONS=""
ARG GIT_DISCOVERY_ACROSS_FILESYSTEM=1

ARG CCACHE_DIR="/ccache"
# A full build of the core plus the bundled modules stores ~0.5 GB of objects
# (1800 translation units, RelWithDebInfo). The old 1 GB ceiling therefore held
# barely two builds' worth, so any two branches in rotation evicted each other.
# 10 GB holds roughly twenty and still costs nothing when unused.
ARG CCACHE_MAXSIZE="10G"
# COPY gives every file a fresh mtime/ctime on each build, so ccache has to be
# told to ignore both and compare contents instead. pch_defines is required for
# ccache to handle the precompiled headers at all.
ARG CCACHE_SLOPPINESS="pch_defines,time_macros,include_file_mtime,include_file_ctime"
ARG CCACHE_COMPRESS=""
# High zstd levels spend substantial CPU for marginal savings, especially while
# a cold build is trying to populate the cache. Level 3 matches the CI builds.
ARG CCACHE_COMPRESSLEVEL="3"
ARG CCACHE_COMPILERCHECK="content"
ARG CCACHE_LOGFILE=""

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

# The Boost list is spelled out because deps/boost asks for exactly five
# components (filesystem, program_options, iostreams, regex, thread) and the
# only other linked usage in the tree is stacktrace. libboost-all-dev drags in
# MPI, Python and the graph libraries on top of that, which is a few hundred
# megabytes downloaded and unpacked for nothing.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked         \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked     \
    apt-get update                                                     \
    && apt-get install -y --no-install-recommends                         \
        build-essential ccache ninja-build cmake clang lld                   \
        default-libmysqlclient-dev libssl-dev                                  \
        libreadline-dev zlib1g-dev libbz2-dev liblzma-dev                   \
        libboost-dev libboost-filesystem-dev libboost-iostreams-dev            \
        libboost-program-options-dev libboost-regex-dev                         \
        libboost-stacktrace-dev libboost-thread-dev

# Ordered least to most volatile: a source edit should not invalidate the layers
# holding the vendored dependencies.
COPY deps /azerothcore/deps
COPY CMakeLists.txt /azerothcore/CMakeLists.txt
COPY conf /azerothcore/conf
COPY modules /azerothcore/modules
COPY src /azerothcore/src

ARG CACHEBUST=1

WORKDIR /azerothcore/build

# The CMake build tree is kept in a cache mount alongside ccache. Any change
# under src/ or modules/ invalidates the COPY layers above and forces this step
# to re-run, and previously it re-ran against an empty directory: a full CMake
# configure, a full Ninja graph and a full link every time, with ccache as the
# only thing standing between that and an hour of compiling. With the tree
# preserved, a re-run is a genuine incremental build and only touches what
# actually changed. The mount is keyed on the settings baked into CMakeCache.txt
# so that builds with different options don't fight over one directory.
#
# Nothing here reaches the image: `cmake --install` copies the artifacts out to
# /azerothcore/env/dist, which is a real layer. If the tree ever does go stale,
# `docker builder prune --filter type=exec.cachemount` clears it.
RUN --mount=type=cache,target=/ccache,sharing=locked \
    --mount=type=cache,target=/azerothcore/build,id=acore-build-$UBUNTU_VERSION-$TARGETARCH-$CTYPE-$CSCRIPTS-$CMODULES-$CTOOLS_BUILD-$CWITH_WARNINGS,sharing=locked \
    cmake /azerothcore \
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
       -DWITH_WARNINGS="$CWITH_WARNINGS"               \
       -DCMAKE_BUILD_TYPE="$CTYPE"                     \
       -DCMAKE_CXX_COMPILER="clang++"                  \
       -DCMAKE_C_COMPILER="clang"                      \
       -DCMAKE_CXX_COMPILER_LAUNCHER="ccache"          \
       -DCMAKE_C_COMPILER_LAUNCHER="ccache"            \
       -DBoost_USE_STATIC_LIBS="ON"                    \
    && cmake --build . --config "$CTYPE" -j $(($(nproc) + 1)) \
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

##################
# Map Extractors #
##################

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
