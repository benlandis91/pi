#!/usr/bin/env python3
"""
PlexPi - Raspberry Pi 5 Plex Music Player Backend
Flask server providing Plex library browsing, playback control, and AirPlay metadata
"""

import os
import json
import time
import threading
import subprocess
import urllib.request
from pathlib import Path
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount
import requests

app = Flask(__name__)
CORS(app)

# ─── Config ──────────────────────────────────────────────────────────────────
CONFIG_FILE = Path("/home/pi/.plexpi/config.json")
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── Plex Connection ──────────────────────────────────────────────────────────
_plex = None
_plex_lock = threading.Lock()

def get_plex():
    global _plex
    with _plex_lock:
        if _plex is not None:
            return _plex
        cfg = load_config()
        if not cfg.get("plex_url") or not cfg.get("plex_token"):
            return None
        try:
            _plex = PlexServer(cfg["plex_url"], cfg["plex_token"])
            return _plex
        except Exception as e:
            print(f"Plex connection error: {e}")
            return None

def reset_plex():
    global _plex
    with _plex_lock:
        _plex = None

# ─── Player State ─────────────────────────────────────────────────────────────
player_state = {
    "status": "stopped",       # stopped | playing | paused
    "source": "plex",          # plex | airplay
    "track": None,
    "artist": None,
    "album": None,
    "album_art_url": None,
    "duration": 0,
    "position": 0,
    "volume": 80,
    "queue": [],
    "queue_index": 0,
    "shuffle": False,
    "repeat": "none",          # none | one | all
    "airplay_active": False,
    "airplay_track": None,
    "airplay_artist": None,
    "airplay_album": None,
}

state_lock = threading.Lock()
mpv_process = None
position_thread = None

# ─── MPV Player ──────────────────────────────────────────────────────────────
MPV_SOCKET = "/tmp/mpv-plexpi.sock"

def mpv_command(cmd):
    """Send a command to MPV via IPC socket."""
    try:
        payload = json.dumps({"command": cmd}) + "\n"
        result = subprocess.run(
            ["socat", "-", f"UNIX-CONNECT:{MPV_SOCKET}"],
            input=payload.encode(),
            capture_output=True,
            timeout=2,
        )
        if result.stdout:
            return json.loads(result.stdout.decode().strip().split("\n")[0])
    except Exception as e:
        print(f"MPV command error: {e}")
    return None

def get_mpv_property(prop):
    res = mpv_command(["get_property", prop])
    if res and res.get("error") == "success":
        return res.get("data")
    return None

def set_mpv_property(prop, val):
    mpv_command(["set_property", prop, val])

def start_mpv(url):
    global mpv_process
    stop_mpv()
    cmd = [
        "mpv",
        "--no-video",
        f"--input-ipc-server={MPV_SOCKET}",
        "--really-quiet",
        "--volume=" + str(player_state["volume"]),
        url,
    ]
    mpv_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_mpv():
    global mpv_process
    if mpv_process:
        mpv_process.terminate()
        try:
            mpv_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            mpv_process.kill()
        mpv_process = None

def position_tracker():
    """Background thread to update playback position."""
    while True:
        time.sleep(1)
        if player_state["status"] == "playing" and player_state["source"] == "plex":
            pos = get_mpv_property("time-pos")
            if pos is not None:
                with state_lock:
                    player_state["position"] = int(pos)
            else:
                # Track ended - advance queue
                advance_queue()

def advance_queue(direction=1):
    with state_lock:
        q = player_state["queue"]
        if not q:
            player_state["status"] = "stopped"
            return
        idx = player_state["queue_index"] + direction
        if player_state["repeat"] == "all":
            idx = idx % len(q)
        elif idx >= len(q):
            player_state["status"] = "stopped"
            player_state["position"] = 0
            return
        player_state["queue_index"] = max(0, min(idx, len(q) - 1))
    play_queue_item(player_state["queue_index"])

def play_queue_item(index):
    plex = get_plex()
    if not plex:
        return
    with state_lock:
        q = player_state["queue"]
        if not q or index >= len(q):
            return
        item = q[index]
        cfg = load_config()
        base_url = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        stream_url = f"{base_url}{item['stream_key']}?X-Plex-Token={token}"
        player_state.update({
            "status": "playing",
            "source": "plex",
            "track": item["title"],
            "artist": item["artist"],
            "album": item["album"],
            "album_art_url": item.get("thumb_url"),
            "duration": item.get("duration", 0),
            "position": 0,
            "queue_index": index,
        })
    start_mpv(stream_url)

# ─── AirPlay Monitor ──────────────────────────────────────────────────────────
def monitor_airplay():
    """Watch shairport-sync MQTT or pipe for metadata."""
    meta_file = Path("/tmp/shairport-sync-metadata")
    # Simple polling approach - reads shairport pipe
    while True:
        time.sleep(2)
        try:
            if meta_file.exists():
                # In production: parse shairport-sync metadata pipe
                # This is a placeholder - see shairport-sync docs for full parsing
                pass
        except Exception:
            pass

