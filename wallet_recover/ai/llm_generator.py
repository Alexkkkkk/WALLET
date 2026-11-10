"""LLM-powered semantic memory reconstruction of forgotten passwords.

Accepts natural language memories in Russian/English, extracts semantic entities,
applies bidirectional transliteration and keyboard-layout conversion, and returns
structured candidate clusters with confidence scores. Gracefully falls back to a
pure-offline heuristic NLP reconstructor when no API key or internet is available.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Sequence

log = logging.getLogger(__name__)

RU_TO_EN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
EN_TO_RU_MULTI = {
    "sch": "щ", "sh": "ш", "ch": "ч", "ts": "ц", "kh": "х", "zh": "ж",
    "yo": "ё", "yu": "ю", "ya": "я",
}
EN_TO_RU = {
    "a": "а", "b": "б", "c": "ц", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "ы", "z": "з",
}

RU_LAYOUT = "йцукенгшщзхъфывапролджэячсмитьбю.ё"
EN_LAYOUT = "qwertyuiop[]asdfghjkl;'zxcvbnm,./`"
RU_TO_EN_KEY = {RU_LAYOUT[i]: EN_LAYOUT[i] for i in range(min(len(RU_LAYOUT), len(EN_LAYOUT)))}
EN_TO_RU_KEY = {EN_LAYOUT[i]: RU_LAYOUT[i] for i in range(min(len(RU_LAYOUT), len(EN_LAYOUT)))}


def transliterate_ru_en(text: str) -> str:
    """Russian -> Latin transliteration (preserves case)."""
    out: list[str] = []
    for ch in text:
        lower = ch.lower()
        mapped = RU_TO_EN.get(lower, lower)
        out.append(mapped.upper() if ch.isupper() else mapped)
    return "".join(out)


def transliterate_en_ru(text: str) -> str:
    """Latin -> Russian transliteration (approximate)."""
    t = text.lower()
    for src, dst in EN_TO_RU_MULTI.items():
        t = t.replace(src, dst)
    out: list[str] = []
    for ch, orig in zip(t, text):
        mapped = EN_TO_RU.get(ch, ch)
        out.append(mapped.upper() if orig.isupper() else mapped)
    return "".join(out)


def keyboard_layout_shift(text: str) -> str:
    """Flip Russian/English keyboard layout for accidentally-shifted input."""
    out: list[str] = []
    for ch in text:
        lo = ch.lower()
        if lo in RU_TO_EN_KEY:
            mapped = RU_TO_EN_KEY[lo]
            out.append(mapped.upper() if ch.isupper() else mapped)
        elif lo in EN_TO_RU_KEY:
            mapped = EN_TO_RU_KEY[lo]
            out.append(mapped.upper() if ch.isupper() else mapped)
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class CandidateIdea:
    password: str
    confidence: float
    source: str = "llm"


@dataclass
class MemoryReconstructionResult:
    entities: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    separators: list[str] = field(default_factory=list)
    candidates: list[CandidateIdea] = field(default_factory=list)
    backend: str = "offline"
    summary: str = ""


SYSTEM_PROMPT = """You are a password recovery assistant helping an authorized wallet owner reconstruct their forgotten legacy Bitcoin wallet password from vague memories.

Analyze the user's memory (which may be in Russian, English, or mixed), and return ONLY valid JSON:
{
  "entities": ["list of base words/names in both Russian and Latin transliteration"],
  "years": ["extracted or implied 4-digit and 2-digit years"],
  "symbols": ["special characters mentioned or likely"],
  "separators": ["delimiters like _, -, ., :"],
  "summary": "one sentence describing the plausible password pattern",
  "candidates": [
    {"password": "...", "confidence": 0.0-1.0, "reasoning": "short"}
  ]
}
Rules:
- Include 20-40 concrete password candidates, ordered from most to least probable.
- Mix Russian and Latin variants (translit), capitalized/lowercase, symbol/year placements.
- Output pure JSON, no markdown fences, no commentary."""


def _ollama_reconstruct(prompt: str, model: str = "mistral",
                        base_url: str = "http://127.0.0.1:11434") -> MemoryReconstructionResult:
    """Use a local Ollama server; the memory prompt stays on this computer."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError
    endpoint = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.1},
    }
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError("Не удалось подключиться к локальному Ollama на " + endpoint) from exc
    raw = data.get("message", {}).get("content", "{}")
    parsed = json.loads(raw)
    candidates = [
        CandidateIdea(password=c.get("password", ""), confidence=float(c.get("confidence", 0.5)),
                      source="ollama:" + model)
        for c in parsed.get("candidates", []) if c.get("password")
    ]
    return MemoryReconstructionResult(
        entities=list(parsed.get("entities", [])),
        years=[str(y) for y in parsed.get("years", [])],
        symbols=list(parsed.get("symbols", [])),
        separators=list(parsed.get("separators", [])),
        candidates=candidates,
        backend="ollama:" + model,
        summary=parsed.get("summary", ""),
    )


