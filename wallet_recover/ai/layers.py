"""Neural network layers implemented in pure NumPy.

No heavy dependencies: runs everywhere Python + NumPy runs, fully offline.
Implements the standard Pre-LN Transformer architecture used by GPT/Llama.
"""
from __future__ import annotations

import math
import numpy as np


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax."""
    x_max = np.max(x, axis=axis, keepdims=True)
    exp_x = np.exp(x - x_max)
    return exp_x / np.sum(exp_x, axis=axis, keepdims=True)


def gelu(x: np.ndarray) -> np.ndarray:
    """Gaussian Error Linear Unit (approximation)."""
    return 0.5 * x * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * np.power(x, 3))))


class Embedding:
    """Token embedding layer."""

    def __init__(self, vocab_size: int, embed_dim: int, rng: np.random.RandomState | None = None):
        rng = rng or np.random.RandomState(42)
        scale = 1.0 / math.sqrt(embed_dim)
        self.weight = rng.randn(vocab_size, embed_dim) * scale

    def forward(self, idx: np.ndarray) -> np.ndarray:
        return self.weight[idx]


class PositionalEmbedding:
    """Learned positional embedding table."""

    def __init__(self, max_len: int, embed_dim: int, rng: np.random.RandomState | None = None):
        rng = rng or np.random.RandomState(42)
        scale = 1.0 / math.sqrt(embed_dim)
        self.weight = rng.randn(max_len, embed_dim) * scale

    def forward(self, seq_len: int) -> np.ndarray:
        return self.weight[:seq_len]


class LayerNorm:
    """Pre-LN style Layer Normalization with learnable scale/bias."""

    def __init__(self, dim: int, eps: float = 1e-5):
        self.eps = eps
        self.gamma = np.ones(dim)
        self.beta = np.zeros(dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        mean = x.mean(axis=-1, keepdims=True)
        var = x.var(axis=-1, keepdims=True)
        return self.gamma * (x - mean) / np.sqrt(var + self.eps) + self.beta


class MultiHeadAttention:
    """Causal Multi-Head Self-Attention."""

    def __init__(self, embed_dim: int, num_heads: int, rng: np.random.RandomState | None = None):
        if embed_dim % num_heads != 0:
            raise ValueError("embed_dim must be divisible by num_heads")
        self.embed_dim = embed_dim
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads
        self.scale = 1.0 / math.sqrt(self.head_dim)
        rng = rng or np.random.RandomState(42)
        scale = 1.0 / math.sqrt(embed_dim)
        self.wq = rng.randn(embed_dim, embed_dim) * scale
        self.wk = rng.randn(embed_dim, embed_dim) * scale
        self.wv = rng.randn(embed_dim, embed_dim) * scale
        self.wo = rng.randn(embed_dim, embed_dim) * scale
        self.bo = np.zeros(embed_dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        b, t, d = x.shape
        q = (x @ self.wq).reshape(b, t, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
        k = (x @ self.wk).reshape(b, t, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
        v = (x @ self.wv).reshape(b, t, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
        att = (q @ k.transpose(0, 1, 3, 2)) * self.scale
        mask = np.triu(np.full((t, t), -1e9), k=1)
        att = att + mask
        att = softmax(att, axis=-1)
        out = (att @ v).transpose(0, 2, 1, 3).reshape(b, t, d)
        return out @ self.wo + self.bo


class FeedForward:
    """Two-layer MLP with GELU activation."""

    def __init__(self, embed_dim: int, hidden_dim: int | None = None, rng: np.random.RandomState | None = None):
        hidden_dim = hidden_dim or embed_dim * 4
        rng = rng or np.random.RandomState(42)
        scale1 = 1.0 / math.sqrt(embed_dim)
        scale2 = 1.0 / math.sqrt(hidden_dim)
        self.w1 = rng.randn(embed_dim, hidden_dim) * scale1
        self.b1 = np.zeros(hidden_dim)
        self.w2 = rng.randn(hidden_dim, embed_dim) * scale2
        self.b2 = np.zeros(embed_dim)

    def forward(self, x: np.ndarray) -> np.ndarray:
        return gelu(x @ self.w1 + self.b1) @ self.w2 + self.b2


class TransformerBlock:
    """Pre-LN Transformer decoder layer."""

    def __init__(self, embed_dim: int, num_heads: int, rng: np.random.RandomState | None = None):
        self.ln1 = LayerNorm(embed_dim)
        self.attn = MultiHeadAttention(embed_dim, num_heads, rng=rng)
        self.ln2 = LayerNorm(embed_dim)
        self.mlp = FeedForward(embed_dim, rng=rng)

    def forward(self, x: np.ndarray) -> np.ndarray:
        x = x + self.attn.forward(self.ln1.forward(x))
        x = x + self.mlp.forward(self.ln2.forward(x))
        return x
