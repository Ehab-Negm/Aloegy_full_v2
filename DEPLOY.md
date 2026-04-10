# AloEgy — Hostinger VPS Deployment Guide (Self-Hosted LiveKit)

## What you need

- Hostinger VPS (Ubuntu 22.04 or 24.04) — minimum 4GB RAM, 2 vCPU
- Domain pointed to VPS IP (e.g. `aloegy.com`)
- SSH access to VPS

---

## Step 1: Point domain to VPS

In Hostinger DNS settings, add:
```
A    @       → YOUR_VPS_IP
A    api     → YOUR_VPS_IP
A    lk      → YOUR_VPS_IP
```

This gives you:
- `aloegy.com` → frontend
- `api.aloegy.com` → backend
- `lk.aloegy.com` → LiveKit server (voice)

---

## Step 2: SSH into VPS and install dependencies

```bash
ssh root@YOUR_VPS_IP

# Update system
apt update && apt upgrade -y

# Install Python 3.11+, Node, Nginx, Certbot
apt install -y python3 python3-pip python3-venv nodejs npm nginx certbot python3-certbot-nginx git

# Install Node 18+ (if apt version is old)
curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
apt install -y nodejs
```

---

## Step 3: Install LiveKit Server

```bash
# Download LiveKit server binary
curl -sSL https://get.livekit.io | bash

# Verify
livekit-server --version
```

### Generate LiveKit API key and secret

```bash
# Generate a key pair (save these!)
livekit-server generate-keys
```

This will output something like:
```
API Key:    APIxxxxxxxxxx
API Secret: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Save these — you'll need them for agent + backend + frontend.**

### Create LiveKit config

```bash
mkdir -p /etc/livekit

cat > /etc/livekit/config.yaml << 'EOF'
port: 7880
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true
turn:
  enabled: true
  domain: lk.aloegy.com
  tls_port: 5349
  udp_port: 3478
keys:
  YOUR_API_KEY: YOUR_API_SECRET
logging:
  level: info
EOF
```

**Replace `YOUR_API_KEY: YOUR_API_SECRET` with the keys you generated.**

### Create LiveKit systemd service

```bash
cat > /etc/systemd/system/livekit.service << 'EOF'
[Unit]
Description=LiveKit Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/livekit-server --config /etc/livekit/config.yaml
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable livekit
systemctl start livekit
```

---

## Step 4: Upload project

```bash
# From your local machine:
scp -r "d:\lovable livekit" root@YOUR_VPS_IP:/opt/aloegy

# OR clone from git:
ssh root@YOUR_VPS_IP
git clone https://github.com/Ehab-Negm/AloEgy.git /opt/aloegy
```

---

## Step 5: Setup Backend

```bash
cd /opt/aloegy/backend

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install uvicorn gunicorn

# Create .env
cp .env.example .env
nano .env
```

Edit `/opt/aloegy/backend/.env`:
```env
APP_ENV=prod
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
BACKEND_API_KEY=GENERATE_A_STRONG_KEY_HERE
JWT_SECRET=GENERATE_A_STRONG_SECRET_HERE
CORS_ORIGINS=https://aloegy.com,https://www.aloegy.com
DATABASE_URL=postgresql://YOUR_SUPABASE_URL_HERE
LIVEKIT_URL=wss://lk.aloegy.com
LIVEKIT_API_KEY=YOUR_LIVEKIT_KEY
LIVEKIT_API_SECRET=YOUR_LIVEKIT_SECRET
```

Generate strong secrets:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Step 6: Setup Agent

```bash
cd /opt/aloegy/agent

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env
cp .env.example .env
nano .env
```

Edit `/opt/aloegy/agent/.env`:
```env
LIVEKIT_URL=wss://lk.aloegy.com
LIVEKIT_API_KEY=YOUR_LIVEKIT_KEY
LIVEKIT_API_SECRET=YOUR_LIVEKIT_SECRET
XAI_API_KEY=your_xai_key
GOOGLE_API_KEY=your_google_key
SONIOX_API_KEY=your_soniox_key
APP_ENV=prod
BACKEND_BASE_URL=http://127.0.0.1:8000
BACKEND_API_KEY=SAME_KEY_AS_BACKEND
```

---

## Step 7: Build Frontend

```bash
cd /opt/aloegy/frontend/entameen-main

npm install

# Set API URL and build
VITE_API_BASE_URL=https://api.aloegy.com npm run build

# Output will be in dist/
```

---

## Step 8: Create systemd services

### Backend service

```bash
cat > /etc/systemd/system/aloegy-backend.service << 'EOF'
[Unit]
Description=AloEgy Backend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/aloegy/backend
Environment=PATH=/opt/aloegy/backend/.venv/bin:/usr/bin
ExecStart=/opt/aloegy/backend/.venv/bin/gunicorn main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 127.0.0.1:8000 \
    --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### Agent service

