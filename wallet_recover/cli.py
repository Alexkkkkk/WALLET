"""Offline wordlist runner; no network access or telemetry."""
import argparse
import multiprocessing
import os
from pathlib import Path
import sys
from . import __version__
from ._console import configure_console_streams
from .bdb import load_wallet
from .crypto import Decryptor

_MASTERS = _KEYS = _DECRYPTOR = None

def init_worker(masters, keys):
    global _MASTERS, _KEYS, _DECRYPTOR
    _MASTERS, _KEYS = masters, keys
    _DECRYPTOR = Decryptor(masters, keys)

def worker(password):
    return password if _DECRYPTOR.try_password(password) else None

def candidates(path):
    from .wordlist import iter_lines
    yield from iter_lines(path)

def positive(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n

def save_password(path, password):
    # Atomic exclusive creation: never overwrite a wallet, wordlist or existing secret.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(password)

def build_parser():
    """Single source of truth for every CLI flag (README and tests follow it)."""
    p = argparse.ArgumentParser(
        prog="wallet-recover",
        description="Offline recovery for your own legacy Bitcoin Core wallet")
    p.add_argument("--version", action="version", version="wallet-recover " + __version__)
    p.add_argument("--wallet", help="Closed wallet backup (BDB)")
    p.add_argument("--wordlist", help="Binary/UTF-8 candidates, one per line")
    p.add_argument("--jobs", type=positive, default=max(1, min(64, os.cpu_count() or 1)))
    p.add_argument("--checkpoint", help="Private atomic checkpoint JSON path (must not be an input file)")
    p.add_argument("--resume", action="store_true", help="Resume --checkpoint, validating wallet and wordlist content")
    p.add_argument("--deduplicate", metavar="NEW_PATH", help="Write a unique wordlist to a NEW private file, then exit")
    p.add_argument("--save", help="NEW output file for exact password bytes (mode 0600)")
    p.add_argument("--print-mkey", action="store_true", help="Inspect parameters without recovery")
    p.add_argument("--diagnose", action="store_true",
                   help="AI-диагностика кошелька: метод KDF, раунды, salt, ckey, сложность")
    p.add_argument("--plan", action="store_true",
                   help="Полный отчёт AI Wallet Copilot: диагностика + бенчмарк + фазы атаки")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--ai-generate", metavar="PATH", help="Generate a prioritized AI wordlist locally; output must not exist")
    p.add_argument("--memory", default="", help="Password fragments/memories (sent only to the explicitly selected provider)")
    p.add_argument("--seed", action="append", default=[], help="Known word/name fragment; may be repeated")
    p.add_argument("--ai-provider", choices=("offline", "ollama", "openai"), default="offline",
                   help="AI provider (default: offline; Ollama is local; openai sends prompt text remotely)")
    p.add_argument("--ai-model", help="Model name (default: mistral for Ollama, gpt-5-mini for OpenAI)")
    p.add_argument("--ai-ollama-url", default="http://127.0.0.1:11434", help="Local Ollama URL")
    p.add_argument("--ai-limit", type=positive, default=5000, help="Maximum generated candidates")
    p.add_argument("--ai-no-rank", action="store_true", help="Skip neural probability ranking")
    p.add_argument("--ai-no-neural", action="store_true", help="Skip experimental local character Transformer sampling")
    p.add_argument("--hashcat-hash", help="Run installed local Hashcat against a locally extracted wallet hash")
    p.add_argument("--hashcat-wordlist", help="Candidate file for local Hashcat (default: --wordlist)")
    p.add_argument("--hashcat-state-dir", default=".wallet-recover-hashcat", help="Private local Hashcat potfile directory")
    p.add_argument("--hashcat-resume", action="store_true", help="Resume saved local Hashcat session")
    p.add_argument("--i-own-wallet", action="store_true", help="Confirm this Hashcat operation targets your wallet or one you are authorized to recover")
    return p

def main(argv=None):
    # PyInstaller's override must intercept spawned workers before GUI dispatch
    # or argparse sees its internal --multiprocessing-fork arguments.
    multiprocessing.freeze_support()
    configure_console_streams()
    if getattr(sys, "frozen", False) and argv is None and not sys.argv[1:]:
        from .gui import main as gui_main
        return gui_main()
    p = build_parser()
    args = p.parse_args(argv)
    try:
        if args.resume and not args.checkpoint:
            p.error("--resume requires --checkpoint")
        if args.deduplicate:
            if not args.wordlist:
                p.error("--deduplicate requires --wordlist")
            from .wordlist import export_unique
            n = export_unique(args.wordlist, args.deduplicate)
            print(f"Unique candidates written: {n}")
            return 0
        if args.selftest:
            from .selftest import run
            run()
            print("Self-test passed (synthetic records; not a real wallet)")
            return 0
        if args.ai_generate:
            if not args.memory and not args.seed and not args.wordlist:
                p.error("--ai-generate requires at least one of --memory, --seed, or --wordlist")
            from .ai.pipeline import export_candidates
            ai_model = args.ai_model or ("gpt-5-mini" if args.ai_provider == "openai" else "mistral")
            count = export_candidates(
                args.ai_generate,
                prompt=args.memory,
                seed_words=args.seed,
                wordlist=args.wordlist,
                max_candidates=args.ai_limit,
                provider=args.ai_provider,
                model=ai_model,
                ollama_url=args.ai_ollama_url,
                include_neural_lm=not args.ai_no_neural,
                rank=not args.ai_no_rank,
            )
            print(f"Создан локальный словарь: {args.ai_generate} ({count} уникальных кандидатов)")
            if args.ai_provider == "openai":
                print("ВНИМАНИЕ: текст воспоминаний был отправлен выбранному внешнему AI-провайдеру.")
            else:
                print("Сетевые запросы не выполнялись; выходной файл создан с правами 0600.")
            return 0
        if args.hashcat_hash:
            if not args.i_own_wallet:
                p.error("Перед Hashcat-проверкой подтвердите владение флагом --i-own-wallet")
            hash_wordlist = args.hashcat_wordlist or args.wordlist
            if not hash_wordlist:
                p.error("Для Hashcat нужны --hashcat-wordlist или --wordlist")
            from .ai.hashcat import run_hashcat
            code = run_hashcat(args.hashcat_hash, hash_wordlist, args.hashcat_state_dir,
                               resume=args.hashcat_resume)
            print("Hashcat завершил работу. Hash и potfile остались на этом компьютере.")
            print(f"Секретный potfile: {Path(args.hashcat_state_dir) / 'wallet.potfile'}")
            return code
        if args.diagnose or args.plan:
            if not args.wallet:
                p.error("--diagnose/--plan требуют --wallet")
            from .plan import wallet_plan
            plan = wallet_plan(args.wallet, jobs=args.jobs)
            if args.plan:
                print(plan.report)
            else:
                print(plan.summary())
                print(f"Скорость (1 ядро): ~{plan.speed_ps:.1f} паролей/с")
            return 0
        if not args.wallet:
            p.error("--wallet is required")
        masters, keys = load_wallet(args.wallet)
        if not masters:
            raise ValueError("No mkey: unencrypted, unsupported or incomplete wallet; not a recovery result")
        for m in masters:
            print(f"mkey {m.id}: method={m.method}, rounds={m.iterations}, salt={m.salt.hex()}")
        print(f"ckey records: {len(keys)}")
        if args.print_mkey:
            return 0
        if any(m.method != 0 or m.other for m in masters):
            raise ValueError("Unsupported KDF parameters; only method 0 without extra parameters supported")
        if not keys:
            raise ValueError("No ckey: cannot confirm a password; refusing padding-only matches")
        if not args.wordlist:
            p.error("--wordlist is required for recovery")
        if not Path(args.wordlist).is_file():
            raise ValueError("Wordlist does not exist")
        if args.save and os.path.lexists(args.save):
            raise ValueError("--save file already exists; choose a new path")
        from .runner import RecoveryEngine
        from .progress import Progress
        if args.checkpoint and args.save and Path(args.checkpoint).resolve() == Path(args.save).resolve():
            raise ValueError("Checkpoint and password output must be different files")
        state = {"found": None, "error": None}
        progress = Progress(total=0)
        def sink(event, *values):
            if event == "total":
                progress.total = values[0]
            elif event == "progress":
                progress.update(values[0], rate=values[1], eta=values[2])
            elif event == "found":
                state["found"] = values[0]
            elif event == "failed":
                state["error"] = values[0]
            elif event == "log":
                print(values[0], file=sys.stderr)
        engine = RecoveryEngine(args.wallet, args.wordlist, args.jobs, sink,
                                checkpoint_path=args.checkpoint, resume=args.resume)
        thread = engine.start()
        interrupted = False
        try:
            while thread.is_alive():
                thread.join(0.1)
        except KeyboardInterrupt:
            interrupted = True
            engine.stop()
            print("Stopping; saving the completed prefix…", file=sys.stderr)
            while thread.is_alive():
                try:
                    thread.join(0.1)
                except KeyboardInterrupt:
                    engine.stop()
        finally:
            progress.update(engine.completed, rate=engine.rate)
            progress.close()
        if state["error"]:
            raise ValueError(state["error"])
        if interrupted:
            return 130
        completed, found, elapsed = engine.completed, state["found"], engine.elapsed
        if found is None:
            print(f"Not found: {completed} completed candidates in {elapsed:.1f}s")
            return 1
        print(f"Confirmed by public-key match after {completed} completed results ({elapsed:.1f}s)")
        if args.save:
            save_password(args.save, found)
            print("Password saved as exact bytes (no newline); protect the output file")
        else:
            print("Password bytes:", repr(found))
            print("Password hex:", found.hex())
        return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
