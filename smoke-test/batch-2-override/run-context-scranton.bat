@echo off
cd /d "C:\Agent Prospecting Ops\seats-prospecting"
".venv\Scripts\python.exe" scripts\live_run.py context -f "smoke-test\batch-2-override\in-context-scranton.txt" -o "smoke-test\batch-2-override\context-scranton.log"
