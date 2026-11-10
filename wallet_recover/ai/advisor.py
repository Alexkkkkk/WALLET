"""AI Wallet Copilot: wallet diagnostics, speed benchmarking, ETA and strategy planning."""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..bdb import load_wallet
from ..crypto import Decryptor


@dataclass
class WalletDiagnosis:
    mkey_count: int = 0
    kdf_method: int = 0
    kdf_rounds: int = 0
    salt_hex: str = ""
    salt_entropy_ok: bool = True
    ckey_count: int = 0
    compressed_count: int = 0
    uncompressed_count: int = 0
    checksum_count: int = 0
    warnings: list[str] = field(default_factory=list)
    difficulty_rating: str = "medium"  # low / medium / high / extreme

    def summary(self) -> str:
        lines = [
            f"mkey: {self.mkey_count} (метод {self.kdf_method}, раундов SHA-512 KDF: {self.kdf_rounds:,})",
            f"salt: {self.salt_hex}",
            f"ckey: {self.ckey_count} (сжатых: {self.compressed_count}, несжатых: {self.uncompressed_count})",
            f"Сложность подбора: {self.difficulty_rating.upper()}",
        ]
        if self.warnings:
            lines.append("Предупреждения: " + "; ".join(self.warnings))
        return "\n".join(lines)


def diagnose(wallet_path: str | Path) -> WalletDiagnosis:
    masters, keys = load_wallet(wallet_path)
    if not masters:
        raise ValueError("Кошелёк не содержит mkey (не зашифрован или повреждён)")
    diag = WalletDiagnosis()
    diag.mkey_count = len(masters)
    m = masters[0]
    diag.kdf_method = m.method
    diag.kdf_rounds = m.iterations
    diag.salt_hex = m.salt.hex()
    diag.salt_entropy_ok = len(set(m.salt)) >= 6
    diag.ckey_count = len(keys)
    diag.compressed_count = sum(1 for k in keys if len(k.pubkey) == 33)
    diag.uncompressed_count = sum(1 for k in keys if len(k.pubkey) == 65)
    if diag.kdf_rounds < 25000:
        diag.difficulty_rating = "low"
    elif diag.kdf_rounds < 200000:
        diag.difficulty_rating = "medium"
    elif diag.kdf_rounds < 1000000:
        diag.difficulty_rating = "high"
    else:
        diag.difficulty_rating = "extreme"
    if any(mm.method != 0 for mm in masters):
        diag.warnings.append("найдены mkey с неподдерживаемым методом шифрования")
    if not keys:
        diag.warnings.append("нет ckey: этот инструмент не сможет подтвердить пароль")
    if not diag.salt_entropy_ok:
        diag.warnings.append("salt имеет подозрительно низкую энтропию")
    return diag


def benchmark(wallet_path: str | Path, duration: float = 2.0, jobs: int = 1) -> float:
    """Measure real attempts/sec on the target wallet using dummy candidates."""
    masters, keys = load_wallet(wallet_path)
    decryptor = Decryptor(masters, keys)
    start = time.monotonic()
    count = 0
    cand = b"__bench_dummy_cand__"
    while time.monotonic() - start < duration:
        decryptor.try_password(cand)
        count += 1
    return count / max(time.monotonic() - start, 1e-3)


def format_duration(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds <= 0:
        return "н/д"
    if seconds < 60:
        return f"{seconds:.0f} с"
    if seconds < 3600:
        return f"{seconds/60:.1f} мин"
    if seconds < 86400:
        return f"{seconds/3600:.1f} ч"
    return f"{seconds/86400:.1f} сут"


def strategy_report(diag: WalletDiagnosis, speed_ps: float, cpu_jobs: int = 4) -> str:
    scaled_speed = speed_ps * cpu_jobs * 0.92
    phases = [
        ("Фаза 1 · AI Semantic Memory", 150, "LLM-реконструкция из воспоминаний + транслит + раскладка"),
        ("Фаза 2 · Probabilistic Rule Expansion", 25000, "l33t + регистры + годы + спецсимволы + склейка"),
        ("Фаза 3 · Neural LM generation & ranking", 500000, "CharTransformer сэмплирование + нейро-приоритезация"),
        ("Фаза 4 · Hybrid wordlist + mutation", 10_000_000, "внешний словарь через AI-ранжировщик"),
    ]
    lines = [
        "# AI-план восстановления пароля",
        "",
        "## Анализ кошелька",
        diag.summary(),
        "",
        f"**Измеренная скорость:** ~{speed_ps:.1f} паролей/с на одно ядро (модель, не замер: ~{scaled_speed:.0f} p/s при {cpu_jobs} процессах)",
        "",
        "## Рекомендуемые фазы атаки (в порядке убывания вероятности успеха)",
        "",
    ]
    cum = 0
    for name, cands, desc in phases:
        t = format_duration(cands / max(scaled_speed, 1e-6))
        cum += cands
        lines.append(f"### {name}")
        lines.append(f"- Кандидатов: **{cands:,}** · Оценка времени: **{t}**")
        lines.append(f"- Что делает: {desc}")
        lines.append("")
    lines.append(f"**Суммарно 4 фазы (~{cum:,} кандидатов):** {format_duration(cum / max(scaled_speed, 1e-6))}")
    lines.append("")
    lines.append("## Совет AI")
    if diag.difficulty_rating in ("high", "extreme"):
        lines.append("Кошелёк имеет высокое число раундов KDF — брутфорс неэффективен. Сфокусируйтесь на Фазах 1-2: "
                     "вспоминайте контекст (имена, даты, питомцы, сайты) и используйте AI-промпт.")
    else:
        lines.append("Раундов KDF немного — инструмент быстро переберёт сотни тысяч кандидатов. Рекомендуется "
                     "сначала Фаза 1 (AI-память), затем Фаза 2 с расширенными мутациями.")
    return "\n".join(lines)
