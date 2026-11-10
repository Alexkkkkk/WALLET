"""Neural candidate prioritizer.

Scores passwords with a hybrid of CharTransformerLM log-likelihood,
structural priors (length distribution, digit/symbol positioning) and edit
proximity to user context, so the most probable candidates are tested first.
"""
from __future__ import annotations

import math
import re
from typing import Iterator, Iterable, Sequence

from .char_transformer import CharTransformerLM

DIGIT_TAIL = re.compile(r"\d{1,4}[^a-zA-Z0-9]*$")


def _edit_dist(a: str, b: str) -> int:
    """Levenshtein distance (bounded)."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 6:
        return 99
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        ndp = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            ndp[j] = dp[j - 1] if ca == cb else 1 + min(dp[j], ndp[j - 1], dp[j - 1])
        dp = ndp
    return dp[-1]


def length_prior(length: int) -> float:
    """Human password length distribution peaks around 9-14 chars."""
    mu, sigma = 11.5, 3.2
    return math.exp(-0.5 * ((length - mu) / sigma) ** 2)


def structure_bonus(password: str) -> float:
    b = 0.0
    if password[:1].isupper() and password[1:2].islower():
        b += 0.25  # capitalized word
    if DIGIT_TAIL.search(password):
        b += 0.35  # digits at the end
    if any(c in "!@#$%^&*()_+-=" for c in password):
        b += 0.2   # has special char
    if password.isdigit():
        b -= 0.5   # all digits -> brute-forceable separately
    if len(password) < 6:
        b -= 0.6   # too short
    return b


def score_candidate(password: str, lm: CharTransformerLM, seeds: Sequence[str] = ()) -> float:
    """Higher score = higher probability to test first."""
    if not password:
        return -1e9
    # LM log-likelihood (typically -3.5 .. -1.2)
    ll = lm.log_likelihood(password)
    # Edit proximity to any user context word
    prox = 0.0
    for s in seeds[:8]:
        d = _edit_dist(password.lower(), s.lower())
        if d == 0:
            prox = max(prox, 1.5)
        elif d == 1:
            prox = max(prox, 1.0)
        elif d == 2:
            prox = max(prox, 0.5)
        elif d <= 4:
            prox = max(prox, 0.2)
    return ll * 1.0 + 1.2 * length_prior(len(password)) + structure_bonus(password) + prox


def rank_candidates(candidates: Iterable[str], lm: CharTransformerLM | None = None,
                    seeds: Sequence[str] = (), limit: int | None = None) -> list[str]:
    """Sort candidates by probability descending."""
    lm = lm or CharTransformerLM(embed_dim=48, num_heads=4, num_layers=2)
    seen = set()
    scored = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            scored.append((score_candidate(c, lm, seeds), c))
    scored.sort(key=lambda t: t[0], reverse=True)
    if limit is not None:
        scored = scored[:limit]
    return [c for _, c in scored]


def ranked_stream(candidates: Iterable[str], lm: CharTransformerLM | None = None,
                  seeds: Sequence[str] = (), chunk_size: int = 500) -> Iterator[str]:
    """Buffer candidates in chunks, sort each chunk by probability, and yield."""
    lm = lm or CharTransformerLM(embed_dim=48, num_heads=4, num_layers=2)
    buf: list[str] = []
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            buf.append(c)
            if len(buf) >= chunk_size:
                for ranked in rank_candidates(buf, lm, seeds):
                    yield ranked
                buf.clear()
    if buf:
        for ranked in rank_candidates(buf, lm, seeds):
            yield ranked
