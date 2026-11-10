"""WALLET — локальное восстановление пароля legacy Bitcoin Core wallet.dat.

Единственный источник версии для всего проекта: ``pyproject.toml`` читает
значение отсюда через ``[tool.setuptools.dynamic]``, CLI отдаёт его по
``--version``, а тест ``tests/test_sync.py`` сверяет его с README.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
