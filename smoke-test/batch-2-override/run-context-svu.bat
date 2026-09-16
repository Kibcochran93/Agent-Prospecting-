@echo off
cd /d "C:\Agent Prospecting Ops\seats-prospecting"
".venv\Scripts\python.exe" scripts\live_run.py context -f "smoke-test\batch-2-override\in-context-svu.txt" -o "smoke-test\batch-2-override\context-svu.log"
