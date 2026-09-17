# Opens the dashboard. Makes sure the bridge is up first, using the same
# idempotent check board_watchdog.ps1 runs on its schedule -- so this never
# launches a second board_serve.py instance, and if you're paused for OOO
# it leaves that alone rather than silently un-pausing you: the page just
# opens showing its honest "bridge not connected" state.

$root = "C:\Agent Prospecting Ops\seats-prospecting"
& "$root\scripts\board_watchdog.ps1"
Start-Process "$root\prospecting_control_panel_local.html"
