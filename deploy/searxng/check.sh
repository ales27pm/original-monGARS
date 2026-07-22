#!/usr/bin/env bash

set -Eeuo pipefail

readonly searxng_image="docker.io/searxng/searxng@sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812"
readonly squid_image="ubuntu/squid@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c"
readonly validation_secret="mongars-searxng-validation-only-secret"

check_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
egress_directory="$(cd -- "$check_directory/../egress-proxy" && pwd)"
search_network="mongars-searx-check-search-$$"
proxy_network="mongars-searx-check-proxy-$$"
egress_network="mongars-searx-check-egress-$$"
proxy_container_id=""
searxng_container_id=""

cleanup() {
  exit_status=$?
  trap - EXIT INT TERM

  if ((exit_status != 0)); then
    if [[ -n "$searxng_container_id" ]]; then
      docker logs "$searxng_container_id" >&2 || true
    fi
    if [[ -n "$proxy_container_id" ]]; then
      docker logs "$proxy_container_id" >&2 || true
    fi
  fi
  if [[ -n "$searxng_container_id" ]]; then
    docker rm --force "$searxng_container_id" >/dev/null 2>&1 || true
  fi
  if [[ -n "$proxy_container_id" ]]; then
    docker rm --force "$proxy_container_id" >/dev/null 2>&1 || true
  fi
  docker network rm "$search_network" "$proxy_network" "$egress_network" \
    >/dev/null 2>&1 || true
  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker info >/dev/null
docker pull "$searxng_image"
docker pull "$squid_image"
docker network create --internal "$search_network" >/dev/null
docker network create --internal "$proxy_network" >/dev/null
docker network create "$egress_network" >/dev/null

proxy_container_id="$(
  docker run --detach --rm \
    --name "mongars-searx-proxy-check-$$" \
    --network "$proxy_network" \
    --network-alias search-egress-proxy \
    --user 13:13 \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --mount "type=bind,src=$egress_directory/squid.conf,dst=/etc/squid/squid.conf,readonly" \
    --tmpfs /run/squid:rw,noexec,nosuid,nodev,size=4m,uid=13,gid=13,mode=0700 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m,uid=13,gid=13,mode=0700 \
    --entrypoint /usr/sbin/squid \
    "$squid_image" \
    -f /etc/squid/squid.conf -NYC
)"
docker network connect "$egress_network" "$proxy_container_id"

searxng_container_id="$(
  docker run --detach --rm \
    --name "mongars-searx-check-$$" \
    --network "$proxy_network" \
    --user 977:977 \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --env FORCE_OWNERSHIP=false \
    --env "SEARXNG_SECRET=$validation_secret" \
    --env HTTP_PROXY=http://search-egress-proxy:3128 \
    --env HTTPS_PROXY=http://search-egress-proxy:3128 \
    --env ALL_PROXY=http://search-egress-proxy:3128 \
    --env NO_PROXY=localhost,127.0.0.1,::1 \
    --env http_proxy=http://search-egress-proxy:3128 \
    --env https_proxy=http://search-egress-proxy:3128 \
    --env all_proxy=http://search-egress-proxy:3128 \
    --env no_proxy=localhost,127.0.0.1,::1 \
    --mount "type=bind,src=$check_directory/settings.yml,dst=/etc/searxng/settings.yml,readonly" \
    --mount "type=bind,src=$check_directory/proxy_probe.py,dst=/etc/searxng/proxy_probe.py,readonly" \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
    --tmpfs /var/cache/searxng:rw,noexec,nosuid,nodev,size=64m,uid=977,gid=977,mode=0700 \
    "$searxng_image"
)"
docker network connect "$search_network" "$searxng_container_id"

for container_id in "$proxy_container_id" "$searxng_container_id"; do
  if [[ ! "$container_id" =~ ^[0-9a-f]{12,64}$ ]]; then
    echo "docker run returned an invalid container ID" >&2
    exit 1
  fi
done

network_names="$(docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$searxng_container_id")"
if [[ "$network_names" == *"$egress_network"* ]]; then
  echo "SearXNG unexpectedly joined the external egress network" >&2
  exit 1
fi
if [[ "$network_names" != *"$search_network"* || "$network_names" != *"$proxy_network"* ]]; then
  echo "SearXNG is missing a required internal network: $network_names" >&2
  exit 1
fi

if ! docker exec "$searxng_container_id" getent hosts search-egress-proxy >/dev/null; then
  echo "SearXNG cannot resolve its fixed egress proxy" >&2
  exit 1
fi

ready_status=""
for _attempt in {1..30}; do
  ready_status="$(
    docker exec "$searxng_container_id" sh -c \
      'wget -q -O /dev/null http://127.0.0.1:8080/config && printf 200' || true
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

docker exec "$searxng_container_id" \
  wget -q -O /dev/null http://127.0.0.1:8080/config

# Exercise SearXNG's exact network client and committed proxy configuration
# from inside the no-egress container before testing the aggregate endpoint.
docker exec "$searxng_container_id" \
  env PYTHONPATH=/usr/local/searxng \
  /usr/local/searxng/.venv/bin/python /etc/searxng/proxy_probe.py

json_body="$(
  docker exec "$searxng_container_id" wget -q -O - \
    'http://127.0.0.1:8080/search?q=monGARS%20configuration%20check&format=json'
)"

python3 -c \
  'import json, sys; payload = json.load(sys.stdin); assert isinstance(payload.get("results"), list)' \
  <<<"$json_body"

html_status="$(
  docker exec "$searxng_container_id" sh -c \
    "wget -S -O /dev/null 'http://127.0.0.1:8080/search?q=monGARS%20configuration%20check&format=html' 2>&1 || true" \
    | awk '/HTTP\// {status=$2} END {print status}'
)"
if [[ "$html_status" != "403" ]]; then
  echo "SearXNG HTML search format was not rejected (HTTP $html_status)" >&2
  exit 1
fi

# Squid writes access records from its worker process and may flush them just
# after SearXNG has returned the aggregate response.
sleep 2
if ! docker logs "$proxy_container_id" 2>&1 | grep -Eq 'TCP_(TUNNEL|MISS)/200'; then
  echo "SearXNG network client did not traverse the egress proxy" >&2
  exit 1
fi

echo "SearXNG proxied configuration check passed"
