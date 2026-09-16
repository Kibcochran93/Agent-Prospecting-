@echo off
cd /d "C:\Agent Prospecting Ops\seats-prospecting"
".venv\Scripts\python.exe" scripts\live_run.py director -f "smoke-test\batch-2-override\in-director-svu.txt" --verdict "ledger-outbox\20260909T165000Z-context-conflict-michael-frye.json" --override "Kib override, prospecting research only" -o "smoke-test\batch-2-override\director-svu.log"
