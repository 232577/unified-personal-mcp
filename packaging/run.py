"""Bundled interpreter entry point; all imports stay in the portable directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from personal_mcp.__main__ import main

if __name__ == "__main__":
    main()

