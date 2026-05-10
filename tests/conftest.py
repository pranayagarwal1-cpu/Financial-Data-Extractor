"""Pytest config — make the project root importable so `agents.*`, `utils.*` resolve."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
