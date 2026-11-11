"""AI export observability and cancellation; all providers are mocked/local."""
import logging
import os
from threading import Event

import pytest

from wallet_recover.ai import pipeline
from wallet_recover.ai.llm_generator import CandidateIdea, MemoryReconstructionResult


@pytest.fixture
def simple_options():
    return dict(seed_words=["ExampleSeed"], max_candidates=12,
                include_neural_lm=False, include_translit=False,
                include_layout_shift=False, rank=False)


def assert_no_export(tmp_path, output):
    assert not output.exists()
    assert not list(tmp_path.glob(".wallet-recover-ai-*.tmp"))


class CheapLM:
    """Deterministic scores keep ranking tests independent of neural speed."""
    def __init__(self, **_kwargs):
        pass

    def log_likelihood(self, candidate):
        return -len(candidate) / 10


@pytest.mark.parametrize("provider", ["offline", "ollama", "openai"])
def test_provider_contract_safe_stages_and_counts(tmp_path, monkeypatch, caplog, provider):
    secret = "PRIVATE_PROMPT_PASSWORD_HASH"
    seen = {}

    def fake_reconstruct(prompt, **kwargs):
        seen.update(prompt=prompt, **kwargs)
        return MemoryReconstructionResult(
            candidates=[CandidateIdea(secret + str(i), 0.9) for i in range(5)],
            backend=secret, summary=secret,
        )

    monkeypatch.setattr(pipeline, "reconstruct_from_prompt", fake_reconstruct)
    monkeypatch.setattr(pipeline, "CharTransformerLM", CheapLM)
    stages, progress = [], []
    output = tmp_path / "private.txt"
    with caplog.at_level(logging.INFO, logger=pipeline.__name__):
        count = pipeline.export_candidates(
            output, prompt=secret, provider=provider, model=secret,
            ollama_url="http://127.0.0.1:9876", include_neural_lm=False,
            progress_callback=lambda stage, done: progress.append((stage, done)),
            stage_callback=stages.append,
        )
    assert count == 5
    assert seen == dict(prompt=secret, provider=provider, model=secret,
                        offline_only=False, ollama_url="http://127.0.0.1:9876")
    for phase in ("generation", "ranking", "writing"):
        counts = [done for stage, done in progress if stage == phase]
        assert counts == sorted(counts)
        assert counts[-1] == count
    assert stages[-1] == "Экспорт кандидатов завершён."
    assert all(any("а" <= ch.lower() <= "я" for ch in message) for message in stages)
    assert secret not in repr(stages) + repr(progress) + caplog.text
    assert output.read_text(encoding="utf-8").splitlines() == [secret + str(i) for i in range(5)]
    if provider != "offline":
        assert any("после завершения запроса" in message for message in stages)
    assert not list(tmp_path.glob(".wallet-recover-ai-*.tmp"))


def test_offline_generation_reports_live_counts_and_private_staging(tmp_path, simple_options):
    output = tmp_path / "words.txt"
    progress, stages = [], []

    def observe(stage, done):
        progress.append((stage, done))
        assert not output.exists(), "The final destination must not expose partial data"
        if stage == "writing" and done:
            temporary, = tmp_path.glob(".wallet-recover-ai-*.tmp")
            if os.name == "posix":
                assert temporary.stat().st_mode & 0o777 == 0o600

    count = pipeline.export_candidates(output, progress_callback=observe,
                                       stage_callback=stages.append, **simple_options)
    assert count == 12
    assert [done for phase, done in progress if phase == "generation"] == list(range(13))
    assert [done for phase, done in progress if phase == "writing"] == list(range(13))
    assert "ranking" not in [phase for phase, _ in progress]
    assert len(output.read_text(encoding="utf-8").splitlines()) == count
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("phase", ["generation", "ranking", "writing"])
def test_cancellation_during_processing_removes_private_partial(
    tmp_path, monkeypatch, simple_options, phase,
):
    monkeypatch.setattr(pipeline, "CharTransformerLM", CheapLM)
    options = dict(simple_options, rank=phase == "ranking")
    stop, stages = Event(), []
    output = tmp_path / "cancelled.txt"

    def observe(stage, processed):
        if stage == phase and processed == 3:
            stop.set()

    with pytest.raises(pipeline.ExportCancelled, match="Экспорт отменён"):
        pipeline.export_candidates(output, progress_callback=observe,
                                   stage_callback=stages.append, stop_event=stop, **options)
    assert_no_export(tmp_path, output)
    assert "Экспорт кандидатов завершён." not in stages
    assert stages[-1] == "Экспорт отменён; итоговый файл не создан."


