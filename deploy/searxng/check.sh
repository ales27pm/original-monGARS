#!/usr/bin/env bash

set -Eeuo pipefail

readonly searxng_image="docker.io/searxng/searxng@sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812"
readonly validation_secret="mongars-searxng-validation-only-secret"

check_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
container_id=""

cleanup() {
  exit_status=$?
  trap - EXIT INT TERM

  if [[ -n "$container_id" ]]; then
    if ((exit_status != 0)); then
      docker logs "$container_id" >&2 || true
    fi
    docker rm --force "$container_id" >/dev/null 2>&1 || true
  fi

  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker info >/dev/null
docker pull "$searxng_image"

container_id="$(
  docker run --detach --rm \
    --label com.mongars.searxng-check=true \
    --user 977:977 \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --env FORCE_OWNERSHIP=false \
    --env "SEARXNG_SECRET=$validation_secret" \
    --mount "type=bind,src=$check_directory/settings.yml,dst=/etc/searxng/settings.yml,readonly" \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
    --tmpfs /var/cache/searxng:rw,noexec,nosuid,nodev,size=64m,uid=977,gid=977,mode=0700 \
    --publish 127.0.0.1::8080 \
    "$searxng_image"
)"

if [[ ! "$container_id" =~ ^[0-9a-f]{12,64}$ ]]; then
  echo "docker run returned an invalid container ID" >&2
  container_id=""
  exit 1
fi

port_mapping="$(docker port "$container_id" 8080/tcp)"
if [[ ! "$port_mapping" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
  echo "unexpected SearXNG port mapping: $port_mapping" >&2
  exit 1
fi
searxng_url="http://$port_mapping"

ready_status=""
for _attempt in {1..30}; do
  ready_status="$(
    curl --noproxy '*' --silent --output /dev/null \
      --write-out '%{http_code}' \
      "$searxng_url/config" || true
  )"
  if [[ "$ready_status" == "200" ]]; then
    break
  fi
  sleep 1
done

if [[ "$ready_status" != "200" ]]; then
  echo "SearXNG did not become ready (HTTP $ready_status)" >&2
  exit 1
fi

docker exec "$container_id" \
  wget -q -O /dev/null http://127.0.0.1:8080/config

json_body="$(
  curl --noproxy '*' --fail --silent --show-error \
    --get \
    --data-urlencode 'q=monGARS configuration check' \
    --data-urlencode 'format=json' \
    "$searxng_url/search"
)"

python3 -c \
  'import json, sys; payload = json.load(sys.stdin); assert isinstance(payload.get("results"), list)' \
  <<<"$json_body"

html_status="$(
  curl --noproxy '*' --silent --output /dev/null \
    --get \
    --data-urlencode 'q=monGARS configuration check' \
    --data-urlencode 'format=html' \
    --write-out '%{http_code}' \
    "$searxng_url/search"
)"
if [[ "$html_status" != "403" ]]; then
  echo "SearXNG HTML search format was not rejected (HTTP $html_status)" >&2
  exit 1
fi

echo "SearXNG configuration check passed"
