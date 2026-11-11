"""Console encoding must not turn a successful CLI operation into a failure."""
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

from wallet_recover import cli

ROOT = Path(__file__).resolve().parents[1]


def invoke_encoded(encoding, *args, entrypoint="module", code=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = encoding + ":strict"
    if code is not None:
        command = [sys.executable, "-c", code]
    elif entrypoint == "script":
        command = [sys.executable, str(ROOT / "wallet_recover.py")]
    else:
        command = [sys.executable, "-m", "wallet_recover"]
    return subprocess.run(command + list(map(str, args)), cwd=str(ROOT), env=env,
                          capture_output=True, timeout=60)


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp866"])
@pytest.mark.parametrize("entrypoint", ["module", "script"])
def test_ai_generation_succeeds_with_legacy_console(encoding, entrypoint, tmp_path):
    output = tmp_path / "generated.txt"
    result = invoke_encoded(
        encoding, "--ai-generate", output, "--memory", "build-check",
        "--ai-provider", "offline", "--ai-limit", "20", entrypoint=entrypoint,
    )
    assert result.returncode == 0, repr(result.stdout + result.stderr)
    assert not result.stderr
    words = output.read_bytes().decode("utf-8").splitlines()
    assert 0 < len(words) <= 20
    message = "Создан локальный словарь: {} ({} уникальных кандидатов)".format(output, len(words))
    assert message.encode(encoding, "backslashreplace") in result.stdout
    assert "Сетевые запросы не выполнялись".encode(encoding, "backslashreplace") in result.stdout
    assert b"UnicodeEncodeError" not in result.stdout + result.stderr


def test_legacy_console_does_not_escape_utf8_wordlist(tmp_path):
    output = tmp_path / "generated.txt"
    result = invoke_encoded(
        "cp1252", "--ai-generate", output, "--seed", "Барсик",
        "--ai-provider", "offline", "--ai-limit", "20", "--ai-no-neural", "--ai-no-rank",
    )
    assert result.returncode == 0, repr(result.stdout + result.stderr)
    raw = output.read_bytes()
    assert "Барсик".encode("utf-8") in raw
    assert b"\\u0411" not in raw
    assert len(raw.decode("utf-8").splitlines()) <= 20


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp866", "utf-8"])
def test_help_succeeds_with_legacy_console(encoding):
    result = invoke_encoded(encoding, "--help")
    assert result.returncode == 0, repr(result.stdout + result.stderr)
    assert "AI-диагностика".encode(encoding, "backslashreplace") in result.stdout


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp866"])
def test_argument_error_with_strict_stderr(encoding):
    # Python normally forces backslashreplace on stderr even with
    # PYTHONIOENCODING=:strict. Explicitly make it strict to test our handling.
    code = (
        "import sys; sys.stderr.reconfigure(errors='strict'); "
        "from wallet_recover.cli import main; "
        "raise SystemExit(main(['--hashcat-hash', 'unused.hash']))"
    )
    result = invoke_encoded(encoding, code=code)
    assert result.returncode == 2, repr(result.stdout + result.stderr)
    assert "подтвердите владение".encode(encoding, "backslashreplace") in result.stderr
    assert b"UnicodeEncodeError" not in result.stderr


class LegacyStream:
    """An encoding-strict text stream without TextIOWrapper.reconfigure."""
    def __init__(self, encoding="ascii"):
        self.encoding = encoding
        self.errors = "strict"
        self.buffer = io.BytesIO()
        self.flushes = 0

    def write(self, text):
        self.buffer.write(text.encode(self.encoding, self.errors))
        return len(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        self.flushes += 1

    def isatty(self):
        return False


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp866", "utf-8"])
def test_reconfigure_preserves_encoding_and_binary_buffer(monkeypatch, tmp_path, encoding):
    from wallet_recover._console import configure_console_streams

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding, errors="strict", newline="\n")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    configure_console_streams()
    assert sys.stdout is stream
    assert sys.stderr is stream
    assert stream.encoding == encoding
    assert stream.errors == "backslashreplace"
    text = "Привет, café, € and 😀\n"
    stream.write(text)
    stream.flush()
    assert raw.getvalue() == text.encode(encoding, "backslashreplace")
    raw_bytes = b"\x00\xff\x80 secret\r\n"
    sys.stdout.buffer.write(raw_bytes)
    assert raw.getvalue().endswith(raw_bytes)
    output = tmp_path / "password.bin"
    cli.save_password(output, raw_bytes)
    assert output.read_bytes() == raw_bytes
    configure_console_streams()
    assert sys.stdout is stream


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp866", "utf-8"])
@pytest.mark.parametrize("reconfigure_error", [None, OSError, ValueError, TypeError])
def test_stream_without_usable_reconfigure(monkeypatch, encoding, reconfigure_error):
    from wallet_recover._console import configure_console_streams

    stream = LegacyStream(encoding)
    if reconfigure_error is not None:
        def unsupported(**kwargs):
            raise reconfigure_error("reconfigure unavailable")
        stream.reconfigure = unsupported
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    configure_console_streams()
    stdout, stderr = sys.stdout, sys.stderr
    text = "Привет, café, € and 😀\n"
    assert stdout.write(text) == len(text)
    stderr.writelines([text])
    stderr.flush()
    assert stream.buffer.getvalue() == (text * 2).encode(encoding, "backslashreplace")
    assert stdout.encoding == encoding
    assert stdout.isatty() is False
    assert stdout.buffer is stream.buffer
    assert stream.flushes == 1
    raw_bytes = b"\x00\xff\x80"
    stdout.buffer.write(raw_bytes)
    assert stream.buffer.getvalue().endswith(raw_bytes)
    configure_console_streams()
    assert sys.stdout is stdout
    assert sys.stderr is stderr


def test_stream_without_encoding_uses_encoding_error(monkeypatch):
    from wallet_recover._console import configure_console_streams

    class MinimalStream:
        def __init__(self):
            self.raw = bytearray()

        def write(self, text):
            self.raw.extend(text.encode("ascii", "strict"))
            return len(text)

    stream = MinimalStream()
    monkeypatch.setattr(sys, "stdout", stream)
    configure_console_streams()
    assert sys.stdout.write("Привет") == 6
    assert stream.raw == "Привет".encode("ascii", "backslashreplace")


@pytest.mark.parametrize("absent", ["stdout", "stderr", "both"])
def test_absent_streams_are_safe_for_cli_and_progress(monkeypatch, absent):
    from wallet_recover.progress import Progress

    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", None if absent in ("stdout", "both") else stdout)
    monkeypatch.setattr(sys, "stderr", None if absent in ("stderr", "both") else stderr)
    assert cli.main(["--selftest"]) == 0
    with pytest.raises(SystemExit) as exc:
        cli.main(["--hashcat-hash", "unused.hash"])
    assert exc.value.code == 2
    progress = Progress(total=1)
    progress.update(1)
    progress.close()
    if absent == "stdout":
        assert "подтвердите владение" in stderr.getvalue()
    elif absent == "stderr":
        assert "Self-test passed" in stdout.getvalue()
        assert "подтвердите владение" not in stdout.getvalue()


@pytest.mark.parametrize("frozen", [False, True])
def test_freeze_support_runs_before_parser(monkeypatch, frozen):
    calls = []
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(cli.multiprocessing, "freeze_support", lambda: calls.append("freeze"))
    monkeypatch.setattr(cli, "configure_console_streams", lambda: calls.append("console"))
    original_parser = cli.build_parser

    def parser():
        calls.append("parser")
        return original_parser()

    monkeypatch.setattr(cli, "build_parser", parser)
    assert cli.main(["--selftest"]) == 0
    assert calls == ["freeze", "console", "parser"]


def test_frozen_no_arguments_launches_gui_after_freeze_support(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["wallet-recover.exe"])
    monkeypatch.setattr(cli.multiprocessing, "freeze_support", lambda: calls.append("freeze"))
    monkeypatch.setattr(cli, "configure_console_streams", lambda: calls.append("console"))
    monkeypatch.setitem(sys.modules, "wallet_recover.gui",
                        SimpleNamespace(main=lambda: calls.append("gui")))
    monkeypatch.setattr(cli, "build_parser", lambda: pytest.fail("GUI dispatch must precede parser"))
    assert cli.main() is None
    assert calls == ["freeze", "console", "gui"]


@pytest.mark.parametrize("frozen, explicit_argv", [(False, None), (True, [])])
def test_cli_no_arguments_keeps_validation(monkeypatch, frozen, explicit_argv):
    from types import SimpleNamespace

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "argv", ["wallet-recover.exe"])
    monkeypatch.setattr(cli.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setitem(sys.modules, "wallet_recover.gui",
                        SimpleNamespace(main=lambda: pytest.fail("Unexpected GUI dispatch")))
    with pytest.raises(SystemExit) as exc:
        cli.main(explicit_argv)
    assert exc.value.code == 2


def test_frozen_selftest_stays_cli(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["wallet-recover.exe", "--selftest"])
    monkeypatch.setattr(cli.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setitem(sys.modules, "wallet_recover.gui",
                        SimpleNamespace(main=lambda: pytest.fail("Unexpected GUI dispatch")))
    assert cli.main() == 0
    assert "Self-test passed" in capsys.readouterr().out


def test_frozen_worker_is_intercepted_before_gui_or_argparse(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["wallet-recover.exe", "--multiprocessing-fork", "parent_pid=1"])

    def worker_bootstrap():
        raise SystemExit(0)

    monkeypatch.setattr(cli.multiprocessing, "freeze_support", worker_bootstrap)
    monkeypatch.setattr(cli, "configure_console_streams", lambda: pytest.fail("Worker must exit first"))
    monkeypatch.setattr(cli, "build_parser", lambda: pytest.fail("Worker must not parse CLI flags"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


@pytest.mark.parametrize("encoding", ["cp1252", "cp866"])
def test_unknown_charmap_stream_escapes_safely(monkeypatch, encoding):
    from wallet_recover._console import configure_console_streams

    class UnknownCharmapStream:
        def __init__(self):
            self.raw = bytearray()

        def write(self, text):
            self.raw.extend(text.encode(encoding, "strict"))
            return len(text)

    stream = UnknownCharmapStream()
    monkeypatch.setattr(sys, "stdout", stream)
    configure_console_streams()
    text = "Привет, café and 😀"
    assert sys.stdout.write(text) == len(text)
    # Without codec metadata, use portable ASCII rather than treating generic
    # 'charmap' as Latin-1, which is neither CP1252 nor CP866.
    assert stream.raw == text.encode("ascii", "backslashreplace")


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp866"])
def test_runtime_error_is_safe_on_legacy_stderr(monkeypatch, encoding):
    stdout, stderr = LegacyStream(encoding), LegacyStream(encoding)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    def fail_load(path):
        raise ValueError("Не найден кошелёк 😀")

    monkeypatch.setattr(cli, "load_wallet", fail_load)
    assert cli.main(["--wallet", "unused.dat"]) == 2
    assert not stdout.buffer.getvalue()
    assert stderr.buffer.getvalue() == "Error: Не найден кошелёк 😀\n".encode(encoding, "backslashreplace")
