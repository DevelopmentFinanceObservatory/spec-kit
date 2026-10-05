"""`dfo_frame_grammar` imports nothing outside its declared dependency
budget after its runtime dependencies are pre-imported (see the
package's own docstring in `dfo_frame_grammar/__init__.py`). Checked against
`sys.modules` after a genuinely clean import in a fresh interpreter, not by
reading `dfo_frame_grammar`'s own source text for import statements: a
source-text scan cannot see a transitive import pulled in by a dependency
`dfo_frame_grammar` imports (`dfo_spec_kit`, say, quietly gaining a new
third-party import of its own later), while checking the modules an actual
import loaded catches that regardless of where in the import graph it was
introduced.

Runs in a fresh subprocess rather than importing inside the current test
process: this package's own test collection, and pytest's collection of a
wider test run alongside it, may already have imported unrelated
third-party modules into `sys.modules` before this test executes, which
would make an in-process check pass or fail for a reason having nothing to
do with what `import dfo_frame_grammar` itself pulls in.
"""

from __future__ import annotations

import subprocess
import sys

# The frame package requires Arrow, and its root imports the Arrow builders,
# so the Arrow submodules they use count as declared. Pre-importing declared
# dependencies makes this check about unexpected imports from its own import
# graph rather than the implementation details of Arrow's import graph.
_DECLARED_DEPENDENCIES = (
    "import pyarrow\nimport pyarrow.compute\nimport pyarrow.csv\n"
    "import dfo_spec_kit.identity\nimport rfc8785\n"
)
_ALLOWED_TOP_LEVEL_MODULES = frozenset({"dfo_frame_grammar"})

_SCRIPT = (
    "import sys\n"
    + _DECLARED_DEPENDENCIES
    + "before = frozenset(sys.modules)\n"
    + "import dfo_frame_grammar\n"
    "allowed = " + repr(_ALLOWED_TOP_LEVEL_MODULES) + "\n"
    "stdlib = sys.stdlib_module_names\n"
    "leaked = sorted(\n"
    "    {\n"
    "        name.split('.')[0]\n"
    "        for name in sys.modules\n"
    "        if name not in before\n"
    "    }\n"
    "    - allowed\n"
    "    - set(stdlib)\n"
    ")\n"
    "if leaked:\n"
    "    raise SystemExit('leaked modules: ' + ', '.join(leaked))\n"
    "print('isolation-ok')\n"
)


def test_importing_dfo_frame_grammar_stays_within_its_dependency_budget() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "isolation-ok" in result.stdout
