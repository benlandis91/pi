# PlexPi 🎵

A beautiful, touch-optimized Plex music player for Raspberry Pi 5 with a 7" touchscreen. Supports AirPlay via shairport-sync so you can stream from any Apple device too.

---

## Features

- **Full Plex library browsing** — Artists → Albums → Tracks
- **Playlist support** — browse and play any audio playlist
- **Search** — find tracks instantly across your library
- **AirPlay receiver** — appears as "PlexPi" on any iPhone, iPad, Mac, or Apple TV
- **Album art** — full-bleed backdrop with extracted color theming
- **Touch controls** — designed for 7" 800×480 screens, no mouse needed
- **Queue management** — view and jump to upcoming tracks
- **Shuffle & Repeat** — all modes supported
- **Volume control** — software slider + system audio
- **Kiosk mode** — boots directly into fullscreen Chromium, no desktop

---

## Hardware

| Component | Notes |
|-----------|-------|
| Raspberry Pi 5 (4GB+ recommended) | 8GB preferred for smooth browsing |
| Official 7" Raspberry Pi Touchscreen | Or any HDMI + USB touch display |
| USB DAC or HiFiBerry DAC+ | Optional but recommended for better audio |
| MicroSD 16GB+ Class 10 | Or NVMe SSD via PCIe |

---

## Quick Start

### 1. Flash Raspberry Pi OS

Use **Raspberry Pi Imager** to flash **Raspberry Pi OS Lite (64-bit)** or the full desktop version. Enable SSH in the imager settings.

### 2. Copy PlexPi files

```bash
scp -r plexamp-pi/ pi@raspberrypi.local:~/
```

### 3. Run setup

```bash
ssh pi@raspberrypi.local
cd ~/plexamp-pi
sudo bash scripts/setup.sh
```

The script will:
- Install all dependencies (mpv, shairport-sync, nginx, Python packages)
- Build shairport-sync from source for AirPlay 2 support
- Configure Chromium to launch in kiosk mode on boot
- Set up auto-login on tty1
- Start all services

### 4. Reboot

```bash
sudo reboot
```

The Pi will boot directly into the PlexPi UI. You'll see the setup screen on first launch.

---

## Finding Your Plex Token

1. Go to app.plex.tv in a browser and sign in
2. Browse to any library item
3. Click the `⋮` menu → **Get Info** → **View XML**
4. In the URL, find `X-Plex-Token=XXXXXXXXXXXXXXXX`
5. Copy that token

Or use your server's local URL:
- Open Plex Web → Settings → Troubleshooting → **Get Online Media**
- Your server URL is typically `http://192.168.1.x:32400`

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 5                      │
│                                                      │
│  ┌─────────────┐    ┌──────────────┐                 │
│  │   Chromium  │◄───│    Nginx     │                 │
│  │  (Kiosk UI) │    │  Port 80     │                 │
│  └─────────────┘    └──────┬───────┘                 │
│        7" Touch            │                         │
│                    ┌───────▼────────┐                │
│                    │  Flask/Gunicorn │                │
│                    │   Port 8080    │                 │
│                    └───────┬────────┘                │
│                            │                         │
│              ┌─────────────┼──────────────┐          │
│              │             │              │           │
│         ┌────▼────┐  ┌─────▼──────┐  ┌───▼────┐     │
│         │PlexAPI  │  │    MPV     │  │ System │     │
│         │(Browse/ │  │ (Playback/ │  │  Audio │     │
│         │ Search) │  │  IPC sock) │  │        │     │
│         └─────────┘  └────────────┘  └────────┘     │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │ shairport-sync (AirPlay)    Port 5000/mDNS   │    │
│  └──────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
         │                        │
    Plex Server              Apple Devices
  (local network)          (iPhone/iPad/Mac)
```

---

## Audio Output Options

### 3.5mm Jack (default)
Works out of the box.

### HDMI Audio
Edit `/etc/asound.conf`:
```
pcm.!default {
    type hw
    card 1  # HDMI is usually card 1
}
```

### USB DAC / HiFiBerry
Follow the HiFiBerry setup guide, then update `/etc/asound.conf` to point to the correct card.

---

## AirPlay Setup

AirPlay works automatically after setup. Your Pi will appear as **"PlexPi - {hostname}"** in the AirPlay menu on any Apple device on the same network.

When an AirPlay stream is detected, the Now Playing bar shows "AirPlay" and the pulsing indicator appears. The Pi handles audio output — control playback from your iPhone/iPad/Mac.

To rename the AirPlay receiver:
```bash
sudo nano /etc/shairport-sync.conf
# Change the name = "..." line
sudo systemctl restart shairport-sync
```

---

## Service Management

```bash
# Check status
sudo systemctl status plexpi
sudo systemctl status shairport-sync
sudo systemctl status nginx

# Restart everything
sudo systemctl restart plexpi shairport-sync nginx

# View logs
sudo journalctl -u plexpi -f
sudo journalctl -u shairport-sync -f
```

---

## Touchscreen Calibration

If touches are off, find your touchscreen device name:
```bash
xinput list
```

Then in `~/.config/openbox/autostart`, uncomment and adjust:
```bash
xinput set-prop "your-device-name" "Coordinate Transformation Matrix" 1 0 0 0 1 0 0 0 1
```

For 90° rotation:
```bash
xinput set-prop "your-device" "Coordinate Transformation Matrix" 0 1 0 -1 0 1 0 0 1
```

---

## Accessing from Other Devices

The web UI is accessible from any browser on your network:
```
http://raspberrypi.local
# or
http://192.168.1.x   # your Pi's IP
```

---

## File Structure

```
plexamp-pi/
├── backend/
│   ├── app.py              # Flask API server
│   └── requirements.txt    # Python dependencies
├── frontend/
│   └── index.html          # Single-file touch UI
└── scripts/
    └── setup.sh            # Full automated setup
```

---

## Troubleshooting

**No audio through AirPlay**
```bash
sudo systemctl restart shairport-sync
pactl list sinks  # Check PulseAudio sinks
```

**Plex connection fails**
- Make sure your Pi and Plex server are on the same network
- Try using the IP address instead of hostname
- Check your token hasn't expired

**Touchscreen not responding**
```bash
dmesg | grep -i touch  # Check if driver loaded
xinput list            # Check input devices
```

**UI not loading**
```bash
sudo systemctl status nginx plexpi
sudo journalctl -u plexpi -f
```
