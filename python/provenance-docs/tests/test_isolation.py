"""`import dfo_provenance_docs` loads nothing beyond markdown-it-py and the
standard library.

Checked against `sys.modules` in a fresh interpreter, so a transitive import
pulled in by a dependency counts, and modules the test process has already
imported do not.
"""

from __future__ import annotations

import subprocess
import sys

_SCRIPT = """
import sys
before = frozenset(sys.modules)
import dfo_provenance_docs
allowed = {"dfo_provenance_docs", "markdown_it", "mdurl"}
leaked = sorted(
    {name.split(".")[0] for name in sys.modules if name not in before}
    - allowed
    - set(sys.stdlib_module_names)
)
if leaked:
    raise SystemExit("leaked modules: " + ", ".join(leaked))
print("isolation-ok")
"""


def test_import_stays_within_its_dependency_budget() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "isolation-ok" in result.stdout
