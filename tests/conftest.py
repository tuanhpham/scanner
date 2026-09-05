"""Cho pytest thay cac module o goc repo (render.py, halts.py, ...)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
