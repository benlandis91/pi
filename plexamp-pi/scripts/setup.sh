#!/bin/bash
# PlexPi Setup Script for Raspberry Pi 5
# Run as: sudo bash setup.sh

set -e
PLEXPI_DIR="/opt/plexpi"
SERVICE_USER="pi"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[PlexPi]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[[ $EUID -ne 0 ]] && err "Run as root: sudo bash setup.sh"

# ── 1. System Updates ────────────────────────────────────────────────────────
log "Updating system packages..."
apt-get update -qq
apt-get install -y \
  python3 python3-pip python3-venv \
  mpv socat \
  alsa-utils alsa-base libasound2-dev \
  pulseaudio pulseaudio-utils \
  avahi-daemon avahi-utils \
  nginx \
  git curl wget \
  libssl-dev libffi-dev \
  2>/dev/null || true

# Verify critical packages installed
for pkg in python3 mpv socat nginx; do
  command -v $pkg &>/dev/null || err "Failed to install required package: $pkg"
done

# ── 2. shairport-sync (AirPlay) ───────────────────────────────────────────────
log "Installing shairport-sync for AirPlay support..."
if ! command -v shairport-sync &>/dev/null; then
  apt-get install -y \
    build-essential autoconf automake libtool pkg-config \
    libpopt-dev libconfig-dev libavahi-client-dev \
    libssl-dev libsoxr-dev libpulse-dev libglib2.0-dev \
    libasound2-dev \
    xmltoman libmosquitto-dev 2>/dev/null || true

  SHAIRPORT_VERSION="4.3.2"
  cd /tmp
  wget -q "https://github.com/mikebrady/shairport-sync/archive/refs/tags/${SHAIRPORT_VERSION}.tar.gz"
  tar xf "${SHAIRPORT_VERSION}.tar.gz"
  cd "shairport-sync-${SHAIRPORT_VERSION}"
  autoreconf -fi

  # Try with ALSA + PulseAudio; fall back to PulseAudio-only if ALSA headers missing
  if pkg-config --exists alsa 2>/dev/null; then
    log "Building shairport-sync with ALSA + PulseAudio..."
    ./configure \
      --sysconfdir=/etc \
      --with-alsa \
      --with-pa \
      --with-avahi \
      --with-ssl=openssl \
      --with-soxr \
      --with-metadata \
      --with-systemd
  else
    warn "ALSA dev headers not found, building with PulseAudio only..."
    ./configure \
      --sysconfdir=/etc \
      --with-pa \
      --with-avahi \
      --with-ssl=openssl \
      --with-soxr \
      --with-metadata \
      --with-systemd
  fi

  make -j4
  make install
  cd /tmp && rm -rf "shairport-sync-${SHAIRPORT_VERSION}" "${SHAIRPORT_VERSION}.tar.gz"
  log "shairport-sync built and installed."
else
  log "shairport-sync already installed."
fi

# ── 3. shairport-sync Config ──────────────────────────────────────────────────
HOSTNAME=$(hostname)
cat > /etc/shairport-sync.conf << SHAIRCONF
general = {
  name = "PlexPi - ${HOSTNAME}";
  output_backend = "pa";
  interpolation = "soxr";
};

metadata = {
  enabled = "yes";
  include_cover_art = "yes";
  pipe_name = "/tmp/shairport-sync-metadata";
  pipe_timeout = 5000;
};

pa = {
  application_name = "PlexPi";
};
SHAIRCONF

systemctl enable shairport-sync
systemctl restart shairport-sync
log "AirPlay (shairport-sync) configured as 'PlexPi - ${HOSTNAME}'"

# ── 4. Python Backend ─────────────────────────────────────────────────────────
log "Setting up Python backend..."
mkdir -p "$PLEXPI_DIR"
cp -r "$(dirname "$0")"/* "$PLEXPI_DIR/"

python3 -m venv "$PLEXPI_DIR/venv"
"$PLEXPI_DIR/venv/bin/pip" install -q --upgrade pip
"$PLEXPI_DIR/venv/bin/pip" install -q \
  flask \
  flask-cors \
  plexapi \
  requests \
  gunicorn

log "Python dependencies installed."

# ── 5. Systemd Service ────────────────────────────────────────────────────────
cat > /etc/systemd/system/plexpi.service << SVCEOF
[Unit]
Description=PlexPi Music Player Backend
After=network.target sound.target

[Service]
User=${SERVICE_USER}
WorkingDirectory=${PLEXPI_DIR}/backend
ExecStart=${PLEXPI_DIR}/venv/bin/gunicorn \
  --bind 127.0.0.1:8080 \
  --workers 1 \
  --threads 4 \
  --timeout 60 \
  app:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable plexpi
systemctl restart plexpi

# ── 6. Nginx (serve frontend + proxy API) ─────────────────────────────────────
log "Configuring Nginx..."
mkdir -p /var/www/plexpi
cp "$PLEXPI_DIR/frontend/index.html" /var/www/plexpi/

cat > /etc/nginx/sites-available/plexpi << 'NGINXEOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    root /var/www/plexpi;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache";
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300;
        proxy_buffering off;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/plexpi /etc/nginx/sites-enabled/plexpi
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# ── 7. Touchscreen & Kiosk Mode ──────────────────────────────────────────────
log "Configuring touchscreen kiosk mode..."
apt-get install -y chromium-browser xserver-xorg x11-xserver-utils xinput xdotool openbox 2>/dev/null || true

mkdir -p /home/${SERVICE_USER}/.config/openbox
cat > /home/${SERVICE_USER}/.config/openbox/autostart << 'KIOSKEOF'
xset s off
xset -dpms
xset s noblank
unclutter -idle 3 &
sleep 2
chromium-browser \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=TranslateUI \
  --no-first-run \
  --touch-events=enabled \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 \
  "http://localhost" &
KIOSKEOF

chown -R ${SERVICE_USER}:${SERVICE_USER} /home/${SERVICE_USER}/.config

BASHRC="/home/${SERVICE_USER}/.bashrc"
if ! grep -q "startx" "$BASHRC" 2>/dev/null; then
  echo '
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  startx /usr/bin/openbox-session -- :0 -nocursor
fi' >> "$BASHRC"
fi

mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << AUTOLOGIN
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${SERVICE_USER} --noclear %I \$TERM
AUTOLOGIN

systemctl daemon-reload

# ── 8. Audio ──────────────────────────────────────────────────────────────────
log "Configuring audio..."
usermod -aG audio ${SERVICE_USER}

# ── 9. unclutter (hide mouse cursor) ─────────────────────────────────────────
apt-get install -y unclutter 2>/dev/null || true

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       PlexPi Setup Complete! 🎵           ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  AirPlay name:  ${YELLOW}PlexPi - ${HOSTNAME}${NC}"
echo -e "  Web UI:        ${YELLOW}http://$(hostname -I | awk '{print $1}')${NC}"
echo ""
echo -e "  Services:"
echo -e "    plexpi:           $(systemctl is-active plexpi)"
echo -e "    shairport-sync:   $(systemctl is-active shairport-sync)"
echo -e "    nginx:            $(systemctl is-active nginx)"
echo ""
echo -e "  ${YELLOW}Reboot to start kiosk mode:${NC}  sudo reboot"
echo ""
