"""Probabilistic mutation and rule-expansion layer for password candidates.

Applies Hashcat-like rule expansion powered by frequency priors:
l33t substitutions, case mutations, year/symbol affixes, fragment chaining,
and 1-edit distance typo variants.
"""
from __future__ import annotations

import itertools
import string
from typing import Iterator, Iterable, Sequence

LEET = {
    "a": ["@", "4"], "e": ["3"], "i": ["1", "!"], "o": ["0"],
    "s": ["$", "5"], "t": ["7"], "b": ["8"], "g": ["9"],
    "А": ["@", "4"], "Е": ["3"], "И": ["1", "!"], "О": ["0"],
    "С": ["$", "5"], "Т": ["7"], "Б": ["8"],
}

COMMON_SUFFIXES = [
    "", "1", "12", "123", "1234", "12345", "123456", "111", "777", "999",
    "007", "!", "!!", "!?", "@", "@@", "#", "$", "_", "-", ".", "*",
    "1!", "123!", "1234!", "!123", "?", "!!?",
]

DEFAULT_SYMBOLS = ["!", "@", "#", "$", "_", "-", ".", "*", "%"]

COMMON_YEARS = [str(y) for y in range(1995, 2027)] + [f"{y:02d}" for y in range(0, 27)]


def leet_variants(word: str, max_variants: int = 64) -> list[str]:
    """Generate l33t-speak variants bounded by max_variants."""
    positions = [(i, LEET[ch.lower()]) for i, ch in enumerate(word) if ch.lower() in LEET]
    results = {word}
    for i, alts in positions[:8]:
        new = set()
        for cand in results:
            for alt in alts:
                new.add(cand[:i] + alt + cand[i + 1:])
                if len(new) + len(results) >= max_variants:
                    break
            if len(new) + len(results) >= max_variants:
                break
        results.update(new)
        if len(results) >= max_variants:
            break
    return list(results)[:max_variants]


def case_variants(word: str) -> list[str]:
    """Generate case variations."""
    vars_: list[str] = [word, word.lower(), word.upper(), word.capitalize()]
    # alternate case: aBcD
    alt = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(word))
    vars_.append(alt)
    vars_.append(alt.swapcase())
    seen = set()
    return [v for v in vars_ if not (v in seen or seen.add(v))]


def edit_distance_1(word: str, max_results: int = 120) -> list[str]:
    """One-character typo variants (delete, transpose, replace, insert)."""
    alphabet = string.ascii_lowercase + "абвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789!@#$%_-"
    w = word
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    deletes = [L + R[1:] for L, R in splits if R]
    transposes = [L + R[1] + R[0] + R[2:] for L, R in splits if len(R) > 1]
    replaces = [L + c + R[1:] for L, R in splits if R for c in alphabet[:40]]
    inserts = [L + c + R for L, R in splits for c in alphabet[:40]]
    seen = {word}
    out: list[str] = []
    for c in itertools.chain(deletes, transposes, replaces, inserts):
        if c not in seen:
            seen.add(c)
            out.append(c)
            if len(out) >= max_results:
                break
    return out


def chain_fragments(fragments: Sequence[str], separators: Sequence[str] = ("", "_", "-", ".", "!"),
                    max_words: int = 2, max_results: int = 1500) -> Iterator[str]:
    """Permute fragments with separators (e.g. 'Dog_2019', 'Crypto.Moon.2021')."""
    frags = [f for f in fragments if f][:10]
    count = 0
    seen = set()
    for depth in range(1, min(max_words + 1, len(frags) + 1)):
        for combo in itertools.permutations(frags, depth):
            if depth == 1:
                cand = combo[0]
                if cand not in seen:
                    seen.add(cand)
                    count += 1
                    yield cand
                continue
            for sep in separators:
                cand = sep.join(combo)
                if cand not in seen:
                    seen.add(cand)
                    count += 1
                    yield cand
                if count >= max_results:
                    return


def smart_expand(
    seeds: Iterable[str],
    years: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    suffixes: Sequence[str] | None = None,
    include_leet: bool = True,
    include_case: bool = True,
    include_typos: bool = False,
    include_chain: bool = True,
    max_candidates: int = 50000,
) -> Iterator[str]:
    """Combine all mutation strategies into a deduplicated candidate stream."""
    seed_list = list(dict.fromkeys(s.strip() for s in seeds if s and s.strip()))[:20]
    years = list(years) if years is not None else COMMON_YEARS[:14]
    symbols = list(symbols) if symbols is not None else DEFAULT_SYMBOLS[:6]
    suffixes = list(suffixes) if suffixes is not None else COMMON_SUFFIXES[:12]

    seen = set()
    count = 0

    def emit(cand: str) -> bool:
        nonlocal count
        if cand and cand not in seen:
            seen.add(cand)
            count += 1
            return True
        return False

    # 1. Exact seeds
    for s in seed_list:
        if emit(s):
            yield s
            if count >= max_candidates:
                return

    # 2. Case variations of seeds
    if include_case:
        for s in seed_list:
            for cv in case_variants(s):
                if emit(cv):
                    yield cv
                    if count >= max_candidates:
                        return

    # 3. Affixes on case variants (years, symbols, common suffixes)
    affix_pool: list[str] = [f for f in dict.fromkeys(symbols + years + suffixes)]
    base_pool = list(seen)[:60]
    for b in base_pool:
        for aff in affix_pool:
            for cand in (f"{b}{aff}", f"{aff}{b}", f"{b}_{aff}", f"{b}-{aff}", f"{b}.{aff}"):
                if emit(cand):
                    yield cand
                    if count >= max_candidates:
                        return

    # 4. Two-word chaining
    if include_chain:
        for cand in chain_fragments(seed_list, max_words=2, max_results=max(500, max_candidates // 10)):
            if emit(cand):
                yield cand
                if count >= max_candidates:
                    return

    # 5. l33t variants
    if include_leet:
        for s in list(seen)[:40]:
            for lv in leet_variants(s, max_variants=32):
                if emit(lv):
                    yield lv
                    if count >= max_candidates:
                        return

    # 6. Typos (off by default, useful for known exact-ish words)
    if include_typos:
        for s in seed_list:
            for tv in edit_distance_1(s):
                if emit(tv):
                    yield tv
                    if count >= max_candidates:
                        return
