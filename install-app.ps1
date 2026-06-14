# Install AutoEarn as a clickable app with an icon (Windows).
# Run once in PowerShell:
#   powershell -ExecutionPolicy Bypass -File install-app.ps1
# Then launch it from the Start Menu or the desktop shortcut.

$ErrorActionPreference = "Stop"
$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launch = Join-Path $Root "app\launch.bat"
$Icon   = Join-Path $Root "app\AutoEarn.ico"

function New-Shortcut($Path) {
  $ws = New-Object -ComObject WScript.Shell
  $sc = $ws.CreateShortcut($Path)
  $sc.TargetPath       = "cmd.exe"
  $sc.Arguments        = "/c `"$Launch`""
  $sc.WorkingDirectory = $Root
  $sc.IconLocation     = $Icon
  $sc.Description       = "AutoEarn - your AI money machine"
  $sc.WindowStyle      = 7   # minimized
  $sc.Save()
}

# Desktop shortcut
$desktop = [Environment]::GetFolderPath("Desktop")
New-Shortcut (Join-Path $desktop "AutoEarn.lnk")

# Start Menu shortcut
$startMenu = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Microsoft\Windows\Start Menu\Programs"
New-Shortcut (Join-Path $startMenu "AutoEarn.lnk")

Write-Host "Installed AutoEarn. Look for the AutoEarn icon on your Desktop and Start Menu."
Write-Host "First click sets things up (~1 min), then the dashboard opens automatically."
