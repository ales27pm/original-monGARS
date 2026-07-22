#!/usr/bin/env bash

set -Eeuo pipefail

readonly caddy_image="mongars-caddy:check"

check_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "$check_directory/../.." && pwd)"
data_volume="mongars-caddy-check-data-$$"
config_volume="mongars-caddy-check-config-$$"

cleanup() {
  exit_status=$?
  trap - EXIT INT TERM
  docker volume rm --force "$data_volume" "$config_volume" >/dev/null 2>&1 || true
  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker info >/dev/null
docker build \
  --file "$check_directory/Dockerfile" \
  --tag "$caddy_image" \
  "$repository_root"

image_user="$(docker image inspect --format '{{.Config.User}}' "$caddy_image")"
if [[ "$image_user" != "65534:65534" ]]; then
  echo "unexpected Caddy image user: $image_user" >&2
  exit 1
fi

if [[ -n "$(docker run --rm --user 0:0 --entrypoint getcap "$caddy_image" /usr/bin/caddy)" ]]; then
  echo "Caddy binary still has a file capability" >&2
  exit 1
fi

# Reproduce an existing root-owned local-CA tree, then run the same networkless,
# narrowly-capable migration used by Compose twice. The second pass proves that
# mode-0700 paths already owned by the runtime identity remain traversable. The
# key bytes must survive while every nested path remains owned by that identity.
docker volume create "$data_volume" >/dev/null
docker volume create "$config_volume" >/dev/null
docker run --rm \
  --user 0:0 \
  --network none \
  --volume "$data_volume:/data" \
  --volume "$config_volume:/config" \
  --entrypoint /bin/sh \
  "$caddy_image" \
  -ec '
    mkdir -p /data/caddy/pki/authorities/local /config/caddy
    printf preserved-ca-key > /data/caddy/pki/authorities/local/root.key
    printf preserved-config > /config/caddy/autosave.json
    chmod 0700 /data/caddy/pki /data/caddy/pki/authorities /data/caddy/pki/authorities/local
    chmod 0600 /data/caddy/pki/authorities/local/root.key /config/caddy/autosave.json
  '

for migration_pass in 1 2; do
  docker run --rm \
    --user 0:0 \
    --network none \
    --read-only \
    --cap-drop ALL \
    --cap-add CHOWN \
    --cap-add DAC_READ_SEARCH \
    --security-opt no-new-privileges:true \
    --pids-limit 32 \
    --volume "$data_volume:/data" \
    --volume "$config_volume:/config" \
    --entrypoint /bin/sh \
    "$caddy_image" \
    -ec 'chown -R 65534:65534 /data/caddy /config/caddy'

  docker run --rm \
    --user 65534:65534 \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --volume "$data_volume:/data:ro" \
    --volume "$config_volume:/config:ro" \
    --entrypoint /bin/sh \
    "$caddy_image" \
    -ec '
      test "$(cat /data/caddy/pki/authorities/local/root.key)" = preserved-ca-key
      test "$(cat /config/caddy/autosave.json)" = preserved-config
      unexpected="$(
        find /data/caddy /config/caddy -exec stat -c "%u:%g" {} \; \
          | grep -v "^65534:65534$" || true
      )"
      test -z "$unexpected"
    '
  echo "Caddy volume migration pass $migration_pass succeeded"
done

docker run --rm \
  --user 65534:65534 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --env MONGARS_HTTPS_HOST=localhost \
  --env MONGARS_MAX_REQUEST_BYTES=2100000 \
  --env MONGARS_MAX_DOCUMENT_REQUEST_BYTES=10500000 \
  --mount "type=bind,src=$check_directory/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
  --tmpfs /data:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534,mode=0700 \
  --tmpfs /config:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534,mode=0700 \
  "$caddy_image" \
  caddy validate --config /etc/caddy/Caddyfile

echo "Caddy configuration and non-root runtime check passed"