```bash
cat > /etc/systemd/system/aloegy-agent.service << 'EOF'
[Unit]
Description=AloEgy Voice Agent
After=network.target aloegy-backend.service livekit.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/aloegy/agent
Environment=PATH=/opt/aloegy/agent/.venv/bin:/usr/bin
ExecStart=/opt/aloegy/agent/.venv/bin/python agent.py start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### Enable and start all services

```bash
systemctl daemon-reload
systemctl enable livekit aloegy-backend aloegy-agent
systemctl start livekit aloegy-backend aloegy-agent

# Check status
systemctl status livekit aloegy-backend aloegy-agent
```

---

## Step 9: Configure Nginx

### LiveKit (lk.aloegy.com)

```bash
cat > /etc/nginx/sites-available/aloegy-livekit << 'EOF'
server {
    listen 80;
    server_name lk.aloegy.com;

    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
EOF
```

### Backend (api.aloegy.com)

```bash
cat > /etc/nginx/sites-available/aloegy-api << 'EOF'
server {
    listen 80;
    server_name api.aloegy.com;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE support
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
EOF
```

### Frontend (aloegy.com)

```bash
cat > /etc/nginx/sites-available/aloegy-frontend << 'EOF'
server {
    listen 80;
    server_name aloegy.com www.aloegy.com;

    root /opt/aloegy/frontend/entameen-main/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF
```

### Enable sites

```bash
ln -s /etc/nginx/sites-available/aloegy-livekit /etc/nginx/sites-enabled/
ln -s /etc/nginx/sites-available/aloegy-api /etc/nginx/sites-enabled/
ln -s /etc/nginx/sites-available/aloegy-frontend /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl restart nginx
```

---

## Step 10: SSL with Let's Encrypt

```bash
certbot --nginx -d aloegy.com -d www.aloegy.com -d api.aloegy.com -d lk.aloegy.com

# Auto-renewal (already set up by certbot, but verify):
certbot renew --dry-run
```

---

## Step 11: Firewall

```bash
ufw allow 22/tcp      # SSH
ufw allow 80/tcp      # HTTP
ufw allow 443/tcp     # HTTPS
ufw allow 7881/tcp    # LiveKit WebRTC over TCP
ufw allow 5349/tcp    # LiveKit TURN TLS
ufw allow 3478/udp    # LiveKit TURN UDP
ufw allow 50000:60000/udp  # LiveKit WebRTC media (UDP)
ufw --force enable
```

---

## Useful commands

```bash
# View logs
journalctl -u livekit -f
journalctl -u aloegy-backend -f
journalctl -u aloegy-agent -f

# Restart after code changes
systemctl restart aloegy-backend
systemctl restart aloegy-agent

# Restart LiveKit
systemctl restart livekit

# Rebuild frontend after changes
cd /opt/aloegy/frontend/entameen-main
VITE_API_BASE_URL=https://api.aloegy.com npm run build

# Check what's running
systemctl status livekit aloegy-backend aloegy-agent nginx
```

---

## Quick checklist

- [ ] DNS: `aloegy.com`, `api.aloegy.com`, `lk.aloegy.com` all point to VPS IP
- [ ] LiveKit server running on port 7880 with generated keys
- [ ] Backend .env: `APP_ENV=prod`, strong `JWT_SECRET`, `BACKEND_API_KEY`, `LIVEKIT_URL=wss://lk.aloegy.com`
- [ ] Agent .env: `LIVEKIT_URL=wss://lk.aloegy.com` + same LiveKit keys + all AI API keys (fresh)
- [ ] Frontend built with `VITE_API_BASE_URL=https://api.aloegy.com`
- [ ] All 3 systemd services running (livekit, backend, agent)
- [ ] Nginx configured with WebSocket support for LiveKit
- [ ] SSL certificates on all 3 subdomains
- [ ] Firewall: 22, 80, 443, 7881, 5349, 3478, 50000-60000/udp
- [ ] Test: `https://api.aloegy.com/health` → `{"status": "ok", "env": "prod"}`
- [ ] Test: `https://aloegy.com` loads frontend
- [ ] Test: Voice call connects through `wss://lk.aloegy.com`

---

## VPS minimum specs

| Concurrent calls | RAM  | vCPU | Bandwidth |
|------------------|------|------|-----------|
| 1-5              | 4GB  | 2    | 100 Mbps  |
| 5-20             | 8GB  | 4    | 200 Mbps  |
| 20-50            | 16GB | 8    | 500 Mbps  |

LiveKit voice uses ~100 Kbps per participant. The main bottleneck is CPU for audio encoding.
