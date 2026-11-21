#!/bin/bash
# Bash-only alternative to `python3 scripts/new_tenant.py auth --profile <profile>` (any OS, verifies
# the token, and signs pscli in too - prefer it).
# Store a PerfectScale public API token (client ID + secret) for the collector's v2 step, which
# pulls HPA triggers, automation status and recommendation maturity that pscli cannot reach.
#
#   bash scripts/set_api_credentials.sh <profile>      # same name as the config's "profile"
#
# Writes ~/.perfectscale/<profile>.env readable by you only. The secret is not echoed and nothing
# reaches shell history. Remove it with: rm ~/.perfectscale/<profile>.env
# Get a token: app.perfectscale.io > avatar > Organization Settings > API Tokens > Generate (Read Only).
set -euo pipefail
profile="${1:-default}"
case "$profile" in *[!A-Za-z0-9._-]*|"") echo "profile name may only contain letters, digits, . _ -"; exit 1;; esac
umask 077
mkdir -p "$HOME/.perfectscale"
read -r -p "PerfectScale client ID: " PS_ID
read -rs -p "PerfectScale client secret (hidden): " PS_SECRET
echo
if [ -z "$PS_ID" ] || [ -z "$PS_SECRET" ]; then
  echo "Both values are needed - nothing saved."
  exit 1
fi
dest="$HOME/.perfectscale/$profile.env"
printf 'PS_ID=%s\nPS_SECRET=%s\n' "$PS_ID" "$PS_SECRET" > "$dest"
chmod 600 "$dest"
unset PS_ID PS_SECRET
echo "Saved to $dest (owner-only). The collector picks it up for profile '$profile'."
