"""Единая точка входа AI Wallet Copilot: диагностика + бенчмарк + план атаки.

Используется одновременно CLI (``--diagnose`` / ``--plan``) и GUI
(кнопка «AI-диагностика и план»), чтобы обе поверхности всегда показывали
один и тот же результат из одной реализации.

Импорт ``.ai.advisor`` (а значит и NumPy) выполняется лениво: базовые команды
``--wallet``/``--print-mkey``/``--selftest`` должны работать без AI-зависимостей.
"""
from __future__ import annotations

import os
import math
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
    report += "\n\n" + entropy_report(measured)
    return WalletPlan(diagnosis=diag, report=report, speed_ps=measured)


def estimate_entropy_bits(char_pool: int, length: int) -> float:
    """Uniform independent-symbol model; not the entropy of a human password."""
    if type(char_pool) is not int or char_pool < 1:
        raise ValueError("char_pool must be a positive integer")
    if type(length) is not int or length < 0:
        raise ValueError("length must be a nonnegative integer")
    return length * math.log2(char_pool)


def time_to_crack(entropy_bits: float, rate_ps: float, expected: bool = True) -> float:
    """Uniform-space approximation N/(2r); not a guarantee of recovery."""
    if not math.isfinite(entropy_bits) or entropy_bits < 0:
        raise ValueError("entropy_bits must be finite and nonnegative")
    if not math.isfinite(rate_ps) or rate_ps <= 0:
        return float("inf")
    exponent = entropy_bits - (1 if expected else 0) - math.log2(rate_ps)
    if exponent > 1023:
        return float("inf")
    return 2.0 ** exponent


def entropy_report(rate_ps: float) -> str:
    lines = [
        "## Энтропия: условные модели пространства кандидатов",
        "Измеренная скорость одного процесса: %.2f p/s. Масштабирование CPU не измерено." % rate_ps,
        "Среднее время ≈ N/(2r), только при равномерном распределении и наличии пароля в пространстве.",
        "Это не измерение энтропии конкретного пароля и не обещание успешного восстановления.",
    ]
    for name, pool, length in [("PIN 6 цифр", 10, 6), ("8 строчных латинских", 26, 8),
                               ("12 букв/цифр", 62, 12), ("16 печатных ASCII", 94, 16),
                               ("20 печатных ASCII", 94, 20)]:
        bits = estimate_entropy_bits(pool, length)
        seconds = time_to_crack(bits, rate_ps)
        if not math.isfinite(seconds):
            duration = "не определено / превышает числовой диапазон"
        elif seconds >= 31536000:
            duration = "%.3g лет" % (seconds / 31536000)
        else:
            duration = "%.3g с" % seconds
        lines.append("- %s: %.1f бит; среднее время %s" % (name, bits, duration))
    lines.append("Стратегия зависит от размера и качества кандидатов, а не только от раундов KDF. "
                 "Сначала известные фрагменты и воспоминания; для локального Bitcoin Core Hashcat mode 11300.")
    return "\n".join(lines)
