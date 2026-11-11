"""Unified AI recovery pipeline combining all AI layers into a prioritized candidate stream."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .llm_generator import reconstruct_from_prompt, transliterate_ru_en, keyboard_layout_shift
from .mutator import smart_expand
from .char_transformer import CharTransformerLM
from .ranker import ranked_stream, score_candidate

log = logging.getLogger(__name__)


class ExportCancelled(InterruptedError):
    """Cooperative cancellation; no completed export is published."""


class _Observer:
    """Only fixed messages and counters may cross the UI/logging boundary."""

    def __init__(self, progress_callback=None, stage_callback=None, stop_event=None):
        self.progress_callback = progress_callback
        self.stage_callback = stage_callback
        self.stop_event = stop_event
        self.last_stage = None

    def check(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ExportCancelled("Экспорт отменён; итоговый файл не создан.")

    @staticmethod
    def _notify(callback, *args):
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                # A failing UI observer must not reveal data or break the worker.
                log.warning("AI status callback failed")

    def stage(self, message):
        self.check()
        if message != self.last_stage:
            self.last_stage = message
            self._notify(self.stage_callback, message)
        self.check()

    def progress(self, stage, processed):
        self.check()
        labels = {"generation": "Генерация кандидатов", "ranking": "Ранжирование кандидатов",
                  "writing": "Запись кандидатов"}
        self._notify(self.progress_callback, stage, processed)
        self.check()


def _observed_ranked_stream(stream, lm, seeds, observer):
    """Match ranked_stream's 400-item ordering, with per-score cancellation."""
    processed = 0

    def rank_chunk(chunk):
        nonlocal processed
        observer.stage("Ранжирование кандидатов локальной моделью.")
        scored = []
        for candidate in chunk:
            observer.check()
            score = score_candidate(candidate, lm, seeds)
            observer.check()
            scored.append((score, candidate))
            processed += 1
            observer.progress("ranking", processed)
        scored.sort(key=lambda item: item[0], reverse=True)
        observer.check()
        return [candidate for _, candidate in scored]

    chunk = []
    try:
        for candidate in stream:
            observer.check()
            chunk.append(candidate)
            if len(chunk) >= 400:
                for candidate in rank_chunk(chunk):
                    observer.check()
                    yield candidate
                chunk.clear()
        if chunk:
            for candidate in rank_chunk(chunk):
                observer.check()
                yield candidate
        observer.check()
    finally:
        stream.close()


