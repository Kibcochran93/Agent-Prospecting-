# Pause the board server for out-of-office. Stops it now if it's running,
# and the OOO marker keeps the scheduled watchdog from starting it back up
# until resume_board.ps1 removes it.
$root = "C:\Agent Prospecting Ops\seats-prospecting"
New-Item -Path "$root\OOO" -ItemType File -Force | Out-Null
$existingPid = (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue).OwningProcess
if ($existingPid) { Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue }
Write-Host "Board server paused. Run resume_board.ps1 to bring it back."
