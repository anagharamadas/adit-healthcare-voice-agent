#!/usr/bin/env bash
#
# Builds outbound-trunk.json from .env at runtime and creates the LiveKit
# SIP outbound trunk via the LiveKit CLI.
#
# The generated outbound-trunk.json contains real SIP credentials and is
# gitignored. Only outbound-trunk.json.example is committed.
#
# Usage:
#   ./create_trunk.sh              build config, then create the trunk
#   ./create_trunk.sh --dry-run    build config and show it redacted, create nothing
#   ./create_trunk.sh --force      skip the duplicate-trunk guard
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force)   FORCE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

CONFIG="outbound-trunk.json"
TRUNK_NAME="twilio-outbound-in"

# --- load .env -------------------------------------------------------------
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in $(pwd)" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a

# --- validate --------------------------------------------------------------
missing=()
for var in TWILIO_SIP_TERMINATION_DOMAIN TWILIO_SIP_AUTH_USERNAME \
           TWILIO_SIP_AUTH_PASSWORD TWILIO_PHONE_NUMBER; do
  [[ -n "${!var:-}" ]] || missing+=("$var")
done
if (( ${#missing[@]} )); then
  echo "ERROR: missing or empty in .env: ${missing[*]}" >&2
  exit 1
fi

# address must be a bare hostname. LiveKit's outbound trunk `address` is not a
# SIP URI, so strip any sip:/sips: scheme and trailing slash if present.
ADDRESS="$TWILIO_SIP_TERMINATION_DOMAIN"
ADDRESS="${ADDRESS#sip:}"
ADDRESS="${ADDRESS#sips:}"
ADDRESS="${ADDRESS%/}"
if [[ "$ADDRESS" == *"/"* ]]; then
  echo "ERROR: TWILIO_SIP_TERMINATION_DOMAIN must be a hostname, not a URI" >&2
  exit 1
fi

if [[ "$TWILIO_PHONE_NUMBER" != +* ]]; then
  echo "ERROR: TWILIO_PHONE_NUMBER must be full E.164 with a leading +" >&2
  exit 1
fi

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 1; }

# --- build config ----------------------------------------------------------
# Field names and shape verified against livekit_sip.proto:
#   CreateSIPOutboundTrunkRequest { SIPOutboundTrunkInfo trunk = 1; }
# The CLI parses this file with protojson, which accepts the proto field
# names (snake_case) used here.
umask 077
jq -n \
  --arg name    "$TRUNK_NAME" \
  --arg address "$ADDRESS" \
  --arg country "IN" \
  --arg number  "$TWILIO_PHONE_NUMBER" \
  --arg user    "$TWILIO_SIP_AUTH_USERNAME" \
  --arg pass    "$TWILIO_SIP_AUTH_PASSWORD" \
  '{trunk: {
      name:                $name,
      address:             $address,
      destination_country: $country,
      numbers:             [$number],
      transport:           "SIP_TRANSPORT_AUTO",
      auth_username:       $user,
      auth_password:       $pass
    }}' > "$CONFIG"
chmod 600 "$CONFIG"

echo "Wrote $CONFIG (mode 600, gitignored). Redacted preview:"
jq '.trunk.auth_username = "<redacted>" | .trunk.auth_password = "<redacted>"' "$CONFIG"
echo

if (( DRY_RUN )); then
  echo "--dry-run: no trunk created."
  exit 0
fi

# --- preflight -------------------------------------------------------------
if ! command -v lk >/dev/null 2>&1; then
  cat >&2 <<'EOM'
ERROR: the LiveKit CLI (lk) is not installed.

  brew install livekit-cli

Then authenticate with either:
  lk cloud auth                    # links the CLI to your LiveKit Cloud project
or rely on LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from .env,
which this script already exports into the environment.
EOM
  exit 1
fi

# --- duplicate guard -------------------------------------------------------
if (( ! FORCE )); then
  existing="$(lk sip outbound list --json 2>/dev/null || true)"
  if [[ -n "$existing" ]] && grep -Fq "$ADDRESS" <<<"$existing"; then
    echo "WARNING: an outbound trunk already references this address." >&2
    echo "Creating another would duplicate it. Re-run with --force to proceed anyway." >&2
    echo "Existing trunks:" >&2
    lk sip outbound list >&2 || true
    exit 1
  fi
fi

# --- create ----------------------------------------------------------------
echo "Creating outbound trunk..."
lk sip outbound create "$CONFIG"
echo
echo "Listing outbound trunks for read-back:"
lk sip outbound list
