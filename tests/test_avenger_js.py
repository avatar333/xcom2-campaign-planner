"""UI-logic test for the configure-mode draft editor (app/static/js/avenger.js).

The project has no browser test harness, so the Alpine component is exercised
under Node with a stubbed DOM. Skipped when Node is not installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "js" / "avenger_draft.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_configure_draft_editor_behaviour():
    result = subprocess.run(["node", str(SCRIPT)], check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "OK" in result.stdout
