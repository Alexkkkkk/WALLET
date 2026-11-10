"""Синхронизация всех поверхностей проекта: код ⇄ CLI ⇄ GUI ⇄ упаковка ⇄ README.

Тест падает, если после изменения одной поверхности другая осталась на старом
контракте: забытый экспорт в ``wallet_recover.ai.__all__``, флаг CLI без
документации, зависимость в ``requirements.txt`` без пары в ``pyproject.toml``,
версия в двух местах, GUI в обход общей бизнес-логики.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

import wallet_recover
import wallet_recover.ai as ai_pkg
from wallet_recover import cli

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "requirements.txt").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
GUI_SOURCE = (ROOT / "wallet_recover" / "gui.py").read_text(encoding="utf-8")

# Полный контракт CLI: любое добавление/удаление флага обязано попасть сюда.
CLI_FLAGS = [
    "--version", "--wallet", "--wordlist", "--jobs", "--save", "--print-mkey",
    "--diagnose", "--plan", "--selftest", "--checkpoint", "--resume", "--deduplicate",
    "--ai-generate", "--memory", "--seed", "--ai-provider", "--ai-model",
    "--ai-ollama-url", "--ai-limit", "--ai-no-rank", "--ai-no-neural",
    "--hashcat-hash", "--hashcat-wordlist", "--hashcat-state-dir",
    "--hashcat-resume", "--i-own-wallet",
]


def _strip_requirements(text):
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!\[\]; ]", line, 1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_version_has_one_source_and_readme_agrees():
    assert re.search(r'^dynamic\s*=\s*\["version"\]', PYPROJECT, re.M), \
        "pyproject должен брать версию из пакета, а не дублировать её"
    assert 'version = {attr = "wallet_recover.__init__.__version__"}' in PYPROJECT
    assert not re.search(r'^version\s*=\s*"', PYPROJECT, re.M), \
        "статическая версия в pyproject снова разойдётся с пакетом"
    assert wallet_recover.__version__ in README, "README не упоминает текущую версию"


def test_ai_package_exports_all_resolve():
    missing = [name for name in ai_pkg.__all__ if not hasattr(ai_pkg, name)]
    assert not missing, "в wallet_recover.ai.__all__ есть несуществующие имена: %s" % missing
    assert len(ai_pkg.__all__) == len(set(ai_pkg.__all__)), "дубликаты в __all__"


def test_every_cli_flag_is_documented_in_readme():
    undocumented = [flag for flag in CLI_FLAGS if flag not in README]
    assert not undocumented, "флаги CLI без описания в README: %s" % undocumented


def test_cli_parser_exposes_exact_contract():
    actions = {opt for action in cli.build_parser()._actions for opt in action.option_strings}
    missing = [flag for flag in CLI_FLAGS if flag not in actions]
    assert not missing, "парсер CLI потерял флаги: %s" % missing
    extra = sorted(opt for opt in actions
                   if opt.startswith("--") and opt not in CLI_FLAGS and opt != "--help")
    assert not extra, "появились флаги вне контракта (добавьте их в CLI_FLAGS и README): %s" % extra


def test_requirements_mirror_pyproject_dependencies():
    block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", PYPROJECT, re.M | re.S)
    assert block, "в pyproject нет блока dependencies"
    declared = _strip_requirements(block.group(1).replace('"', "").replace(",", "\n"))
    pinned = _strip_requirements(REQUIREMENTS)
    assert declared == pinned, (
        "requirements.txt и pyproject расходятся: только в pyproject=%s, только в requirements=%s"
        % (sorted(declared - pinned), sorted(pinned - declared)))


def test_entry_points_resolve():
    scripts = re.findall(r'^([\w-]+)\s*=\s*"([\w.]+):(\w+)"', PYPROJECT, re.M)
    assert scripts, "в pyproject нет ни одного скрипта"
    for _, module, attr in scripts:
        imported = __import__(module, fromlist=[attr])
        assert callable(getattr(imported, attr)), "%s:%s не вызывается" % (module, attr)


def test_gui_and_cli_share_the_copilot_logic():
    assert "wallet_plan" in GUI_SOURCE, "GUI должен вызывать общий wallet_recover.plan"
    assert "wallet_plan" in cli.__doc__ + Path(cli.__file__).read_text(encoding="utf-8")
    assert "def wallet_plan" in (ROOT / "wallet_recover" / "plan.py").read_text(encoding="utf-8")


def test_gui_covers_every_backend_feature():
    source = GUI_SOURCE.lower()
    for needle in ("проверить кошелёк", "словарь", "ai-словар", "hashcat", "план",
                   "wallet_plan", "recoveryengine"):
        assert needle in source, "GUI не покрывает возможность: %s" % needle


def test_module_entry_points_delegate_to_cli():
    assert "from .cli import main" in (ROOT / "wallet_recover" / "__main__.py").read_text(encoding="utf-8")
    assert "wallet_recover.cli import main" in (ROOT / "wallet_recover.py").read_text(encoding="utf-8")


def test_cli_version_and_help_run():
    for args in (["--version"], ["--help"]):
        result = subprocess.run([sys.executable, "-m", "wallet_recover"] + args,
                                cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
    version = subprocess.run([sys.executable, "-m", "wallet_recover", "--version"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert wallet_recover.__version__ in version.stdout


def test_diagnose_and_plan_flags_are_wired_to_shared_module():
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "--diagnose" in source and "--plan" in source
    assert "from .plan import wallet_plan" in source
