#!/usr/bin/env bash
# Create only memhub-specific configuration and the ubuntu user service.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/common.sh"
load_deploy_config "${1:?Usage: bash deploy/setup-tunnel.sh /srv/memhub/app/memhub.prod.toml}"
umask 077
operator_home=$(getent passwd "$operator_user" | cut -d: -f6)
tunnel_config="$operator_home/.cloudflared/$tunnel_name.yml"
unit_path="$operator_home/.config/systemd/user/cloudflared-$tunnel_name.service"
for path in "$tunnel_config" "$unit_path"; do
  if [[ -e "$path" ]]; then
    echo "Tunnel configuration already exists: $path" >&2
    exit 1
  fi
done
cloudflared_bin=$(command -v cloudflared)
tunnel_id=$("$cloudflared_bin" tunnel list --output json | python3.12 -c \
  'import json,sys; print(next((t["id"] for t in json.load(sys.stdin) if t["name"] == sys.argv[1]), ""))' "$tunnel_name")
if [[ -z "$tunnel_id" ]]; then
  "$cloudflared_bin" tunnel create "$tunnel_name"
  tunnel_id=$("$cloudflared_bin" tunnel list --output json | python3.12 -c \
    'import json,sys; print(next(t["id"] for t in json.load(sys.stdin) if t["name"] == sys.argv[1]))' "$tunnel_name")
fi
test -f "$operator_home/.cloudflared/$tunnel_id.json"
mkdir -p "$operator_home/.cloudflared" "$operator_home/.config/systemd/user"
cat > "$tunnel_config" <<CONFIG
tunnel: $tunnel_id
credentials-file: $operator_home/.cloudflared/$tunnel_id.json
metrics: $tunnel_metrics
ingress:
  - hostname: $hostname
    service: http://$host:$port
  - service: http_status:404
CONFIG
"$cloudflared_bin" tunnel --config "$tunnel_config" ingress validate
cat > "$unit_path" <<UNIT
[Unit]
Description=Cloudflare Tunnel for memhub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$cloudflared_bin tunnel --config $tunnel_config --no-autoupdate run
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
sudo -n loginctl enable-linger "$operator_user"
systemctl --user daemon-reload
systemctl --user enable --now "cloudflared-$tunnel_name.service"
curl --retry 8 --retry-connrefused --retry-delay 1 --max-time 2 --fail --silent \
  "http://$tunnel_metrics/ready"
"$cloudflared_bin" tunnel route dns --overwrite-dns "$tunnel_id" "$hostname"
printf '\nTunnel ready: %s/mcp\n' "$public_url"
