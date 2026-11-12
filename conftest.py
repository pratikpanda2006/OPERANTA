"""
Root conftest.py — pytest configuration for MarginGuard backend tests.

Adds the backend/ directory to sys.path so that `from app.xxx import yyy`
works in all test files without requiring a manual PYTHONPATH environment
variable. This mirrors how uvicorn runs main.py from inside the backend/ dir.
"""
import sys
from pathlib import Path

# Insert backend/ at the front of sys.path so `import app` resolves to
# backend/app/ regardless of from which directory pytest is invoked.
backend_dir = Path(__file__).parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
