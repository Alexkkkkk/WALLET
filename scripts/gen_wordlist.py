#!/usr/bin/env python3
"""Generate a bounded, personal candidate wordlist locally.

This script never downloads password leaks or makes network requests. Use it only
with your own wallet and your own remembered fragments.
"""
from __future__ import annotations

import argparse
import itertools
import os
from pathlib import Path

LEET = {
    "a": ("4", "@"),
    "e": ("3",),
    "i": ("1", "!"),
    "o": ("0",),
    "s": ("5", "$"),
    "t": ("7",),
    "l": ("1",),
}
SUFFIXES = ("", "1", "12", "123", "!", "!!", "123!", "007", "00")
PREFIXES = ("", "1", "12", "!", "@", "#", "my", "the")


def leet_variants(word: str, limit: int = 32) -> set[str]:
    variants = {word}
    for index, char in enumerate(word.lower()):
        replacements = LEET.get(char)
        if not replacements:
            continue
        next_variants = set(variants)
        for value in variants:
            for replacement in replacements:
                next_variants.add(value[:index] + replacement + value[index + 1:])
                if len(next_variants) >= limit:
                    break
            if len(next_variants) >= limit:
                break
        variants = next_variants
    return variants


def mutations(word: str) -> list[str]:
    bases = {word, word.lower(), word.upper(), word.capitalize(), word.title()}
    bases.update(leet for base in tuple(bases) for leet in leet_variants(base))
    result: list[str] = []
    seen: set[str] = set()
    for base in bases:
        for prefix in PREFIXES:
            for suffix in SUFFIXES:
                candidate = prefix + base + suffix
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    result.append(candidate)
    return result


def candidates(seeds: list[str], dates: list[str], years: range):
    clean_seeds = [item.strip() for item in seeds if item.strip()]
    clean_dates = [item.strip() for item in dates if item.strip()]
    year_values = [str(year) for year in years]
    for seed in clean_seeds:
        yield seed
        yield from mutations(seed)
        for value in (*clean_dates, *year_values):
            yield from (f"{seed}{value}", f"{value}{seed}", f"{seed}_{value}", f"{seed}{value}!")
    for left, right in itertools.permutations(clean_seeds, 2):
        yield from (left + right, left + "_" + right, left.capitalize() + right.capitalize())


def write_unique(output: Path, values, limit: int) -> int:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp")
    seen: set[str] = set()
    count = 0
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            for value in values:
                if value in seen:
                    continue
                if count >= limit:
                    break
                seen.add(value)
                stream.write(value + "\n")
                count += 1
        os.replace(temp, output)
        try:
            output.chmod(0o600)
        except OSError:
            pass
        return count
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local personal wordlist generator")
    parser.add_argument("--seed", action="append", default=[], help="Remembered word/name; repeatable")
    parser.add_argument("--date", action="append", default=[], help="Remembered date fragment; repeatable")
    parser.add_argument("--out", required=True, help="New output wordlist path")
    parser.add_argument("--limit", type=int, default=100_000, help="Maximum candidates")
    parser.add_argument("--from-year", type=int, default=2009)
    parser.add_argument("--to-year", type=int, default=2015)
    args = parser.parse_args(argv)
    if not args.seed:
        parser.error("at least one --seed is required")
    if args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.from_year > args.to_year:
        parser.error("--from-year must not exceed --to-year")
    count = write_unique(
        Path(args.out).expanduser(),
        candidates(args.seed, args.date, range(args.from_year, args.to_year + 1)),
        args.limit,
    )
    print(f"Created {count} unique local candidates: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
