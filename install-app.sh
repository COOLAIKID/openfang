#!/usr/bin/env bash
# Install AutoEarn as a clickable app with an icon (macOS / Linux).
# Run once:   ./install-app.sh
# Then launch it from your Applications / app menu like any other app.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH="$ROOT/app/launch.sh"
chmod +x "$LAUNCH"

OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
  # ---- macOS: build a real .app bundle in ~/Applications ----
  APP="$HOME/Applications/AutoEarn.app"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>AutoEarn</string>
  <key>CFBundleDisplayName</key><string>AutoEarn</string>
  <key>CFBundleIdentifier</key><string>com.autoearn.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>AutoEarn</string>
  <key>CFBundleIconFile</key><string>AutoEarn.icns</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

  cat > "$APP/Contents/MacOS/AutoEarn" <<RUN
#!/bin/bash
exec "$LAUNCH"
RUN
  chmod +x "$APP/Contents/MacOS/AutoEarn"
  cp "$ROOT/app/AutoEarn.icns" "$APP/Contents/Resources/AutoEarn.icns"

  echo "✅ Installed: $APP"
  echo "   Open it from Launchpad / Applications, or run: open \"$APP\""

elif [ "$OS" = "Linux" ]; then
  # ---- Linux: desktop entry + icon in the app menu ----
  APPS="$HOME/.local/share/applications"
  ICONS="$HOME/.local/share/icons/hicolor/512x512/apps"
  mkdir -p "$APPS" "$ICONS"
  cp "$ROOT/app/AutoEarn.png" "$ICONS/autoearn.png"

  cat > "$APPS/autoearn.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=AutoEarn
GenericName=Your AI money machine
Comment=Open your AI team's dashboard
Exec=$LAUNCH
Icon=autoearn
Terminal=false
Categories=Finance;Office;Utility;
StartupNotify=true
DESK
  chmod +x "$APPS/autoearn.desktop"
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
  gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

  echo "✅ Installed AutoEarn to your app menu."
  echo "   Search 'AutoEarn' in your apps and click it."
else
  echo "Unsupported OS: $OS. On Windows, run: powershell -ExecutionPolicy Bypass -File install-app.ps1"
  exit 1
fi

echo "First click sets things up (~1 min), then the dashboard opens automatically."
