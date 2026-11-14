"""Dev launcher without a console window: pythonw paperleaf.pyw [files]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paperleaf.app import main  # noqa: E402

raise SystemExit(main())
