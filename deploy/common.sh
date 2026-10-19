#!/usr/bin/env bash
# Shared helpers. Deployment values come from the explicit production TOML.
set -euo pipefail

load_deploy_config() {
  config_path=$(python3.12 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1")
  local assignments
  assignments=$(sudo -n python3.12 - "$config_path" <<'PY'
import shlex
import sys
import tomllib
from urllib.parse import urlsplit

with open(sys.argv[1], 'rb') as handle:
    config = tomllib.load(handle)
deploy = config['deployment']
values = {key: deploy[key] for key in (
    'operator_user', 'service_user', 'home_dir', 'app_dir', 'state_dir',
    'code_remote', 'memory_remote', 'python_command', 'health_timeout_seconds',
    'tunnel_name', 'tunnel_metrics',
)}
values.update({key: config[key] for key in ('memory_dir', 'env_path', 'host', 'port', 'public_url')})
values['hostname'] = urlsplit(config['public_url']).hostname
for key, value in values.items():
    if value is None or str(value) == '':
        raise ValueError(f'Missing deployment parameter: {key}')
    print(f'{key}={shlex.quote(str(value))}')
PY
)
  eval "$assignments"
  if [[ $(id -un) != "$operator_user" ]]; then
    echo "Run this script as $operator_user." >&2
    exit 1
  fi
}

as_memhub() {
  sudo -n -H -u "$service_user" -- "$@"
}

wait_for_health() {
  local deadline=$((SECONDS + health_timeout_seconds))
  while (( SECONDS < deadline )); do
    if [[ $(curl --connect-timeout 1 --max-time 2 --silent --output /dev/null \
      --write-out '%{http_code}' "http://$host:$port/healthz") == 200 ]]; then
      return 0
    fi
    sleep 1
  done
  echo "Health check did not return 200 within $health_timeout_seconds seconds." >&2
  return 1
}
