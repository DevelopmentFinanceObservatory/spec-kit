"""`import dfo_descriptions` loads nothing beyond the standard library and
`rfc8785`, the JCS serialiser the size bound counts bytes with.

Checked against `sys.modules` in a fresh interpreter, so a transitive import
counts, and modules the test process has already imported do not.
"""

from __future__ import annotations

import subprocess
import sys

_SCRIPT = """
import sys
before = frozenset(sys.modules)
import dfo_descriptions
leaked = sorted(
    {name.split(".")[0] for name in sys.modules if name not in before}
    - {"dfo_descriptions", "rfc8785"}
    - set(sys.stdlib_module_names)
)
if leaked:
    raise SystemExit("leaked modules: " + ", ".join(leaked))
print("isolation-ok")
"""


def test_import_stays_within_the_standard_library_and_rfc8785() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "isolation-ok" in result.stdout
