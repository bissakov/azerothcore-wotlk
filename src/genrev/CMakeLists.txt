#
# This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
#
# This file is free software; as a special exception the author gives
# unlimited permission to copy and/or distribute it, with or without
# modifications, as long as this notice is preserved.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY, to the extent permitted by law; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#

# Need to pass old ${CMAKE_BINARY_DIR} as param because its different at build stage
#
# This re-runs genrev.cmake in script mode, which sees none of the cache, so any
# revision supplied at configure time has to be handed over explicitly. Without
# this the build step would re-detect from git and overwrite the injected value
# with "unknown" on trees that have no repository to read.
add_custom_target(revision.h ALL
  COMMAND "${CMAKE_COMMAND}"
    -DBUILDDIR="${CMAKE_BINARY_DIR}"
    -DREV_HASH="${REV_HASH}"
    -DREV_BRANCH="${REV_BRANCH}"
    -DREV_DATE="${REV_DATE}"
    -DWITHOUT_GIT="${WITHOUT_GIT}"
    -P "${CMAKE_SOURCE_DIR}/src/cmake/genrev.cmake"
    "${CMAKE_BINARY_DIR}"
  WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")

set_target_properties(revision.h
  PROPERTIES
    FOLDER
      "server")
