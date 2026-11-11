#!/usr/bin/env python3
"""Build the BSD Python driver against vcpkg's Sleepycat-licensed BDB 4.8.

No random DLLs or Berkeley DB 6+ AGPL/commercial-license override are used.
The vcpkg revision and Oracle source checksum are fixed by the CI workflow/port.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

DRIVER_URL = "https://files.pythonhosted.org/packages/e1/55/b3d95a49173c769df8d5def6f9c44e58df2fe63dba9e9141693333cdbd99/berkeleydb-18.1.15.tar.gz"
DRIVER_SHA256 = "7afa53143d754c6bb2c85656c1325ebae518adcfcd1b59e13cc2abb88ddf758e"


def patch_setup(text):
    # Upstream Windows setup already links a static libdb48s with /MD. Only
    # omit the unrelated db_* utilities/DLL it assumes accompany that library.
    marker = "kw_params = dict("
    if text.count(marker) != 1:
        raise ValueError("Unexpected upstream setup layout")
    return text.replace(marker, "if os.name == 'nt':\n    utils = []\n\n" + marker)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path, help="vcpkg installed/x64-windows-static-md")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This helper builds Windows x64 wheels only")
    prefix = args.prefix.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = output / "source"
    source.mkdir(exist_ok=True)
    with urllib.request.urlopen(DRIVER_URL, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != DRIVER_SHA256:
        raise RuntimeError("Driver source checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            path = (source / member.name).resolve()
            if source != path and source not in path.parents:
                raise RuntimeError("Unsafe source archive path")
            if member.issym() or member.islnk():
                raise RuntimeError("Unexpected source archive link")
        tar.extractall(str(source))
    driver = source / "berkeleydb-18.1.15"
    shutil.copytree(str(prefix / "include"), str(driver / "db" / "include"), dirs_exist_ok=True)
    header = (driver / "db" / "include" / "db.h").read_text(errors="strict")
    if "Berkeley DB 4.8" not in header:
        raise RuntimeError("Only the reviewed BDB 4.8 build is allowed")
    libdir = driver / "db" / "lib"
    libdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(prefix / "lib" / "libdb48.lib"), str(libdir / "libdb48s.lib"))
    setup = driver / "setup3.py"
    setup.write_text(patch_setup(setup.read_text(encoding="utf-8")), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--no-build-isolation", "--wheel-dir", str(output), str(driver)], check=True)
    wheel = list(output.glob("berkeleydb-18.1.15-*.whl"))
    if len(wheel) != 1:
        raise RuntimeError("Expected exactly one Windows Berkeley DB wheel")
    subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", str(wheel[0])], check=True)
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(str(prefix / "share" / "berkeleydb" / "copyright"), str(licenses / "Berkeley-DB-4.8.txt"))
    for path in driver.glob("*LICENSE*"):
        if path.is_file():
            shutil.copy2(str(path), str(licenses / ("berkeleydb-driver-" + path.name)))
    print("Built and installed static BDB 4.8 driver:", wheel[0].name)


if __name__ == "__main__":
    main()
