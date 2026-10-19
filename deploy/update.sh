#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/common.sh"
load_deploy_config "${1:?Usage: bash deploy/update.sh /srv/memhub/app/memhub.prod.toml}"
as_memhub git -C "$app_dir" pull --ff-only
as_memhub "$app_dir/.venv/bin/pip" install -r "$app_dir/requirements.txt"
as_memhub "$app_dir/.venv/bin/pip" install --no-deps "$app_dir"
for unit in memhub.service memhub-backup.service memhub-backup.timer; do
  sudo -n install -o root -g root -m 644 "$app_dir/deploy/$unit" "/etc/systemd/system/$unit"
done
sudo -n systemctl daemon-reload
sudo -n systemctl restart memhub.service
sudo -n systemctl enable --now memhub-backup.timer
wait_for_health
printf 'Updated; /healthz returned 200.\n'
