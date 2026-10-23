"""Puts `tools/` on the import path, where `capture_client_contract.py`
imports `validate_client_contract` from when it runs as a script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
