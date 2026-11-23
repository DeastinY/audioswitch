"""Entry point for the standalone Windows .exe (PyInstaller)."""
import sys

from audioswitch.app import main

sys.exit(main())
