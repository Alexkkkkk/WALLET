"""Character-level Transformer language model for password generation and scoring.

Learns character transition patterns of human passwords (e.g. consonants follow vowels,
numbers and symbols cluster at the end, years follow names) and generates high-probability
candidate continuations as well as perplexity-based likelihood scores.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence
import numpy as np

from .layers import Embedding, PositionalEmbedding, LayerNorm, TransformerBlock, softmax


SPECIAL_TOKENS = ["<pad>", "<sos>", "<eos>", "<unk>"]


class CharTokenizer:
    """Byte/char tokenizer covering ASCII printable chars + Cyrillic alphabet."""

    def __init__(self):
        chars = SPECIAL_TOKENS[:]
        # ASCII printable
        chars += [chr(c) for c in range(32, 127)]
        # Cyrillic upper + lower + ё/Ё
        cyr = list(range(0x0410, 0x0450)) + [0x0401, 0x0451]
        chars += [chr(c) for c in cyr if chr(c) not in chars]
        self.char_to_id = {c: i for i, c in enumerate(chars)}
        self.id_to_char = {i: c for c, i in self.char_to_id.items()}
        self.pad = self.char_to_id["<pad>"]
        self.sos = self.char_to_id["<sos>"]
        self.eos = self.char_to_id["<eos>"]
        self.unk = self.char_to_id["<unk>"]
        self.vocab_size = len(chars)

    def encode(self, text: str, add_special: bool = False) -> list[int]:
        ids = [self.char_to_id.get(c, self.unk) for c in text]
        if add_special:
            ids = [self.sos] + ids + [self.eos]
        return ids

    def decode(self, ids: Iterable[int]) -> str:
        out = []
        for i in ids:
            if i in (self.sos, self.pad):
                continue
            if i == self.eos:
                break
            out.append(self.id_to_char.get(i, ""))
        return "".join(out)


class StatisticalFallback:
    """Bigram/trigram character transition model used as a reliable prior."""

    def __init__(self, tokenizer: CharTokenizer):
        self.tok = tokenizer
        V = tokenizer.vocab_size
        self.counts = np.ones((V, V), dtype=np.float64)  # Laplace smoothing
        self._cache = None
        self._build_priors()

    def _build_priors(self):
        # Seed the bigram matrix with realistic human password patterns
        patterns = [
            "password", "123456789", "qwerty", "iloveyou", "admin", "welcome",
            "monkey", "dragon", "master", "sunshine", "princess", "football",
            "shadow", "michael", "jessica", "charlie", "superman", "batman",
            "trustno1", "letmein", "starwars", "hello", "secret", "bitcoin",
            "satoshi", "blockchain", "crypto", "pass123", "pass2021",
            "Барсик", "Рекс", "Мурка", "Москва", "Сочи", "Привет", "пароль",
        ]
        years = ["1998", "1999", "2000", "2010", "2015", "2018", "2019", "2020", "2021", "2022", "2023", "2024"]
        symbols = ["!", "@", "#", "$", "_", "-", "."]
        corpus: list[str] = []
        for p in patterns:
            corpus.append(p)
            for y in years:
                corpus.append(f"{p}{y}")
                corpus.append(f"{p}_{y}")
                for s in symbols:
                    corpus.append(f"{p}{s}{y}")
                    corpus.append(f"{p}{y}{s}")
        for s in corpus:
            ids = self.tok.encode(s, add_special=True)
            for a, b in zip(ids[:-1], ids[1:]):
                self.counts[a, b] += 1.0
        self._refresh()

    def update(self, strings: Sequence[str], weight: float = 5.0):
        for s in strings:
            ids = self.tok.encode(s, add_special=True)
            for a, b in zip(ids[:-1], ids[1:]):
                self.counts[a, b] += weight
        self._refresh()

    def _refresh(self):
        self._cache = self.counts / self.counts.sum(axis=1, keepdims=True)

    def next_dist(self, prev_token: int) -> np.ndarray:
        if self._cache is None:
            self._refresh()
        return self._cache[prev_token]


class CharTransformerLM:
    """Lightweight character-level autoregressive Transformer language model."""

    def __init__(self, embed_dim: int = 64, num_heads: int = 4, num_layers: int = 2,
                 max_len: int = 128, rng: np.random.RandomState | None = None):
        self.rng = rng or np.random.RandomState(1337)
        self.tokenizer = CharTokenizer()
        V = self.tokenizer.vocab_size
        self.embed_dim = embed_dim
        self.max_len = max_len
        self.tok_embed = Embedding(V, embed_dim, rng=self.rng)
        self.pos_embed = PositionalEmbedding(max_len, embed_dim, rng=self.rng)
        self.blocks = [TransformerBlock(embed_dim, num_heads, rng=self.rng) for _ in range(num_layers)]
        self.ln_f = LayerNorm(embed_dim)
        scale = 1.0 / math.sqrt(embed_dim)
        self.head = self.rng.randn(embed_dim, V) * scale
        self.head_bias = np.zeros(V)
        self.fallback = StatisticalFallback(self.tokenizer)
        self._hybrid_alpha = 0.6  # weight of neural model vs statistical prior

    def forward(self, tokens: np.ndarray) -> np.ndarray:
        """tokens: (batch, seq) -> logits (batch, seq, vocab)"""
        b, t = tokens.shape
        x = self.tok_embed.forward(tokens) + self.pos_embed.forward(t)
        for block in self.blocks:
            x = block.forward(x)
        x = self.ln_f.forward(x)
        return x @ self.head + self.head_bias

    def next_distribution(self, token_seq: Sequence[int], temperature: float = 1.0) -> np.ndarray:
        if len(token_seq) == 0:
            token_seq = [self.tokenizer.sos]
        seq = list(token_seq)[-self.max_len:]
        tokens = np.array([seq], dtype=np.int64)
        logits = self.forward(tokens)[0, -1]
        probs_neural = softmax(logits / max(temperature, 1e-4))
        probs_stat = self.fallback.next_dist(seq[-1])
        probs = self._hybrid_alpha * probs_neural + (1.0 - self._hybrid_alpha) * probs_stat
        probs = probs / probs.sum()
        return probs

    def sample(self, prompt: str = "", max_len: int = 32, temperature: float = 0.85,
               top_k: int = 8) -> str:
        """Autoregressively sample one candidate continuation."""
        prefix = self.tokenizer.encode(prompt, add_special=False)
        tokens = [self.tokenizer.sos] + prefix
        for _ in range(max_len):
            probs = self.next_distribution(tokens, temperature=temperature)
            probs[self.tokenizer.unk] = 0.0
            probs[self.tokenizer.pad] = 0.0
            if len(tokens) == 1:
                probs[self.tokenizer.eos] = 0.0  # don't stop immediately
            if top_k and top_k < probs.size:
                idx = np.argpartition(-probs, top_k)[:top_k]
                mask = np.zeros_like(probs)
                mask[idx] = probs[idx]
                s = mask.sum()
                if s > 0:
                    probs = mask / s
            probs = probs / probs.sum()
            nxt = int(self.rng.choice(probs.size, p=probs))
            if nxt == self.tokenizer.eos:
                break
            tokens.append(nxt)
            if len(tokens) >= self.max_len - 1:
                break
        return self.tokenizer.decode(tokens)

    def log_likelihood(self, text: str) -> float:
        """Average log-probability per character under the model."""
        ids = self.tokenizer.encode(text, add_special=True)
        if len(ids) < 2:
            return -10.0
        seq = ids[:-1]
        targets = ids[1:]
        if len(seq) > self.max_len:
            seq = seq[:self.max_len]
            targets = targets[:self.max_len]
        logits = self.forward(np.array([seq], dtype=np.int64))[0]
        probs = softmax(logits, axis=-1)
        ll = 0.0
        for t, target in enumerate(targets):
            ll += float(np.log(max(probs[t, target], 1e-12)))
        return ll / len(targets)

    def perplexity(self, text: str) -> float:
        """Perplexity = exp(-mean log prob). Lower is more 'natural'."""
        return float(np.exp(-self.log_likelihood(text)))

    def train_on(self, strings: Sequence[str], steps: int = 60, lr: float = 0.01) -> float:
        """Lightweight fine-tuning of the output head on user fragments/words."""
        if not strings:
            return 0.0
        self.fallback.update(strings)
        last_loss = 0.0
        data: list[list[int]] = [self.tokenizer.encode(s, add_special=True)[:self.max_len] for s in strings if s]
        if not data:
            return 0.0
        for step in range(steps):
            seq = data[step % len(data)]
            if len(seq) < 2:
                continue
            tokens = np.array([seq[:-1]], dtype=np.int64)
            target = np.array(seq[1:], dtype=np.int64)
            b, t = tokens.shape
            x = self.tok_embed.forward(tokens) + self.pos_embed.forward(t)
            for block in self.blocks:
                x = block.forward(x)
            hidden = self.ln_f.forward(x)
            logits = hidden @ self.head + self.head_bias
            probs = softmax(logits, axis=-1)
            loss = float(-np.log(np.clip(probs[0, np.arange(t), target], 1e-12, 1.0)).mean())
            last_loss = loss
            dlogits = probs.copy()
            dlogits[0, np.arange(t), target] -= 1.0
            dlogits /= t
            grad_head = hidden[0].T @ dlogits[0]
            grad_bias = dlogits[0].sum(axis=0)
            grad_head = np.clip(grad_head, -5.0, 5.0)
            grad_bias = np.clip(grad_bias, -5.0, 5.0)
            self.head -= lr * grad_head
            self.head_bias -= lr * grad_bias
        return last_loss

    def generate_batch(self, seeds: Sequence[str], count: int = 30,
                       temperature: float = 0.85, top_k: int = 8) -> list[str]:
        """Generate candidates, optionally continuing each seed prompt."""
        results: list[str] = []
        seeds = list(seeds) or [""]
        per_seed = max(1, count // len(seeds))
        for seed in seeds:
            for _ in range(per_seed):
                c = self.sample(prompt=seed, temperature=temperature, top_k=top_k)
                if c and c not in results:
                    results.append(c)
        return results
