# Load compatibility stubs so all test imports resolve
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ui.compat  # noqa: F401
