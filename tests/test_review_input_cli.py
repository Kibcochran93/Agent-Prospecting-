"""--from-log is the only supported way to hand the Reviewer a run log.

The point is that the unsafe path stops being available, not that a safer one
exists beside it. These tests run the CLI as a subprocess and assert it refuses
before any agent is built, so nothing here needs an API key.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

LEAKY = """## Rewritten batch

### Murray State University

**To: Provost**
**Subject:** Reading two different indicators

Confirm the slice at checkpoint 4 before we proceed.
"""

CLEAN = """## Rewritten batch

### Murray State University

**To: Provost**
**Subject:** Reading two different indicators

Retention held at 78% while spring headcount fell 4.3%.
"""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "seats_prospecting.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(SRC), "PATH": "", "SYSTEMROOT": "C:\\Windows"},
    )


def test_a_leaky_log_stops_the_run_before_the_agent_is_built(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(LEAKY, encoding="utf-8")
    proc = _run(["reviewer", "--from-log", str(log)])
    assert proc.returncode == 3, proc.stderr[-800:]
    assert "checkpoint" in proc.stderr.casefold()
    assert "Verdict" not in proc.stdout, "the reviewer must not have run"


def test_from_log_is_refused_on_every_other_agent(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(CLEAN, encoding="utf-8")
    for agent in ("director", "briefing", "campaign"):
        proc = _run([agent, "--from-log", str(log)])
        assert proc.returncode == 2, f"{agent}: {proc.stderr[-400:]}"


def test_the_reviewer_still_refuses_a_session_with_from_log(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(CLEAN, encoding="utf-8")
    proc = _run(["reviewer", "--from-log", str(log), "--session", "s1"])
    assert proc.returncode == 2
    assert "grades against drift" in proc.stderr


def test_the_builder_entry_point_writes_the_artifact_to_stdout(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(CLEAN, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "seats_prospecting.review_input", str(log)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(SRC), "PATH": "", "SYSTEMROOT": "C:\\Windows"},
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "--- ARTIFACT BEGINS ---" in proc.stdout
    assert "Reading two different indicators" in proc.stdout
    assert "kept:" in proc.stderr


def test_the_out_flag_writes_utf8_so_no_one_has_to_redirect(tmp_path):
    """PowerShell's '>' writes UTF-16. The Reviewer's stdin is UTF-8, and this
    project has already lost a run to exactly that mismatch."""
    log = tmp_path / "run.log"
    log.write_text(CLEAN.replace("78%", "78% \u2014 Kib\u2019s note"), encoding="utf-8")
    out = tmp_path / "artifact.txt"
    proc = subprocess.run(
        [sys.executable, "-m", "seats_prospecting.review_input", str(log), "-o", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(SRC), "PATH": "", "SYSTEMROOT": "C:\\Windows"},
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    raw = out.read_bytes()
    assert not raw.startswith(b"\xff\xfe") and not raw.startswith(b"\xfe\xff"), "UTF-16 BOM"
    assert "Kib\u2019s note" in raw.decode("utf-8")
