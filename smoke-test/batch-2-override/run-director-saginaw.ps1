Set-Location 'C:\Agent Prospecting Ops\seats-prospecting'
$dir = 'smoke-test\batch-2-override'
$verdict = 'ledger-outbox\20260909T163706Z-context-conflict-veronica-d-wilson-ph-d.json'
$args = @('scripts\live_run.py','director','-f',"$dir\in-director-saginaw.txt",'--verdict',$verdict,'--override','Kib override, prospecting research only','-o',"$dir\director-saginaw.log")
Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList $args -RedirectStandardError "$dir\director-saginaw.err" -RedirectStandardOutput "$dir\director-saginaw.stdout" -NoNewWindow
