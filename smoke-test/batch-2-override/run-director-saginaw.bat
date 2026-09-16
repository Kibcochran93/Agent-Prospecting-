@echo off
cd /d "C:\Agent Prospecting Ops\seats-prospecting"
".venv\Scripts\python.exe" scripts\live_run.py director -f "smoke-test\batch-2-override\in-director-saginaw.txt" --verdict "ledger-outbox\20260909T163706Z-context-conflict-veronica-d-wilson-ph-d.json" --override "Kib override, prospecting research only" -o "smoke-test\batch-2-override\director-saginaw.log"
