# Resume the board server after pause_board.ps1. Removes the OOO marker
# and starts it right away rather than waiting for the next scheduled check.
$root = "C:\Agent Prospecting Ops\seats-prospecting"
Remove-Item -Path "$root\OOO" -ErrorAction SilentlyContinue
& "$root\scripts\board_watchdog.ps1"
Write-Host "Board server resumed."
