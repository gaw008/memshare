#!/usr/bin/env bash
# First install only: an existing service account or application is an error.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/common.sh"
load_deploy_config "${1:?Usage: bash deploy/install.sh /path/to/memhub.prod.toml}"
umask 077
sudo -n true

if getent passwd "$service_user" >/dev/null; then
  echo "System user already exists: $service_user" >&2
  exit 1
fi
for path in "$app_dir" "$memory_dir" "$state_dir" /etc/systemd/system/memhub.service \
  /etc/systemd/system/memhub-backup.service /etc/systemd/system/memhub-backup.timer; do
  if sudo -n test -e "$path"; then
    echo "First-install target already exists: $path" >&2
    exit 1
  fi
done
# Stage both deploy keys and SSH aliases here before creating the account.
sudo -n test -f "$home_dir/.ssh/config"
sudo -n useradd --system --user-group --home-dir "$home_dir" --no-create-home \
  --shell /usr/sbin/nologin "$service_user"
sudo -n install -d -o "$service_user" -g "$service_user" -m 700 \
  "$home_dir" "$app_dir" "$memory_dir" "$state_dir"
sudo -n chown -R "$service_user:$service_user" "$home_dir/.ssh"
sudo -n chmod 700 "$home_dir/.ssh"
as_memhub git clone "$code_remote" "$app_dir"
# Install the explicitly supplied production parameters, including local edits.
sudo -n install -o "$service_user" -g "$service_user" -m 600 \
  "$config_path" "$app_dir/memhub.prod.toml"
as_memhub "$python_command" -m venv "$app_dir/.venv"
as_memhub "$app_dir/.venv/bin/pip" install -r "$app_dir/requirements.txt"
as_memhub "$app_dir/.venv/bin/pip" install --no-deps "$app_dir"
as_memhub "$app_dir/.venv/bin/memhub" --config "$app_dir/memhub.prod.toml" init
as_memhub git -C "$memory_dir" remote add origin "$memory_remote"
as_memhub "$python_command" - "$env_path" <<'PY'
import json
import os
import secrets
import sys

descriptor = os.open(sys.argv[1], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
    tokens = {secrets.token_urlsafe(48): identity for identity in ('claude-code', 'gemini-cli')}
    handle.write('STATIC_TOKENS=' + "'" + json.dumps(tokens) + "'\n")
os.chmod(sys.argv[1], 0o600)
PY
for unit in memhub.service memhub-backup.service memhub-backup.timer; do
  sudo -n install -o root -g root -m 644 "$app_dir/deploy/$unit" "/etc/systemd/system/$unit"
done
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now memhub.service memhub-backup.timer
wait_for_health
printf 'Installed. Next: set-password, then setup-tunnel.sh. Static tokens are in %s.\n' "$env_path"
