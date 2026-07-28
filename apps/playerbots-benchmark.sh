#!/usr/bin/env bash

set -euo pipefail

duration="${1:-120}"
label="${2:-manual}"
interval="${BENCH_INTERVAL:-2}"
container="${BENCH_CONTAINER:-ac-worldserver}"
run_id="$(date +%Y%m%d-%H%M%S)-${label}"
result_dir="var/benchmarks/${run_id}"

if ! [[ "${duration}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Duration must be a positive number of seconds." >&2
  exit 2
fi

mkdir -p "${result_dir}"

container_id="$(docker inspect --format '{{.Id}}' "${container}")"
container_pid="$(docker inspect --format '{{.State.Pid}}' "${container}")"
started_at="$(date --iso-8601=seconds)"

{
  echo "run_id=${run_id}"
  echo "label=${label}"
  echo "started_at=${started_at}"
  echo "duration_seconds=${duration}"
  echo "sample_interval_seconds=${interval}"
  echo "container=${container}"
  echo "container_id=${container_id}"
  echo "container_pid=${container_pid}"
  echo "core_commit=$(git rev-parse HEAD)"
  echo "module_commit=$(git -C modules/mod-playerbots rev-parse HEAD)"
  echo "kernel=$(uname -sr)"
  echo "cpu=$(lscpu | awk -F: '/Model name/ {sub(/^[ \t]+/, "", $2); print $2; exit}')"
  echo "logical_cpus=$(nproc)"
  echo "memory_kib=$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
} > "${result_dir}/metadata.env"

docker inspect "${container}" > "${result_dir}/container-inspect.json"
docker compose config > "${result_dir}/compose-config.yml"
printf 'timestamp,cpu_percent,memory_bytes,pids,host_load1,host_mem_available_kib\n' \
  > "${result_dir}/samples.csv"

end_epoch="$(( $(date +%s) + duration ))"
while (( $(date +%s) < end_epoch )); do
  timestamp="$(date --iso-8601=seconds)"
  stats="$(docker stats --no-stream --format '{{.CPUPerc}},{{.MemUsage}},{{.PIDs}}' "${container}")"
  cpu_percent="${stats%%,*}"
  rest="${stats#*,}"
  memory_human="${rest%%,*}"
  pids="${rest##*,}"
  memory_bytes="$(awk '/VmRSS/ {print $2 * 1024}' "/proc/${container_pid}/status")"
  load1="$(awk '{print $1}' /proc/loadavg)"
  mem_available="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
  printf '%s,%s,%s,%s,%s,%s\n' \
    "${timestamp}" "${cpu_percent%\%}" "${memory_bytes}" "${pids}" "${load1}" "${mem_available}" \
    >> "${result_dir}/samples.csv"
  printf '\r%s CPU=%s memory=%s PIDs=%s' "${timestamp}" "${cpu_percent}" "${memory_human}" "${pids}"
  sleep "${interval}"
done
printf '\n'

docker logs --since "${started_at}" "${container}" > "${result_dir}/worldserver.log" 2>&1

awk -F, '
  NR == 1 { next }
  {
    count++
    cpu += $2
    mem += $3
    if (count == 1 || $2 < cpu_min) cpu_min = $2
    if ($2 > cpu_max) cpu_max = $2
    if (count == 1 || $3 < mem_min) mem_min = $3
    if ($3 > mem_max) mem_max = $3
  }
  END {
    if (!count) exit 1
    printf "samples=%d\n", count
    printf "cpu_mean_percent=%.2f\n", cpu / count
    printf "cpu_min_percent=%.2f\n", cpu_min
    printf "cpu_max_percent=%.2f\n", cpu_max
    printf "memory_mean_bytes=%.0f\n", mem / count
    printf "memory_min_bytes=%.0f\n", mem_min
    printf "memory_max_bytes=%.0f\n", mem_max
  }
' "${result_dir}/samples.csv" > "${result_dir}/summary.env"

grep -E 'Update time diff:|Mean:|Median:|Percentiles' "${result_dir}/worldserver.log" \
  > "${result_dir}/update-times.log" || true
grep -Ei 'assert|crash|deadlock|fatal|segmentation|queue.*(full|drop)|error' \
  "${result_dir}/worldserver.log" > "${result_dir}/errors.log" || true

echo "Benchmark saved to ${result_dir}"
cat "${result_dir}/summary.env"
