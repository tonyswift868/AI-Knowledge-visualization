"""Allowlisted 7z extraction using the publisher-bundled 7-Zip console.

The console is supplied at build time, covered by the signed app manifest and
never downloaded by this module. Work, lists and logs stay beside staging.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


def executable():
    path = Path(os.environ.get("MF_ASSET_7ZIP", str(Path(__file__).with_name("7zr.exe"))))
    if not path.is_file():
        from .download import DownloadError
        raise DownloadError("安装失败", "安装程序缺少 7z 解压组件，请使用完整安装包修复。")
    return path.resolve()


def _run(arguments, work, cancel, *, limit=64 * 1024 * 1024):
    from .download import DownloadError, check_cancel
    check_cancel(cancel)
    log = work / "7zip.log"
    with log.open("wb") as output:
        process = subprocess.Popen([str(executable()), *map(str, arguments)], cwd=work,
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while process.poll() is None:
                if cancel.wait(.05):
                    check_cancel(cancel)
                if log.stat().st_size > limit:
                    raise DownloadError("校验失败", "7z 压缩包目录信息过大。")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        check_cancel(cancel)
    if process.returncode or log.stat().st_size > limit:
        raise DownloadError("校验失败", "7z 压缩包损坏或解压失败，未安装。")
    return log.read_text(encoding="utf-8", errors="strict")


def inventory(source, work, cancel):
    """Read native technical listing; only Path records describe members."""
    raw = _run(["l", "-slt", "-ba", "-sccUTF-8", "--", source], work, cancel)
    records = []
    for block in raw.split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" in fields:
            fields["Path"] = fields["Path"].replace("\\", "/")
            attributes = fields.get("Attributes", "").split()
            if attributes:
                fields["Folder"] = "+" if "D" in attributes[0] else "-"
            records.append(fields)
    return records


def unpack(asset, source, ready, cancel):
    from .download import DownloadError, _linked, check_cancel, _relative, filesystem_path
    ready = filesystem_path(ready)
    expected = {member.path: member for member in asset.members}
    with tempfile.TemporaryDirectory(prefix="7z-", dir=ready.parent) as temporary:
        work = Path(temporary)
        seen = set()
        for item in inventory(source, work, cancel):
            name = item["Path"]
            if name not in expected:
                continue
            member = expected[name]
            if (name in seen or item.get("Folder") != "-" or
                    any("link" in key.casefold() or "reparse" in key.casefold() for key in item) or
                    int(item.get("Size", -1)) != member.size or "\n" in name or "\r" in name):
                raise DownloadError("校验失败", "7z 压缩包内容与校验清单不符，未安装。")
            _relative(name)
            seen.add(name)
        if seen != set(expected):
            raise DownloadError("校验失败", "7z 压缩包缺少必要文件，未安装。")
        listing = work / "members.txt"
        listing.write_text("\n".join(expected) + "\n", encoding="utf-8")
        extracted = work / "files"
        extracted.mkdir()
        # Exact names only: disable wildcard matching, recursion and prompts.
        _run(["x", "-y", "-r-", "-spd", "-scsUTF-8", "-sccUTF-8", "-bso0", "-bsp0",
              "-o" + str(extracted), "-i@" + str(listing), "--", source], work, cancel)
        for name, member in expected.items():
            check_cancel(cancel)
            path = extracted / name
            if (any(_linked(p) for p in (path, *path.parents) if p == extracted or extracted in p.parents)
                    or not path.is_file() or path.stat().st_size != member.size):
                raise DownloadError("校验失败", "7z 解压文件不符合清单，未安装：" + name)
            target = ready / member.install_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise DownloadError("校验失败", "7z 安装路径重复，未安装。")
            os.replace(path, target)