# Start background threads
threading.Thread(target=position_tracker, daemon=True).start()
threading.Thread(target=monitor_airplay, daemon=True).start()

# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET", "POST"])
def config():
    if request.method == "POST":
        data = request.json
        cfg = load_config()
        cfg.update(data)
        save_config(cfg)
        reset_plex()
        return jsonify({"ok": True})
    cfg = load_config()
    # Don't expose token
    safe = {k: v for k, v in cfg.items() if k != "plex_token"}
    safe["configured"] = bool(cfg.get("plex_token"))
    return jsonify(safe)

@app.route("/api/connect", methods=["POST"])
def connect():
    """Connect to Plex via username/password or direct URL+token."""
    data = request.json
    try:
        if data.get("username") and data.get("password"):
            account = MyPlexAccount(data["username"], data["password"])
            resources = account.resources()
            servers = [r for r in resources if r.provides == "server"]
            if not servers:
                return jsonify({"error": "No Plex servers found"}), 400
            # Use first server or let user pick
            resource = servers[0]
            server = resource.connect()
            cfg = load_config()
            cfg["plex_url"] = server._baseurl
            cfg["plex_token"] = account.authenticationToken
            cfg["server_name"] = resource.name
            save_config(cfg)
            reset_plex()
            return jsonify({"ok": True, "server": resource.name, "servers": [s.name for s in servers]})
        elif data.get("url") and data.get("token"):
            plex = PlexServer(data["url"], data["token"])
            cfg = load_config()
            cfg["plex_url"] = data["url"]
            cfg["plex_token"] = data["token"]
            cfg["server_name"] = plex.friendlyName
            save_config(cfg)
            reset_plex()
            return jsonify({"ok": True, "server": plex.friendlyName})
        else:
            return jsonify({"error": "Provide username+password or url+token"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/libraries")
def libraries():
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    try:
        libs = [
            {"key": s.key, "title": s.title, "type": s.type}
            for s in plex.library.sections()
            if s.type == "artist"
        ]
        return jsonify(libs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/library/<key>/artists")
def artists(key):
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    try:
        section = plex.library.sectionByID(int(key))
        cfg = load_config()
        base = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        result = []
        for a in section.all():
            thumb = f"{base}{a.thumb}?X-Plex-Token={token}" if a.thumb else None
            result.append({"key": a.key, "title": a.title, "thumb_url": thumb})
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/artist/<path:key>/albums")
def artist_albums(key):
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    try:
        artist = plex.fetchItem(f"/{key}" if not key.startswith("/") else key)
        cfg = load_config()
        base = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        result = []
        for album in artist.albums():
            thumb = f"{base}{album.thumb}?X-Plex-Token={token}" if album.thumb else None
            result.append({
                "key": album.key,
                "title": album.title,
                "year": album.year,
                "thumb_url": thumb,
                "artist": artist.title,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/album/<path:key>/tracks")
def album_tracks(key):
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    try:
        album = plex.fetchItem(f"/{key}" if not key.startswith("/") else key)
        cfg = load_config()
        base = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        thumb = f"{base}{album.thumb}?X-Plex-Token={token}" if album.thumb else None
        result = []
        for track in album.tracks():
            media = track.media[0] if track.media else None
            stream_key = media.parts[0].key if media and media.parts else None
            result.append({
                "key": track.key,
                "title": track.title,
                "track_number": track.trackNumber,
                "duration": int(track.duration / 1000) if track.duration else 0,
                "artist": track.grandparentTitle,
                "album": track.parentTitle,
                "thumb_url": thumb,
                "stream_key": stream_key,
            })
        return jsonify({"tracks": result, "album_art": thumb, "album_title": album.title})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/search")
def search():
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    query = request.args.get("q", "")
    if not query:
        return jsonify([])
    try:
        cfg = load_config()
        base = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        results = plex.search(query, mediatype="track", limit=30)
        items = []
        for track in results:
            media = track.media[0] if track.media else None
            stream_key = media.parts[0].key if media and media.parts else None
            thumb = f"{base}{track.thumb}?X-Plex-Token={token}" if track.thumb else None
            items.append({
                "key": track.key,
                "title": track.title,
                "artist": track.grandparentTitle,
                "album": track.parentTitle,
                "duration": int(track.duration / 1000) if track.duration else 0,
                "thumb_url": thumb,
                "stream_key": stream_key,
            })
        return jsonify(items)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlists")
def playlists():
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    try:
        cfg = load_config()
        base = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        result = []
        for pl in plex.playlists():
            if pl.playlistType == "audio":
                thumb = f"{base}{pl.composite}?X-Plex-Token={token}" if pl.composite else None
                result.append({
                    "key": pl.key,
                    "title": pl.title,
                    "count": pl.leafCount,
                    "thumb_url": thumb,
                })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<path:key>/tracks")
def playlist_tracks(key):
    plex = get_plex()
    if not plex:
        return jsonify({"error": "Not connected"}), 503
    try:
        pl = plex.fetchItem(f"/{key}" if not key.startswith("/") else key)
        cfg = load_config()
        base = cfg.get("plex_url", "")
        token = cfg.get("plex_token", "")
        result = []
        for track in pl.items():
            media = track.media[0] if track.media else None
            stream_key = media.parts[0].key if media and media.parts else None
            thumb = f"{base}{track.thumb}?X-Plex-Token={token}" if track.thumb else None
            result.append({
                "key": track.key,
                "title": track.title,
                "artist": track.grandparentTitle,
                "album": track.parentTitle,
                "duration": int(track.duration / 1000) if track.duration else 0,
                "thumb_url": thumb,
                "stream_key": stream_key,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Playback Control ─────────────────────────────────────────────────────────

@app.route("/api/player/play", methods=["POST"])
def play():
    data = request.json or {}
    tracks = data.get("tracks", [])
    index = data.get("index", 0)
    if tracks:
        with state_lock:
            player_state["queue"] = tracks
            player_state["queue_index"] = index
        play_queue_item(index)
    else:
        # Resume
        if player_state["status"] == "paused":
            set_mpv_property("pause", False)
            with state_lock:
                player_state["status"] = "playing"
    return jsonify(player_state)

@app.route("/api/player/pause", methods=["POST"])
def pause():
    set_mpv_property("pause", True)
    with state_lock:
        player_state["status"] = "paused"
    return jsonify(player_state)

@app.route("/api/player/stop", methods=["POST"])
def stop():
    stop_mpv()
    with state_lock:
        player_state["status"] = "stopped"
        player_state["position"] = 0
    return jsonify(player_state)

@app.route("/api/player/next", methods=["POST"])
def next_track():
    advance_queue(1)
    return jsonify(player_state)

@app.route("/api/player/prev", methods=["POST"])
def prev_track():
    if player_state["position"] > 3:
        # Restart current track
        mpv_command(["seek", 0, "absolute"])
        with state_lock:
            player_state["position"] = 0
    else:
        advance_queue(-1)
    return jsonify(player_state)

@app.route("/api/player/seek", methods=["POST"])
def seek():
    pos = request.json.get("position", 0)
    mpv_command(["seek", pos, "absolute"])
    with state_lock:
        player_state["position"] = pos
    return jsonify(player_state)

@app.route("/api/player/volume", methods=["POST"])
def volume():
    vol = int(request.json.get("volume", 80))
    vol = max(0, min(100, vol))
    set_mpv_property("volume", vol)
    with state_lock:
        player_state["volume"] = vol
    # Also set system volume
    subprocess.run(["amixer", "sset", "Master", f"{vol}%"], capture_output=True)
    return jsonify(player_state)

@app.route("/api/player/shuffle", methods=["POST"])
def shuffle():
    with state_lock:
        player_state["shuffle"] = not player_state["shuffle"]
        if player_state["shuffle"]:
            import random
            q = player_state["queue"]
            current = player_state["queue_index"]
            current_item = q[current] if q else None
            random.shuffle(q)
            if current_item and current_item in q:
                player_state["queue_index"] = q.index(current_item)
    return jsonify(player_state)

@app.route("/api/player/repeat", methods=["POST"])
def repeat():
    modes = ["none", "all", "one"]
    with state_lock:
        cur = player_state["repeat"]
        player_state["repeat"] = modes[(modes.index(cur) + 1) % len(modes)]
    return jsonify(player_state)

@app.route("/api/player/state")
def state():
    return jsonify(player_state)

@app.route("/api/player/events")
def events():
    """SSE endpoint for real-time state updates."""
    def generate():
        last = None
        while True:
            current = json.dumps(player_state)
            if current != last:
                yield f"data: {current}\n\n"
                last = current
            time.sleep(0.5)
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.route("/api/airplay/status")
def airplay_status():
    """Check if shairport-sync is running."""
    result = subprocess.run(["systemctl", "is-active", "shairport-sync"], capture_output=True)
    active = result.stdout.decode().strip() == "active"
    return jsonify({"active": active, "name": load_config().get("airplay_name", "PlexPi")})

@app.route("/api/proxy/art")
def proxy_art():
    """Proxy album art to avoid CORS issues."""
    url = request.args.get("url")
    if not url:
        return "", 404
    try:
        cfg = load_config()
        token = cfg.get("plex_token", "")
        if "X-Plex-Token" not in url:
            url += f"&X-Plex-Token={token}"
        resp = requests.get(url, stream=True, timeout=5)
        return Response(
            resp.iter_content(chunk_size=8192),
            content_type=resp.headers.get("content-type", "image/jpeg"),
        )
    except Exception as e:
        return str(e), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
