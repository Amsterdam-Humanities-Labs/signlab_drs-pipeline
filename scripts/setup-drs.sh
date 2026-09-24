#!/bin/bash
# Set up (or check) a DRS studio Mac: the Mac the Sony FX30 cameras are
# connected to. It runs the camera server (fx30MultiRecord), the camera
# controller app and this video pipeline.
#
# Dry run by default: it only prints what it would do. Add --apply to do it.
# Safe to run again: every step checks first and skips what is already done.
#
#   scripts/setup-drs.sh                          # check only (dry run)
#   scripts/setup-drs.sh --apply --server https://signcollect.nl --rclone
#   VIDEOFIX_TOKEN=... scripts/setup-drs.sh --apply   # also store the videoFix token
#
# What it cannot do (it prints these at the end): install DaVinci Resolve
# Studio, enable Resolve scripting, create the Resolve project, set up the
# cameras, grant macOS privacy permissions, log in to Tailscale.

set -u

APPLY=0
SERVER_URL=""
WANT_RCLONE=0
CAMERAS=3
DISPLAY_SCREEN=""
SKIP_BUILD=0
INSTALL_AGENT=1
# Token for videoFix/api.php. Taken from the environment, not an option, so it
# does not end up in the shell history or the process list.
NEW_VIDEOFIX_TOKEN="${VIDEOFIX_TOKEN:-}"

DRS_USER="signlab"
DRS_HOME="/Users/$DRS_USER"
REPO_DIR="$DRS_HOME/drs"                  # paths in startupScript.py are fixed to this
SDK_DIR="$DRS_HOME/signlab_Sony-SDK-MACOS-API"
SDK_REPO="https://github.com/Amsterdam-Humanities-Labs/signlab_Sony-SDK-MACOS-API.git"
CACHE_DISK="/Volumes/cacheDisk"
STAGING_DIR="$CACHE_DISK/fx30_staging"
RCLONE_DIR="$DRS_HOME/rclone"
RCLONE_BIN="$RCLONE_DIR/rclone"
MOUNT_DIR="$DRS_HOME/signCollect"
PROJECT_FOLDER="AIHR-FGW-TEST-SIGNLAB (Projectfolder)"
BREW="/opt/homebrew/bin/brew"
BREW_PY="/opt/homebrew/opt/python@3.13/bin/python3.13"
SYS_PY="/usr/bin/python3"
AGENT_LABEL="nl.signcollect.drs-startup"
AGENT_PLIST="$DRS_HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
SUDOERS_FILE="/etc/sudoers.d/signlab-network"

usage() {
    cat <<'EOF'
Usage: scripts/setup-drs.sh [options]

  --apply               Make the changes. Without it the script only checks
                        and prints what it would do (same as --dry-run).
  --dry-run             Check only (the default).
  --server URL          SignCollect server to upload to, for example
                        https://signcollect.nl. Written to .env as
                        SIGNCOLLECT_URL. Default: keep what .env has, or
                        https://signcollect.nl.
  --rclone              Set up the rclone remote "signcollect:" for the
                        research drive (runs the interactive `rclone config`).
  --cameras N           Number of FX30 cameras in the studio (default 3).
  --display-screen NAME Part of the macOS name of the screen that shows the
                        QR code, for the camera controller app (config.json).
  --sdk-dir DIR         Where to clone signlab_Sony-SDK-MACOS-API
                        (default /Users/signlab/signlab_Sony-SDK-MACOS-API).
  --skip-build          Do not build fx30MultiRecord.
  --no-launch-agent     Do not install the login item that starts the pipeline.
  -h, --help            Show this help.

Environment:
  VIDEOFIX_TOKEN        Token for videoFix/api.php (services/crop_fix.py).
                        Written to .env. Same value as VIDEOFIX_TOKEN in the
                        server's <webroot>/.env. Default: keep what .env has.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --dry-run) APPLY=0 ;;
        --server) SERVER_URL="${2:-}"; shift ;;
        --rclone) WANT_RCLONE=1 ;;
        --cameras) CAMERAS="${2:-}"; shift ;;
        --display-screen) DISPLAY_SCREEN="${2:-}"; shift ;;
        --sdk-dir) SDK_DIR="${2:-}"; shift ;;
        --skip-build) SKIP_BUILD=1 ;;
        --no-launch-agent) INSTALL_AGENT=0 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$CAMERAS" in
    ''|*[!0-9]*) echo "--cameras needs a number" >&2; exit 2 ;;
