"""Единая точка входа AI Wallet Copilot: диагностика + бенчмарк + план атаки.

Используется одновременно CLI (``--diagnose`` / ``--plan``) и GUI
(кнопка «AI-диагностика и план»), чтобы обе поверхности всегда показывали
один и тот же результат из одной реализации.

Импорт ``.ai.advisor`` (а значит и NumPy) выполняется лениво: базовые команды
``--wallet``/``--print-mkey``/``--selftest`` должны работать без AI-зависимостей.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_jobs() -> int:
    """Разумное число потоков по умолчанию — как в CLI и GUI."""
    return max(1, min(64, os.cpu_count() or 1))


@dataclass
class WalletPlan:
    """Результат работы Copilot: диагноз, отчёт и измеренная скорость."""

    diagnosis: object  # wallet_recover.ai.advisor.WalletDiagnosis
    report: str
    speed_ps: float

    @property
    def difficulty(self) -> str:
        return getattr(self.diagnosis, "difficulty_rating", "medium")

    def summary(self) -> str:
        return self.diagnosis.summary()


def wallet_plan(wallet_path: str | Path, jobs: int | None = None,
                duration: float = 2.0, speed_ps: float | None = None) -> WalletPlan:
    """Диагностировать кошелёк и построить стратегию восстановления.

    ``speed_ps`` позволяет передать уже измеренную скорость (например, из
    предыдущего прогона), чтобы не тратить время на повторный бенчмарк.
    """
    from .ai.advisor import benchmark, diagnose, strategy_report

    path = Path(wallet_path).expanduser()
    diag = diagnose(path)
    measured = speed_ps if speed_ps is not None else benchmark(path, duration=duration)
    report = strategy_report(diag, measured, cpu_jobs=jobs or default_jobs())
    return WalletPlan(diagnosis=diag, report=report, speed_ps=measured)
