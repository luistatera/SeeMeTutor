"""Pytest configuration — ensure backend modules are importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
