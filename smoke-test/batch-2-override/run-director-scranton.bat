@echo off
cd /d "C:\Agent Prospecting Ops\seats-prospecting"
".venv\Scripts\python.exe" scripts\live_run.py director -f "smoke-test\batch-2-override\in-director-scranton.txt" --verdict "ledger-outbox\20260909T165849Z-context-conflict-jason-schwass.json" --override "Kib override, prospecting research only" -o "smoke-test\batch-2-override\director-scranton.log"
