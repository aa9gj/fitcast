"""pytest configuration. Adds the project root to sys.path so tests can
import pipeline.py and skill_extractor.py without a package install."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
