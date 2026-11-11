#!/usr/bin/env python3
"""Release notes use the same version as the CLI and Python package."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wallet_recover import __version__


def release_notes(version):
    tag = "v" + version
    base = "https://github.com/Alexkkkkk/WALLET/releases/download/" + tag
    archive = "WALLET-%s-windows10-11-x64.zip" % tag
    return """## Что скачать

**Windows 10 / Windows 11, 64 бита:** скачайте [{archive}]({base}/{archive}),
распакуйте ZIP и запустите `WALLET.exe`. Python и pip устанавливать не нужно.

**Windows 7 SP1:** готовой проверенной EXE-сборки пока нет. Этот ZIP НЕ подходит
для Windows 7. Ни v0.4.0, ни v0.4.1, ни старый wheel v0.2.0 не являются
подтверждённой версией для Windows 7. Экспериментальная legacy-конфигурация
требует Python 3.8.10, совместимых нативных библиотек и отдельной проверки
именно на Windows 7. Не подменяйте её сборкой для современной Windows.

`.whl` — пакет для установленного Python; `.tar.gz` и автоматически добавленные
`Source code` — исходники. Это НЕ готовые приложения Windows.

## Проверки и состав

Версия программы, пакета и тега: **{tag}**. В portable EXE включены Python,
Tcl/Tk, Berkeley DB, pycryptodome, cryptography, NumPy, OpenAI-клиент и модули
приложения. Ollama, его модели и Hashcat устанавливаются отдельно при необходимости.
Основной поиск и AI-провайдер `offline` не используют сеть.

Перед публикацией обязательны тесты Linux/Python 3.10–3.12 и сборки
Windows/Python 3.11 и 3.12. Проверки готового EXE: `--version`, `--selftest`,
офлайн AI при ограниченной кодировке консоли, чтение синтетической Berkeley DB,
восстановление синтетического пароля с двумя процессами и видимое окно GUI.
EXE запускается вне исходников и без DLL-путей Conda. Windows CI выполняется
на Windows Server 2022, а не непосредственно на Windows 10/11 или Windows 7.
Реальные пользовательские кошельки не использовались в тестах.

Рядом с ZIP приложен `{archive}.sha256`. Проверка в PowerShell:

```powershell
Get-FileHash .\\{archive} -Algorithm SHA256
```

Инструкции и ограничения: [README](https://github.com/Alexkkkkk/WALLET/blob/{tag}/README.md).
Используйте только резервную копию своего кошелька или с разрешения владельца.
Не публикуйте кошельки, словари и найденные пароли.
""".format(version=version, tag=tag, archive=archive, base=base)


if __name__ == "__main__":
    ref = os.environ.get("GITHUB_REF_NAME", "v" + __version__)
    if os.environ.get("GITHUB_REF_TYPE") == "tag" and ref != "v" + __version__:
        raise SystemExit("Release tag does not match package version")
    print(release_notes(__version__))
