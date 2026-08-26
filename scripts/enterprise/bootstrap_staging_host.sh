#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP_VERSION="mentorflow-staging-bootstrap.v1"
APPLY=0
DEPLOY_USER="mentorflow-deploy"
TIMEZONE="UTC"
SSH_PORT="22"
SWAP_POLICY="CONFIGURE_IF_ABSENT"
SWAP_SIZE_MB="2048"
ROOT_DIR="/opt/mentorflow"
RECEIPT="/opt/mentorflow/artifacts/bootstrap-receipt.json"
AUTHORIZED_KEY_FILE=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
  echo "usage: $0 [--apply] [--deploy-user USER] [--authorized-key-file PATH] [--timezone TZ] [--ssh-port PORT] [--swap-policy POLICY] [--swap-size-mb MB] [--receipt PATH]" >&2
}

while (($#)); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --deploy-user) DEPLOY_USER="$2"; shift 2 ;;
    --authorized-key-file) AUTHORIZED_KEY_FILE="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    --swap-policy) SWAP_POLICY="$2"; shift 2 ;;
    --swap-size-mb) SWAP_SIZE_MB="$2"; shift 2 ;;
    --receipt) RECEIPT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "invalid deploy user" >&2; exit 2; }
[[ -n "$TIMEZONE" ]] || { echo "timezone must not be empty" >&2; exit 2; }
[[ "$SSH_PORT" =~ ^[0-9]+$ ]] && ((SSH_PORT >= 1 && SSH_PORT <= 65535)) || { echo "invalid SSH port" >&2; exit 2; }
[[ "$SWAP_POLICY" == "CONFIGURE_IF_ABSENT" || "$SWAP_POLICY" == "REQUIRE_EXISTING" || "$SWAP_POLICY" == "DISABLED_REVIEWED" ]] || { echo "invalid swap policy" >&2; exit 2; }
[[ "$SWAP_SIZE_MB" =~ ^[0-9]+$ ]] && ((SWAP_SIZE_MB >= 512)) || { echo "swap size must be at least 512 MB" >&2; exit 2; }
[[ "$RECEIPT" == "$ROOT_DIR/artifacts/"* && "$RECEIPT" != *"/../"* && "$RECEIPT" != *"/./"* ]] || { echo "receipt must remain under $ROOT_DIR/artifacts" >&2; exit 2; }

if ((APPLY == 0)); then
  "$PYTHON_BIN" - "$BOOTSTRAP_VERSION" "$DEPLOY_USER" "$TIMEZONE" "$SSH_PORT" "$SWAP_POLICY" "$SWAP_SIZE_MB" "$([[ -n "$AUTHORIZED_KEY_FILE" ]] && printf provided || printf existing-required)" <<'PY'
import json, sys
print(json.dumps({
    "schema": "mentorflow.staging-bootstrap-plan.v1",
    "bootstrap_version": sys.argv[1],
    "status": "DRY_RUN",
    "deploy_user": sys.argv[2],
    "timezone": sys.argv[3],
    "ssh_port": int(sys.argv[4]),
    "swap_policy": sys.argv[5],
    "swap_size_mb": int(sys.argv[6]),
    "authorized_key_gate": sys.argv[7],
    "provider_api_calls": 0,
    "credentials_written": False,
}, sort_keys=True))
PY
  exit 0
fi