def test_pre_cancelled_export_never_calls_provider(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("A cancelled job must not contact a provider")

    monkeypatch.setattr(pipeline, "reconstruct_from_prompt", unexpected)
    stop = Event()
    stop.set()
    output = tmp_path / "cancelled.txt"
    with pytest.raises(InterruptedError):
        pipeline.export_candidates(output, prompt="synthetic", stop_event=stop)
    assert_no_export(tmp_path, output)


def test_provider_call_finishes_before_cancellation_checkpoint(tmp_path, monkeypatch):
    stop = Event()
    calls, progress, stages = [], [], []

    def fake_provider(*_args, **_kwargs):
        calls.append("entered")
        stop.set()  # Simulate a GUI stop request while the blocking call runs.
        calls.append("returned")
        return MemoryReconstructionResult(candidates=[CandidateIdea("private", 1)], backend="ollama:test")

    monkeypatch.setattr(pipeline, "reconstruct_from_prompt", fake_provider)
    output = tmp_path / "cancelled.txt"
    with pytest.raises(pipeline.ExportCancelled):
        pipeline.export_candidates(
            output, prompt="synthetic memory", provider="ollama", rank=False,
            include_neural_lm=False, stop_event=stop, stage_callback=stages.append,
            progress_callback=lambda phase, done: progress.append((phase, done)),
        )
    assert calls == ["entered", "returned"]
    assert any("после завершения запроса" in message for message in stages)
    assert all(done == 0 for _, done in progress)
    assert_no_export(tmp_path, output)


def test_provider_fallback_reports_actual_offline_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "reconstruct_from_prompt", lambda *_args, **_kwargs:
                        MemoryReconstructionResult(candidates=[CandidateIdea("synthetic", 1)],
                                                   backend="offline_nlp"))
    stages = []
    assert pipeline.export_candidates(
        tmp_path / "words.txt", prompt="synthetic", provider="openai",
        rank=False, include_neural_lm=False, stage_callback=stages.append,
    ) == 1
    assert "AI-провайдер недоступен; используется офлайн-анализ." in stages


def test_cancellation_at_finalization_prevents_publication(tmp_path, simple_options):
    stop = Event()
    stages = []
    output = tmp_path / "cancelled.txt"

    def stage_callback(message):
        stages.append(message)
        if message.startswith("Завершение экспорта;"):
            stop.set()

    with pytest.raises(pipeline.ExportCancelled):
        pipeline.export_candidates(output, stage_callback=stage_callback,
                                   stop_event=stop, **simple_options)
    assert_no_export(tmp_path, output)
    assert "Экспорт кандидатов завершён." not in stages


def test_stop_after_commit_does_not_undo_completed_export(tmp_path, simple_options):
    stop = Event()
    output = tmp_path / "complete.txt"

    def stage_callback(message):
        if message == "Экспорт кандидатов завершён.":
            assert output.exists()
            stop.set()

    count = pipeline.export_candidates(output, stop_event=stop,
                                       stage_callback=stage_callback, **simple_options)
    assert count == 12
    assert len(output.read_text(encoding="utf-8").splitlines()) == count


def test_generation_exception_cleans_temp_and_reports_only_safe_stage(tmp_path, monkeypatch):
    secret = "SENSITIVE_PASSWORD_HASH"

    def failing_stream(**_kwargs):
        yield secret
        raise RuntimeError(secret)

    monkeypatch.setattr(pipeline, "build_candidate_stream", failing_stream)
    stages = []
    output = tmp_path / "failed.txt"
    with pytest.raises(RuntimeError):
        pipeline.export_candidates(output, stage_callback=stages.append)
    assert_no_export(tmp_path, output)
    assert secret not in repr(stages)
    assert stages[-1] == "Экспорт не завершён; частичный файл не опубликован."


def test_fsync_error_does_not_publish_partial_export(tmp_path, monkeypatch, simple_options):
    def fail_sync(_fd):
        raise OSError("synthetic disk error")

    monkeypatch.setattr(os, "fsync", fail_sync)
    output = tmp_path / "failed.txt"
    with pytest.raises(OSError, match="synthetic disk error"):
        pipeline.export_candidates(output, **simple_options)
    assert_no_export(tmp_path, output)


@pytest.mark.parametrize("existing_kind", ["file", "dangling_symlink"])
def test_existing_destination_is_never_overwritten(tmp_path, monkeypatch, simple_options, existing_kind):
    output = tmp_path / "existing.txt"
    if existing_kind == "file":
        output.write_text("keep me", encoding="utf-8")
    else:
        try:
            output.symlink_to(tmp_path / "missing.txt")
        except OSError:
            pytest.skip("Symlinks unavailable on this filesystem")

    def unexpected(**_kwargs):
        pytest.fail("Existing destinations must be rejected before generation")

    monkeypatch.setattr(pipeline, "build_candidate_stream", unexpected)
    with pytest.raises(FileExistsError):
        pipeline.export_candidates(output, **simple_options)
    if existing_kind == "file":
        assert output.read_text(encoding="utf-8") == "keep me"
    else:
        assert output.is_symlink()
    assert not list(tmp_path.glob(".wallet-recover-ai-*.tmp"))


