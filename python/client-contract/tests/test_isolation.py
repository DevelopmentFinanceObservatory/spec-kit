"""`import dfo_client_contract` loads nothing beyond the standard library.

Checked against `sys.modules` in a fresh interpreter, so a transitive import
counts, and modules the test process has already imported do not.
"""

from __future__ import annotations

import subprocess
import sys

_SCRIPT = """
import sys
before = frozenset(sys.modules)
import dfo_client_contract
leaked = sorted(
    {name.split(".")[0] for name in sys.modules if name not in before}
    - {"dfo_client_contract"}
    - set(sys.stdlib_module_names)
)
if leaked:
    raise SystemExit("leaked modules: " + ", ".join(leaked))
print("isolation-ok")
"""


def test_import_stays_within_the_standard_library() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "isolation-ok" in result.stdout
