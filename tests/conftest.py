"""Skip GUI tests gracefully when Tkinter is missing.

Without this, a system without python3-tk fails the *entire* pytest run at
collection time instead of just skipping the GUI tests.
"""
try:
    import tkinter  # noqa: F401
except ImportError:
    collect_ignore = ["test_gui.py"]