def test_racing_writer_cannot_be_overwritten(tmp_path, simple_options):
    output = tmp_path / "racing.txt"
    stages = []

    def racing_writer(phase, processed):
        if phase == "writing" and processed == 1:
            output.write_text("other writer", encoding="utf-8")

    with pytest.raises(FileExistsError):
        pipeline.export_candidates(output, progress_callback=racing_writer,
                                   stage_callback=stages.append, **simple_options)
    assert output.read_text(encoding="utf-8") == "other writer"
    assert not list(tmp_path.glob(".wallet-recover-ai-*.tmp"))
    assert "Экспорт кандидатов завершён." not in stages


def test_callback_failures_are_nonfatal_and_do_not_log_exception_secrets(
    tmp_path, simple_options, caplog,
):
    def failing_callback(*_args):
        raise ValueError("SENSITIVE_CALLBACK_ERROR")

    with caplog.at_level(logging.WARNING, logger=pipeline.__name__):
        count = pipeline.export_candidates(
            tmp_path / "complete.txt", stage_callback=failing_callback,
            progress_callback=failing_callback, **simple_options,
        )
    assert count == 12
    assert "AI status callback failed" in caplog.text
    assert "SENSITIVE_CALLBACK_ERROR" not in caplog.text


def test_observed_ranking_preserves_original_chunk_order(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "CharTransformerLM", CheapLM)
    wordlist = tmp_path / "input.txt"
    wordlist.write_text("\n".join("Candidate%04d!" % i for i in range(410)), encoding="utf-8")
    options = dict(wordlist=wordlist, max_candidates=410, include_neural_lm=False, rank=True)
    expected = list(pipeline.build_candidate_stream(**options))
    progress = []
    observed = list(pipeline.build_candidate_stream(
        progress_callback=lambda stage, done: progress.append((stage, done)), **options,
    ))
    assert observed == expected
    assert [done for stage, done in progress if stage == "ranking"] == list(range(1, 411))


def test_wordlist_loop_cancellation_removes_partial_export(tmp_path):
    wordlist = tmp_path / "input.txt"
    wordlist.write_text("first\nsecond\nthird\n", encoding="utf-8")
    stop = Event()
    output = tmp_path / "cancelled.txt"

    def progress_callback(phase, processed):
        if phase == "generation" and processed == 2:
            stop.set()

    with pytest.raises(pipeline.ExportCancelled):
        pipeline.export_candidates(output, wordlist=wordlist, include_neural_lm=False,
                                   rank=False, stop_event=stop, progress_callback=progress_callback)
    assert_no_export(tmp_path, output)
    assert wordlist.read_text(encoding="utf-8") == "first\nsecond\nthird\n"


def test_neural_sampling_checks_cancellation_after_one_sample(tmp_path, monkeypatch):
    stop = Event()
    samples = []

    class FakeLM:
        def __init__(self, **_kwargs):
            pass

        def sample(self, **kwargs):
            samples.append(kwargs)
            stop.set()
            return "synthetic-neural-candidate"

    monkeypatch.setattr(pipeline, "CharTransformerLM", FakeLM)
    output = tmp_path / "cancelled.txt"
    with pytest.raises(pipeline.ExportCancelled):
        pipeline.export_candidates(output, rank=False, stop_event=stop)
    assert len(samples) == 1
    assert_no_export(tmp_path, output)


def test_training_finishes_before_its_cancellation_checkpoint(tmp_path, monkeypatch):
    stop = Event()
    trained = []

    class FakeLM:
        def __init__(self, **_kwargs):
            pass

        def train_on(self, strings, **kwargs):
            trained.append((strings, kwargs))
            stop.set()

        def sample(self, **_kwargs):
            pytest.fail("Cancellation after training must prevent sampling")

    monkeypatch.setattr(pipeline, "CharTransformerLM", FakeLM)
    monkeypatch.setattr(pipeline, "smart_expand", lambda *_args, **_kwargs: iter(["training-seed"]))
    output = tmp_path / "cancelled.txt"
    with pytest.raises(pipeline.ExportCancelled):
        pipeline.export_candidates(output, seed_words=["training-seed"], rank=False, stop_event=stop)
    assert len(trained) == 1
    assert trained[0][1] == dict(steps=40, lr=0.02)
    assert_no_export(tmp_path, output)


def test_empty_export_completes_without_false_candidate_count(tmp_path):
    progress = []
    output = tmp_path / "empty.txt"
    count = pipeline.export_candidates(
        output, rank=False, include_neural_lm=False,
        progress_callback=lambda phase, done: progress.append((phase, done)),
    )
    assert count == 0
    assert output.read_bytes() == b""
    assert ("writing", 0) in progress