((EUID == 0)) || { echo "--apply requires root" >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo "/etc/os-release missing" >&2; exit 1; }
if [[ -n "$AUTHORIZED_KEY_FILE" && ! -r "$AUTHORIZED_KEY_FILE" ]]; then
  echo "authorized key file is not readable" >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || { echo "Ubuntu 24.04 is required" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg jq ufw chrony rsync python3 sudo openssh-server

install -d -m 0755 /etc/apt/keyrings
if [[ ! -s /etc/apt/keyrings/docker.gpg ]]; then
  curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc.tmp
  gpg --dearmor --yes --output /etc/apt/keyrings/docker.gpg /etc/apt/keyrings/docker.asc.tmp
  rm -f /etc/apt/keyrings/docker.asc.tmp
fi
chmod 0644 /etc/apt/keyrings/docker.gpg
ARCH="$(dpkg --print-architecture)"
CODENAME="${UBUNTU_CODENAME:-noble}"
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' "$ARCH" "$CODENAME" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker chrony ssh

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
passwd --lock "$DEPLOY_USER" >/dev/null
usermod --append --groups docker "$DEPLOY_USER"
DEPLOY_HOME="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
[[ -n "$DEPLOY_HOME" && "$DEPLOY_HOME" != "/" ]] || { echo "deploy user home is invalid" >&2; exit 1; }
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$DEPLOY_HOME/.ssh"
AUTHORIZED_KEYS="$DEPLOY_HOME/.ssh/authorized_keys"
touch "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"
chown "$DEPLOY_USER:$DEPLOY_USER" "$AUTHORIZED_KEYS"
if [[ -n "$AUTHORIZED_KEY_FILE" ]]; then
  ssh-keygen -l -f "$AUTHORIZED_KEY_FILE" >/dev/null || { echo "authorized key file is invalid" >&2; exit 1; }
  while IFS= read -r key_line || [[ -n "$key_line" ]]; do
    key_line="${key_line%$'\r'}"
    [[ -z "${key_line//[[:space:]]/}" ]] && continue
    grep -qxF -- "$key_line" "$AUTHORIZED_KEYS" || printf '%s\n' "$key_line" >> "$AUTHORIZED_KEYS"
  done < "$AUTHORIZED_KEY_FILE"
fi
[[ -s "$AUTHORIZED_KEYS" ]] || { echo "deploy user must have an authorized SSH key before SSH hardening" >&2; exit 1; }
ssh-keygen -l -f "$AUTHORIZED_KEYS" >/dev/null || { echo "deploy user authorized_keys is invalid" >&2; exit 1; }

cat > /etc/sudoers.d/mentorflow-deploy <<EOF
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/bin/systemctl status docker, /usr/bin/systemctl restart docker, /usr/bin/journalctl -u docker --no-pager, /usr/sbin/ufw status
EOF
chmod 0440 /etc/sudoers.d/mentorflow-deploy
visudo --check --file=/etc/sudoers.d/mentorflow-deploy

install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/60-mentorflow-staging.conf <<EOF
Port $SSH_PORT
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 4
X11Forwarding no
AllowUsers $DEPLOY_USER
EOF
sshd -t

timedatectl set-timezone "$TIMEZONE"
timedatectl set-ntp true

ufw default deny incoming
ufw default allow outgoing
ufw allow "$SSH_PORT/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
systemctl reload ssh

install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 \
  "$ROOT_DIR" "$ROOT_DIR/releases" "$ROOT_DIR/data" "$ROOT_DIR/backups" \
  "$ROOT_DIR/artifacts" "$ROOT_DIR/logs"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$ROOT_DIR/data/secrets"

case "$SWAP_POLICY" in
  CONFIGURE_IF_ABSENT)
    if ! swapon --show=NAME --noheadings | grep -q .; then
      [[ ! -e /swapfile ]] || { echo "/swapfile exists but is not active; review it manually" >&2; exit 1; }
      fallocate -l "${SWAP_SIZE_MB}M" /swapfile || dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_SIZE_MB" status=progress
      chmod 0600 /swapfile
      mkswap /swapfile >/dev/null
      swapon /swapfile
      grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    ;;
  REQUIRE_EXISTING)
    swapon --show=NAME --noheadings | grep -q . || { echo "existing swap is required" >&2; exit 1; }
    ;;
  DISABLED_REVIEWED) ;;
esac

install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 "$(dirname "$RECEIPT")"
"$PYTHON_BIN" - "$RECEIPT" "$BOOTSTRAP_VERSION" "$DEPLOY_USER" "$TIMEZONE" "$SSH_PORT" "$SWAP_POLICY" <<'PY'
import json, os, pathlib, platform, sys, tempfile
from datetime import datetime, timezone

artifact_root = pathlib.Path("/opt/mentorflow/artifacts").resolve()
path = pathlib.Path(sys.argv[1]).resolve()
if not path.is_relative_to(artifact_root):
    raise SystemExit("receipt path escaped /opt/mentorflow/artifacts")
payload = {
    "schema": "mentorflow.staging-bootstrap-receipt.v1",
    "bootstrap_version": sys.argv[2],
    "status": "APPLIED",
    "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    "host": platform.node(),
    "deploy_user": sys.argv[3],
    "timezone": sys.argv[4],
    "ssh_port": int(sys.argv[5]),
    "swap_policy": sys.argv[6],
    "docker_configured": True,
    "compose_plugin_configured": True,
    "firewall_configured": True,
    "provider_api_calls": 0,
    "credentials_written": False,
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
chown "$DEPLOY_USER:$DEPLOY_USER" "$RECEIPT"
echo "bootstrap complete: $RECEIPT"