def build_candidate_stream(
    prompt: str = "",
    seed_words: Sequence[str] = (),
    wordlist: str | Path | None = None,
    max_candidates: int = 25000,
    include_translit: bool = True,
    include_layout_shift: bool = True,
    include_leet: bool = True,
    include_case: bool = True,
    include_typos: bool = False,
    include_chain: bool = True,
    include_neural_lm: bool = True,
    rank: bool = True,
    offline_only: bool = False,
    provider: str = "offline",
    model: str = "mistral",
    ollama_url: str = "http://127.0.0.1:11434",
    progress_callback: Callable[[str], None] | None = None,
    stage_callback: Callable[[str], None] | None = None,
    stop_event=None,
) -> Iterator[str]:
    """Yield prioritized candidates; optional observers never receive secrets.

    ``progress_callback(stage, processed)`` receives a safe phase name and
    cumulative count for generation (unique accepted candidates), ranking
    (scored candidates), or writing. ``stage_callback(message)`` receives fixed
    Russian stage text.
    Callbacks run synchronously on the caller's thread and ordinary callback
    exceptions are ignored. A threading.Event-compatible ``stop_event`` raises
    ExportCancelled at cooperative checkpoints, not a normal end-of-stream.
    Provider requests, one training call and one sample/score cannot be forcibly
    interrupted; cancellation is checked immediately before and after them.
    """
    observer = _Observer(progress_callback, stage_callback, stop_event)
    observer.check()
    seen: set[str] = set()
    collected_seeds: list[str] = []
    for seed in seed_words:
        observer.check()
        if seed.strip():
            collected_seeds.append(seed.strip())

    def emit(candidate: str):
        observer.check()
        if candidate and candidate not in seen:
            seen.add(candidate)
            observer.progress("generation", len(seen))
            return True
        return False

    def neural_stream(lm, prompts, need):
        # Keep generate_batch's seed allocation and sampling order, but do not
        # materialize a whole batch before checking cancellation/progress.
        per_seed = max(1, need // len(prompts))
        for seed in prompts:
            for _ in range(per_seed):
                observer.check()
                candidate = lm.sample(prompt=seed, temperature=0.85, top_k=8)
                observer.check()
                yield candidate

    def raw_stream() -> Iterator[str]:
        observer.stage("Локальная генерация кандидатов.")
        observer.progress("generation", 0)
        # --- Layer 1: Semantic memory reconstruction from prompt ---
        if prompt.strip():
            if offline_only or provider == "offline":
                observer.stage("Офлайн-анализ воспоминаний.")
            else:
                observer.stage("Ожидание ответа AI-провайдера; отмена возможна после завершения запроса.")
            observer.check()
            recon = reconstruct_from_prompt(prompt, model=model, provider=provider,
                                            offline_only=offline_only, ollama_url=ollama_url)
            observer.check()
            log.info("AI reconstruction produced %d candidates", len(recon.candidates))
            if recon.backend == "offline_nlp" and not offline_only and provider != "offline":
                observer.stage("AI-провайдер недоступен; используется офлайн-анализ.")
            for idea in recon.candidates:
                if emit(idea.password):
                    yield idea.password
                    if len(seen) >= max_candidates:
                        return
            for entity in recon.entities:
                observer.check()
                if entity and entity.lower() not in [s.lower() for s in collected_seeds]:
                    collected_seeds.append(entity)

        # Transliteration & layout expansions of all seeds
        observer.stage("Локальные преобразования и генерация вариантов.")
        extra_seeds: list[str] = []
        for seed in list(collected_seeds):
            observer.check()
            if include_translit:
                transformed = transliterate_ru_en(seed)
                if transformed != seed:
                    extra_seeds.append(transformed)
            if include_layout_shift:
                transformed = keyboard_layout_shift(seed)
                if transformed != seed:
                    extra_seeds.append(transformed)
        collected_seeds.extend(dict.fromkeys(extra_seeds))

        # --- Layer 2: Probabilistic rule-expansion & mutations ---
        if collected_seeds:
            for candidate in smart_expand(
                collected_seeds,
                include_leet=include_leet,
                include_case=include_case,
                include_typos=include_typos,
                include_chain=include_chain,
                max_candidates=max_candidates,
            ):
                if emit(candidate):
                    yield candidate
                    if len(seen) >= max_candidates:
                        return

        # --- Layer 3: External wordlist (if provided) ---
        if wordlist and Path(wordlist).is_file():
            observer.stage("Чтение локального словаря кандидатов.")
            with open(wordlist, "rb") as source:
                for line in source:
                    observer.check()
                    candidate = line.rstrip(b"\r\n").decode("utf-8", "ignore")
                    if emit(candidate):
                        yield candidate
                        if len(seen) >= max_candidates:
                            return

        # --- Layer 4: Neural Char-Transformer generation ---
        if include_neural_lm and len(seen) < max_candidates:
            observer.stage("Подготовка локальной символьной модели.")
            lm = CharTransformerLM(embed_dim=48, num_heads=4, num_layers=2)
            observer.check()
            train_subset = [s for s in list(seen)[:20] if 4 <= len(s) <= 24]
            if train_subset:
                observer.stage("Обучение локальной модели; отмена проверяется после завершения обучения.")
                lm.train_on(train_subset, steps=40, lr=0.02)
                observer.check()
            observer.stage("Генерация кандидатов локальной символьной моделью.")
            prompts = collected_seeds[:6] or [""]
            need = min(max_candidates - len(seen), 300)
            for candidate in neural_stream(lm, prompts, need):
                if emit(candidate):
                    yield candidate
                    if len(seen) >= max_candidates:
                        return
        observer.check()

    stream = raw_stream()
    if rank:
        observer.check()
        lm_for_rank = CharTransformerLM(embed_dim=48, num_heads=4, num_layers=2)
        observer.check()
        if progress_callback is not None or stage_callback is not None or stop_event is not None:
            return _observed_ranked_stream(stream, lm_for_rank, collected_seeds[:8], observer)
        return ranked_stream(stream, lm=lm_for_rank, seeds=collected_seeds[:8], chunk_size=400)
    return stream


def export_candidates(
    path: str | Path,
    *,
    progress_callback: Callable[[str], None] | None = None,
    stage_callback: Callable[[str], None] | None = None,
    stop_event=None,
    **kwargs,
) -> int:
    """Write a private, exclusive wordlist and return its completed line count.

    Existing calls/kwargs remain supported. See build_candidate_stream for the
    observer contract; this function additionally reports cumulative writing counts.
    A private 0600 sibling temporary file is atomically linked to ``path`` only
    after the stream completes and the final cancellation check passes. Neither
    failure nor cancellation publishes a partial export; temporary files are
    removed on exit. An existing destination (including a dangling symlink) is
    never overwritten. The filesystem must support hard links.

    Cancellation raises ExportCancelled (an InterruptedError), not a partial
    count. The final link is the commit point: a later stop request does not undo
    a completed export. Blocking provider calls/training/sample/score operations
    finish before their next checkpoint; no immediate interruption is promised.
    Callbacks run on the caller's thread; marshal them to the GUI event queue.
    """
    path = Path(path)
    observer = _Observer(progress_callback, stage_callback, stop_event)
    observer.check()
    if os.path.lexists(path):
        raise FileExistsError("Файл экспорта уже существует.")
    observer.stage("Подготовка приватного файла экспорта.")
    observer.progress("writing", 0)
    fd, temporary = tempfile.mkstemp(prefix=".wallet-recover-ai-", suffix=".tmp", dir=str(path.parent))
    stream = None
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            stream = build_candidate_stream(
                progress_callback=progress_callback, stage_callback=stage_callback,
                stop_event=stop_event, **kwargs,
            )
            for candidate in stream:
                observer.check()
                if not count:
                    observer.stage("Запись кандидатов в приватный файл.")
                output.write(candidate + "\n")
                count += 1
                observer.progress("writing", count)
            observer.check()
            output.flush()
            os.fsync(output.fileno())
        observer.stage("Завершение экспорта; итоговый файл ещё не опубликован.")
        observer.check()
        # Unlike replace/rename, link is atomic AND fails if another writer
        # created the destination while generation was in progress.
        os.link(temporary, path)
    except ExportCancelled:
        observer._notify(stage_callback, "Экспорт отменён; итоговый файл не создан.")
        raise
    except BaseException:
        observer._notify(stage_callback, "Экспорт не завершён; частичный файл не опубликован.")
        raise
    finally:
        try:
            if stream is not None:
                stream.close()
        finally:
            os.unlink(temporary)
    # No cancellation checkpoint after the atomic commit point.
    observer._notify(stage_callback, "Экспорт кандидатов завершён.")
    return count