esac
if [ -n "$SERVER_URL" ]; then
    case "$SERVER_URL" in
        https://*|http://*) SERVER_URL="${SERVER_URL%/}" ;;
        *) echo "--server must start with https:// or http://" >&2; exit 2 ;;
    esac
fi

# ------------------------------------------------------------------ helpers

WARNINGS=""
MANUAL=""

say()     { printf '\n== %s\n' "$*"; }
ok()      { printf '   ok    %s\n' "$*"; }
todo()    { printf '   todo  %s\n' "$*"; }
warn()    { printf '   WARN  %s\n' "$*"; WARNINGS="$WARNINGS\n - $*"; }
manual()  { MANUAL="$MANUAL\n - $*"; }

# run CMD...: print it, and run it only with --apply.
run() {
    if [ "$APPLY" -eq 1 ]; then
        printf '   run   %s\n' "$*"
        "$@"
        local rc=$?
        [ $rc -eq 0 ] || warn "command failed ($rc): $*"
        return $rc
    fi
    printf '   would %s\n' "$*"
    return 0
}

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ 1. system

check_system() {
    say "System"
    if [ "$(uname -s)" != "Darwin" ]; then
        echo "   This script is for macOS only." >&2
        exit 1
    fi
    local ver major minor
    ver="$(sw_vers -productVersion)"
    major="${ver%%.*}"
    minor="$(echo "$ver" | cut -d. -f2)"
    if [ "$major" -gt 12 ] || { [ "$major" -eq 12 ] && [ "${minor:-0}" -ge 1 ]; }; then
        ok "macOS $ver (fx30MultiRecord needs 12.1 or later)"
    else
        warn "macOS $ver is older than 12.1, which fx30MultiRecord needs"
    fi

    if [ "$(uname -m)" = "arm64" ]; then
        ok "Apple Silicon (arm64)"
    else
        warn "Not Apple Silicon: the services call /opt/homebrew/bin/ffmpeg, the Apple Silicon Homebrew path"
    fi

    if [ "$(id -un)" = "$DRS_USER" ]; then
        ok "running as $DRS_USER"
    else
        warn "running as $(id -un), not $DRS_USER: the pipeline paths are fixed to $DRS_HOME"
    fi

    local here
    here="$(cd "$(dirname "$0")/.." && pwd)"
    if [ "$here" = "$REPO_DIR" ]; then
        ok "checkout is $REPO_DIR"
    else
        warn "this checkout is $here; startupScript.py expects $REPO_DIR"
    fi

    if xcode-select -p >/dev/null 2>&1 && xcodebuild -version >/dev/null 2>&1; then
        ok "Xcode: $(xcodebuild -version | head -1)"
    else
        warn "Xcode (the full app, not only the command line tools) is missing; fx30MultiRecord is built with it"
        manual "Install Xcode from the App Store, open it once, then run: sudo xcode-select -s /Applications/Xcode.app"
    fi

    if [ -x "$SYS_PY" ] && "$SYS_PY" -c 'import sys' >/dev/null 2>&1; then
        ok "$SYS_PY ($("$SYS_PY" -V 2>&1))"
    else
        warn "$SYS_PY does not work; install the command line tools: xcode-select --install"
    fi
}

# ------------------------------------------------------------------ 2. homebrew

BREW_FORMULAE="cmake autoconf automake libtool ffmpeg python@3.13 zbar"

check_brew() {
    say "Homebrew and packages"
    if [ ! -x "$BREW" ]; then
        warn "Homebrew is not installed at $BREW"
        manual "Install Homebrew (https://brew.sh), then run this script again"
        return
    fi
    ok "Homebrew $("$BREW" --version | head -1)"
    local f
    for f in $BREW_FORMULAE; do
        if "$BREW" list --formula "$f" >/dev/null 2>&1; then
            ok "$f"
        else
            todo "$f is missing"
            run "$BREW" install "$f"
        fi
    done
}

# ------------------------------------------------------------------ 3. apps

