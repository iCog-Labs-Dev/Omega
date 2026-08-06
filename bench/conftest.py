"""Put bench/ on the import path so the tests import the same modules the runner does."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
