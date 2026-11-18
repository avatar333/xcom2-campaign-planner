"""UI-logic tests for the research queue ordering (no browser harness exists
in this project, so the pure logic runs under Node; skipped without Node) and
a syntax check of the graph renderer."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_queue_ordering_logic():
    result = subprocess.run(["node", str(ROOT / "js" / "research_queue.test.js")], check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "OK" in result.stdout


@needs_node
@pytest.mark.parametrize("name", ["research_graph.js", "research_queue.js", "avenger.js"])
def test_scripts_parse(name):
    script = ROOT.parent / "app" / "static" / "js" / name
    result = subprocess.run(["node", "--check", str(script)], check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