check_apps() {
    say "Apps"
    local resolve="/Applications/DaVinci Resolve/DaVinci Resolve.app"
    local modules="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py"
    if [ -d "$resolve" ]; then
        if grep -qi "studio" "$resolve/Contents/Info.plist" 2>/dev/null; then
            ok "DaVinci Resolve Studio"
        else
            warn "DaVinci Resolve is installed, but this could not confirm it is the Studio (paid) edition. The pipeline needs Studio for scripting"
        fi
    else
        warn "DaVinci Resolve Studio is not installed"
        manual "Buy and install DaVinci Resolve Studio (the paid edition) from blackmagicdesign.com; this script cannot install it"
    fi
    if [ -f "$modules" ]; then
        ok "Resolve scripting module found"
    else
        warn "Resolve scripting module missing: $modules"
    fi
    manual "In DaVinci Resolve: Preferences > System > General > External scripting using: Local. Restart Resolve"
    manual "In DaVinci Resolve: create a project named lala6 (services/batch_queue.py loads it by that name). Copy its settings from the current DRS; config/Settings.setting renders 2160x3840"

    if [ -d "/Applications/Tailscale.app" ]; then
        ok "Tailscale"
    else
        warn "Tailscale is not installed; the server reaches the camera server over Tailscale"
        run "$BREW" install --cask tailscale
    fi
    manual "Log in to Tailscale on the same tailnet as the SignCollect server; note the Mac's tailnet name for fx30proxy.php (signlab_camera-control)"

    if [ -d "/Applications/Google Chrome.app" ]; then
        ok "Google Chrome"
    else
        warn "Google Chrome is not installed; the controller opens Camera Control and the QR screen in Chrome"
        run "$BREW" install --cask google-chrome
    fi
}

# ------------------------------------------------------------------ 4. python

check_python() {
    say "Python packages"
    local req_svc="$REPO_DIR/requirements-services.txt"
    local req_qr="$REPO_DIR/requirements-qr.txt"

    if "$SYS_PY" -c 'import requests, cv2, mediapipe, numpy, pyautogui, pynput, websocket, psutil' >/dev/null 2>&1; then
        ok "service packages for $SYS_PY"
    else
        todo "service packages for $SYS_PY"
        run "$SYS_PY" -m pip install --user -r "$req_svc"
    fi

    if [ -x "$REPO_DIR/bin/python3" ] && [ -f "$REPO_DIR/pyvenv.cfg" ] \
        && "$REPO_DIR/bin/python3" -c 'import qreader, pyzbar, cv2, requests' >/dev/null 2>&1; then
        ok "QR scanner venv ($REPO_DIR/bin/python3)"
    else
        todo "QR scanner venv in $REPO_DIR (Homebrew python@3.13)"
        run "$BREW_PY" -m venv "$REPO_DIR"
        run "$REPO_DIR/bin/pip" install -r "$req_qr"
    fi

    # startupScript.py starts qr/qr_scanner_service.py; qr/ is not in git.
    if [ -f "$REPO_DIR/qr/qr_scanner_service.py" ]; then
        ok "qr/ exists"
    else
        todo "qr/ is missing: copy it from qr_scanner/"
        run cp -R "$REPO_DIR/qr_scanner" "$REPO_DIR/qr"
    fi
}

# ------------------------------------------------------------------ 5. folders

check_folders() {
    say "Folders"
    local d
    for d in "$REPO_DIR/logs" "$REPO_DIR/import" "$REPO_DIR/export" "$REPO_DIR/temp"; do
        if [ -d "$d" ]; then ok "$d"; else todo "$d"; run mkdir -p "$d"; fi
    done
    if [ -d "$CACHE_DISK" ]; then
        ok "$CACHE_DISK is mounted ($(df -h "$CACHE_DISK" | awk 'NR==2 {print $4}') free)"
        for d in "$STAGING_DIR/inbox" "$CACHE_DISK/rclone"; do
            if [ -d "$d" ]; then ok "$d"; else todo "$d"; run mkdir -p "$d"; fi
        done
    else
        warn "$CACHE_DISK is not mounted. Name the external disk for camera downloads and the rclone cache 'cacheDisk'"
    fi
}

# ------------------------------------------------------------------ 6. .env

