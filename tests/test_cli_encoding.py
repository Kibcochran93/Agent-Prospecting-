"""The Reviewer's input path must survive the characters the agents actually emit.

The Reviewer takes a pasted artifact on stdin and nothing else. Every artifact
this system produces contains curly quotes, because the models write them. On
Windows, stdin defaults to cp1252 with surrogateescape, so those bytes decode to
lone surrogates and the OpenAI SDK then fails to serialize the request:

    UnicodeEncodeError: 'utf-8' codec can't encode character '\\udc9d'

That is not a cosmetic problem. It meant the Reviewer crashed on every real
artifact it was ever handed, which is why it had never produced a review. These
tests assert the reconfiguration is in place and that a realistic artifact
round-trips.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "seats_prospecting" / "cli.py"

# The exact shapes that broke it: curly apostrophe, curly double quotes, en and
# em dash, ellipsis, non-breaking space.
NASTY = "Don’t “quote” me – or — me… or me."


def test_cli_reconfigures_stdin_not_only_the_output_streams():
    source = CLI.read_text(encoding="utf-8")
    assert "sys.stdin, sys.stdout, sys.stderr" in source, (
        "cli.py reconfigures the output streams but not stdin. The Reviewer reads "
        "its artifact from stdin, so leaving it on cp1252 breaks the only agent "
        "whose input is pasted text."
    )


def test_the_reconfigure_failure_is_swallowed_not_raised():
    """A pytest capture object has no reconfigure. Startup must not depend on it."""
    source = CLI.read_text(encoding="utf-8")
    assert 'hasattr(_stream, "reconfigure")' in source
    assert "except (ValueError, OSError):" in source


def test_a_realistic_artifact_survives_a_utf8_stdin_round_trip():
    """Decoding under UTF-8 keeps the characters; decoding under cp1252 does not."""
    raw = NASTY.encode("utf-8")

    good = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace").read()
    assert good == NASTY
    good.encode("utf-8")  # would raise if surrogates had leaked in

    bad = io.TextIOWrapper(
        io.BytesIO(raw), encoding="cp1252", errors="surrogateescape"
    ).read()
    import pytest

    with pytest.raises(UnicodeEncodeError):
        bad.encode("utf-8")


def test_the_cli_reads_curly_quotes_from_stdin_without_crashing(tmp_path):
    """End to end, with a stub that stops before any network call.

    Runs the real cli module far enough to read stdin and echo what it decoded.
    A cp1252 stdin fails here; a UTF-8 stdin does not.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "sys.path.insert(0, r'%s')\n" % (ROOT / "src")
        + "import seats_prospecting.cli as cli\n"
        "text = sys.stdin.read()\n"
        "text.encode('utf-8')\n"
        "sys.stdout.write('len=%d' % len(text))\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "artifact.txt"
    artifact.write_bytes(NASTY.encode("utf-8"))

    with artifact.open("rb") as stdin:
        proc = subprocess.run(
            [sys.executable, str(probe)],
            stdin=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert proc.returncode == 0, (
        f"the cli's stdin handling still cannot read a normal artifact:\n"
        f"{proc.stderr[-1500:]}"
    )
    assert "len=" in proc.stdout
