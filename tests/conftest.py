"""Skip GUI tests gracefully when Tkinter is missing.

Without this, a system without python3-tk fails the *entire* pytest run at
collection time instead of just skipping the GUI tests.
"""
import gc


def pytest_runtest_setup(item):
    # Destroyed Tk roots from previous tests may be in reference cycles.
    # Collect on pytest's main thread before a recovery thread can trigger GC:
    # Tcl Variable finalizers on a worker thread otherwise wait for a dead UI.
    gc.collect()


try:
    import tkinter  # noqa: F401
except ImportError:
    collect_ignore = ["test_gui.py"]