"""WALLEt AI subsystem: layered neural password recovery intelligence.

Layer 1: CharTransformerLM - autoregressive character-level Transformer model
Layer 2: LLM semantic memory reconstruction with transliteration & keyboard layout shift
Layer 3: Probabilistic mutation & rule-expansion (leet, case, affixes, fragments, typos)
Layer 4: Neural candidate prioritizer / probability ranker
Layer 5: AI Wallet Copilot diagnostics, benchmarks and strategy planner
Pipeline: unified orchestrated candidate stream with ranking & checkpoint support
"""
from .layers import (
    Embedding,
    PositionalEmbedding,
    LayerNorm,
    MultiHeadAttention,
    FeedForward,
    TransformerBlock,
)
from .char_transformer import CharTransformerLM, CharTokenizer
from .llm_generator import (
    reconstruct_from_prompt,
    transliterate_ru_en,
    transliterate_en_ru,
    keyboard_layout_shift,
    MemoryReconstructionResult,
)
from .mutator import (
    leet_variants,
    case_variants,
    edit_distance_1,
    chain_fragments,
    smart_expand,
)
from .ranker import rank_candidates, ranked_stream, score_candidate
from .advisor import diagnose, benchmark, strategy_report, WalletDiagnosis
from .pipeline import build_candidate_stream, export_candidates

__all__ = [
    "Embedding", "PositionalEmbedding", "LayerNorm", "MultiHeadAttention",
    "FeedForward", "TransformerBlock",
    "CharTransformerLM", "CharTokenizer",
    "reconstruct_from_prompt", "transliterate_ru_en", "transliterate_en_ru",
    "keyboard_layout_shift", "MemoryReconstructionResult",
    "leet_variants", "case_variants", "edit_distance_1", "chain_fragments", "smart_expand",
    "rank_candidates", "ranked_stream", "score_candidate",
    "diagnose", "benchmark", "strategy_report", "WalletDiagnosis",
    "build_candidate_stream", "export_candidates",
    "build_hashcat_command", "run_hashcat", "HASHCAT_MODE_BITCOIN_WALLET",
]
from .hashcat import build_hashcat_command, run_hashcat, HASHCAT_MODE_BITCOIN_WALLET
