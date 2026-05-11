#!/usr/bin/env bash
# Install LiveKit-SIP on the existing AloEgy VPS (Hostinger KVM).
# Idempotent — safe to re-run.
#
# Assumes the existing setup from DEPLOY.md is already in place:
#   - livekit-server installed as systemd service `livekit`
#   - /etc/livekit/config.yaml exists with API key APICCqUMXRRWoH9
#   - nginx is fronting wss://lk.aloegy.ai
#
# After this script: livekit-sip listens on 5060/5061 and bridges SIP
# calls to the existing livekit-server.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (or via sudo)" >&2
    exit 1
fi

LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-APICCqUMXRRWoH9}"
LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-}"
PUBLIC_IP="${PUBLIC_IP:-}"

if [[ -z "$LIVEKIT_API_SECRET" ]]; then
    echo "LIVEKIT_API_SECRET env not set; pass it: LIVEKIT_API_SECRET=... ./install-sip.sh" >&2
    exit 2
fi
if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP=$(curl -fsS --max-time 5 ifconfig.me 2>/dev/null || true)
fi
if [[ -z "$PUBLIC_IP" ]]; then
    echo "PUBLIC_IP env not set and could not auto-detect; pass it: PUBLIC_IP=1.2.3.4 ./install-sip.sh" >&2
    exit 2
fi

echo "==> public IP: $PUBLIC_IP"

# 1. Redis (livekit-sip can run without it but recommended for state).
if ! command -v redis-cli >/dev/null 2>&1; then
    echo "==> installing redis-server"
    apt update
    apt install -y redis-server
    systemctl enable --now redis-server
fi

# 2. Download livekit-sip binary (latest release).
if ! command -v livekit-sip >/dev/null 2>&1; then
    echo "==> installing livekit-sip"
    arch="$(uname -m)"
    case "$arch" in
        x86_64) sip_arch="amd64" ;;
        aarch64|arm64) sip_arch="arm64" ;;
        *) echo "unsupported arch: $arch" >&2; exit 3 ;;
    esac
    tmp=$(mktemp -d)
    curl -fsSL "https://github.com/livekit/sip/releases/latest/download/livekit-sip_linux_${sip_arch}.tar.gz" \
        -o "$tmp/sip.tgz"
    tar -xzf "$tmp/sip.tgz" -C "$tmp"
    install -m 0755 "$tmp/livekit-sip" /usr/local/bin/livekit-sip
    rm -rf "$tmp"
fi

# 3. Drop the YAML config (idempotent — overwrite is fine, no per-tenant
# state lives here).
mkdir -p /etc/livekit
cat > /etc/livekit/sip.yaml <<EOF
api_key: ${LIVEKIT_API_KEY}
ws_url: ws://127.0.0.1:7880

sip_port: 5060
sips_port: 5061

rtp_port:
  start: 10000
  end: 20000

nat_external_ip: ${PUBLIC_IP}

invite_timeout: 8s

logging:
  level: info
  json: true
EOF

# 4. Env file with the secret + public IP, mode 600 so only root reads it.
cat > /etc/livekit/sip.env <<EOF
LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET}
PUBLIC_IP=${PUBLIC_IP}
EOF
chmod 600 /etc/livekit/sip.env

# 5. systemd unit.
cat > /etc/systemd/system/aloegy-sip.service <<'EOF'
[Unit]
Description=LiveKit SIP Gateway (AloEgy)
After=network.target livekit.service redis-server.service
Requires=livekit.service

[Service]
Type=simple
User=root
EnvironmentFile=/etc/livekit/sip.env
ExecStart=/usr/local/bin/livekit-sip --config /etc/livekit/sip.yaml
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

# 6. Firewall openings.
if command -v ufw >/dev/null 2>&1; then
    echo "==> opening firewall ports"
    ufw allow 5060/udp || true
    ufw allow 5060/tcp || true
    ufw allow 5061/tcp || true
    ufw allow 10000:20000/udp || true
fi

# 7. Boot it.
systemctl daemon-reload
systemctl enable aloegy-sip
systemctl restart aloegy-sip

sleep 1
systemctl --no-pager --full status aloegy-sip || true

echo
echo "✅ livekit-sip installed."
echo "   verify:    journalctl -u aloegy-sip -f"
echo "   live SIP:  apt install -y sngrep && sngrep -d any port 5060 or port 5061"
echo
echo "next steps:"
echo "  1. add DNS A record:  sip.aloegy.ai → ${PUBLIC_IP}"
echo "  2. add the same LIVEKIT_API_KEY/SECRET to backend .env if not already there"
echo "  3. provision a tenant:"
echo "       curl -X POST https://api.aloegy.ai/admin/restaurants/<id>/sip-provision \\"
echo "            -H 'Authorization: Bearer <admin-jwt>' \\"
echo "            -H 'Content-Type: application/json' \\"
echo "            -d '{\"did\": \"+201001234567\", \"issabelIp\": \"<customer-pbx-ip>\"}'"
