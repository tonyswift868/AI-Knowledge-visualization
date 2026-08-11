#!/usr/bin/env python3
"""Trace test-like wheel modules required by the Qwen inference import path.

Run this with the verified portable Python and ``-I -S -B``.  Missing
test-like modules are loaded directly from their audited official wheels for
observation only.  The trace does not alter the portable tree and is not a
substitute for the final no-fallback import smoke.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import traceback
import zipfile


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw_temporary = tempfile.mkstemp(prefix=".trace-", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def is_strict_testlike_member(member: str) -> bool:
    parts = [part.casefold() for part in member.split("/")]
    filename = parts[-1]
    return any(
        part in {"test", "tests", "testing"} or part.startswith("test_")
        for part in parts
    ) or filename.endswith("_test.py")


def module_name_from_member(member: str) -> tuple[str, bool] | None:
    if member.endswith("/__init__.py"):
        raw = member[: -len("/__init__.py")]
        is_package = True
    elif member.endswith(".py"):
        raw = member[:-3]
        is_package = False
    else:
        return None
    parts = raw.split("/")
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    return ".".join(parts), is_package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse-root", required=True)
    parser.add_argument("--site-packages", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()

    wheelhouse_root = Path(arguments.wheelhouse_root).resolve(strict=True)
    site_packages = Path(arguments.site_packages).resolve(strict=True)
    runner = Path(arguments.runner).resolve(strict=True)
    output = Path(arguments.output).resolve(strict=False)
    if any(path.drive.casefold() != "e:" for path in (wheelhouse_root, site_packages, runner, output)):
        raise RuntimeError("all trace inputs and outputs must be on E:")
    receipt_paths = sorted((wheelhouse_root / "receipts").glob("*.json"))
    if len(receipt_paths) != 52:
        raise RuntimeError(f"expected 52 wheel audit receipts, found {len(receipt_paths)}")
    member_sources: dict[str, dict[str, object]] = {}
    module_sources: dict[str, dict[str, object]] = {}
    wheel_summaries: dict[str, dict[str, object]] = {}
    for receipt_path in receipt_paths:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        package = receipt["name"]
        wheel = (wheelhouse_root / "wheels" / receipt["wheel"]["filename"]).resolve(strict=True)
        archive = zipfile.ZipFile(wheel)
        archive_members = set(archive.namelist())
        wheel_summaries[package] = {
            "path": str(wheel),
            "sha256": receipt["wheel"]["sha256"],
            "receipt": str(receipt_path),
            "receipt_sha256": sha256_file(receipt_path),
        }
        for audited in receipt["members"]:
            member = audited["path"]
            if member in member_sources:
                continue
            source = {
                "package": package,
                "wheel": wheel,
                "archive": archive,
                "archive_members": archive_members,
                "receipt": audited,
            }
            member_sources[member] = source
            if not is_strict_testlike_member(member):
                continue
            module_identity = module_name_from_member(member)
            if module_identity is None:
                continue
            module_name, is_package = module_identity
            if module_name.split(".", 1)[0].casefold() in {"test", "tests", "testing"}:
                continue
            if module_name in module_sources:
                raise RuntimeError(f"duplicate test-like module across wheels: {module_name}")
            module_sources[module_name] = {**source, "member": member, "is_package": is_package}
    events = {"network": [], "listener": [], "subprocess": []}
    suppressed_expected_probes: list[str] = []

    def deny_network(_socket: socket.socket, address: object) -> None:
        events["network"].append(repr(address))
        raise RuntimeError("network blocked by test-like import trace")

    def deny_create_connection(address: object, *_args: object, **_kwargs: object) -> None:
        events["network"].append(repr(address))
        raise RuntimeError("network blocked by test-like import trace")

    def deny_bind(_socket: socket.socket, address: object) -> None:
        events["listener"].append(
            {"address": repr(address), "stack": traceback.format_stack(limit=8)}
        )
        raise RuntimeError("listeners blocked by test-like import trace")

    def deny_listen(_socket: socket.socket, backlog: int = 0) -> None:
        events["listener"].append(str(backlog))
        raise RuntimeError("listeners blocked by test-like import trace")

    original_popen = subprocess.Popen

    class GuardedPopen(original_popen):
        def __init__(self, *args: object, **kwargs: object) -> None:
            events["subprocess"].append(repr(args[0] if args else kwargs.get("args")))
            raise RuntimeError("subprocess blocked by test-like import trace")

    def deny_system(command: object) -> int:
        events["subprocess"].append(repr(command))
        raise RuntimeError("subprocess blocked by test-like import trace")

    class EmptySoxProbe:
        def readlines(self) -> list[str]:
            return []

        def close(self) -> None:
            return None

    def guarded_popen(command: object, *args: object, **kwargs: object):
        if command == "sox -h" and not args and not kwargs:
            suppressed_expected_probes.append("sox -h")
            return EmptySoxProbe()
        return deny_system(command)

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_create_connection
    socket.socket.bind = deny_bind
    socket.socket.listen = deny_listen
    subprocess.Popen = GuardedPopen
    os.system = deny_system
    os.popen = guarded_popen

    loaded_from_wheel: dict[str, dict[str, str]] = {}

    class WheelTestingLoader(importlib.abc.Loader):
        def __init__(self, fullname: str, source: dict[str, object]) -> None:
            self.fullname = fullname
            self.source = source

        def create_module(self, _spec: object) -> None:
            return None

        def exec_module(self, module: object) -> None:
            wheel = self.source["wheel"]
            member = self.source["member"]
            archive = self.source["archive"]
            audited = self.source["receipt"]
            origin = str(wheel) + "\\" + str(member).replace("/", "\\")
            module.__file__ = origin
            if self.source["is_package"]:
                module.__path__ = [origin.rsplit("\\", 1)[0]]
            content = archive.read(member)
            if len(content) != audited["bytes"] or hashlib.sha256(content).hexdigest() != audited["sha256"]:
                raise RuntimeError(f"audited test-like wheel member changed: {member}")
            loaded_from_wheel[self.fullname] = {
                "package": str(self.source["package"]),
                "member": str(member),
            }
            code = compile(content, origin, "exec")
            exec(code, module.__dict__)

    class WheelTestingFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname: str, _path: object = None, _target: object = None):
            source = module_sources.get(fullname)
            if source is None:
                return None
            loader = WheelTestingLoader(fullname, source)
            return importlib.util.spec_from_loader(
                fullname, loader, is_package=bool(source["is_package"])
            )

    sys.meta_path.insert(0, WheelTestingFinder())
    stages: dict[str, dict[str, str | None]] = {}

    def snapshot(label: str) -> None:
        observed = {}
        site_prefix = str(site_packages).casefold() + "\\"
        for name, module in sorted(sys.modules.items()):
            origin = (
                getattr(getattr(module, "__spec__", None), "origin", None)
                or getattr(module, "__file__", None)
            )
            if name in loaded_from_wheel:
                observed[name] = origin
                continue
            if isinstance(origin, str) and origin.casefold().startswith(site_prefix):
                member = origin[len(str(site_packages)) + 1 :].replace("\\", "/")
                if is_strict_testlike_member(member):
                    observed[name] = origin
        stages[label] = observed

    import torch

    snapshot("torch")
    import accelerate

    snapshot("accelerate")
    import transformers

    snapshot("transformers")
    from qwen_tts import Qwen3TTSModel

    snapshot("qwen_tts")
    runner_spec = importlib.util.spec_from_file_location("mentorflow_qwen_runner_trace", runner)
    if runner_spec is None or runner_spec.loader is None:
        raise RuntimeError("production runner could not be imported")
    runner_module = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner_module)
    backend = runner_module.QwenModelBackend(
        pack_root=runner.parent,
        device="cuda:0",
        minimum_driver="999.0",
    )
    snapshot("runner")
    if backend._model is not None:
        raise RuntimeError("trace unexpectedly loaded a model")
    traced_modules = stages["runner"]
    traced_members: dict[str, list[str]] = {}
    for module_name, raw_origin in traced_modules.items():
        if not raw_origin:
            raise RuntimeError(f"test-like module has no origin: {module_name}")
        fallback = loaded_from_wheel.get(module_name)
        if fallback is not None:
            member = fallback["member"]
        else:
            source = Path(str(raw_origin)).resolve(strict=True)
            if not source.is_relative_to(site_packages):
                raise RuntimeError(
                    f"test-like module escaped the audited wheel or portable tree: {raw_origin}"
                )
            member = source.relative_to(site_packages).as_posix()
        if member not in member_sources:
            raise RuntimeError(f"traced module is absent from the audited wheel receipt: {member}")
        traced_members.setdefault(member, []).append(module_name)

    static_references = []
    for package in ("torch", "accelerate", "qwen_tts"):
        package_root = site_packages / package
        for source in sorted(package_root.rglob("*.py")):
            for line_number, line in enumerate(
                source.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                if "torch.testing" in line or ("torch" in line and "test_" in line):
                    static_references.append(
                        {
                            "path": source.relative_to(site_packages).as_posix(),
                            "line": line_number,
                            "text": line.strip(),
                        }
                    )

    exact_members = []
    for member, module_names in sorted(traced_members.items()):
        source = member_sources[member]
        audited = source["receipt"]
        package = str(source["package"])
        exact_members.append(
            {
                "package": package,
                "path": member,
                "bytes": audited["bytes"],
                "sha256": audited["sha256"],
                "modules": sorted(module_names),
                "wheel_sha256": wheel_summaries[package]["sha256"],
                "wheel_receipt": wheel_summaries[package]["receipt"],
                "loaded_via_temporary_wheel_finder": any(
                    item["member"] == member for item in loaded_from_wheel.values()
                ),
            }
        )
    report = {
        "schema": "mentorflow.qwen-runtime-testlike-import-trace.v1",
        "status": "passed",
        "scope": "torch, accelerate, transformers, qwen_tts and production runner imports",
        "not_proven": ["model loading", "CUDA execution", "audio synthesis", "service startup"],
        "wheelhouse": {
            "root": str(wheelhouse_root),
            "audit_receipts": len(receipt_paths),
            "testlike_import_candidates": len(module_sources),
        },
        "temporary_finder": {
            "purpose": "observe exact missing test-like runtime imports without extracting or selecting a directory",
            "modules": dict(sorted(loaded_from_wheel.items())),
        },
        "stages": stages,
        "exact_runtime_members": exact_members,
        "static_references": static_references,
        "safety": {
            "events": events,
            "suppressed_expected_import_probes": suppressed_expected_probes,
            "model_loaded": backend._model is not None,
            "cuda_api_called_by_trace": False,
            "tree_modified": False,
        },
        "versions": {
            "python": sys.version.split()[0],
            "torch": str(torch.__version__),
            "accelerate": accelerate.__version__,
            "transformers": transformers.__version__,
            "qwen_symbol": Qwen3TTSModel.__name__,
        },
    }
    atomic_json(output, report)
    print("TESTLIKE_TRACE_JSON " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