set_env_key() {  # set_env_key FILE KEY VALUE: replace or append KEY=VALUE
    local file="$1" key="$2" value="$3" tmp
    if grep -q "^$key=" "$file" 2>/dev/null; then
        tmp="$(mktemp)"
        awk -v k="$key" -v v="$value" 'index($0, k"=")==1 {print k"="v; next} {print}' "$file" > "$tmp" \
            && cat "$tmp" > "$file"
        rm -f "$tmp"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

check_env() {
    say "Configuration (.env)"
    local env="$REPO_DIR/.env" current=""
    if [ -f "$env" ]; then
        ok ".env exists"
        current="$(grep '^SIGNCOLLECT_URL=' "$env" | tail -1 | cut -d= -f2-)"
    else
        todo "create .env from .env.example (mode 600)"
        run cp "$REPO_DIR/.env.example" "$env"
        run chmod 600 "$env"
    fi
    local want="${SERVER_URL:-${current:-https://signcollect.nl}}"
    if [ "$current" = "$want" ]; then
        ok "SIGNCOLLECT_URL=$want"
    else
        todo "SIGNCOLLECT_URL=$want"
        if [ "$APPLY" -eq 1 ]; then
            set_env_key "$env" SIGNCOLLECT_URL "$want"
            printf '   run   set SIGNCOLLECT_URL in %s\n' "$env"
        else
            printf '   would set SIGNCOLLECT_URL=%s in %s\n' "$want" "$env"
        fi
    fi
    SERVER_URL="$want"

    # The value is never printed.
    local token=""
    [ -f "$env" ] && token="$(grep '^VIDEOFIX_TOKEN=' "$env" | tail -1 | cut -d= -f2-)"
    if [ -n "$NEW_VIDEOFIX_TOKEN" ] && [ "$NEW_VIDEOFIX_TOKEN" != "$token" ]; then
        todo "VIDEOFIX_TOKEN (from the environment)"
        if [ "$APPLY" -eq 1 ]; then
            set_env_key "$env" VIDEOFIX_TOKEN "$NEW_VIDEOFIX_TOKEN"
            printf '   run   set VIDEOFIX_TOKEN in %s\n' "$env"
        else
            printf '   would set VIDEOFIX_TOKEN in %s\n' "$env"
        fi
    elif [ -n "$token" ]; then
        ok "VIDEOFIX_TOKEN is set"
    else
        warn "VIDEOFIX_TOKEN is not set: services/crop_fix.py gets 401 from videoFix/api.php"
        manual "Set VIDEOFIX_TOKEN in $env to the value of VIDEOFIX_TOKEN in the server's <webroot>/.env, or run again with VIDEOFIX_TOKEN=... in the environment"
    fi

    if [ -f "$env" ] && grep -q '^DB_PASSWORD=$' "$env"; then
        manual "Fill in DB_PASSWORD in $env if you use services/qrConvert.py or tools/check_studiofiles.py (never commit it)"
    fi
}

# ------------------------------------------------------------------ 7. Sony SDK

check_sdk() {
    say "Sony camera server (signlab_Sony-SDK-MACOS-API)"
    if [ -d "$SDK_DIR/.git" ]; then
        ok "clone at $SDK_DIR"
        run git -C "$SDK_DIR" pull --ff-only
    else
        todo "clone $SDK_REPO"
        run git clone "$SDK_REPO" "$SDK_DIR"
    fi

    local build="$SDK_DIR/simpleCli/build"
    local bin="$build/Release/fx30MultiRecord"
    if [ "$SKIP_BUILD" -eq 1 ]; then
        todo "build skipped (--skip-build)"
    else
        # As in the README: cmake -GXcode, then build the fx30MultiRecord target.
        [ -d "$build" ] || run mkdir -p "$build"
        if [ ! -f "$build/CMakeCache.txt" ]; then
            run /opt/homebrew/bin/cmake -S "$SDK_DIR/simpleCli" -B "$build" -GXcode
        fi
        run /opt/homebrew/bin/cmake --build "$build" --config Release --target fx30MultiRecord
    fi
    if [ -x "$bin" ]; then ok "$bin"; else todo "$bin not built yet"; fi

    # Camera controller app (PyQt6), which starts fx30MultiRecord itself.
    local ctl="$SDK_DIR/pyqtController"
    if [ -x "$ctl/venv/bin/python" ] && "$ctl/venv/bin/python" -c 'import PyQt6' >/dev/null 2>&1; then
        ok "controller venv"
    else
        todo "controller venv with PyQt6"
        run "$BREW_PY" -m venv "$ctl/venv"
        run "$ctl/venv/bin/pip" install PyQt6
    fi

    if [ -f "$ctl/config.json" ]; then
        ok "controller config.json exists (not changed)"
    else
        todo "write $ctl/config.json"
        local screen="${DISPLAY_SCREEN:-<display_screen>}"
        if [ "$APPLY" -eq 1 ]; then
            cat > "$ctl/config.json" <<EOF
{
  "api_url": "http://localhost:8080",
  "server_binary": "$bin",
  "server_port": 8080,
  "studio_root": "$MOUNT_DIR/$PROJECT_FOLDER/studioFiles",
  "staging_dir": "$STAGING_DIR",
  "rclone_binary": "$RCLONE_BIN",
  "rclone_remote_root": "signcollect:$PROJECT_FOLDER/studioFiles",
  "download_subdir": "raw",
  "poll_interval_ms": 1500,
  "expected_cameras": $CAMERAS,
  "studio_url": "$SERVER_URL/studio_beta/opnameView.html",
  "display_url": "$SERVER_URL/opnameLR.html",
  "display_screen": "$screen"
}
EOF
            printf '   run   wrote %s\n' "$ctl/config.json"
        else
            printf '   would write config.json: %s cameras, server %s, QR screen "%s"\n' "$CAMERAS" "$SERVER_URL" "$screen"
        fi
        [ -n "$DISPLAY_SCREEN" ] || manual "Set display_screen in $ctl/config.json to part of the QR screen's name (System Settings > Displays)"
    fi
    manual "Camera Control (signlab_camera-control opnameView.html) lists the studio's camera serials in CAMERA_MAP and cameraArray; edit them to match these cameras (serials: curl -s localhost:8080/api/status)"
}

# ------------------------------------------------------------------ 8. rclone

check_rclone() {
    say "rclone (research drive)"
    if [ "$WANT_RCLONE" -eq 0 ]; then
        if [ -x "$RCLONE_BIN" ] && "$RCLONE_BIN" listremotes 2>/dev/null | grep -qx 'signcollect:'; then
            ok "remote signcollect: exists"
        else
            warn "no rclone remote 'signcollect:'. startupScript.py still starts the rclone mount, and all pipeline paths are under $MOUNT_DIR. Run again with --rclone, or change startupScript.py"
        fi
        return
    fi
    if [ -x "$RCLONE_BIN" ]; then
        ok "$RCLONE_BIN ($("$RCLONE_BIN" version 2>/dev/null | head -1))"
    else
        # Homebrew's rclone cannot mount on macOS, so use the official build.
        local arch="arm64" zip="/tmp/rclone-osx.zip"
        [ "$(uname -m)" = "arm64" ] || arch="amd64"
        todo "install the official rclone build in $RCLONE_DIR"
        run curl -fsSL -o "$zip" "https://downloads.rclone.org/rclone-current-osx-$arch.zip"
        run mkdir -p "$RCLONE_DIR"
        run /usr/bin/unzip -o -j "$zip" "*/rclone" -d "$RCLONE_DIR"
        run chmod 755 "$RCLONE_BIN"
    fi
    if [ -d "/Library/Filesystems/macfuse.fs" ]; then
        ok "macFUSE"
    else
        warn "macFUSE is not installed; rclone mount needs it"
        manual "Install macFUSE (https://osxfuse.github.io) and allow its system extension in System Settings > Privacy & Security"
    fi
    if [ -x "$RCLONE_BIN" ] && "$RCLONE_BIN" listremotes 2>/dev/null | grep -qx 'signcollect:'; then
        ok "remote signcollect: exists"
    else
        todo "create the remote 'signcollect:' (interactive)"
        if [ "$APPLY" -eq 1 ]; then
            echo "   Name the remote exactly: signcollect"
            echo "   For a research drive this is usually type 'webdav' with the drive's URL and an app password."
            echo "   The password is stored in ~/.config/rclone/rclone.conf of $DRS_USER, never in git."
            "$RCLONE_BIN" config
        else
            printf '   would run %s config (you answer the questions)\n' "$RCLONE_BIN"
        fi
    fi
    if [ -d "$MOUNT_DIR" ]; then
        ok "mount point $MOUNT_DIR"
    else
        todo "mount point $MOUNT_DIR"
        run mkdir -p "$MOUNT_DIR"
    fi
    manual "On the research drive, create '$PROJECT_FOLDER/studioFiles' and the marker file '$PROJECT_FOLDER/do_not_remove_for_rclone' (startupScript.py checks the mount with it)"
}

# ------------------------------------------------------------------ 9. sudoers

check_sudoers() {
    say "Passwordless sudo for services/network_manager.py"
    local iface="en0"
    local ts="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    local rule="$DRS_USER ALL=(root) NOPASSWD: /sbin/ifconfig $iface up, /sbin/ifconfig $iface down, $ts up, $ts down"
    if [ -f "$SUDOERS_FILE" ]; then
        ok "$SUDOERS_FILE exists"
        return
    fi
    todo "write $SUDOERS_FILE (asks for your password)"
    if [ "$APPLY" -eq 1 ]; then
        local tmp
        tmp="$(mktemp)"
        printf '%s\n' "$rule" > "$tmp"
        if /usr/sbin/visudo -cf "$tmp" >/dev/null; then
            run sudo install -m 0440 -o root -g wheel "$tmp" "$SUDOERS_FILE"
        else
            warn "sudoers rule did not validate; not installed"
        fi
        rm -f "$tmp"
    else
        printf '   would install: %s\n' "$rule"
    fi
}

# ------------------------------------------------------------------ 10. launch agent

check_agent() {
    say "Start the pipeline at login (LaunchAgent)"
    if [ "$INSTALL_AGENT" -eq 0 ]; then
        todo "skipped (--no-launch-agent). Start by hand: cd $REPO_DIR && /usr/bin/python3 startupScript.py"
        return
    fi
    # Starts startupScript.py once per login, unless it already runs.
    # No KeepAlive: startupScript.py starts scripts/watchdog.sh, which restarts it.
    local cmd="cd $REPO_DIR && if [ -f startup.pid ] && kill -0 \$(cat startup.pid) 2>/dev/null; then exit 0; fi; exec /usr/bin/python3 $REPO_DIR/startupScript.py >/dev/null 2>&1"
    local plist
    plist="$(cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>-c</string><string>$cmd</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
</dict>
</plist>
EOF
)"
    if [ -f "$AGENT_PLIST" ] && [ "$(cat "$AGENT_PLIST")" = "$plist" ]; then
        ok "$AGENT_PLIST is up to date"
    else
        todo "write $AGENT_PLIST"
        if [ "$APPLY" -eq 1 ]; then
            mkdir -p "$(dirname "$AGENT_PLIST")"
            printf '%s\n' "$plist" > "$AGENT_PLIST"
            printf '   run   wrote %s\n' "$AGENT_PLIST"
            launchctl bootout "gui/$(id -u)/$AGENT_LABEL" >/dev/null 2>&1
        fi
    fi
    if launchctl print "gui/$(id -u)/$AGENT_LABEL" >/dev/null 2>&1; then
        ok "LaunchAgent is loaded"
    else
        run launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST"
    fi
}

# ------------------------------------------------------------------ main

if [ "$APPLY" -eq 1 ]; then
    echo "setup-drs: APPLY mode, changes will be made."
else
    echo "setup-drs: dry run, nothing is changed. Add --apply to make the changes."
fi

check_system
check_brew
check_apps
check_python
check_folders
check_env
check_sdk
check_rclone
check_sudoers
check_agent

manual "macOS: give Terminal/python3 Accessibility and Input Monitoring (mouse.py, keyboard_monitor.py) and allow Automation of System Events (batch_queue.py hides Resolve)"
manual "macOS: System Settings > Energy: never sleep. Enable Remote Login (ssh) if the server must write capture logs to this Mac (fx30capturelog.php)"
manual "Cameras: USB Connection Mode 'Remote Shoot (PC Remote)'; clip names starting with L, M or R (and A, B for extra cameras). See the docs: Recording studio > Install the DRS Mac"

say "Summary"
if [ -n "$WARNINGS" ]; then
    printf 'Warnings:%b\n' "$WARNINGS"
fi
printf '\nManual steps this script cannot do:%b\n' "$MANUAL"
if [ "$APPLY" -eq 0 ]; then
    printf '\nThis was a dry run. Run again with --apply to make the changes.\n'
fi
