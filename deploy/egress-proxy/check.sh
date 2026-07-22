#!/usr/bin/env bash

set -Eeuo pipefail

readonly squid_image="ubuntu/squid@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c"

check_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
network_name="mongars-egress-check-$$"
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
  docker network rm "$network_name" >/dev/null 2>&1 || true
  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker info >/dev/null
docker pull "$squid_image"
docker network create "$network_name" >/dev/null

container_id="$(
  docker run --detach --rm \
    --name "mongars-egress-check-$$" \
    --network "$network_name" \
    --network-alias search-egress-proxy \
    --user 13:13 \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --add-host private-rebind.test:10.0.0.1 \
    --add-host metadata-rebind.test:169.254.169.254 \
    --add-host 'private-v6-ula.test=[fd00::1]' \
    --add-host 'private-v6-linklocal.test=[fe80::1]' \
    --add-host 'private-v6-mapped.test=[::ffff:10.0.0.1]' \
    --add-host 'private-v6-nat64.test=[64:ff9b:1::a00:1]' \
    --mount "type=bind,src=$check_directory/squid.conf,dst=/etc/squid/squid.conf,readonly" \
    --tmpfs /run/squid:rw,noexec,nosuid,nodev,size=4m,uid=13,gid=13,mode=0700 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m,uid=13,gid=13,mode=0700 \
    --publish 127.0.0.1::3128 \
    --entrypoint /usr/sbin/squid \
    "$squid_image" \
    -f /etc/squid/squid.conf -NYC
)"

if [[ ! "$container_id" =~ ^[0-9a-f]{12,64}$ ]]; then
  echo "docker run returned an invalid container ID" >&2
  container_id=""
  exit 1
fi

port_mapping="$(docker port "$container_id" 3128/tcp)"
if [[ ! "$port_mapping" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
  echo "unexpected proxy port mapping: $port_mapping" >&2
  exit 1
fi
proxy_url="http://$port_mapping"

proxy_status=""
for _attempt in {1..30}; do
  proxy_status="$(
    curl --noproxy '' --proxy "$proxy_url" --silent --output /dev/null \
      --write-out '%{http_code}' http://127.0.0.1/ || true
  )"
  if [[ "$proxy_status" == "403" ]]; then
    break
  fi
  sleep 1
done
if [[ "$proxy_status" != "403" ]]; then
  echo "egress proxy did not become ready with a deny response (HTTP $proxy_status)" >&2
  exit 1
fi

for blocked_url in \
  http://private-rebind.test/ \
  http://metadata-rebind.test/latest/meta-data/ \
  http://private-v6-ula.test/ \
  http://private-v6-linklocal.test/ \
  http://private-v6-mapped.test/ \
  http://private-v6-nat64.test/ \
  http://127.0.0.1/; do
  blocked_status="$(
    curl --noproxy '' --proxy "$proxy_url" --silent --output /dev/null \
      --write-out '%{http_code}' "$blocked_url" || true
  )"
  if [[ "$blocked_status" != "403" ]]; then
    echo "blocked destination was not rejected: $blocked_url (HTTP $blocked_status)" >&2
    exit 1
  fi
done

# For HTTPS, Squid must resolve and reject private destinations before opening
# the CONNECT tunnel. `%{http_connect}` exposes the proxy's response even
# though the end-to-end HTTPS response code remains 000.
for blocked_host in \
  private-rebind.test \
  metadata-rebind.test \
  private-v6-ula.test \
  private-v6-linklocal.test \
  private-v6-mapped.test \
  private-v6-nat64.test; do
  connect_status="$(
    curl --noproxy '' --proxy "$proxy_url" --silent --output /dev/null \
      --connect-timeout 5 --max-time 10 --write-out '%{http_connect}' \
      "https://$blocked_host/" || true
  )"
  if [[ "$connect_status" != "403" ]]; then
    echo "blocked HTTPS destination opened a tunnel: $blocked_host (CONNECT $connect_status)" >&2
    exit 1
  fi
done

public_result="$(
  curl --noproxy '' --proxy "$proxy_url" --silent --output /dev/null \
    --connect-timeout 5 --max-time 20 --write-out '%{http_connect}:%{http_code}' \
    https://example.com/ || true
)"
if [[ "$public_result" != "200:200" ]]; then
  echo "public HTTPS control failed through the egress proxy (CONNECT:HTTP $public_result)" >&2
  exit 1
fi

echo "Search egress proxy ACL check passed"