def _llm_reconstruct(prompt: str, model: str = "gpt-5-mini") -> MemoryReconstructionResult:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        reasoning_effort="minimal",
        max_completion_tokens=2500,
    )
    raw = response.choices[0].message.content or "{}"
    data = json.loads(raw)
    cand_list = [
        CandidateIdea(password=c.get("password", ""), confidence=float(c.get("confidence", 0.5)),
                      source=f"llm:{model}")
        for c in data.get("candidates", []) if c.get("password")
    ]
    return MemoryReconstructionResult(
        entities=list(data.get("entities", [])),
        years=[str(y) for y in data.get("years", [])],
        symbols=list(data.get("symbols", [])),
        separators=list(data.get("separators", [])),
        candidates=cand_list,
        backend=f"llm:{model}",
        summary=data.get("summary", ""),
    )


def _offline_reconstruct(prompt: str) -> MemoryReconstructionResult:
    # Year extraction: 1970-2035 and 2-digit years
    years = sorted(set(re.findall(r"\b(19\d{2}|20\d{2})\b", prompt)))
    short_years = []
    for y in years:
        short_years.append(y[-2:])
    # Symbol extraction
    sym_map = {"восклицатель": "!", "собач": "@", "решетк": "#", "доллар": "$", "процент": "%",
               "плюс": "+", "тире": "-", "минус": "-", "подчеркив": "_", "точка": ".", "звездоч": "*"}
    symbols: list[str] = list(set(re.findall(r"[!@#$%^&*()_\-+=\[\]{}:;,\./?]", prompt)))
    plower = prompt.lower()
    for needle, sym in sym_map.items():
        if needle in plower and sym not in symbols:
            symbols.append(sym)
    if not symbols:
        symbols = ["!", "@", "$"]
    # Tokenization: keep alphanumeric tokens of 2+ chars
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", prompt)
    stop = {"помню", "был", "пароль", "что", "вроде", "может", "быть", "года", "год", "и",
            "знак", "знаки", "или", "не", "точно", "имя", "слово", "про", "лето", "зима",
            "remember", "the", "password", "was", "and", "or", "maybe", "like", "year"}
    entities: list[str] = []
    for t in tokens:
        if t.lower() in stop or t.isdigit():
            continue
        entities.append(t)
    # Deduplicate preserving order
    seen = set()
    uniq_entities = []
    for e in entities:
        if e.lower() not in seen:
            seen.add(e.lower())
            uniq_entities.append(e)
    # Add translit versions
    for e in list(uniq_entities):
        tr = transliterate_ru_en(e)
        if tr != e and tr.lower() not in seen:
            seen.add(tr.lower())
            uniq_entities.append(tr)
    # Build candidate combinations
    separators = ["", "_", "-", ".", "!"]
    all_years = years + short_years
    candidates: list[CandidateIdea] = []
    base_variants: list[str] = []
    for e in uniq_entities[:8]:
        for var in {e, e.lower(), e.title(), e.upper()}:
            base_variants.append(var)
    base_variants = list(dict.fromkeys(base_variants))
    def add(p: str, conf: float):
        if p and p not in [c.password for c in candidates]:
            candidates.append(CandidateIdea(p, conf, source="offline_nlp"))
    for b in base_variants[:20]:
        add(b, 0.4)
        for y in (all_years[:6] or [""]):
            for sym in symbols[:4]:
                add(f"{b}{y}", 0.75)
                add(f"{b}{y}{sym}", 0.85)
                add(f"{b}{sym}{y}", 0.8)
                add(f"{b}_{y}", 0.7)
                add(f"{b}_{y}{sym}", 0.75)
                add(f"{b}{sym}", 0.65)
                add(f"{y}{b}", 0.6)
                add(f"{y}{b}{sym}", 0.65)
    # Layout-shifted variants
    for b in base_variants[:6]:
        shifted = keyboard_layout_shift(b)
        add(shifted, 0.5)
        for y in all_years[:2]:
            add(f"{shifted}{y}", 0.55)
    candidates.sort(key=lambda c: -c.confidence)
    return MemoryReconstructionResult(
        entities=uniq_entities,
        years=all_years,
        symbols=symbols,
        separators=separators,
        candidates=candidates[:40],
        backend="offline_nlp",
        summary=f"Базовые сущности: {', '.join(uniq_entities[:5])}; годы: {', '.join(all_years[:4])}",
    )


def reconstruct_from_prompt(prompt: str, model: str = "mistral", provider: str = "offline",
                            offline_only: bool = False,
                            ollama_url: str = "http://127.0.0.1:11434") -> MemoryReconstructionResult:
    """Analyze a memory prompt using offline heuristics, local Ollama, or remote OpenAI.

    Privacy-safe default is ``offline``. Remote OpenAI must be explicitly selected.
    """
    prompt = prompt.strip()
    if not prompt:
        return MemoryReconstructionResult(summary="Пустой промпт")
    if offline_only or provider == "offline":
        return _offline_reconstruct(prompt)
    if provider == "ollama":
        try:
            return _ollama_reconstruct(prompt, model=model, base_url=ollama_url)
        except Exception as exc:
            log.warning("Local Ollama failed (%s), falling back to offline", exc)
            return _offline_reconstruct(prompt)
    if provider == "openai":
        try:
            return _llm_reconstruct(prompt, model=model)
        except Exception as exc:
            log.warning("Remote LLM reconstruction failed (%s), falling back to offline", exc)
            return _offline_reconstruct(prompt)
    if provider != "offline":
        raise ValueError("provider must be one of: offline, ollama, openai")
    return _offline_reconstruct(prompt)
