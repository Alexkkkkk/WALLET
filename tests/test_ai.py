import os

import numpy as np
import pytest

from wallet_recover.ai.char_transformer import CharTokenizer, CharTransformerLM
from wallet_recover.ai.hashcat import build_hashcat_command
from wallet_recover.ai.layers import LayerNorm, MultiHeadAttention, TransformerBlock, softmax
from wallet_recover.ai.llm_generator import (
    keyboard_layout_shift,
    reconstruct_from_prompt,
    transliterate_en_ru,
    transliterate_ru_en,
)
from wallet_recover.ai.mutator import case_variants, leet_variants, smart_expand
from wallet_recover.ai.pipeline import export_candidates
from wallet_recover.ai.ranker import rank_candidates


def test_transformer_layers_shape_and_numerical_stability():
    x = np.random.RandomState(3).randn(2, 5, 16)
    assert np.allclose(softmax(np.array([[10000.0, 10001.0]]), axis=-1).sum(), 1.0)
    assert LayerNorm(16).forward(x).shape == x.shape
    assert MultiHeadAttention(16, 4).forward(x).shape == x.shape
    assert TransformerBlock(16, 4).forward(x).shape == x.shape


def test_char_tokenizer_and_transformer_lm():
    tok = CharTokenizer()
    encoded = tok.encode("Барсик123!", add_special=True)
    assert tok.decode(encoded) == "Барсик123!"
    lm = CharTransformerLM(embed_dim=16, num_heads=4, num_layers=1, max_len=32)
    assert lm.forward(np.array([encoded[:-1]])).shape == (1, len(encoded) - 1, tok.vocab_size)
    assert lm.perplexity("Barsik2019!") > 0
    assert isinstance(lm.sample("Barsik", max_len=8), str)


def test_transliteration_and_keyboard_layout():
    assert transliterate_ru_en("Барсик2019!") == "Barsik2019!"
    assert transliterate_en_ru("Barsik2019!") == "Барсик2019!"
    assert keyboard_layout_shift("Ghbdtn") == "Привет"


def test_offline_memory_reconstruction():
    result = reconstruct_from_prompt("кот Барсик, 2019 год, восклицательный знак", provider="offline")
    assert result.backend == "offline_nlp"
    assert "2019" in result.years
    assert result.candidates
    assert any("Barsik" in c.password or "Барсик" in c.password for c in result.candidates)


def test_ollama_uses_localhost_endpoint(monkeypatch):
    import json
    import urllib.request
    from wallet_recover.ai.llm_generator import _ollama_reconstruct

    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"message": {"content": json.dumps({
                "entities": ["Barsik"], "years": ["2019"], "candidates": [
                    {"password": "Barsik2019!", "confidence": 0.9}
                ]
            })}}).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = _ollama_reconstruct("кот Барсик, 2019", model="mistral")
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert result.backend == "ollama:mistral"
    assert result.candidates[0].password == "Barsik2019!"


def test_mutation_and_ranking():
    assert "p@ssword" in leet_variants("password")
    assert "HELLO" in case_variants("hello")
    candidates = list(smart_expand(["Barsik", "2019"], max_candidates=100))
    assert len(candidates) <= 100
    assert len(candidates) == len(set(candidates))
    ranked = rank_candidates(["zzzzzz", "Barsik2019!", "Barsik2019!"], seeds=["Barsik", "2019"])
    assert ranked[0] == "Barsik2019!"
    assert len(ranked) == 2


def test_ai_export_is_private_and_never_overwrites(tmp_path):
    output = tmp_path / "candidates.txt"
    count = export_candidates(
        output, prompt="кот Барсик 2019", max_candidates=30, provider="offline",
        include_neural_lm=False, rank=False,
    )
    assert count > 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == count
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        export_candidates(output, seed_words=["other"], max_candidates=2)


def test_hashcat_command_is_local_and_private(tmp_path):
    hash_file = tmp_path / "wallet.hash"
    wordlist = tmp_path / "words.txt"
    hash_file.write_text("$bitcoin$synthetic", encoding="utf-8")
    wordlist.write_text("synthetic-candidate\n", encoding="utf-8")
    state = tmp_path / "private-state"
    command = build_hashcat_command(hash_file, wordlist, state, executable="hashcat")
    assert command[:5] == ["hashcat", "-m", "11300", "-a", "0"]
    assert str(hash_file) in command and str(wordlist) in command
    assert "http" not in " ".join(command).lower()
    if os.name == "posix":
        assert state.stat().st_mode & 0o777 == 0o700
        assert (state / "wallet.potfile").stat().st_mode & 0o777 == 0o600
