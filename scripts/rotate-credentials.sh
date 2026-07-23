#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: rotate-credentials.sh [OPTIONS]

Rotate monGARS credential secrets safely and atomically.

Options:
  --api-token-file PATH       Path to the monGARS API token secret file
  --approval-hmac-key-file PATH
                             Path to the approval HMAC key secret file
  --api-only                  Rotate only the API token
  --approval-only             Rotate only the approval HMAC key
  --help                      Show this help text
EOF
}

die() {
  echo "mongars-rotate-credentials: $1" >&2
  exit 1
}

assert_writable_secret_file() {
  local file=$1
  local parent_dir

  parent_dir="$(dirname -- "$file")"
  if [[ ! -f "$file" ]]; then
    if [[ ! -d "$parent_dir" ]]; then
      die "parent directory does not exist: $parent_dir"
    fi
    if [[ ! -w "$parent_dir" ]]; then
      die "parent directory is not writable: $parent_dir"
    fi
    return
  fi
  if [[ ! -w "$file" ]]; then
    die "secret file is not writable: $file"
  fi
}

require_command() {
  local name=$1
  if ! command -v "$name" >/dev/null 2>&1; then
    die "required command is missing: $name"
  fi
}

tmp_files_to_remove=()

cleanup_tmp_files() {
  local tmp_file
  for tmp_file in "${tmp_files_to_remove[@]:-}"; do
    rm -f -- "$tmp_file" || true
  done
}

write_secret_atomically() {
  local value=$1
  local file=$2
  local label=$3
  local tmp_file

  tmp_file="$(mktemp "${file}.tmp.XXXXXX")"
  tmp_files_to_remove+=("$tmp_file")
  printf '%s\n' "$value" > "$tmp_file"
  chmod 0640 "$tmp_file"
  chown "$(id -u):$(id -g)" "$tmp_file" || true
  mv -f "$tmp_file" "$file"
  tmp_files_to_remove=()
  echo "${label} rotated into: $file"
}

random_secret() {
  local size=${1:-32}
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$size"
    return
  fi
  python3 - "$size" <<'PY'
import secrets
import sys

size = int(sys.argv[1])
print(secrets.token_hex(size))
PY
}

api_token_file="${MONGARS_API_TOKEN_FILE:-./secrets/api_token.txt}"
approval_hmac_key_file="${MONGARS_APPROVAL_HMAC_KEY_FILE:-./secrets/approval_hmac_key.txt}"
rotate_api_token=true
rotate_approval_key=true

while (($# > 0)); do
  case "$1" in
    --api-token-file)
      if (($# < 2)); then
        die "--api-token-file requires a value"
      fi
      api_token_file=$2
      shift 2
      ;;
    --approval-hmac-key-file)
      if (($# < 2)); then
        die "--approval-hmac-key-file requires a value"
      fi
      approval_hmac_key_file=$2
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    --api-only)
      rotate_api_token=true
      rotate_approval_key=false
      shift
      ;;
    --approval-only)
      rotate_api_token=false
      rotate_approval_key=true
      shift
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_command python3
trap cleanup_tmp_files EXIT
if [[ "$rotate_api_token" == true ]]; then
  assert_writable_secret_file "$api_token_file"
fi
if [[ "$rotate_approval_key" == true ]]; then
  assert_writable_secret_file "$approval_hmac_key_file"
fi

if [[ "$rotate_api_token" == true ]]; then
  new_api_token="$(random_secret 32)"
  write_secret_atomically "$new_api_token" "$api_token_file" "API token"
fi

if [[ "$rotate_approval_key" == true ]]; then
  new_approval_hmac_key="$(random_secret 64)"
  write_secret_atomically "$new_approval_hmac_key" "$approval_hmac_key_file" "Approval HMAC key"
fi

echo "Rotation complete. Restart services that read these files (api and worker) to apply new secrets."
