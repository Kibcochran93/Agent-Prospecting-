# Keeps board_serve.py up on weekdays, and self-heals if it dies mid-day --
# it has before (see HANDOFF.md). Meant to be triggered by Task Scheduler on
# a repeating schedule, not run by hand. Idempotent: safe to run every 15
# minutes without ever producing a second instance.
#
# Pause for out-of-office: create a file named OOO in the project root (see
# pause_board.ps1 / resume_board.ps1). Its presence stops the server if it's
# running and skips starting it again -- the same shape as queue/PAUSED for
# the job queue, just one level up, for the server itself.

$root = "C:\Agent Prospecting Ops\seats-prospecting"
Set-Location $root

function Get-BoardPid {
    (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue).OwningProcess
}

if (Test-Path "$root\OOO") {
    $existingPid = Get-BoardPid
    if ($existingPid) {
        Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

if (Get-BoardPid) {
    exit 0  # already up, nothing to do
}

Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "scripts\board_serve.py" `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$root\queue\logs\board_serve.out.log" `
    -RedirectStandardError "$root\queue\logs\board_serve.err.log"
