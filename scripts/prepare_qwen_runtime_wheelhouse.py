#!/usr/bin/env python3
"""Resolve and audit an official, inference-only Qwen3-TTS Windows wheelhouse.

The script is intentionally release-oriented:

* versions come from the already validated CPython 3.12 reference environment;
* only wheels compatible with that interpreter are accepted;
* only ``files.pythonhosted.org`` and ``download.pytorch.org`` may serve bytes;
* source distributions, build steps, third-party mirrors, Gradio and training
  packages are rejected;
* ``--plan`` fetches index metadata only, while ``--download`` is the sole mode
  which writes wheel bodies;
* every downloaded wheel is inspected as a ZIP and produces an exact metadata,
  RECORD, member and licence receipt.  No wheel is installed and no model is
  imported or executed.

Run this file with the validated Qwen virtual environment's Python executable.
All output and temporary files must be under an E: wheelhouse root.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import importlib.metadata
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

try:
    from backend.app.immutable_evidence import ImmutableEvidenceError, immutable_write
    from backend.app.plain_file_copy import (
        PlainFileCopyError,
        assert_no_reparse_ancestors,
        copy_plain_file_exact,
    )
except ModuleNotFoundError:  # Direct ``python scripts/prepare_*.py`` execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.app.immutable_evidence import ImmutableEvidenceError, immutable_write
    from backend.app.plain_file_copy import (  # type: ignore[no-redef]
        PlainFileCopyError,
        assert_no_reparse_ancestors,
        copy_plain_file_exact,
    )


PLAN_SCHEMA = "mentorflow.qwen-runtime-wheelhouse-plan.v1"
STRICT_12HZ_PLAN_SCHEMA = "mentorflow.qwen-runtime-wheelhouse-plan.v2"
LOCK_SCHEMA = "mentorflow.qwen-runtime-dependency-lock.v1"
RECEIPT_SCHEMA = "mentorflow.python-wheel-audit-receipt.v1"
INVENTORY_SCHEMA = "mentorflow.qwen-runtime-third-party-inventory.v1"
SUPPLEMENT_SCHEMA = "mentorflow.python-license-supplement-receipt.v1"
ROOT_DISTRIBUTION = "qwen-tts"
EXPECTED_QWEN_VERSION = "0.1.1"
EXPECTED_TORCH_VERSION = "2.11.0+cu128"
EXPECTED_PYTHON = (3, 12)
EXPECTED_MACHINE = "AMD64"
EXPECTED_TESTLIKE_TRACE_SHA256 = "79ca75aa0827a199326524635f364729fa34bf6ac5fc7146cb7705bddb58ee4a"
LICENSE_EVIDENCE_SCHEMA = "mentorflow.qwen-runtime-license-evidence.v1"
EXPECTED_STRICT_WHEEL_LICENSE_FILES = 103
EXPECTED_STRICT_EXTERNAL_LICENSE_SUPPLEMENTS = 1
EXPECTED_STRICT_TOTAL_LICENSE_FILES = 104
OFFICIAL_HOSTS = {"files.pythonhosted.org", "download.pytorch.org"}
PYTORCH_DISTRIBUTIONS = {"torch", "torchaudio"}
# qwen-tts leaves ``sox`` unpinned. The validated development environment
# resolved 1.5.0, but that release is sdist-only. Version 1.4.1 is the newest
# official PyPI wheel and still satisfies qwen-tts' declaration.
BINARY_ONLY_VERSION_OVERRIDES = {"sox": "1.4.1"}
# PyTorch imports its public ``torch.testing`` facade during ``import torch``;
# the Qwen/Transformers path also reaches a small set of test-named NumPy,
# SciPy, and PyTorch modules.  The exact set below is bound to the guarded
# official-wheel trace receipt during materialization so no directory is
# implicitly allowed.
RUNTIME_REQUIRED_TESTLIKE_MEMBERS = {
    "numpy/testing/__init__.py",
    "numpy/testing/_private/__init__.py",
    "numpy/testing/_private/extbuild.py",
    "numpy/testing/_private/utils.py",
    "numpy/testing/overrides.py",
    "scipy/stats/_bws_test.py",
    "scipy/stats/_page_trend_test.py",
    "torch/_inductor/test_operators.py",
    "torch/testing/__init__.py",
    "torch/testing/_comparison.py",
    "torch/testing/_creation.py",
    "torch/testing/_internal/__init__.py",
    "torch/testing/_internal/distributed/__init__.py",
    "torch/testing/_internal/distributed/fake_pg.py",
    "torch/testing/_internal/logging_tensor.py",
    "torch/testing/_utils.py",
}
EXCLUDED_DISTRIBUTIONS = {
    "deepspeed",
    "flash-attn",
    "gradio",
    "gradio-client",
    "hf-gradio",
    "peft",
}
STRICT_12HZ_EXCLUDED_DISTRIBUTIONS = frozenset(
    {
        *EXCLUDED_DISTRIBUTIONS,
        # These two qwen-tts requirements belong exclusively to the upstream
        # 25Hz tokenizer path.  The release overlay removes that path before
        # the production package entry is imported.
        "onnxruntime",
        "sox",
    }
)
STRICT_12HZ_PROFILE_RELATIVE = PurePosixPath("v2/12hz-only")
STRICT_12HZ_GENERATIONS_RELATIVE = PurePosixPath("v2/generations")
GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,79}$")
LICENSE_SUPPLEMENTS = {
    ("flatbuffers", "25.12.19"): {
        "repository": "https://github.com/google/flatbuffers",
        "repository_slug": "google/flatbuffers",
        "tag": "v25.12.19",
        "commit": "7e163021e59cca4f8e1e35a7c828b5c6b7915953",
        "path": "LICENSE",
    },
    ("tokenizers", "0.22.2"): {
        "repository": "https://github.com/huggingface/tokenizers",
        "repository_slug": "huggingface/tokenizers",
        "tag": "v0.22.2",
        "commit": "f383101a26663708484cac0727792aad74f78234",
        "path": "LICENSE",
    },
}
RESOLVED_LICENSE_EXPRESSIONS = {
    "accelerate": "Apache-2.0",
    "certifi": "MPL-2.0",
    "charset-normalizer": "MIT",
    "colorama": "BSD-3-Clause",
    "decorator": "BSD-2-Clause",
    "einops": "MIT",
    "flatbuffers": "Apache-2.0",
    "huggingface-hub": "Apache-2.0",
    "jinja2": "BSD-3-Clause",
    "librosa": "ISC",
    "mpmath": "BSD-3-Clause",
    "numba": "BSD-2-Clause",
    "onnxruntime": "MIT",
    "protobuf": "BSD-3-Clause",
    "psutil": "BSD-3-Clause",
    "pyyaml": "MIT",
    "qwen-tts": "Apache-2.0",
    "requests": "Apache-2.0",
    "safetensors": "Apache-2.0",
    "scipy": "BSD-3-Clause AND LicenseRef-SciPy-Bundled",
    "soundfile": "BSD-3-Clause AND LGPL-2.1-or-later",
    "sox": "BSD-3-Clause",
    "sympy": "BSD-3-Clause AND MIT",
    "threadpoolctl": "BSD-3-Clause",
    "tokenizers": "Apache-2.0",
    "torch": "BSD-3-Clause",
    "torchaudio": "BSD-2-Clause",
    "tqdm": "MPL-2.0 AND MIT",
    "transformers": "Apache-2.0",
}
FORBIDDEN_NAME_PARTS = ("test", "training", "trainer")
WHEELHOUSE_ROOT_DEFAULT = Path(
    r"E:\Kristina_Runtime\cache\downloads\qwen-runtime-wheelhouse"
)
PORTABLE_PYTHON_MATERIAL_ROOT_DEFAULT = Path(
    r"E:\Kristina_Runtime\materials\python-embed-3.12\3.12.10-amd64"
)
QWEN_MODEL_ROOT_DEFAULT = Path(
    os.environ.get(
        "MENTORFLOW_QWEN_MODEL_ROOT",
        r"E:\Kristina_Runtime\models\qwen3-tts\Qwen3-TTS-12Hz-0.6B-Base",
    )
)
USER_AGENT = "MentorFlow-Qwen-Wheelhouse-Auditor/1.0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class WheelhouseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class InstalledDistribution:
    name: str
    version: str
    requires_dist: tuple[str, ...]
    required_by: tuple[str, ...]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    try:
        immutable_write(path, content)
    except ImmutableEvidenceError as exc:
        detail = f"{exc}; audit_temp={exc.temporary_path}" if exc.temporary_path else str(exc)
        raise WheelhouseError(exc.code, detail) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_evidence_pin(path: Path, *, schema: str, **extra: Any) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "schema": schema,
        **extra,
    }


def _license_evidence_summary(
    wheel_files: list[dict[str, Any]],
    external_supplements: list[dict[str, Any]],
) -> dict[str, Any]:
    wheel_files = sorted(
        wheel_files,
        key=lambda item: (item["component_id"], item["path"], item["sha256"]),
    )
    external_supplements = sorted(
        external_supplements,
        key=lambda item: (item["component_id"], item["path"], item["sha256"]),
    )
    total_hashes = sorted(
        [str(item["sha256"]) for item in wheel_files]
        + [str(item["sha256"]) for item in external_supplements]
    )
    return {
        "schema": LICENSE_EVIDENCE_SCHEMA,
        "wheel_file_count": len(wheel_files),
        "external_supplement_file_count": len(external_supplements),
        "total_file_count": len(total_hashes),
        "wheel_files": wheel_files,
        "external_supplements": external_supplements,
        "total_sha256": total_hashes,
    }


def _plain_tree_stats(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    files = sorted(
        (path.relative_to(root).as_posix(), path)
        for path in root.rglob("*")
        if path.is_file()
    )
    total_bytes = 0
    for relative, path in files:
        size = path.stat().st_size
        file_sha = _sha256_file(path)
        total_bytes += size
        digest.update(f"{relative}\0{size}\0{file_sha}\n".encode("utf-8"))
    return {
        "path": str(root),
        "files": len(files),
        "bytes": total_bytes,
        "tree_sha256": digest.hexdigest(),
    }


def _ensure_release_host(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or host not in OFFICIAL_HOSTS:
        raise WheelhouseError("UNTRUSTED_WHEEL_URL", f"wheel URL is not official: {url}")
    return host


def _request(url: str, *, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def _read_json(url: str) -> Mapping[str, Any]:
    with urllib.request.urlopen(_request(url), timeout=45) as response:
        final = response.geturl()
        if urllib.parse.urlparse(final).hostname != "pypi.org":
            raise WheelhouseError("INDEX_REDIRECT_UNTRUSTED", f"PyPI JSON redirected to {final}")
        value = json.load(response)
    if not isinstance(value, Mapping):
        raise WheelhouseError("INDEX_RESPONSE_INVALID", f"invalid JSON object from {url}")
    return value


def _marker_environment() -> dict[str, str]:
    environment = default_environment()
    environment.update(
        {
            "extra": "",
            "implementation_name": "cpython",
            "os_name": "nt",
            "platform_machine": EXPECTED_MACHINE,
            "platform_system": "Windows",
            "python_full_version": "3.12.10",
            "python_version": "3.12",
            "sys_platform": "win32",
        }
    )
    return environment


def _runtime_closure() -> list[InstalledDistribution]:
    if sys.version_info[:2] != EXPECTED_PYTHON:
        raise WheelhouseError(
            "REFERENCE_PYTHON_INVALID",
            f"run with CPython 3.12, got {sys.version.split()[0]}",
        )
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "")
    if machine.casefold() not in {"amd64", "x86_64"}:
        raise WheelhouseError("REFERENCE_ABI_INVALID", f"expected AMD64, got {machine!r}")

    environment = _marker_environment()
    queue = [ROOT_DISTRIBUTION]
    resolved: dict[str, tuple[str, tuple[str, ...]]] = {}
    parents: dict[str, set[str]] = {ROOT_DISTRIBUTION: set()}
    while queue:
        name = canonicalize_name(queue.pop(0))
        if name in EXCLUDED_DISTRIBUTIONS or name in resolved:
            continue
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise WheelhouseError(
                "REFERENCE_DEPENDENCY_MISSING", f"reference environment lacks {name}"
            ) from exc
        canonical = canonicalize_name(str(distribution.metadata.get("Name") or name))
        requires_dist = tuple(distribution.requires or ())
        resolved[canonical] = (
            BINARY_ONLY_VERSION_OVERRIDES.get(canonical, distribution.version),
            requires_dist,
        )
        for raw in requires_dist:
            requirement = Requirement(raw)
            dependency = canonicalize_name(requirement.name)
            if dependency in EXCLUDED_DISTRIBUTIONS:
                continue
            if requirement.marker is not None and not requirement.marker.evaluate(environment):
                continue
            parents.setdefault(dependency, set()).add(canonical)
            if dependency not in resolved:
                queue.append(dependency)

    forbidden = sorted(set(resolved).intersection(EXCLUDED_DISTRIBUTIONS))
    if forbidden:
        raise WheelhouseError("EXCLUDED_DEPENDENCY_PRESENT", f"excluded packages: {forbidden}")
    if resolved.get(ROOT_DISTRIBUTION, (None,))[0] != EXPECTED_QWEN_VERSION:
        raise WheelhouseError("QWEN_VERSION_INVALID", "reference qwen-tts version drifted")
    if resolved.get("torch", (None,))[0] != EXPECTED_TORCH_VERSION:
        raise WheelhouseError("TORCH_VERSION_INVALID", "reference torch version drifted")
    if resolved.get("torchaudio", (None,))[0] != EXPECTED_TORCH_VERSION:
        raise WheelhouseError("TORCHAUDIO_VERSION_INVALID", "reference torchaudio version drifted")

    return [
        InstalledDistribution(
            name=name,
            version=value[0],
            requires_dist=value[1],
            required_by=tuple(sorted(parents.get(name, set()))),
        )
        for name, value in sorted(resolved.items())
    ]


def _tag_rank() -> dict[Any, int]:
    return {tag: index for index, tag in enumerate(sys_tags())}


def _select_pypi_wheel(distribution: InstalledDistribution) -> dict[str, Any]:
    url = (
        "https://pypi.org/pypi/"
        + urllib.parse.quote(distribution.name)
        + "/"
        + urllib.parse.quote(distribution.version)
        + "/json"
    )
    payload = _read_json(url)
    supported = set(sys_tags())
    rank = _tag_rank()
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    for item in payload.get("urls", []):
        if not isinstance(item, Mapping) or item.get("packagetype") != "bdist_wheel":
            continue
        filename = str(item.get("filename") or "")
        if not filename.endswith(".whl"):
            continue
        try:
            parsed_name, parsed_version, _build, tags = parse_wheel_filename(filename)
        except Exception:
            continue
        if canonicalize_name(parsed_name) != distribution.name or str(parsed_version) != distribution.version:
            continue
        overlap = tags.intersection(supported)
        if not overlap:
            continue
        candidates.append((min(rank[tag] for tag in overlap), item))
    if not candidates:
        raise WheelhouseError(
            "OFFICIAL_WHEEL_UNAVAILABLE",
            f"{distribution.name}=={distribution.version} has no compatible PyPI wheel",
        )
    item = min(candidates, key=lambda pair: (pair[0], str(pair[1].get("filename"))))[1]
    wheel_url = str(item.get("url") or "")
    host = _ensure_release_host(wheel_url)
    digest = str((item.get("digests") or {}).get("sha256") or "").casefold()
    size = item.get("size")
    if not HEX64.fullmatch(digest) or not isinstance(size, int) or size <= 0:
        raise WheelhouseError("INDEX_ARTIFACT_INVALID", f"invalid PyPI artifact for {distribution.name}")
    return {
        "name": distribution.name,
        "version": distribution.version,
        "filename": str(item["filename"]),
        "bytes": size,
        "sha256": digest,
        "url": wheel_url,
        "host": host,
        "index_url": url,
        "requires_dist_reference": list(distribution.requires_dist),
        "required_by": list(distribution.required_by),
    }


def _pytorch_wheel(distribution: InstalledDistribution) -> dict[str, Any]:
    simple_url = f"https://download.pytorch.org/whl/cu128/{distribution.name}/"
    with urllib.request.urlopen(_request(simple_url), timeout=45) as response:
        if (urllib.parse.urlparse(response.geturl()).hostname or "").casefold() != "download.pytorch.org":
            raise WheelhouseError("INDEX_REDIRECT_UNTRUSTED", "PyTorch index redirected off host")
        body = response.read().decode("utf-8", "strict")
    hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", body, flags=re.IGNORECASE)
    expected_filename = (
        f"{distribution.name}-{distribution.version}-cp312-cp312-win_amd64.whl"
    )
    matches: list[str] = []
    for href in hrefs:
        resolved = urllib.parse.urljoin(simple_url, html.unescape(href))
        filename = urllib.parse.unquote(urllib.parse.urlparse(resolved).path.rsplit("/", 1)[-1])
        if filename.casefold() == expected_filename.casefold():
            matches.append(resolved)
    if len(matches) != 1:
        raise WheelhouseError(
            "OFFICIAL_WHEEL_UNAVAILABLE",
            f"expected one official {expected_filename}, found {len(matches)}",
        )
    index_wheel_url = matches[0]
    parsed_index_url = urllib.parse.urlparse(index_wheel_url)
    # The official index currently links its R2 CDN directly. Fetch the same
    # immutable path through download.pytorch.org so the actual byte request
    # remains on the explicitly trusted release host.
    wheel_url = urllib.parse.urlunparse(
        parsed_index_url._replace(netloc="download.pytorch.org")
    )
    host = _ensure_release_host(wheel_url)
    fragment = parsed_index_url.fragment
    digest = fragment.removeprefix("sha256=").casefold() if fragment.startswith("sha256=") else ""
    if not HEX64.fullmatch(digest):
        raise WheelhouseError("INDEX_ARTIFACT_INVALID", f"missing SHA256 for {expected_filename}")
    with urllib.request.urlopen(_request(wheel_url, method="HEAD"), timeout=45) as response:
        if (urllib.parse.urlparse(response.geturl()).hostname or "").casefold() != host:
            raise WheelhouseError("WHEEL_REDIRECT_UNTRUSTED", f"wheel redirected to {response.geturl()}")
        try:
            size = int(response.headers.get("Content-Length") or "0")
        except ValueError:
            size = 0
    if size <= 0:
        raise WheelhouseError("INDEX_ARTIFACT_INVALID", f"missing size for {expected_filename}")
    return {
        "name": distribution.name,
        "version": distribution.version,
        "filename": expected_filename,
        "bytes": size,
        "sha256": digest,
        "url": wheel_url,
        "host": host,
        "index_url": simple_url,
        "index_artifact_url": index_wheel_url,
        "requires_dist_reference": list(distribution.requires_dist),
        "required_by": list(distribution.required_by),
    }


def build_plan() -> dict[str, Any]:
    distributions = _runtime_closure()
    artifacts: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for distribution in distributions:
        try:
            if distribution.name in PYTORCH_DISTRIBUTIONS:
                artifact = _pytorch_wheel(distribution)
            else:
                artifact = _select_pypi_wheel(distribution)
            artifacts.append(artifact)
        except WheelhouseError as exc:
            blockers.append(
                {"name": distribution.name, "version": distribution.version, "code": exc.code, "message": exc.message}
            )
    total = sum(int(item["bytes"]) for item in artifacts)
    return {
        "schema": PLAN_SCHEMA,
        "target": {
            "implementation": "CPython",
            "python": "3.12",
            "abi": "cp312",
            "platform": "win_amd64",
            "torch_index": "cu128",
        },
        "roots": [f"{ROOT_DISTRIBUTION}=={EXPECTED_QWEN_VERSION}"],
        "resolution_policy": {
            "official_hosts": sorted(OFFICIAL_HOSTS),
            "wheel_only": True,
            "sdist": False,
            "compile": False,
            "third_party_mirror": False,
            "declared_but_excluded": sorted(EXCLUDED_DISTRIBUTIONS),
            "binary_only_version_overrides": BINARY_ONLY_VERSION_OVERRIDES,
            "qwen_install_mode": "wheel extraction or pip --no-deps against this exact lock",
            "reason": "qwen-tts declares Gradio for its demo CLI; MentorFlow uses only the loopback inference runner",
        },
        "artifact_count": len(artifacts),
        "dependency_count": len(distributions),
        "download_bytes": total,
        "download_gib": round(total / (1024**3), 6),
        "artifacts": sorted(artifacts, key=lambda item: item["name"]),
        "blockers": blockers,
    }


def _safe_wheel_member(info: zipfile.ZipInfo) -> PurePosixPath:
    raw = info.filename
    path = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or ":" in raw
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise WheelhouseError("WHEEL_MEMBER_UNSAFE", f"unsafe wheel member: {raw!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode & 0o170000 == 0o120000:
        raise WheelhouseError("WHEEL_MEMBER_UNSAFE", f"wheel symlink forbidden: {raw}")
    return path


def _record_rows(raw: bytes) -> list[dict[str, Any]]:
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise WheelhouseError("WHEEL_RECORD_INVALID", "RECORD is not valid UTF-8 CSV") from exc
    result = []
    for index, row in enumerate(rows):
        if len(row) != 3:
            raise WheelhouseError("WHEEL_RECORD_INVALID", f"RECORD row {index} has {len(row)} columns")
        result.append({"path": row[0], "hash": row[1], "size": row[2]})
    return result


def _record_sha256_token(digest: str) -> str:
    import base64

    return "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode("ascii")


def _license_member(path: PurePosixPath) -> bool:
    lowered_parts = [part.casefold() for part in path.parts]
    dist_info_license = any(
        part.endswith(".dist-info")
        and index + 1 < len(lowered_parts)
        and lowered_parts[index + 1] == "licenses"
        for index, part in enumerate(lowered_parts)
    )
    # Legal notices are not consistently named LICENSE.  Notably, the real
    # ONNX Runtime and scikit-learn wheels use ThirdPartyNotices.txt and a
    # nested liblinear/COPYRIGHT respectively.  Match the basename only so a
    # Python module below a directory named ``licenses`` is not misclassified.
    normalized_name = re.sub(r"[^a-z0-9]", "", path.name.casefold())
    return dist_info_license or normalized_name.startswith(
        (
            "license",
            "licence",
            "copying",
            "notice",
            "thirdpartynotice",
            "copyright",
        )
    )


def audit_wheel(path: Path, planned: Mapping[str, Any]) -> dict[str, Any]:
    actual_size = path.stat().st_size
    actual_sha = _sha256_file(path)
    if actual_size != planned["bytes"] or actual_sha != planned["sha256"]:
        raise WheelhouseError("WHEEL_DIGEST_MISMATCH", f"download mismatch: {path.name}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            members: list[dict[str, Any]] = []
            metadata_candidates: list[tuple[PurePosixPath, bytes]] = []
            record_candidates: list[tuple[PurePosixPath, bytes]] = []
            licenses: list[dict[str, Any]] = []
            for info in infos:
                member_path = _safe_wheel_member(info)
                data = archive.read(info)
                member = {
                    "path": member_path.as_posix(),
                    "bytes": len(data),
                    "sha256": _sha256_bytes(data),
                }
                members.append(member)
                if (
                    member_path.name == "METADATA"
                    and len(member_path.parent.parts) == 1
                    and member_path.parent.name.endswith(".dist-info")
                ):
                    metadata_candidates.append((member_path, data))
                if (
                    member_path.name == "RECORD"
                    and len(member_path.parent.parts) == 1
                    and member_path.parent.name.endswith(".dist-info")
                ):
                    record_candidates.append((member_path, data))
                if _license_member(member_path):
                    licenses.append(member)
    except zipfile.BadZipFile as exc:
        raise WheelhouseError("WHEEL_ZIP_INVALID", f"invalid wheel ZIP: {path.name}") from exc
    if len(metadata_candidates) != 1 or len(record_candidates) != 1:
        raise WheelhouseError("WHEEL_DIST_INFO_INVALID", f"METADATA/RECORD cardinality invalid: {path.name}")
    metadata_path, metadata_raw = metadata_candidates[0]
    record_path, record_raw = record_candidates[0]
    if metadata_path.parent != record_path.parent:
        raise WheelhouseError("WHEEL_DIST_INFO_INVALID", f"METADATA and RECORD disagree: {path.name}")
    parsed = BytesParser(policy=email_policy.default).parsebytes(metadata_raw)
    metadata_name = canonicalize_name(str(parsed.get("Name") or ""))
    metadata_version = str(parsed.get("Version") or "")
    if metadata_name != planned["name"] or metadata_version != planned["version"]:
        raise WheelhouseError("WHEEL_METADATA_MISMATCH", f"name/version mismatch: {path.name}")
    record = _record_rows(record_raw)
    wheel_paths = {item["path"] for item in members}
    record_paths = {item["path"] for item in record}
    if wheel_paths != record_paths:
        raise WheelhouseError(
            "WHEEL_RECORD_CLOSURE_MISMATCH",
            f"RECORD file set differs for {path.name}",
        )
    members_by_path = {item["path"]: item for item in members}
    for row in record:
        member = members_by_path[row["path"]]
        if row["path"] == record_path.as_posix():
            if row["hash"] or row["size"]:
                raise WheelhouseError(
                    "WHEEL_RECORD_INVALID", f"RECORD self row is not empty: {path.name}"
                )
            continue
        if row["hash"] != _record_sha256_token(member["sha256"]) or row["size"] != str(member["bytes"]):
            raise WheelhouseError(
                "WHEEL_RECORD_DIGEST_MISMATCH",
                f"RECORD hash/size mismatch for {path.name}:{row['path']}",
            )
    return {
        "schema": RECEIPT_SCHEMA,
        "name": metadata_name,
        "version": metadata_version,
        "purl": f"pkg:pypi/{metadata_name}@{metadata_version}",
        "wheel": {
            "filename": path.name,
            "bytes": actual_size,
            "sha256": actual_sha,
            "url": planned["url"],
            "host": planned["host"],
        },
        "metadata": {
            "path": metadata_path.as_posix(),
            "bytes": len(metadata_raw),
            "sha256": _sha256_bytes(metadata_raw),
            "name": str(parsed.get("Name") or ""),
            "canonical_name": metadata_name,
            "version": metadata_version,
            "license_expression": str(parsed.get("License-Expression") or ""),
            "license": str(parsed.get("License") or ""),
            "project_urls": list(parsed.get_all("Project-URL") or ()),
            "requires_dist": list(parsed.get_all("Requires-Dist") or ()),
        },
        "record": {
            "path": record_path.as_posix(),
            "bytes": len(record_raw),
            "sha256": _sha256_bytes(record_raw),
            "rows": record,
        },
        "members": sorted(members, key=lambda item: item["path"]),
        "licenses": sorted(licenses, key=lambda item: item["path"]),
        "blockers": (
            []
            if licenses
            else [
                {
                    "code": "WHEEL_LICENSE_FILE_MISSING",
                    "message": "wheel contains no LICENSE/COPYING/NOTICE member",
                }
            ]
        ),
    }


def _download_one(root: Path, artifact: Mapping[str, Any]) -> Path:
    wheels = root / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    target = wheels / str(artifact["filename"])
    if target.is_file():
        if target.stat().st_size == artifact["bytes"] and _sha256_file(target) == artifact["sha256"]:
            return target
        raise WheelhouseError("EXISTING_WHEEL_MISMATCH", f"existing wheel differs: {target}")
    partial = wheels / (target.name + ".part")
    offset = partial.stat().st_size if partial.is_file() else 0
    expected_size = int(artifact["bytes"])
    if offset > expected_size:
        raise WheelhouseError("PARTIAL_WHEEL_INVALID", f"oversized partial wheel: {partial}")
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(str(artifact["url"]), headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        final_host = (urllib.parse.urlparse(response.geturl()).hostname or "").casefold()
        if final_host != artifact["host"]:
            raise WheelhouseError("WHEEL_REDIRECT_UNTRUSTED", f"wheel redirected to {response.geturl()}")
        status = int(getattr(response, "status", 200))
        if offset:
            expected_range = f"bytes {offset}-"
            content_range = str(response.headers.get("Content-Range") or "")
            if status != 206 or not content_range.startswith(expected_range):
                raise WheelhouseError(
                    "WHEEL_RANGE_INVALID",
                    f"resume response is not the requested range for {artifact['filename']}",
                )
        elif status != 200:
            raise WheelhouseError(
                "WHEEL_HTTP_INVALID", f"unexpected HTTP {status} for {artifact['filename']}"
            )
        mode = "ab" if offset else "xb"
        downloaded = offset
        last_report = time.monotonic()
        next_quarter = ((downloaded * 4 // expected_size) + 1) / 4
        with partial.open(mode) as stream:
            while True:
                chunk = response.read(4 * 1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                ratio = downloaded / expected_size
                if ratio >= next_quarter or now - last_report >= 300:
                    print(
                        f"DOWNLOAD_PROGRESS {artifact['name']} {downloaded}/{expected_size} "
                        f"({ratio:.1%})",
                        flush=True,
                    )
                    while next_quarter <= ratio:
                        next_quarter += 0.25
                    last_report = now
            stream.flush()
            os.fsync(stream.fileno())
    if partial.stat().st_size != expected_size or _sha256_file(partial) != artifact["sha256"]:
        raise WheelhouseError("WHEEL_DIGEST_MISMATCH", f"download mismatch: {artifact['filename']}")
    os.replace(partial, target)
    print(f"DOWNLOAD_VERIFIED {artifact['name']} {expected_size} {artifact['sha256']}", flush=True)
    return target


def _validate_locked_dependency_closure(receipts: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    versions = {item["name"]: item["version"] for item in receipts}
    environment = _marker_environment()
    blockers: list[dict[str, str]] = []
    for receipt in receipts:
        for raw in receipt["metadata"]["requires_dist"]:
            requirement = Requirement(raw)
            dependency = canonicalize_name(requirement.name)
            if requirement.marker is not None and not requirement.marker.evaluate(environment):
                continue
            if dependency in EXCLUDED_DISTRIBUTIONS:
                if receipt["name"] == ROOT_DISTRIBUTION and dependency == "gradio":
                    continue
                blockers.append(
                    {
                        "name": receipt["name"],
                        "version": receipt["version"],
                        "code": "UNEXPECTED_EXCLUDED_DEPENDENCY",
                        "message": raw,
                    }
                )
                continue
            actual = versions.get(dependency)
            if actual is None:
                blockers.append(
                    {
                        "name": receipt["name"],
                        "version": receipt["version"],
                        "code": "LOCK_DEPENDENCY_MISSING",
                        "message": raw,
                    }
                )
            elif requirement.specifier and not requirement.specifier.contains(actual, prereleases=True):
                blockers.append(
                    {
                        "name": receipt["name"],
                        "version": receipt["version"],
                        "code": "LOCK_DEPENDENCY_VERSION_MISMATCH",
                        "message": f"{raw}; locked {dependency}=={actual}",
                    }
                )
    return blockers


def download_and_audit(root: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("blockers"):
        raise WheelhouseError("PLAN_BLOCKED", "cannot download a plan with ABI/index blockers")
    missing_bytes = sum(
        int(item["bytes"])
        for item in plan["artifacts"]
        if not (root / "wheels" / str(item["filename"])).is_file()
    )
    free_bytes = shutil.disk_usage(root.anchor).free
    if free_bytes < missing_bytes + 512 * 1024 * 1024:
        raise WheelhouseError(
            "DISK_SPACE_INSUFFICIENT",
            f"E: free={free_bytes}, required_with_headroom={missing_bytes + 512 * 1024 * 1024}",
        )
    print(
        f"DISK_SPACE_OK free={free_bytes} missing_download={missing_bytes} root={root}",
        flush=True,
    )
    receipts = []
    blockers = []
    for artifact in plan["artifacts"]:
        path = _download_one(root, artifact)
        receipt = audit_wheel(path, artifact)
        receipts.append(receipt)
        _atomic_write(root / "receipts" / f"{artifact['name']}=={artifact['version']}.json", _json_bytes(receipt))
        blockers.extend(
            {
                "name": artifact["name"],
                "version": artifact["version"],
                **blocker,
            }
            for blocker in receipt["blockers"]
        )
    blockers.extend(_validate_locked_dependency_closure(receipts))
    lock = {
        "schema": LOCK_SCHEMA,
        "target": plan["target"],
        "install_policy": {
            "offline": True,
            "no_index": True,
            "only_binary": True,
            "qwen_no_deps": True,
            "model_execution": False,
            "version_overrides": BINARY_ONLY_VERSION_OVERRIDES,
        },
        "packages": [
            {
                "name": receipt["name"],
                "version": receipt["version"],
                "purl": receipt["purl"],
                "wheel_filename": receipt["wheel"]["filename"],
                "wheel_bytes": receipt["wheel"]["bytes"],
                "wheel_sha256": receipt["wheel"]["sha256"],
                "metadata_sha256": receipt["metadata"]["sha256"],
                "record_sha256": receipt["record"]["sha256"],
                "license_files": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in receipt["licenses"]
                ],
            }
            for receipt in sorted(receipts, key=lambda item: item["name"])
        ],
        "blockers": blockers,
    }
    inventory = {
        "schema": INVENTORY_SCHEMA,
        "component_count": len(receipts),
        "wheel_bytes": sum(item["wheel"]["bytes"] for item in receipts),
        "components": [
            {
                "name": item["name"],
                "version": item["version"],
                "purl": item["purl"],
                "license_expression": item["metadata"]["license_expression"],
                "license": item["metadata"]["license"],
                "license_files": item["licenses"],
                "source_url": item["wheel"]["url"],
                "source_host": item["wheel"]["host"],
            }
            for item in sorted(receipts, key=lambda value: value["name"])
        ],
        "blockers": blockers,
    }
    _atomic_write(root / "dependency-lock.json", _json_bytes(lock))
    _atomic_write(root / "THIRD_PARTY_INVENTORY.json", _json_bytes(inventory))
    return {"lock": lock, "inventory": inventory}


def _read_official_github_json(url: str) -> Mapping[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "api.github.com":
        raise WheelhouseError("LICENSE_SOURCE_UNTRUSTED", f"invalid GitHub API URL: {url}")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        if urllib.parse.urlparse(response.geturl()).hostname != "api.github.com":
            raise WheelhouseError("LICENSE_SOURCE_UNTRUSTED", "GitHub API redirected off host")
        value = json.load(response)
    if not isinstance(value, Mapping):
        raise WheelhouseError("LICENSE_SOURCE_INVALID", f"invalid GitHub response: {url}")
    return value


def fetch_license_supplements(root: Path) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    for (name, version), declaration in sorted(LICENSE_SUPPLEMENTS.items()):
        wheel_receipt_path = root / "receipts" / f"{name}=={version}.json"
        if not wheel_receipt_path.is_file():
            raise WheelhouseError("WHEEL_RECEIPT_MISSING", f"missing wheel receipt: {wheel_receipt_path}")
        wheel_receipt = json.loads(wheel_receipt_path.read_text(encoding="utf-8"))
        if wheel_receipt.get("schema") != RECEIPT_SCHEMA or wheel_receipt.get("licenses"):
            raise WheelhouseError(
                "LICENSE_SUPPLEMENT_NOT_NEEDED", f"wheel is not an audited missing-license case: {name}"
            )
        project_urls = wheel_receipt.get("metadata", {}).get("project_urls", [])
        repository = declaration["repository"]
        if not any(repository in str(item) for item in project_urls):
            raise WheelhouseError(
                "LICENSE_REPOSITORY_UNBOUND",
                f"wheel METADATA does not bind {name} to {repository}",
            )
        slug = declaration["repository_slug"]
        tag = declaration["tag"]
        expected_commit = declaration["commit"]
        tag_api_url = f"https://api.github.com/repos/{slug}/commits/{tag}"
        tag_payload = _read_official_github_json(tag_api_url)
        actual_commit = str(tag_payload.get("sha") or "").casefold()
        if actual_commit != expected_commit:
            raise WheelhouseError(
                "LICENSE_TAG_COMMIT_MISMATCH",
                f"{slug} {tag} resolved to {actual_commit}, expected {expected_commit}",
            )
        relative = declaration["path"]
        immutable_url = f"https://raw.githubusercontent.com/{slug}/{expected_commit}/{relative}"
        request = urllib.request.Request(immutable_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=45) as response:
            if urllib.parse.urlparse(response.geturl()).hostname != "raw.githubusercontent.com":
                raise WheelhouseError("LICENSE_SOURCE_UNTRUSTED", "license redirected off GitHub")
            license_bytes = response.read()
        if not license_bytes or len(license_bytes) > 1024 * 1024:
            raise WheelhouseError("LICENSE_SOURCE_INVALID", f"invalid license bytes for {name}")
        destination_root = root / "license-supplements" / f"{name}=={version}"
        license_path = destination_root / Path(relative).name
        _atomic_write(license_path, license_bytes)
        receipt = {
            "schema": SUPPLEMENT_SCHEMA,
            "package": {
                "name": name,
                "version": version,
                "purl": wheel_receipt["purl"],
                "wheel_filename": wheel_receipt["wheel"]["filename"],
                "wheel_sha256": wheel_receipt["wheel"]["sha256"],
                "metadata_sha256": wheel_receipt["metadata"]["sha256"],
            },
            "pypi_metadata_binding": {
                "project_urls": project_urls,
                "repository": repository,
            },
            "upstream_release": {
                "repository": repository,
                "tag": tag,
                "tag_api_url": tag_api_url,
                "resolved_commit": expected_commit,
                "commit_url": f"https://github.com/{slug}/commit/{expected_commit}",
            },
            "license_file": {
                "repository_path": relative,
                "immutable_url": immutable_url,
                "bytes": len(license_bytes),
                "sha256": _sha256_bytes(license_bytes),
                "saved_relative_path": license_path.relative_to(root).as_posix(),
            },
        }
        _atomic_write(destination_root / "license-supplement-receipt.json", _json_bytes(receipt))
        receipts.append(receipt)
    inventory = {
        "schema": "mentorflow.python-license-supplement-inventory.v1",
        "supplement_count": len(receipts),
        "release_status": "READY_FOR_STRICT_BUILDER_VALIDATION",
        "receipts": receipts,
    }
    _atomic_write(root / "license-supplement-receipts.json", _json_bytes(inventory))
    return inventory


def run_sox_override_import_smoke(root: Path) -> dict[str, Any]:
    """Prove the binary-only sox override imports without loading a model.

    This deliberately reuses the validated Qwen development interpreter and
    its already validated non-sox dependencies. It is not a clean-machine or
    complete offline-install acceptance test.
    """

    plan = json.loads((root / "resolution-plan.json").read_text(encoding="utf-8"))
    artifact = next(
        (
            item
            for item in plan.get("artifacts", [])
            if item.get("name") == "sox" and item.get("version") == "1.4.1"
        ),
        None,
    )
    if artifact is None:
        raise WheelhouseError("SOX_OVERRIDE_NOT_LOCKED", "sox==1.4.1 is absent from the plan")
    wheel = root / "wheels" / artifact["filename"]
    if (
        not wheel.is_file()
        or wheel.stat().st_size != artifact["bytes"]
        or _sha256_file(wheel) != artifact["sha256"]
    ):
        raise WheelhouseError("SOX_OVERRIDE_WHEEL_INVALID", "locked sox wheel is missing or changed")
    staging = root / "import-smoke" / "sox-1.4.1"
    staging.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = _safe_wheel_member(info)
            destination = staging.joinpath(*relative.parts)
            _atomic_write(destination, archive.read(info))
    project_root = Path(__file__).resolve().parents[1]
    runner = project_root / "backend" / "runtime" / "qwen_base_clone_server.py"
    runner_sha = _sha256_file(runner)
    child_code = r'''
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import sys

override = Path(sys.argv[1]).resolve()
runner_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(override))
import sox
sox_path = Path(sox.__file__).resolve()
if not sox_path.is_relative_to(override):
    raise RuntimeError(f"sox override was not selected: {sox_path}")
sox_version = next(
    dist.version
    for dist in metadata.distributions(path=[str(override)])
    if (dist.metadata.get("Name") or "").lower() == "sox"
)
from qwen_tts import Qwen3TTSModel
import torch
spec = importlib.util.spec_from_file_location("mentorflow_qwen_runner_smoke", runner_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
backend = module.QwenModelBackend(pack_root=runner_path.parent, device="cuda:0", minimum_driver="999.0")
if backend._model is not None:
    raise RuntimeError("runner unexpectedly loaded a model")
print("SMOKE_JSON " + json.dumps({
    "sox_version": sox_version,
    "sox_module": str(sox_path),
    "qwen_tts_version": metadata.version("qwen-tts"),
    "qwen_model_symbol": Qwen3TTSModel.__name__,
    "torch_version": torch.__version__,
    "runner_module": str(runner_path),
    "model_loaded": backend._model is not None,
}, sort_keys=True))
'''
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": str(root / "import-smoke" / "cache" / "huggingface"),
            "TORCH_HOME": str(root / "import-smoke" / "cache" / "torch"),
            "NUMBA_CACHE_DIR": str(root / "import-smoke" / "cache" / "numba"),
            "TMP": str(root / "tmp"),
            "TEMP": str(root / "tmp"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", child_code, str(staging), str(runner)],
        cwd=str(project_root),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=180,
        check=False,
        shell=False,
    )
    line = next(
        (item.removeprefix("SMOKE_JSON ") for item in completed.stdout.splitlines() if item.startswith("SMOKE_JSON ")),
        None,
    )
    if completed.returncode != 0 or line is None:
        raise WheelhouseError(
            "SOX_OVERRIDE_IMPORT_FAILED",
            f"returncode={completed.returncode}; stderr={completed.stderr[-2000:]}",
        )
    result = json.loads(line)
    if (
        result.get("sox_version") != "1.4.1"
        or result.get("qwen_tts_version") != EXPECTED_QWEN_VERSION
        or result.get("torch_version") != EXPECTED_TORCH_VERSION
        or result.get("model_loaded") is not False
    ):
        raise WheelhouseError("SOX_OVERRIDE_IMPORT_MISMATCH", f"unexpected smoke result: {result}")
    receipt = {
        "schema": "mentorflow.qwen-runtime-sox-override-import-smoke.v1",
        "status": "passed",
        "scope": "sox-1.4.1 override plus qwen and runner import in validated development environment",
        "not_proven": [
            "clean portable Python installation",
            "complete wheelhouse import isolation",
            "CUDA model loading",
            "audio synthesis",
        ],
        "network_mode": "offline environment flags",
        "model_execution": False,
        "interpreter": sys.executable,
        "sox_wheel": {
            "filename": artifact["filename"],
            "bytes": artifact["bytes"],
            "sha256": artifact["sha256"],
        },
        "runner": {"path": str(runner), "sha256": runner_sha},
        "result": result,
    }
    _atomic_write(root / "sox-1.4.1-import-smoke.json", _json_bytes(receipt))
    installability_plan = {
        "schema": "mentorflow.qwen-runtime-offline-installability-plan.v1",
        "status": "planned_not_executed",
        "model_execution": False,
        "inputs": {
            "dependency_lock": "dependency-lock.json",
            "wheel_directory": "wheels",
            "package_count": len(plan["artifacts"]),
        },
        "steps": [
            "verify python.org CPython 3.12 embeddable archive and PSF licence receipt",
            "on an isolated E: staging root, verify every wheel size, SHA256, tag, METADATA, RECORD and licence binding",
            "extract every exact wheel member to pack-relative Lib/site-packages without resolving indexes or executing setup code",
            "disable user site and network caches; point TEMP, HF_HOME, TORCH_HOME and NUMBA_CACHE_DIR to E:",
            "import torch, soundfile, librosa, transformers, accelerate, qwen_tts and the production runner without loading a model",
            "assert canonical package versions against dependency-lock.json and assert Gradio/training packages are absent",
            "discard the isolated staging root after recording exact stdout, package paths, hashes and exit code",
        ],
        "acceptance": [
            "all imports originate from the isolated pack-relative staging tree",
            "no network request, C: cache write, model load, CUDA kernel, service listener or synthesis occurs",
            "the two licence supplements pass the separately implemented strict builder contract",
        ],
    }
    _atomic_write(root / "OFFLINE_IMPORT_INSTALLABILITY_PLAN.json", _json_bytes(installability_plan))
    return receipt


def validate_real_supplements_with_builder(root: Path) -> dict[str, Any]:
    """Run the production dependency validator over the two real supplements."""

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts import build_qwen3_tts_runtime_pack as builder

    plan = json.loads((root / "resolution-plan.json").read_text(encoding="utf-8"))
    plan_artifacts = {item["name"]: item for item in plan["artifacts"]}
    validation_root = root / "builder-validation"
    reports = []
    fragments = []
    locks = []
    for (name, version), _declaration in sorted(LICENSE_SUPPLEMENTS.items()):
        artifact = plan_artifacts[name]
        if artifact["version"] != version:
            raise WheelhouseError("SUPPLEMENT_PLAN_MISMATCH", f"plan version mismatch for {name}")
        wheel = root / "wheels" / artifact["filename"]
        wheel_receipt = json.loads(
            (root / "receipts" / f"{name}=={version}.json").read_text(encoding="utf-8")
        )
        supplement_path = (
            root
            / "license-supplements"
            / f"{name}=={version}"
            / "license-supplement-receipt.json"
        )
        supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
        license_path = root / Path(*PurePosixPath(supplement["license_file"]["saved_relative_path"]).parts)
        expanded = validation_root / "site-packages" / f"{name}=={version}"
        expanded.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(wheel, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = _safe_wheel_member(info)
                destination = expanded.joinpath(*relative.parts)
                data = archive.read(info)
                if destination.is_file() and (
                    destination.stat().st_size != len(data)
                    or _sha256_file(destination) != _sha256_bytes(data)
                ):
                    raise WheelhouseError(
                        "BUILDER_VALIDATION_STAGING_MISMATCH",
                        f"existing extracted member differs: {destination}",
                    )
                if not destination.exists():
                    _atomic_write(destination, data)
        include = sorted({item["path"].split("/", 1)[0] for item in wheel_receipt["members"]})
        tree_sha = builder.calculate_allowlist_tree_sha256(expanded, include)
        record_path = wheel_receipt["record"]["path"]
        license_sha = supplement["license_file"]["sha256"]
        purl = supplement["package"]["purl"]
        builder_receipt_payload = {
            "schema": "mentorflow.python-wheel-receipt.v1",
            "name": name,
            "version": version,
            "purl": purl,
            "wheel_filename": wheel.name,
            "wheel_bytes": wheel.stat().st_size,
            "wheel_sha256": artifact["sha256"],
            "record_path": record_path,
            "record_sha256": wheel_receipt["record"]["sha256"],
            "tree_sha256": tree_sha,
            "license_sha256": [license_sha],
            "source_url": artifact["url"],
        }
        builder_receipt_path = validation_root / "receipts" / f"{name}=={version}.python-wheel-receipt.v1.json"
        _atomic_write(builder_receipt_path, _json_bytes(builder_receipt_payload))
        component = {
            "id": name,
            "kind": "dependency",
            "name": name,
            "version": version,
            "supplier": "Upstream PyPI project",
            "root": str(expanded),
            "target_root": builder.TARGET_ROOTS["dependency"],
            "include": include,
            "expected_tree_sha256": tree_sha,
            "license_expression": "Apache-2.0",
            "license_files": [
                {"path": str(license_path), "sha256": license_sha}
            ],
            "license_supplement": {
                "path": str(supplement_path),
                "sha256": _sha256_file(supplement_path),
            },
            "receipt": {
                "path": str(builder_receipt_path),
                "sha256": _sha256_file(builder_receipt_path),
            },
            "homepage": supplement["pypi_metadata_binding"]["repository"],
            "purl": purl,
            "wheel": {
                "path": str(wheel),
                "bytes": wheel.stat().st_size,
                "sha256": artifact["sha256"],
            },
        }
        lock = {
            "component_id": name,
            "name": name,
            "version": version,
            "purl": purl,
            "tree_sha256": tree_sha,
            "record_path": record_path,
            "record_sha256": wheel_receipt["record"]["sha256"],
            "wheel_sha256": artifact["sha256"],
            "license_sha256": [license_sha],
        }
        component_plan = builder._plan_component(validation_root, component)
        builder._validate_dependency_component(validation_root, component_plan, lock)
        reports.append(
            {
                "name": name,
                "version": version,
                "status": "builder_dependency_plan_validated",
                "files": len(component_plan.files),
                "tree_sha256": tree_sha,
                "supplement_receipt_sha256": _sha256_file(supplement_path),
            }
        )
        fragments.append(component)
        locks.append(lock)
    fragment = {
        "schema": "mentorflow.qwen-runtime-builder-dependency-fragment.v1",
        "components": fragments,
        "dependency_lock": locks,
    }
    _atomic_write(validation_root / "real-license-supplement-components.json", _json_bytes(fragment))
    report = {
        "schema": "mentorflow.qwen-runtime-real-supplement-builder-validation.v1",
        "status": "passed",
        "components": reports,
    }
    _atomic_write(validation_root / "real-license-supplement-validation.json", _json_bytes(report))
    return report


def _is_runtime_test_member(relative: str) -> bool:
    if relative.casefold() in RUNTIME_REQUIRED_TESTLIKE_MEMBERS:
        return False
    path = PurePosixPath(relative)
    parts = [part.casefold() for part in path.parts]
    filename = path.name.casefold()
    return any(
        part in {"test", "tests", "testing"} or part.startswith("test_")
        for part in parts
    ) or filename.endswith("_test.py")


def _materialize_runtime_testlike_contract(root: Path, material_root: Path) -> tuple[Path, dict[str, Any]]:
    trace_path = root / "portable-import-smoke-v1" / "evidence" / "testlike-import-trace.json"
    if not trace_path.is_file():
        raise WheelhouseError(
            "RUNTIME_TESTLIKE_TRACE_MISSING",
            f"guarded runtime import trace is missing: {trace_path}",
        )
    trace_sha256 = _sha256_file(trace_path)
    if trace_sha256 != EXPECTED_TESTLIKE_TRACE_SHA256:
        raise WheelhouseError(
            "RUNTIME_TESTLIKE_TRACE_HASH_MISMATCH",
            f"trace changed: expected={EXPECTED_TESTLIKE_TRACE_SHA256}, actual={trace_sha256}",
        )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    safety = trace.get("safety") if isinstance(trace, dict) else None
    events = safety.get("events") if isinstance(safety, dict) else None
    exact_members = trace.get("exact_runtime_members") if isinstance(trace, dict) else None
    if (
        trace.get("schema") != "mentorflow.qwen-runtime-testlike-import-trace.v1"
        or trace.get("status") != "passed"
        or not isinstance(exact_members, list)
        or {item.get("path") for item in exact_members if isinstance(item, dict)}
        != RUNTIME_REQUIRED_TESTLIKE_MEMBERS
        or not isinstance(events, dict)
        or events.get("network") != []
        or events.get("subprocess") != []
        or not isinstance(events.get("listener"), list)
        or len(events["listener"]) != 1
        or safety.get("model_loaded") is not False
        or safety.get("tree_modified") is not False
        or safety.get("cuda_api_called_by_trace") is not False
        or safety.get("suppressed_expected_import_probes") != ["sox -h"]
    ):
        raise WheelhouseError(
            "RUNTIME_TESTLIKE_TRACE_INVALID",
            "guarded trace does not match the approved no-model/no-network evidence boundary",
        )
    stages = trace.get("stages")
    if not isinstance(stages, dict) or set(stages) != {
        "torch", "accelerate", "transformers", "qwen_tts", "runner"
    }:
        raise WheelhouseError("RUNTIME_TESTLIKE_TRACE_INVALID", "trace stages are incomplete")
    entries = []
    for item in exact_members:
        if not isinstance(item, dict):
            raise WheelhouseError("RUNTIME_TESTLIKE_TRACE_INVALID", "trace member is not an object")
        package = str(item.get("package") or "")
        relative = str(item.get("path") or "")
        modules = item.get("modules")
        if (
            relative not in RUNTIME_REQUIRED_TESTLIKE_MEMBERS
            or package not in {"numpy", "scipy", "torch"}
            or not isinstance(item.get("bytes"), int)
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("wheel_sha256") or ""))
            or not isinstance(modules, list)
            or not modules
        ):
            raise WheelhouseError(
                "RUNTIME_TESTLIKE_TRACE_INVALID",
                f"invalid exact trace member: {relative}",
            )
        entry_stages = sorted(
            stage
            for stage, loaded in stages.items()
            if isinstance(loaded, dict) and any(module in loaded for module in modules)
        )
        if not entry_stages:
            raise WheelhouseError(
                "RUNTIME_TESTLIKE_TRACE_INVALID",
                f"trace member has no import stage: {relative}",
            )
        entries.append(
            {
                "package": package,
                "path": relative,
                "bytes": item["bytes"],
                "sha256": item["sha256"],
                "wheel_sha256": item["wheel_sha256"],
                "modules": sorted(set(modules)),
                "stages": entry_stages,
            }
        )
    entries.sort(key=lambda item: (item["package"], item["path"]))
    contract = {
        "schema": "mentorflow.qwen-runtime-required-testlike-contract.v1",
        "source_trace": {
            "schema": trace["schema"],
            "sha256": trace_sha256,
        },
        "scope": "guarded official-wheel runtime import closure",
        "safety": {
            "network_connect_attempts": 0,
            "listener_attempts": 1,
            "subprocess_attempts": 0,
            "model_loaded": False,
            "tree_modified": False,
            "cuda_api_called": False,
            "sox_probe_suppressed": True,
        },
        "entries": entries,
    }
    contract_path = material_root / "receipts" / "runtime-required-testlike-contract.json"
    _atomic_write(contract_path, _json_bytes(contract))
    return contract_path, contract


def audit_builder_payload_compatibility(root: Path) -> dict[str, Any]:
    packages = []
    total_files = 0
    total_bytes = 0
    for receipt_path in sorted((root / "receipts").glob("*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        excluded = []
        for member in receipt["members"]:
            path = PurePosixPath(member["path"])
            if _is_runtime_test_member(path.as_posix()):
                excluded.append(member)
        if excluded:
            packages.append(
                {
                    "name": receipt["name"],
                    "version": receipt["version"],
                    "wheel_filename": receipt["wheel"]["filename"],
                    "wheel_sha256": receipt["wheel"]["sha256"],
                    "receipt_sha256": _sha256_file(receipt_path),
                    "forbidden_member_count": len(excluded),
                    "forbidden_member_bytes": sum(item["bytes"] for item in excluded),
                    "forbidden_members": excluded,
                }
            )
            total_files += len(excluded)
            total_bytes += sum(item["bytes"] for item in excluded)
    report = {
        "schema": "mentorflow.qwen-runtime-builder-payload-compatibility.v1",
        "status": "BLOCKED_EXACT_WHEEL_CLOSURE_CONFLICTS_WITH_RUNTIME_ONLY_POLICY"
        if packages
        else "compatible",
        "policy": {
            "allowed_exclusion_segments": ["test", "tests", "testing", "test_*"],
            "allowed_exclusion_filename_patterns": ["test_*", "*_test.py"],
            "runtime_required_testlike_members": sorted(RUNTIME_REQUIRED_TESTLIKE_MEMBERS),
            "runtime_required_reason": "unconditional public torch import closure",
            "training_and_finetuning_are_not_automatically_excluded": True,
            "current_builder_requires_selected_equals_wheel_equals_record": True,
            "silent_pruning_allowed": False,
        },
        "affected_packages": len(packages),
        "forbidden_member_count": total_files,
        "forbidden_member_bytes": total_bytes,
        "packages": packages,
    }
    _atomic_write(root / "builder-payload-compatibility.json", _json_bytes(report))
    return report


def _validated_generation_id(value: str | None) -> str | None:
    if value is None:
        return None
    generation_id = str(value)
    if not GENERATION_ID.fullmatch(generation_id):
        raise WheelhouseError(
            "GENERATION_ID_INVALID",
            "generation id must be 8-80 ASCII letters, digits, underscore, or hyphen",
        )
    return generation_id


def _strict_12hz_profile_root(root: Path, generation_id: str | None = None) -> Path:
    generation_id = _validated_generation_id(generation_id)
    if generation_id is None:
        return root.joinpath(*STRICT_12HZ_PROFILE_RELATIVE.parts)
    return root.joinpath(
        *STRICT_12HZ_GENERATIONS_RELATIVE.parts,
        generation_id,
        "12hz-only",
    )


def _load_audit_receipts(receipts_root: Path) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for path in sorted(receipts_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = canonicalize_name(str(payload.get("name") or ""))
        if (
            payload.get("schema") != RECEIPT_SCHEMA
            or not name
            or name in receipts
            or not str(payload.get("version") or "")
        ):
            raise WheelhouseError(
                "AUDIT_RECEIPT_SET_INVALID",
                f"invalid or duplicate audited wheel receipt: {path}",
            )
        receipts[name] = payload
    return receipts


def _strict_12hz_closure(
    source_plan: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve the 12Hz release closure from audited wheel METADATA.

    The upstream qwen-tts wheel exposes both 12Hz and 25Hz tokenizers from one
    distribution.  The verified in-memory overlay makes the 25Hz imports
    unreachable, so only its two exclusive root requirements (SoX and ONNX
    Runtime) are policy-excluded.  Every other edge is followed from the exact
    ``Requires-Dist`` values in the audited wheels with CPython 3.12/Windows
    marker evaluation.  This deliberately does not prune ordinary packages
    merely because one import trace happened not to load them.
    """

    raw_artifacts = source_plan.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise WheelhouseError("PLAN_INVALID", "source plan has no artifacts")
    artifacts: dict[str, Mapping[str, Any]] = {}
    for raw in raw_artifacts:
        if not isinstance(raw, Mapping):
            raise WheelhouseError("PLAN_INVALID", "source plan artifact is not an object")
        name = canonicalize_name(str(raw.get("name") or ""))
        if not name or name in artifacts:
            raise WheelhouseError("PLAN_INVALID", f"invalid or duplicate source artifact: {name!r}")
        artifacts[name] = raw
    if ROOT_DISTRIBUTION not in artifacts:
        raise WheelhouseError("PLAN_INVALID", "source plan does not contain qwen-tts")

    environment = _marker_environment()
    queue = [ROOT_DISTRIBUTION]
    selected: set[str] = set()
    parents: dict[str, set[str]] = {ROOT_DISTRIBUTION: set()}
    edges: list[dict[str, str]] = []
    policy_skips: list[dict[str, str]] = []
    while queue:
        name = canonicalize_name(queue.pop(0))
        if name in selected:
            continue
        if name in STRICT_12HZ_EXCLUDED_DISTRIBUTIONS:
            raise WheelhouseError(
                "STRICT_12HZ_ROOT_INVALID",
                f"excluded dependency became a queued release root: {name}",
            )
        artifact = artifacts.get(name)
        receipt = receipts.get(name)
        if artifact is None or receipt is None:
            raise WheelhouseError(
                "STRICT_12HZ_DEPENDENCY_MISSING",
                f"audited artifact/receipt is missing for reachable package {name}",
            )
        version = str(receipt.get("version") or "")
        if (
            canonicalize_name(str(receipt.get("name") or "")) != name
            or version != str(artifact.get("version") or "")
            or str(receipt.get("wheel", {}).get("sha256") or "")
            != str(artifact.get("sha256") or "")
        ):
            raise WheelhouseError(
                "STRICT_12HZ_AUDIT_BINDING_MISMATCH",
                f"audited receipt does not bind the planned artifact for {name}",
            )
        selected.add(name)
        raw_requirements = receipt.get("metadata", {}).get("requires_dist")
        if raw_requirements is None:
            raw_requirements = []
        if not isinstance(raw_requirements, list) or any(
            not isinstance(item, str) for item in raw_requirements
        ):
            raise WheelhouseError(
                "STRICT_12HZ_METADATA_INVALID",
                f"audited Requires-Dist is invalid for {name}",
            )
        for raw_requirement in raw_requirements:
            try:
                requirement = Requirement(raw_requirement)
            except Exception as exc:
                raise WheelhouseError(
                    "STRICT_12HZ_METADATA_INVALID",
                    f"invalid Requires-Dist for {name}: {raw_requirement}",
                ) from exc
            dependency = canonicalize_name(requirement.name)
            if requirement.marker is not None and not requirement.marker.evaluate(environment):
                continue
            if dependency in STRICT_12HZ_EXCLUDED_DISTRIBUTIONS:
                policy_skips.append(
                    {
                        "parent": name,
                        "dependency": dependency,
                        "requires_dist": raw_requirement,
                        "reason": (
                            "verified-12hz-overlay-excludes-25hz-root"
                            if dependency in {"onnxruntime", "sox"}
                            else "release-policy-excluded"
                        ),
                    }
                )
                continue
            dependency_receipt = receipts.get(dependency)
            dependency_artifact = artifacts.get(dependency)
            if dependency_receipt is None or dependency_artifact is None:
                raise WheelhouseError(
                    "STRICT_12HZ_DEPENDENCY_MISSING",
                    f"{name} requires unavailable {dependency}: {raw_requirement}",
                )
            try:
                dependency_version = Version(str(dependency_receipt.get("version") or ""))
            except InvalidVersion as exc:
                raise WheelhouseError(
                    "STRICT_12HZ_METADATA_INVALID",
                    f"invalid audited version for {dependency}",
                ) from exc
            if requirement.specifier and dependency_version not in requirement.specifier:
                raise WheelhouseError(
                    "STRICT_12HZ_VERSION_MISMATCH",
                    f"{name} requires {raw_requirement}, audited {dependency_version}",
                )
            parents.setdefault(dependency, set()).add(name)
            edges.append(
                {
                    "parent": name,
                    "dependency": dependency,
                    "requires_dist": raw_requirement,
                }
            )
            if dependency not in selected:
                queue.append(dependency)

    removed = sorted(set(artifacts) - selected)
    forbidden = sorted(selected.intersection(STRICT_12HZ_EXCLUDED_DISTRIBUTIONS))
    if forbidden:
        raise WheelhouseError(
            "STRICT_12HZ_EXCLUDED_DEPENDENCY_PRESENT",
            f"strict 12Hz closure contains excluded packages: {forbidden}",
        )
    selected_artifacts = []
    for name in sorted(selected):
        artifact = dict(artifacts[name])
        receipt = receipts[name]
        artifact["required_by"] = sorted(parents.get(name, set()))
        artifact["requires_dist_reference"] = list(
            receipt.get("metadata", {}).get("requires_dist") or []
        )
        artifact["requires_dist_source"] = "audited-wheel-METADATA"
        selected_artifacts.append(artifact)
    return {
        "selected_names": sorted(selected),
        "removed_names": removed,
        "artifacts": selected_artifacts,
        "edges": sorted(edges, key=lambda item: (item["parent"], item["dependency"], item["requires_dist"])),
        "policy_skips": sorted(
            policy_skips,
            key=lambda item: (item["parent"], item["dependency"], item["requires_dist"]),
        ),
    }


def materialize_strict_12hz_resolution_plan(
    root: Path,
    *,
    generation_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Create a fresh, additive v2 plan and audit-receipt set from cached wheels."""

    source_plan_path = root / "resolution-plan.json"
    source_receipts_root = root / "receipts"
    if not source_plan_path.is_file() or not source_receipts_root.is_dir():
        raise WheelhouseError(
            "STRICT_12HZ_SOURCE_MISSING",
            "the audited v1 plan and receipts are required as immutable cache inputs",
        )
    source_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    source_receipts = _load_audit_receipts(source_receipts_root)
    closure = _strict_12hz_closure(source_plan, source_receipts)
    profile_root = _strict_12hz_profile_root(root, generation_id)
    profile_root.mkdir(parents=True, exist_ok=True)
    audit_root = profile_root / "audit-receipts"
    audit_root.mkdir(parents=True, exist_ok=True)

    artifacts = closure["artifacts"]
    expected_receipt_names = {
        f"{item['name']}=={item['version']}.json" for item in artifacts
    }
    existing_receipt_names = {path.name for path in audit_root.glob("*.json")}
    unexpected = sorted(existing_receipt_names - expected_receipt_names)
    if unexpected:
        raise WheelhouseError(
            "STRICT_12HZ_V2_RECEIPT_SET_INVALID",
            f"v2 audit receipt directory contains unexpected files: {unexpected}",
        )
    fresh_receipts = []
    for artifact in artifacts:
        wheel = root / "wheels" / str(artifact["filename"])
        if not wheel.is_file():
            raise WheelhouseError("WHEEL_MISSING", f"cached wheel is missing: {wheel}")
        receipt = audit_wheel(wheel, artifact)
        receipt_path = audit_root / f"{artifact['name']}=={artifact['version']}.json"
        _atomic_write(receipt_path, _json_bytes(receipt))
        fresh_receipts.append(receipt)

    fresh_by_name = {
        canonicalize_name(str(item["name"])): item for item in fresh_receipts
    }
    fresh_closure = _strict_12hz_closure(source_plan, fresh_by_name)
    if fresh_closure["selected_names"] != closure["selected_names"]:
        raise WheelhouseError(
            "STRICT_12HZ_CLOSURE_DRIFT",
            "fresh v2 wheel audit changed the strict 12Hz dependency closure",
        )
    removed_artifacts = [
        {
            "name": name,
            "version": next(
                str(item["version"])
                for item in source_plan["artifacts"]
                if canonicalize_name(str(item["name"])) == name
            ),
            "reason": (
                "verified-12hz-overlay-excludes-25hz-root"
                if name in {"onnxruntime", "sox"}
                else "unreachable-after-25hz-root-removal"
            ),
        }
        for name in fresh_closure["removed_names"]
    ]
    plan = {
        "schema": STRICT_12HZ_PLAN_SCHEMA,
        "profile": "12hz-only",
        **({"generation_id": generation_id} if generation_id is not None else {}),
        "target": source_plan["target"],
        "dependency_count": len(fresh_closure["artifacts"]),
        "artifact_count": len(fresh_closure["artifacts"]),
        "download_bytes": sum(int(item["bytes"]) for item in fresh_closure["artifacts"]),
        "download_gib": round(
            sum(int(item["bytes"]) for item in fresh_closure["artifacts"])
            / (1024**3),
            6,
        ),
        "blockers": [],
        "source_plan": {
            "schema": source_plan.get("schema"),
            "path": str(source_plan_path),
            "sha256": _sha256_file(source_plan_path),
            "artifact_count": len(source_plan["artifacts"]),
        },
        "closure_policy": {
            "algorithm": "audited-wheel-METADATA-reachability",
            "marker_environment": _marker_environment(),
            "production_entry": [
                "install_qwen_12hz_only_runtime(pack_root)",
                "from qwen_tts import Qwen3TTSModel",
            ],
            "excluded_25hz_roots": ["onnxruntime", "sox"],
            "trace_gate": (
                "portable v2 product-entry probe must show both modules absent, "
                "the 25Hz tokenizer absent, and only the expected guarded ::1:0 bind"
            ),
            "ordinary_trace_pruning": False,
        },
        "policy_skips": fresh_closure["policy_skips"],
        "closure_edges": fresh_closure["edges"],
        "removed_artifacts": removed_artifacts,
        "artifacts": fresh_closure["artifacts"],
    }
    _atomic_write(profile_root / "resolution-plan.json", _json_bytes(plan))
    return plan, fresh_receipts


def materialize_builder_dependency_inputs(
    root: Path,
    *,
    strict_12hz_v2: bool = False,
    generation_id: str | None = None,
) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts import build_qwen3_tts_runtime_pack as builder

    generation_id = _validated_generation_id(generation_id)
    profile_root = (
        _strict_12hz_profile_root(root, generation_id) if strict_12hz_v2 else root
    )
    if strict_12hz_v2:
        plan, audit_receipts = materialize_strict_12hz_resolution_plan(
            root,
            generation_id=generation_id,
        )
    else:
        plan = json.loads((root / "resolution-plan.json").read_text(encoding="utf-8"))
        audit_receipts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((root / "receipts").glob("*.json"))
        ]
    artifacts = {item["name"]: item for item in plan["artifacts"]}
    material_root = (
        profile_root / "builder-inputs"
        if strict_12hz_v2
        else root / "builder-inputs-v1"
    )
    site_packages = material_root / "site-packages"
    receipts_root = material_root / "receipts"
    preflight_selected_paths: list[str] = []
    preflight_windows_paths: dict[str, str] = {}
    try:
        for audit in audit_receipts:
            for member in audit["members"]:
                if _is_runtime_test_member(member["path"]):
                    continue
                relative = builder._safe_relative(
                    member["path"],
                    field=f"{audit['name']}.wheel_member",
                )
                folded = builder.windows_casefold_key(relative)
                previous = preflight_windows_paths.get(folded)
                if previous is not None:
                    raise WheelhouseError(
                        "BUILDER_STAGING_WINDOWS_PATH_COLLISION",
                        f"selected wheels collide on Windows: {previous!r} and {relative!r}",
                    )
                preflight_windows_paths[folded] = relative
                preflight_selected_paths.append(relative)
        staging_path_budget = builder.calculate_install_path_budget(
            preflight_selected_paths
        )
        staging_path_acceptance = builder.validate_install_root_budget(
            site_packages,
            staging_path_budget,
            relative_paths=preflight_selected_paths,
        )
    except builder.BuildError as exc:
        raise WheelhouseError(exc.code, str(exc)) from exc
    print(
        "BUILDER_STAGING_PATH_BUDGET_OK "
        f"max_relative={staging_path_budget['max_relative_path']} "
        f"max_relative_chars={staging_path_budget['max_relative_path_chars']} "
        f"root_chars={staging_path_acceptance['root_chars']} "
        f"max_root_chars={staging_path_acceptance['maximum_root_chars']}",
        flush=True,
    )
    site_packages.mkdir(parents=True, exist_ok=True)
    receipts_root.mkdir(parents=True, exist_ok=True)
    runtime_trace_path, runtime_trace_contract = _materialize_runtime_testlike_contract(
        root,
        material_root,
    )
    runtime_trace_sha256 = _sha256_file(runtime_trace_path)

    selected_uncompressed = sum(
        member["bytes"]
        for receipt in audit_receipts
        for member in receipt["members"]
        if not _is_runtime_test_member(member["path"])
    )
    free_bytes = shutil.disk_usage(root.anchor).free
    if free_bytes < selected_uncompressed + 1024 * 1024 * 1024:
        raise WheelhouseError(
            "DISK_SPACE_INSUFFICIENT",
            f"E: free={free_bytes}, extraction plus headroom={selected_uncompressed + 1024 * 1024 * 1024}",
        )
    print(
        f"BUILDER_STAGING_SPACE_OK free={free_bytes} selected_uncompressed={selected_uncompressed}",
        flush=True,
    )

    components = []
    locks = []
    inventory_components = []
    wheel_license_files: list[dict[str, Any]] = []
    external_license_supplements: list[dict[str, Any]] = []
    expected_selected_paths: set[str] = set()
    for package_index, audit in enumerate(audit_receipts, start=1):
        name = audit["name"]
        version = audit["version"]
        artifact = artifacts[name]
        wheel = root / "wheels" / audit["wheel"]["filename"]
        if _sha256_file(wheel) != artifact["sha256"]:
            raise WheelhouseError("WHEEL_DIGEST_MISMATCH", f"wheel changed before staging: {wheel}")
        exclusions = [
            {
                "path": member["path"],
                "bytes": member["bytes"],
                "sha256": member["sha256"],
                "reason": "non-runtime-test",
            }
            for member in audit["members"]
            if _is_runtime_test_member(member["path"])
        ]
        exclusions.sort(key=lambda item: item["path"])
        excluded_paths = {item["path"] for item in exclusions}
        runtime_required_testlike = [
            {
                "path": member["path"],
                "bytes": member["bytes"],
                "sha256": member["sha256"],
                "reason": "runtime-import-trace",
            }
            for member in audit["members"]
            if member["path"] in RUNTIME_REQUIRED_TESTLIKE_MEMBERS
        ]
        runtime_required_testlike.sort(key=lambda item: item["path"])
        traced_for_package = [
            item for item in runtime_trace_contract["entries"] if item["package"] == name
        ]
        if runtime_required_testlike:
            expected_trace_items = {
                item["path"]: (item["bytes"], item["sha256"], item["wheel_sha256"])
                for item in traced_for_package
            }
            actual_trace_items = {
                item["path"]: (item["bytes"], item["sha256"], artifact["sha256"])
                for item in runtime_required_testlike
            }
            if actual_trace_items != expected_trace_items:
                raise WheelhouseError(
                    "RUNTIME_TESTLIKE_TRACE_MISMATCH",
                    f"selected members differ from guarded trace for {name}",
                )
        elif traced_for_package:
            raise WheelhouseError(
                "RUNTIME_TESTLIKE_TRACE_MISMATCH",
                f"trace declares members that the audited wheel does not select for {name}",
            )
        required_protected = {
            audit["metadata"]["path"],
            audit["record"]["path"],
            *(item["path"] for item in audit["licenses"]),
        }
        if excluded_paths.intersection(required_protected):
            raise WheelhouseError(
                "WHEEL_EXCLUSION_PROTECTED_MEMBER",
                f"test rule selected protected wheel metadata/license for {name}",
            )
        selected = [item for item in audit["members"] if item["path"] not in excluded_paths]
        with zipfile.ZipFile(wheel, "r") as archive:
            archive_members = {info.filename: info for info in archive.infolist() if not info.is_dir()}
            for member in selected:
                relative = member["path"]
                info = archive_members.get(relative)
                if info is None:
                    raise WheelhouseError("WHEEL_MEMBER_MISSING", f"missing {wheel.name}:{relative}")
                data = archive.read(info)
                if len(data) != member["bytes"] or _sha256_bytes(data) != member["sha256"]:
                    raise WheelhouseError("WHEEL_MEMBER_MISMATCH", f"changed {wheel.name}:{relative}")
                destination = site_packages.joinpath(*PurePosixPath(relative).parts)
                if relative in expected_selected_paths:
                    raise WheelhouseError("WHEEL_MEMBER_COLLISION", f"duplicate installed path: {relative}")
                expected_selected_paths.add(relative)
                if destination.is_file():
                    if destination.stat().st_size != len(data) or _sha256_file(destination) != member["sha256"]:
                        raise WheelhouseError(
                            "BUILDER_STAGING_MISMATCH", f"existing staged file differs: {destination}"
                        )
                else:
                    _atomic_write(destination, data)
        include = sorted({item["path"].split("/", 1)[0] for item in selected})
        tree_sha = builder.calculate_allowlist_tree_sha256(
            site_packages,
            include,
            runtime_required_testlike=runtime_required_testlike or None,
        )
        purl = builder._canonical_pypi_purl(name, version)
        supplement_path = (
            root / "license-supplements" / f"{name}=={version}" / "license-supplement-receipt.json"
        )
        if supplement_path.is_file():
            supplement_payload = json.loads(supplement_path.read_text(encoding="utf-8"))
            license_files = [
                root
                / Path(*PurePosixPath(supplement_payload["license_file"]["saved_relative_path"]).parts)
            ]
            supplement_declaration = {
                "path": str(supplement_path),
                "sha256": _sha256_file(supplement_path),
            }
            external_license_supplements.append(
                {
                    "component_id": name,
                    "path": str(supplement_payload["license_file"]["saved_relative_path"]),
                    "sha256": str(supplement_payload["license_file"]["sha256"]),
                    "receipt_sha256": supplement_declaration["sha256"],
                }
            )
        else:
            license_files = [
                site_packages.joinpath(*PurePosixPath(item["path"]).parts)
                for item in audit["licenses"]
            ]
            supplement_declaration = None
        wheel_license_files.extend(
            {
                "component_id": name,
                "path": str(item["path"]),
                "sha256": str(item["sha256"]),
            }
            for item in audit["licenses"]
        )
        if not license_files or any(not path.is_file() for path in license_files):
            raise WheelhouseError("LICENSE_EVIDENCE_MISSING", f"license material missing for {name}")
        license_hashes = sorted({_sha256_file(path) for path in license_files})
        expression = str(audit["metadata"].get("license_expression") or "").strip()
        if not expression:
            expression = RESOLVED_LICENSE_EXPRESSIONS.get(name, "")
        if not expression:
            raise WheelhouseError("LICENSE_EXPRESSION_UNRESOLVED", f"SPDX expression missing for {name}")
        receipt_payload = {
            "schema": "mentorflow.python-wheel-receipt.v1",
            "name": name,
            "version": version,
            "purl": purl,
            "wheel_filename": wheel.name,
            "wheel_bytes": wheel.stat().st_size,
            "wheel_sha256": artifact["sha256"],
            "record_path": audit["record"]["path"],
            "record_sha256": audit["record"]["sha256"],
            "tree_sha256": tree_sha,
            "license_sha256": license_hashes,
            "source_url": artifact["url"],
        }
        if exclusions:
            receipt_payload["wheel_exclusions"] = exclusions
        if runtime_required_testlike:
            receipt_payload["runtime_required_testlike"] = runtime_required_testlike
            receipt_payload["runtime_import_trace_sha256"] = runtime_trace_sha256
        receipt_path = receipts_root / f"{name}=={version}.python-wheel-receipt.v1.json"
        _atomic_write(receipt_path, _json_bytes(receipt_payload))
        component = {
            "id": name,
            "kind": "dependency",
            "name": name,
            "version": version,
            "supplier": "Upstream Python package publisher",
            "root": str(site_packages),
            "target_root": builder.TARGET_ROOTS["dependency"],
            "include": include,
            "expected_tree_sha256": tree_sha,
            "license_expression": expression,
            "license_files": [
                {"path": str(path), "sha256": _sha256_file(path)} for path in license_files
            ],
            "receipt": {"path": str(receipt_path), "sha256": _sha256_file(receipt_path)},
            "homepage": (
                str(audit["metadata"].get("project_urls", [""])[0]).split(", ", 1)[-1]
                if audit["metadata"].get("project_urls")
                else "https://pypi.org/project/" + name + "/" + version + "/"
            ),
            "purl": purl,
            "wheel": {
                "path": str(wheel),
                "bytes": wheel.stat().st_size,
                "sha256": artifact["sha256"],
            },
        }
        if supplement_declaration is not None:
            component["license_supplement"] = supplement_declaration
        if exclusions:
            component["wheel_exclusions"] = exclusions
        if runtime_required_testlike:
            component["runtime_required_testlike"] = runtime_required_testlike
            component["runtime_import_trace"] = {
                "path": str(runtime_trace_path),
                "sha256": runtime_trace_sha256,
            }
        lock = {
            "component_id": name,
            "name": name,
            "version": version,
            "purl": purl,
            "tree_sha256": tree_sha,
            "record_path": audit["record"]["path"],
            "record_sha256": audit["record"]["sha256"],
            "wheel_sha256": artifact["sha256"],
            "license_sha256": license_hashes,
        }
        if exclusions:
            lock["wheel_exclusions"] = exclusions
        if runtime_required_testlike:
            lock["runtime_required_testlike"] = runtime_required_testlike
            lock["runtime_import_trace_sha256"] = runtime_trace_sha256
        component_plan = builder._plan_component(material_root, component)
        builder._validate_dependency_component(material_root, component_plan, lock)
        components.append(component)
        locks.append(lock)
        inventory_components.append(
            {
                "name": name,
                "version": version,
                "purl": purl,
                "license_expression": expression,
                "license_sha256": license_hashes,
                "wheel_sha256": artifact["sha256"],
                "selected_files": len(selected),
                "excluded_test_files": len(exclusions),
                "excluded_test_bytes": sum(item["bytes"] for item in exclusions),
                "wheel_exclusions": exclusions,
                "runtime_required_testlike": runtime_required_testlike,
                "runtime_import_trace_sha256": runtime_trace_sha256
                if runtime_required_testlike
                else None,
            }
        )
        print(
            f"BUILDER_COMPONENT_VALIDATED {package_index}/{len(audit_receipts)} "
            f"{name} selected={len(selected)} excluded={len(exclusions)}",
            flush=True,
        )
    license_evidence = _license_evidence_summary(
        wheel_license_files,
        external_license_supplements,
    )
    if strict_12hz_v2 and (
        license_evidence["wheel_file_count"] != EXPECTED_STRICT_WHEEL_LICENSE_FILES
        or license_evidence["external_supplement_file_count"]
        != EXPECTED_STRICT_EXTERNAL_LICENSE_SUPPLEMENTS
        or license_evidence["total_file_count"] != EXPECTED_STRICT_TOTAL_LICENSE_FILES
    ):
        raise WheelhouseError(
            "LICENSE_EVIDENCE_COUNT_MISMATCH",
            "strict v2 requires exactly 103 in-wheel plus 1 external supplement license file",
        )
    actual_paths = {
        path.relative_to(site_packages).as_posix()
        for path in site_packages.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_selected_paths:
        raise WheelhouseError(
            "BUILDER_STAGING_FILE_SET_MISMATCH",
            f"extra={sorted(actual_paths - expected_selected_paths)[:20]}, missing={sorted(expected_selected_paths - actual_paths)[:20]}",
        )
    from backend.runtime.qwen_base_clone_server import build_qwen_12hz_overlay_contract

    source_overlay_contract = build_qwen_12hz_overlay_contract(site_packages)
    source_overlay_path = receipts_root / "qwen-12hz-source-overlay.json"
    _atomic_write(source_overlay_path, _json_bytes(source_overlay_contract))
    source_overlay_declaration = {
        "path": str(source_overlay_path),
        "sha256": _sha256_file(source_overlay_path),
    }
    builder._read_source_overlay_contract(
        material_root,
        source_overlay_declaration,
        component_id="mentorflow-qwen-runner",
    )
    fragment = {
        "schema": (
            "mentorflow.qwen-runtime-builder-dependency-fragment.v2"
            if strict_12hz_v2
            else "mentorflow.qwen-runtime-builder-dependency-fragment.v1"
        ),
        **({"profile": "12hz-only"} if strict_12hz_v2 else {}),
        **(
            {"generation_id": generation_id}
            if strict_12hz_v2 and generation_id is not None
            else {}
        ),
        "components": components,
        "dependency_lock": locks,
        "runner_source_overlay": source_overlay_declaration,
        **({"license_evidence": license_evidence} if strict_12hz_v2 else {}),
        **(
            {
                "resolution_plan": {
                    "path": str(profile_root / "resolution-plan.json"),
                    "sha256": _sha256_file(profile_root / "resolution-plan.json"),
                },
                "runtime_policy": {
                    "product_entry": [
                        "install_qwen_12hz_only_runtime(pack_root)",
                        "from qwen_tts import Qwen3TTSModel",
                    ],
                    "numba_threading_layer": "workqueue",
                    "tbb": "optional-not-shipped",
                    "forbidden_distributions": ["onnxruntime", "sox"],
                },
            }
            if strict_12hz_v2
            else {}
        ),
    }
    lock_payload = {
        "schema": "mentorflow.qwen-runtime-dependency-lock.v1",
        "target": plan["target"],
        "package_count": len(locks),
        "packages": locks,
    }
    inventory = {
        "schema": (
            "mentorflow.qwen-runtime-third-party-builder-inventory.v2"
            if strict_12hz_v2
            else "mentorflow.qwen-runtime-third-party-builder-inventory.v1"
        ),
        **({"profile": "12hz-only"} if strict_12hz_v2 else {}),
        **(
            {"generation_id": generation_id}
            if strict_12hz_v2 and generation_id is not None
            else {}
        ),
        "package_count": len(inventory_components),
        "selected_files": len(expected_selected_paths),
        "selected_bytes": sum(
            member["bytes"]
            for audit in audit_receipts
            for member in audit["members"]
            if not _is_runtime_test_member(member["path"])
        ),
        "license_files": (
            license_evidence["total_file_count"]
            if strict_12hz_v2
            else sum(len(audit["licenses"]) for audit in audit_receipts)
        ),
        **(
            {
                "wheel_license_files": license_evidence["wheel_file_count"],
                "external_license_supplement_files": license_evidence[
                    "external_supplement_file_count"
                ],
                "license_evidence": license_evidence,
            }
            if strict_12hz_v2
            else {}
        ),
        "excluded_test_files": sum(item["excluded_test_files"] for item in inventory_components),
        "excluded_test_bytes": sum(item["excluded_test_bytes"] for item in inventory_components),
        "runtime_required_testlike_members": sorted(RUNTIME_REQUIRED_TESTLIKE_MEMBERS),
        "runtime_required_reason": "guarded official-wheel runtime import trace",
        "runtime_import_trace": {
            "contract_sha256": runtime_trace_sha256,
            "source_trace_sha256": runtime_trace_contract["source_trace"]["sha256"],
        },
        "source_overlay": {
            "contract_sha256": source_overlay_declaration["sha256"],
            "policy": source_overlay_contract["policy"],
            "upstream_wheel_sha256": source_overlay_contract["upstream_wheel"]["sha256"],
            "transforms": source_overlay_contract["transforms"],
        },
        "components": inventory_components,
        **(
            {
                "removed_packages": [
                    item["name"] for item in plan["removed_artifacts"]
                ],
                "resolution_plan_sha256": _sha256_file(
                    profile_root / "resolution-plan.json"
                ),
                "tbb": {
                    "status": "optional-not-shipped",
                    "selected_numba_threading_layer": "workqueue",
                },
            }
            if strict_12hz_v2
            else {}
        ),
    }
    fragment_output = material_root / "builder-dependency-fragment.json"
    lock_output = profile_root / "builder-dependency-lock.json"
    inventory_output = profile_root / "THIRD_PARTY_BUILDER_INVENTORY.json"
    _atomic_write(fragment_output, _json_bytes(fragment))
    _atomic_write(lock_output, _json_bytes(lock_payload))
    _atomic_write(inventory_output, _json_bytes(inventory))
    if strict_12hz_v2:
        report = {
            "schema": "mentorflow.qwen-runtime-builder-dependency-validation.v2",
            "status": "passed",
            "profile": "12hz-only",
            **({"generation_id": generation_id} if generation_id is not None else {}),
            "fragment": _json_evidence_pin(
                fragment_output,
                schema="mentorflow.qwen-runtime-builder-dependency-fragment.v2",
            ),
            "dependency_lock": _json_evidence_pin(
                lock_output,
                schema="mentorflow.qwen-runtime-dependency-lock.v1",
                package_count=len(locks),
            ),
            "inventory": _json_evidence_pin(
                inventory_output,
                schema="mentorflow.qwen-runtime-third-party-builder-inventory.v2",
                package_count=len(inventory_components),
            ),
            "resolution_plan": _json_evidence_pin(
                profile_root / "resolution-plan.json",
                schema="mentorflow.qwen-runtime-wheelhouse-plan.v2",
                profile="12hz-only",
            ),
            "package_count": len(components),
            "selected_files": len(expected_selected_paths),
            "selected_bytes": inventory["selected_bytes"],
            "license_files": inventory["license_files"],
            "wheel_license_files": inventory["wheel_license_files"],
            "external_license_supplement_files": inventory[
                "external_license_supplement_files"
            ],
            "license_evidence": inventory["license_evidence"],
            "excluded_test_files": inventory["excluded_test_files"],
            "excluded_test_bytes": inventory["excluded_test_bytes"],
            "runtime_required_testlike_members": sorted(RUNTIME_REQUIRED_TESTLIKE_MEMBERS),
            "runtime_import_trace": {
                "contract_sha256": runtime_trace_sha256,
                "source_trace_sha256": runtime_trace_contract["source_trace"]["sha256"],
            },
            "source_overlay": {
                "path": source_overlay_declaration["path"],
                "sha256": source_overlay_declaration["sha256"],
                "policy": source_overlay_contract["policy"],
                "upstream_wheel_sha256": source_overlay_contract["upstream_wheel"]["sha256"],
                "transforms": source_overlay_contract["transforms"],
            },
            "site_packages": _plain_tree_stats(site_packages),
            "removed_packages": inventory["removed_packages"],
            "numba_threading_layer": "workqueue",
            "tbb": "optional-not-shipped",
        }
    else:
        report = {
            "schema": "mentorflow.qwen-runtime-builder-dependency-validation.v1",
            "status": "passed",
            "package_count": len(components),
            "selected_files": len(expected_selected_paths),
            "selected_bytes": inventory["selected_bytes"],
            "license_files": inventory["license_files"],
            "excluded_test_files": inventory["excluded_test_files"],
            "excluded_test_bytes": inventory["excluded_test_bytes"],
            "runtime_required_testlike_members": sorted(RUNTIME_REQUIRED_TESTLIKE_MEMBERS),
            "runtime_import_trace_sha256": runtime_trace_sha256,
            "runtime_import_source_trace_sha256": runtime_trace_contract["source_trace"]["sha256"],
            "source_overlay_sha256": source_overlay_declaration["sha256"],
            "source_overlay_transforms": source_overlay_contract["transforms"],
            "site_packages": str(site_packages),
        }
    validation_output = (
        profile_root / "evidence" / "builder-dependency-validation.core.json"
        if strict_12hz_v2
        else material_root / "builder-dependency-validation.json"
    )
    _atomic_write(validation_output, _json_bytes(report))
    return report


def _portable_probe_source_v2() -> str:
    return r'''from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


events = {"bind": [], "connect": [], "listen": [], "subprocess": [], "model_load": []}


def _deny_bind(self, address):
    events["bind"].append(repr(address))
    raise RuntimeError("socket bind disabled by portable product-entry smoke")


def _deny_connect(self, address):
    events["connect"].append(repr(address))
    raise RuntimeError("network connect disabled by portable product-entry smoke")


def _deny_create_connection(address, *args, **kwargs):
    events["connect"].append(repr(address))
    raise RuntimeError("network connect disabled by portable product-entry smoke")


def _deny_listen(self, backlog=0):
    events["listen"].append(f"listen:{backlog}")
    raise RuntimeError("listeners disabled by portable product-entry smoke")


def _deny_process(*args, **kwargs):
    events["subprocess"].append(repr(args[0] if args else kwargs.get("args")))
    raise RuntimeError("subprocess disabled by portable product-entry smoke")


_original_popen = subprocess.Popen


class _GuardedPopen(_original_popen):
    def __init__(self, *args, **kwargs):
        events["subprocess"].append(repr(args[0] if args else kwargs.get("args")))
        raise RuntimeError("subprocess disabled by portable product-entry smoke")


socket.socket.bind = _deny_bind
socket.socket.connect = _deny_connect
socket.socket.connect_ex = _deny_connect
socket.create_connection = _deny_create_connection
socket.socket.listen = _deny_listen
subprocess.Popen = _GuardedPopen
os.system = _deny_process


def _native_module_paths():
    if os.name != "nt":
        raise RuntimeError("portable native-module probe requires Windows")
    psapi = ctypes.WinDLL("Psapi.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.EnumProcessModules.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HMODULE),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.EnumProcessModules.restype = wintypes.BOOL
    kernel32.GetModuleFileNameW.argtypes = [
        wintypes.HMODULE,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD
    process = kernel32.GetCurrentProcess()
    modules = (wintypes.HMODULE * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcessModules(
        process, modules, ctypes.sizeof(modules), ctypes.byref(needed)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    count = min(needed.value // ctypes.sizeof(wintypes.HMODULE), len(modules))
    paths = []
    for index in range(count):
        buffer = ctypes.create_unicode_buffer(32768)
        if kernel32.GetModuleFileNameW(modules[index], buffer, len(buffer)):
            paths.append(str(Path(buffer.value).resolve(strict=True)))
    return sorted(set(paths), key=str.casefold)


lock_path = Path(sys.argv[1]).resolve(strict=True)
site_packages = Path(sys.argv[2]).resolve(strict=True)
runner_path = Path(sys.argv[3]).resolve(strict=True)
runtime_python = Path(sys.argv[4]).resolve(strict=True)
runtime_root = runtime_python.parent
lock = json.loads(lock_path.read_text(encoding="utf-8"))


def _canonicalize_name(value):
    return re.sub(r"[-_.]+", "-", value).casefold()


expected = {
    _canonicalize_name(item["name"]): item["version"] for item in lock["packages"]
}
actual = {}
metadata_paths = {}
for distribution in metadata.distributions(path=[str(site_packages)]):
    name = _canonicalize_name(distribution.metadata["Name"])
    if name in actual:
        raise RuntimeError(f"duplicate distribution metadata: {name}")
    actual[name] = distribution.version
    distribution_path = Path(distribution._path).resolve(strict=True)
    if not distribution_path.is_relative_to(site_packages):
        raise RuntimeError(f"distribution metadata escaped portable tree: {distribution_path}")
    metadata_paths[name] = str(distribution_path)
if actual != expected:
    raise RuntimeError(
        f"distribution closure mismatch: missing={sorted(set(expected) - set(actual))}, "
        f"extra={sorted(set(actual) - set(expected))}, "
        f"versions={sorted(name for name in expected if actual.get(name) != expected[name])}"
    )

excluded_distributions = [
    "deepspeed", "flash-attn", "flatbuffers", "gradio", "gradio-client",
    "hf-gradio", "onnxruntime", "peft", "protobuf", "sox",
]
excluded_modules = [
    "deepspeed", "flash_attn", "flatbuffers", "gradio", "gradio_client",
    "google.protobuf", "onnxruntime", "peft", "sox",
]
for name in excluded_distributions:
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        pass
    else:
        raise RuntimeError(f"excluded distribution is installed: {name}")
for module_name in excluded_modules:
    try:
        excluded_spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        excluded_spec = None
    if excluded_spec is not None:
        raise RuntimeError(f"excluded module is importable: {module_name}")

native_modules_before_product_entry = _native_module_paths()
spec = importlib.util.spec_from_file_location("mentorflow_qwen_runner_portable_smoke", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError("production runner spec could not be created")
runner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner_module)
overlay = runner_module.install_qwen_12hz_only_runtime(runtime_python.parents[1])
if (
    overlay["sox_loaded"]
    or overlay["tokenizer_25hz_loaded"]
    or overlay["tbbpool_loaded"]
    or overlay["numba_threading_layer"] != "workqueue"
    or overlay["tbb"] != "optional-not-shipped"
):
    raise RuntimeError(f"strict 12Hz overlay state is invalid: {overlay}")

# This is the production package entry.  Do not replace it with a list of
# hand-picked dependency imports: the resulting sys.modules set is the trace.
from qwen_tts import Qwen3TTSModel
import qwen_tts


def _deny_model_load(cls, *args, **kwargs):
    events["model_load"].append(repr(args[0] if args else None))
    raise RuntimeError("model loading disabled by portable product-entry smoke")


Qwen3TTSModel.from_pretrained = classmethod(_deny_model_load)
backend = runner_module.QwenModelBackend(
    pack_root=runtime_python.parents[1],
    device="cuda:0",
    minimum_driver="999.0",
)
if backend._model is not None:
    raise RuntimeError("runner unexpectedly loaded a model")

numba_runtime = runner_module._enforce_numba_workqueue()
numba_module = sys.modules.get("numba")
if numba_module is None:
    raise RuntimeError("the real Qwen product entry did not load its pinned Numba path")
active_numba_config = getattr(getattr(numba_module, "config", None), "THREADING_LAYER", None)
if active_numba_config != "workqueue" or numba_runtime["configured"] != "workqueue":
    raise RuntimeError(
        f"Numba workqueue gate drifted: active={active_numba_config!r}, state={numba_runtime}"
    )

for forbidden in (
    "onnxruntime", "sox", "flatbuffers", "google.protobuf",
    "qwen_tts.core.tokenizer_25hz", "numba.np.ufunc.tbbpool",
):
    if any(name == forbidden or name.startswith(forbidden + ".") for name in sys.modules):
        raise RuntimeError(f"product entry loaded forbidden module: {forbidden}")

expected_events = {
    "bind": ["('::1', 0)"],
    "connect": [],
    "listen": [],
    "subprocess": [],
    "model_load": [],
}
if events != expected_events:
    raise RuntimeError(f"guarded operation trace differs: expected={expected_events}, actual={events}")

qwen_path = Path(qwen_tts.__file__).resolve(strict=True)
if not qwen_path.is_relative_to(site_packages):
    raise RuntimeError(f"qwen_tts imported outside portable tree: {qwen_path}")
loaded_portable_modules = {}
for name, module in sorted(sys.modules.items()):
    raw_path = getattr(module, "__file__", None)
    if not raw_path:
        continue
    try:
        module_path = Path(raw_path).resolve(strict=True)
    except (OSError, RuntimeError):
        continue
    if module_path.is_relative_to(site_packages):
        loaded_portable_modules[name] = str(module_path)


native_modules = _native_module_paths()
native_modules_added = sorted(
    set(native_modules) - set(native_modules_before_product_entry),
    key=str.casefold,
)
windows_root = Path(os.environ["SystemRoot"]).resolve(strict=True)
nvidia_names = {"nvcuda.dll", "nvml.dll", "nvapi64.dll"}
native_boundaries = {
    "portable": [],
    "windows_system": [],
    "nvidia_driver": [],
    "host_injected_baseline": [],
    "host_external_added": [],
}
for raw_path in native_modules:
    module_path = Path(raw_path)
    name = module_path.name.casefold()
    if module_path.is_relative_to(runtime_root):
        native_boundaries["portable"].append(raw_path)
    elif name in nvidia_names or "nvidia" in raw_path.casefold():
        native_boundaries["nvidia_driver"].append(raw_path)
    elif module_path.is_relative_to(windows_root):
        native_boundaries["windows_system"].append(raw_path)
    elif raw_path in native_modules_before_product_entry:
        native_boundaries["host_injected_baseline"].append(raw_path)
    else:
        native_boundaries["host_external_added"].append(raw_path)
resolved_sys_path = [str(Path(item).resolve(strict=False)) for item in sys.path]
for entry in resolved_sys_path:
    if not Path(entry).is_relative_to(runtime_python):
        raise RuntimeError(f"sys.path escaped portable Python: {entry}")
if Path(sys.executable).resolve(strict=True).parent != runtime_python:
    raise RuntimeError(f"unexpected interpreter: {sys.executable}")
if sys.flags.isolated != 1:
    raise RuntimeError("portable interpreter is not isolated")

print("PORTABLE_SMOKE_JSON " + json.dumps({
    "status": "passed",
    "product_entry": [
        "install_qwen_12hz_only_runtime(pack_root)",
        "from qwen_tts import Qwen3TTSModel",
    ],
    "python_version": sys.version.split()[0],
    "python_executable": str(Path(sys.executable).resolve(strict=True)),
    "python_prefix": str(Path(sys.prefix).resolve(strict=True)),
    "sys_path": resolved_sys_path,
    "isolated": sys.flags.isolated,
    "dont_write_bytecode": sys.dont_write_bytecode,
    "package_count": len(actual),
    "packages": dict(sorted(actual.items())),
    "metadata_paths": dict(sorted(metadata_paths.items())),
    "excluded_distributions": excluded_distributions,
    "excluded_modules": excluded_modules,
    "loaded_portable_modules": loaded_portable_modules,
    "qwen_model_symbol": Qwen3TTSModel.__name__,
    "qwen_module_path": str(qwen_path),
    "overlay": {
        key: value if not isinstance(value, Path) else str(value)
        for key, value in overlay.items()
    },
    "numba": {
        "configured": numba_runtime["configured"],
        "active_config": active_numba_config,
        "tbbpool_loaded": False,
        "tbb": "optional-not-shipped",
    },
    "runner_path": str(runner_path),
    "backend_model_loaded": backend._model is not None,
    "guarded_attempts": events,
    "loaded_native_modules": native_modules,
    "native_modules_before_product_entry": native_modules_before_product_entry,
    "native_modules_added_by_product_entry": native_modules_added,
    "native_boundaries": native_boundaries,
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
}, ensure_ascii=False, sort_keys=True))
'''


WINDOWS_SYSTEM_PE_BOUNDARY = frozenset(
    {
        "advapi32.dll",
        "avrt.dll",
        "bcrypt.dll",
        "bcryptprimitives.dll",
        "cabinet.dll",
        "cfgmgr32.dll",
        "comctl32.dll",
        "comdlg32.dll",
        "crypt32.dll",
        "d3d12.dll",
        "d3dcompiler_47.dll",
        "dbghelp.dll",
        "dnsapi.dll",
        "dxgi.dll",
        "gdi32.dll",
        "imm32.dll",
        "imagehlp.dll",
        "iphlpapi.dll",
        "kernel32.dll",
        "mfplat.dll",
        "mfreadwrite.dll",
        "mpr.dll",
        "msvcrt.dll",
        "msi.dll",
        "netapi32.dll",
        "normaliz.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleaut32.dll",
        "pdh.dll",
        "powrprof.dll",
        "propsys.dll",
        "psapi.dll",
        "rpcrt4.dll",
        "secur32.dll",
        "setupapi.dll",
        "shell32.dll",
        "shlwapi.dll",
        "ucrtbase.dll",
        "user32.dll",
        "userenv.dll",
        "version.dll",
        "winhttp.dll",
        "wininet.dll",
        "winmm.dll",
        "wintrust.dll",
        "wldap32.dll",
        "ws2_32.dll",
    }
)
NVIDIA_DRIVER_PE_BOUNDARY = frozenset({"nvcuda.dll", "nvml.dll", "nvapi64.dll"})
APPROVED_HOST_INJECTED_OBSERVERS = {
    "audiodevprops2.dll": "SteelSeries France SASU",
}


def _pe_imports(path: Path) -> dict[str, Any]:
    """Parse regular and delay-load DLL names without executing a PE file."""

    data = path.read_bytes()
    try:
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise ValueError("missing DOS header")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise ValueError("missing PE signature")
        section_count = struct.unpack_from("<H", data, pe_offset + 6)[0]
        optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
        optional = pe_offset + 24
        if optional + optional_size > len(data):
            raise ValueError("truncated optional header")
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic == 0x10B:
            directory_offset = optional + 96
            directory_count_offset = optional + 92
            image_base = struct.unpack_from("<I", data, optional + 28)[0]
        elif magic == 0x20B:
            directory_offset = optional + 112
            directory_count_offset = optional + 108
            image_base = struct.unpack_from("<Q", data, optional + 24)[0]
        else:
            raise ValueError(f"unsupported optional-header magic {magic:#x}")
        directory_count = struct.unpack_from("<I", data, directory_count_offset)[0]
        size_of_headers = struct.unpack_from("<I", data, optional + 60)[0]
        section_table = optional + optional_size
        sections = []
        for index in range(section_count):
            offset = section_table + index * 40
            if offset + 40 > len(data):
                raise ValueError("truncated section table")
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
                "<IIII", data, offset + 8
            )
            sections.append((virtual_address, max(virtual_size, raw_size), raw_offset, raw_size))

        def rva_offset(rva: int) -> int:
            if rva < size_of_headers and rva < len(data):
                return rva
            for virtual_address, span, raw_offset, raw_size in sections:
                if virtual_address <= rva < virtual_address + span:
                    delta = rva - virtual_address
                    if delta >= raw_size or raw_offset + delta >= len(data):
                        break
                    return raw_offset + delta
            raise ValueError(f"RVA is not backed by file data: {rva:#x}")

        def c_string(rva: int) -> str:
            offset = rva_offset(rva)
            end = data.find(b"\0", offset, min(len(data), offset + 512))
            if end < 0:
                raise ValueError("unterminated import DLL name")
            return data[offset:end].decode("ascii").casefold()

        regular = set()
        if directory_count > 1 and directory_offset + 16 <= optional + optional_size:
            import_rva, import_size = struct.unpack_from("<II", data, directory_offset + 8)
            if import_rva:
                cursor = rva_offset(import_rva)
                for _index in range(min(max(import_size // 20 + 1, 1), 4096)):
                    if cursor + 20 > len(data):
                        raise ValueError("truncated import descriptor")
                    descriptor = struct.unpack_from("<IIIII", data, cursor)
                    if descriptor == (0, 0, 0, 0, 0):
                        break
                    regular.add(c_string(descriptor[3]))
                    cursor += 20
                else:
                    raise ValueError("unterminated import descriptor table")

        delayed = set()
        if directory_count > 13 and directory_offset + 14 * 8 <= optional + optional_size:
            delay_rva, delay_size = struct.unpack_from("<II", data, directory_offset + 13 * 8)
            if delay_rva:
                cursor = rva_offset(delay_rva)
                for _index in range(min(max(delay_size // 32 + 1, 1), 4096)):
                    if cursor + 32 > len(data):
                        raise ValueError("truncated delay-import descriptor")
                    descriptor = struct.unpack_from("<IIIIIIII", data, cursor)
                    if descriptor == (0, 0, 0, 0, 0, 0, 0, 0):
                        break
                    attributes, name_value = descriptor[0], descriptor[1]
                    name_rva = name_value if attributes & 1 else name_value - image_base
                    if name_rva <= 0:
                        raise ValueError("invalid delay-import DLL name address")
                    delayed.add(c_string(name_rva))
                    cursor += 32
                else:
                    raise ValueError("unterminated delay-import descriptor table")
        return {
            "imports": sorted(regular),
            "delay_imports": sorted(delayed),
        }
    except (UnicodeDecodeError, struct.error, ValueError) as exc:
        raise WheelhouseError("PE_PARSE_FAILED", f"cannot parse {path}: {exc}") from exc


def audit_portable_pe_dependencies(
    smoke_root: Path,
    *,
    generation_id: str | None = None,
) -> dict[str, Any]:
    runtime_root = smoke_root / "runtime"
    binaries = sorted(
        path
        for path in runtime_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".dll", ".exe", ".pyd"}
    )
    bundled_names: dict[str, list[str]] = {}
    for path in binaries:
        bundled_names.setdefault(path.name.casefold(), []).append(
            path.relative_to(smoke_root).as_posix()
        )
    entries = []
    unresolved = []
    windows_system = []
    nvidia_driver = []
    tbb_optional = []
    for path in binaries:
        relative = path.relative_to(smoke_root).as_posix()
        parsed = _pe_imports(path)
        dependencies = []
        for kind, names in (("import", parsed["imports"]), ("delay-import", parsed["delay_imports"])):
            for name in names:
                item = {"name": name, "kind": kind}
                if name in bundled_names:
                    item["boundary"] = "portable-bundled"
                    item["providers"] = sorted(bundled_names[name])
                elif name.startswith(("api-ms-win-", "ext-ms-win-")) or name in WINDOWS_SYSTEM_PE_BOUNDARY:
                    item["boundary"] = "windows-system"
                    windows_system.append({"binary": relative, "dependency": name, "kind": kind})
                elif name in NVIDIA_DRIVER_PE_BOUNDARY:
                    item["boundary"] = "nvidia-driver"
                    nvidia_driver.append({"binary": relative, "dependency": name, "kind": kind})
                elif (
                    name == "tbb12.dll"
                    and "/numba/np/ufunc/tbbpool" in "/" + relative.casefold()
                    and relative.casefold().endswith(".pyd")
                ):
                    item["boundary"] = "optional-not-shipped"
                    tbb_optional.append({"binary": relative, "dependency": name, "kind": kind})
                else:
                    item["boundary"] = "unresolved"
                    unresolved.append({"binary": relative, "dependency": name, "kind": kind})
                dependencies.append(item)
        entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "dependencies": dependencies,
            }
        )
    passed = not unresolved and len(tbb_optional) == 1
    report = {
        "schema": "mentorflow.qwen-runtime-portable-pe-dependencies.v2",
        "status": "passed" if passed else "failed",
        "profile": "12hz-only",
        **({"generation_id": generation_id} if generation_id is not None else {}),
        "scope": "all EXE/DLL/PYD files in the copied portable runtime tree",
        "binary_count": len(entries),
        "dependency_edge_count": sum(len(item["dependencies"]) for item in entries),
        "unresolved": unresolved,
        "windows_system_boundary": windows_system,
        "nvidia_driver_boundary": nvidia_driver,
        "tbb": {
            "status": "optional-not-shipped",
            "selected_numba_threading_layer": "workqueue",
            "edges": tbb_optional,
        },
        "binaries": entries,
    }
    report_path = smoke_root / "evidence" / "portable-pe-dependency-audit.json"
    _atomic_write(report_path, _json_bytes(report))
    if unresolved:
        raise WheelhouseError(
            "PORTABLE_PE_DEPENDENCY_UNRESOLVED",
            f"portable PE tree has unresolved imports: {unresolved[:20]}",
        )
    if len(tbb_optional) != 1:
        raise WheelhouseError(
            "PORTABLE_TBB_BOUNDARY_INVALID",
            f"expected exactly one optional tbbpool->tbb12.dll edge, got {tbb_optional}",
        )
    return {**report, "path": str(report_path), "sha256": _sha256_file(report_path)}


def _authenticode_signature(path: Path) -> dict[str, Any]:
    pwsh = shutil.which("pwsh.exe")
    if not pwsh:
        raise WheelhouseError(
            "AUTHENTICODE_TOOL_MISSING",
            "PowerShell 7 is required to verify an injected host module signature",
        )
    target_b64 = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {{ throw 'PowerShell 7 required' }}
$target = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{target_b64}'))
$signature = Get-AuthenticodeSignature -LiteralPath $target
[pscustomobject]@{{
  ps_version = $PSVersionTable.PSVersion.ToString()
  status = [string]$signature.Status
  status_message = $signature.StatusMessage
  publisher_subject = $signature.SignerCertificate.Subject
  issuer = $signature.SignerCertificate.Issuer
  certificate_thumbprint = $signature.SignerCertificate.Thumbprint
  certificate_not_after_utc = if ($signature.SignerCertificate) {{
    $signature.SignerCertificate.NotAfter.ToUniversalTime().ToString('o')
  }} else {{ $null }}
  is_os_binary = [bool]$signature.IsOSBinary
}} | ConvertTo-Json -Depth 4 -Compress
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=60,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise WheelhouseError(
            "AUTHENTICODE_CHECK_FAILED",
            f"Authenticode verification failed for {path}: {completed.stderr[-1000:]}",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WheelhouseError(
            "AUTHENTICODE_CHECK_FAILED",
            f"Authenticode result was not JSON for {path}",
        ) from exc
    if not isinstance(payload, dict):
        raise WheelhouseError(
            "AUTHENTICODE_CHECK_FAILED",
            f"Authenticode result was not an object for {path}",
        )
    return payload


def refresh_portable_python_authenticode_receipt(
    python_material_root: Path,
) -> dict[str, Any]:
    """Bind every shipped PE/CAT to a live valid Authenticode observation.

    Only SOURCE_RECEIPT.json and BUILDER_COMPONENT.fragment.json are rewritten;
    official/builder runtime bytes remain untouched.
    """

    from scripts import build_qwen3_tts_runtime_pack as builder

    material_root = python_material_root.resolve(strict=True)
    source_root = material_root / "builder-input"
    receipt_path = material_root / "SOURCE_RECEIPT.json"
    component_path = material_root / "BUILDER_COMPONENT.fragment.json"
    if not source_root.is_dir() or not receipt_path.is_file() or not component_path.is_file():
        raise WheelhouseError(
            "PORTABLE_PYTHON_MATERIAL_MISSING",
            f"portable Python material is incomplete: {material_root}",
        )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    component = json.loads(component_path.read_text(encoding="utf-8"))
    expected_receipt_fields = {
        "schema",
        "component",
        "selection",
        "upstream",
        "archive",
        "verification",
        "extraction",
        "builder_derivative",
    }
    if frozenset(receipt) not in {
        frozenset(expected_receipt_fields),
        frozenset(expected_receipt_fields | {"authenticode"}),
    }:
        raise WheelhouseError(
            "PORTABLE_PYTHON_RECEIPT_INVALID",
            "portable Python source receipt fields are not the known v1/v2 contract",
        )
    if receipt.get("schema") not in {
        "mentorflow.python-org-embeddable-source-receipt.v1",
        builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
    }:
        raise WheelhouseError("PORTABLE_PYTHON_RECEIPT_INVALID", "unexpected portable Python source receipt schema")
    include = component.get("include")
    if not isinstance(include, list) or not include:
        raise WheelhouseError("PORTABLE_PYTHON_COMPONENT_INVALID", "portable component include set is invalid")
    expected_tree = component.get("expected_tree_sha256")
    actual_tree = builder.calculate_allowlist_tree_sha256(source_root, include)
    if actual_tree != expected_tree or actual_tree != builder.TRUSTED_PORTABLE_PYTHON_TREE_SHA256:
        raise WheelhouseError(
            "PORTABLE_PYTHON_TREE_MISMATCH",
            "portable Python runtime bytes changed before Authenticode receipt generation",
        )
    signed_paths = sorted(
        path
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".exe", ".dll", ".pyd", ".cat"}
    )
    rows = []
    for path in signed_paths:
        signature = _authenticode_signature(path)
        subject = str(signature.get("publisher_subject") or "")
        publisher = next(
            (
                candidate
                for candidate in sorted(builder.TRUSTED_PORTABLE_PYTHON_PUBLISHERS)
                if f"O={candidate}" in subject
            ),
            "",
        )
        thumbprint = str(signature.get("certificate_thumbprint") or "").upper()
        if (
            signature.get("status") != "Valid"
            or not publisher
            or not re.fullmatch(r"[0-9A-F]{40}", thumbprint)
        ):
            raise WheelhouseError(
                "PORTABLE_PYTHON_AUTHENTICODE_INVALID",
                f"portable Python file is unsigned or has an untrusted publisher: {path}",
            )
        rows.append(
            {
                "path": path.relative_to(source_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "status": "Valid",
                "publisher": publisher,
                "publisher_subject": subject,
                "certificate_thumbprint": thumbprint,
            }
        )
    receipt["schema"] = builder.PORTABLE_PYTHON_RECEIPT_SCHEMA
    receipt["authenticode"] = {
        "schema": builder.PORTABLE_PYTHON_AUTHENTICODE_SCHEMA,
        "status": "passed",
        "verifier": "PowerShell 7 Get-AuthenticodeSignature",
        "files": rows,
    }
    _atomic_write(receipt_path, _json_bytes(receipt))
    component_receipt = component.get("receipt")
    if not isinstance(component_receipt, dict) or component_receipt.get("path") != "SOURCE_RECEIPT.json":
        raise WheelhouseError(
            "PORTABLE_PYTHON_COMPONENT_INVALID",
            "portable component receipt path is not SOURCE_RECEIPT.json",
        )
    component["receipt"] = {
        "path": "SOURCE_RECEIPT.json",
        "bytes": receipt_path.stat().st_size,
        "sha256": _sha256_file(receipt_path),
    }
    _atomic_write(component_path, _json_bytes(component))
    rewritten = json.loads(component_path.read_text(encoding="utf-8"))
    rewritten["root"] = str(source_root)
    rewritten["license_files"] = [
        {
            **item,
            "path": str(material_root / str(item["path"])),
        }
        for item in rewritten["license_files"]
    ]
    rewritten["receipt"] = {
        **rewritten["receipt"],
        "path": str(receipt_path),
    }
    try:
        plan = builder._plan_component(material_root, rewritten)
        builder._validate_portable_python_component(plan)
    except builder.BuildError as exc:
        raise WheelhouseError(exc.code, str(exc)) from exc
    return {
        "schema": "mentorflow.portable-python-authenticode-refresh.v1",
        "status": "passed",
        "runtime_bytes_modified": False,
        "signed_file_count": len(rows),
        "tree_sha256": actual_tree,
        "source_receipt": _json_evidence_pin(
            receipt_path,
            schema=builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
        ),
        "component_fragment": {
            "path": str(component_path),
            "bytes": component_path.stat().st_size,
            "sha256": _sha256_file(component_path),
        },
    }


def _material_relative_pin(
    material_root: Path,
    path: Path,
    *,
    schema: str | None = None,
) -> dict[str, Any]:
    path = path.resolve(strict=True)
    try:
        relative = path.relative_to(material_root).as_posix()
    except ValueError as exc:
        raise WheelhouseError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            f"material escaped portable Python root: {path}",
        ) from exc
    value: dict[str, Any] = {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }
    if schema is not None:
        value["schema"] = schema
    return value


def _portable_material_tree_records(
    root: Path,
    *,
    builder: Any,
) -> list[dict[str, Any]]:
    records = [
        {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path, relative in builder._walk_allowlist_entry(root, ".")
    ]
    records.sort(
        key=lambda item: (
            builder.windows_casefold_key(item["path"]),
            item["path"],
        )
    )
    return records


def materialize_portable_python_v2_materials(
    python_material_root: Path,
) -> dict[str, Any]:
    """Add a non-cyclic v2 inventory and component fragment without rewriting v1."""

    from scripts import build_qwen3_tts_runtime_pack as builder

    material_root = python_material_root.resolve(strict=True)
    if material_root.drive.casefold() != "e:":
        raise WheelhouseError(
            "PORTABLE_PYTHON_DRIVE_INVALID",
            f"portable Python material must be on E:, got {material_root}",
        )
    legacy_inventory_path = material_root / "MATERIAL_INVENTORY.json"
    previous_component_path = material_root / "BUILDER_COMPONENT.fragment.json"
    source_receipt_path = material_root / "SOURCE_RECEIPT.json"
    for path in (
        legacy_inventory_path,
        previous_component_path,
        source_receipt_path,
        material_root / "runtime",
        material_root / "builder-input",
        material_root / "receipts",
        material_root / "PSF-LICENSE.txt",
        material_root / "NOTICE.txt",
    ):
        if not path.exists():
            raise WheelhouseError(
                "PORTABLE_PYTHON_MATERIAL_MISSING",
                f"portable Python material is incomplete: {path}",
            )
    previous_component = json.loads(
        previous_component_path.read_text(encoding="utf-8")
    )
    source_receipt = json.loads(source_receipt_path.read_text(encoding="utf-8"))
    if (
        source_receipt.get("schema") != builder.PORTABLE_PYTHON_RECEIPT_SCHEMA
        or previous_component.get("id") != "python-3.12.10-embed-amd64"
        or "material_inventory" in previous_component
        or "schema" in previous_component
    ):
        raise WheelhouseError(
            "PORTABLE_PYTHON_INVENTORY_INVALID",
            "legacy portable Python materials are not the exact expected inputs",
        )
    tree_roots = {
        "official_runtime": "runtime",
        "builder_input": "builder-input",
        "source_artifacts": "receipts",
    }
    trees = {}
    for field, relative_root in tree_roots.items():
        records = _portable_material_tree_records(
            material_root / relative_root,
            builder=builder,
        )
        digest = hashlib.sha256()
        for row in sorted(records, key=lambda item: item["path"]):
            digest.update(
                f"{row['path']}\0{row['bytes']}\0{row['sha256']}\n".encode("utf-8")
            )
        trees[field] = {
            "root": relative_root,
            "file_count": len(records),
            "total_bytes": sum(row["bytes"] for row in records),
            "tree_sha256": digest.hexdigest(),
            "files": records,
        }
    source_pin = _material_relative_pin(
        material_root,
        source_receipt_path,
        schema=builder.PORTABLE_PYTHON_RECEIPT_SCHEMA,
    )
    receipt_declaration = previous_component.get("receipt")
    if not isinstance(receipt_declaration, dict) or any(
        receipt_declaration.get(field) != source_pin[field]
        for field in ("path", "bytes", "sha256")
    ):
        raise WheelhouseError(
            "PORTABLE_PYTHON_INVENTORY_MISMATCH",
            "previous component does not bind the current source receipt",
        )
    inventory = {
        "schema": builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
        "component": {
            "id": "python-3.12.10-embed-amd64",
            "version": builder.TRUSTED_PORTABLE_PYTHON_VERSION,
            "distribution": "python.org-embeddable",
            "platform": "win-x64",
            "abi": "cp312",
        },
        "trees": trees,
        "evidence": {
            "source_receipt": source_pin,
            "psf_license": _material_relative_pin(
                material_root,
                material_root / "PSF-LICENSE.txt",
            ),
            "notice": _material_relative_pin(
                material_root,
                material_root / "NOTICE.txt",
            ),
        },
    }
    inventory_path = material_root / "MATERIAL_INVENTORY.v2.json"
    _atomic_write(inventory_path, _json_bytes(inventory))
    component = dict(previous_component)
    component["schema"] = builder.PORTABLE_PYTHON_COMPONENT_SCHEMA
    component["material_inventory"] = _material_relative_pin(
        material_root,
        inventory_path,
        schema=builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
    )
    component_path = material_root / "BUILDER_COMPONENT.v2.fragment.json"
    _atomic_write(component_path, _json_bytes(component))

    rewritten = dict(component)
    rewritten["root"] = str(material_root / "builder-input")
    rewritten["license_files"] = [
        {**row, "path": str(material_root / str(row["path"]))}
        for row in component["license_files"]
    ]
    rewritten["receipt"] = {
        **component["receipt"],
        "path": str(source_receipt_path),
    }
    rewritten["material_inventory"] = {
        **component["material_inventory"],
        "path": str(inventory_path),
    }
    try:
        plan = builder._plan_component(material_root, rewritten)
        builder._validate_portable_python_component(
            plan,
            spec_dir=material_root,
            require_material_inventory=True,
        )
    except builder.BuildError as exc:
        raise WheelhouseError(exc.code, str(exc)) from exc
    return {
        "schema": "mentorflow.portable-python-v2-materialization.v1",
        "status": "passed",
        "runtime_bytes_modified": False,
        "legacy_files_modified": False,
        "legacy_inventory_sha256": _sha256_file(legacy_inventory_path),
        "legacy_component_fragment_sha256": _sha256_file(previous_component_path),
        "tree_sha256": plan.tree_sha256,
        "material_inventory": _json_evidence_pin(
            inventory_path,
            schema=builder.PORTABLE_PYTHON_MATERIAL_INVENTORY_SCHEMA,
        ),
        "component_fragment": {
            "path": str(component_path),
            "bytes": component_path.stat().st_size,
            "sha256": _sha256_file(component_path),
        },
        "source_receipt": source_pin,
    }


def _host_injected_observer_record(
    raw_path: str,
    *,
    pe_declared_import_names: set[str],
) -> dict[str, Any]:
    path = Path(raw_path).resolve(strict=True)
    basename = path.name.casefold()
    declared_edge = basename in pe_declared_import_names
    if declared_edge:
        raise WheelhouseError(
            "HOST_EXTERNAL_NATIVE_DEPENDENCY",
            f"external host module is named by a portable PE import edge: {path}",
        )
    signature = _authenticode_signature(path)
    expected_publisher = APPROVED_HOST_INJECTED_OBSERVERS.get(basename)
    subject = str(signature.get("publisher_subject") or "")
    if (
        expected_publisher is None
        or signature.get("status") != "Valid"
        or f"O={expected_publisher}" not in subject
    ):
        raise WheelhouseError(
            "HOST_INJECTED_MODULE_UNAPPROVED",
            f"unknown, unsigned, or unexpected-publisher host module was injected: {path}",
        )
    return {
        "absolute_path": str(path),
        "basename": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "first_appearance_stage": "production-runner-and-qwen-product-entry",
        "present_before_product_entry": False,
        "present_after_product_entry": True,
        "pe_import_or_delay_import_edge": False,
        "runtime_prerequisite": False,
        "classification": "signed-host-injected-observer-not-portable-dependency",
        "approved_publisher": expected_publisher,
        "authenticode": signature,
    }


def _finalize_v2_builder_validation(
    core_validation_path: Path,
    *,
    final_validation_path: Path,
    portable_receipt_path: Path,
    pe_receipt_path: Path,
) -> dict[str, Any]:
    core = json.loads(core_validation_path.read_text(encoding="utf-8"))
    expected_core = {
        "schema",
        "status",
        "profile",
        "fragment",
        "dependency_lock",
        "inventory",
        "resolution_plan",
        "package_count",
        "selected_files",
        "selected_bytes",
        "license_files",
        "wheel_license_files",
        "external_license_supplement_files",
        "license_evidence",
        "excluded_test_files",
        "excluded_test_bytes",
        "runtime_required_testlike_members",
        "runtime_import_trace",
        "source_overlay",
        "site_packages",
        "removed_packages",
        "numba_threading_layer",
        "tbb",
    }
    generation_id = core.get("generation_id")
    if generation_id is not None:
        expected_core.add("generation_id")
    if (
        set(core) != expected_core
        or core.get("schema")
        != "mentorflow.qwen-runtime-builder-dependency-validation.v2"
        or core.get("profile") != "12hz-only"
        or core.get("status") != "passed"
    ):
        raise WheelhouseError(
            "BUILDER_INPUTS_INVALID",
            "cannot finalize a skeletal or non-v2 builder validation",
        )
    core_sha256 = _sha256_bytes(_json_bytes(core))
    portable = json.loads(portable_receipt_path.read_text(encoding="utf-8"))
    pe = json.loads(pe_receipt_path.read_text(encoding="utf-8"))
    if (
        portable.get("schema")
        != "mentorflow.qwen-runtime-portable-import-smoke.v2"
        or portable.get("status") != "passed"
        or portable.get("profile") != "12hz-only"
        or portable.get("generation_id") != generation_id
        or Path(
            str(portable.get("inputs", {}).get("builder_validation", {}).get("path") or "")
        ).absolute()
        != core_validation_path.absolute()
        or portable.get("inputs", {}).get("builder_validation", {}).get("sha256")
        != core_sha256
        or pe.get("schema")
        != "mentorflow.qwen-runtime-portable-pe-dependencies.v2"
        or pe.get("status") != "passed"
        or pe.get("profile") != "12hz-only"
        or pe.get("generation_id") != generation_id
        or portable.get("pe_dependency_audit", {}).get("sha256")
        != _sha256_file(pe_receipt_path)
    ):
        raise WheelhouseError(
            "PORTABLE_VALIDATION_FINALIZE_INVALID",
            "portable product-entry/PE receipts do not bind the exact validation core",
        )
    final = {
        **core,
        "pre_product_entry_validation_sha256": core_sha256,
        "pe_dependency_audit": _json_evidence_pin(
            pe_receipt_path,
            schema="mentorflow.qwen-runtime-portable-pe-dependencies.v2",
            status="passed",
            profile="12hz-only",
        ),
        "portable_product_entry_receipt": _json_evidence_pin(
            portable_receipt_path,
            schema="mentorflow.qwen-runtime-portable-import-smoke.v2",
            status="passed",
            profile="12hz-only",
        ),
    }
    _atomic_write(final_validation_path, _json_bytes(final))
    return final


def run_portable_import_smoke(
    root: Path,
    python_material_root: Path,
    model_root: Path,
    *,
    strict_12hz_v2: bool = False,
    generation_id: str | None = None,
    portable_smoke_root: Path | None = None,
) -> dict[str, Any]:
    """Exercise the exact runtime closure with a fresh E:-only portable Python tree.

    The probe imports the production dependency surface and runner, but guards
    every Python socket connection/listener and subprocess entrypoint.  It does
    not load model material, call a CUDA API, synthesize audio, or start a
    service.  Wheel members are copied rather than linked so the smoke tree is
    an independent approximation of the final portable pack layout.
    """

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts import build_qwen3_tts_runtime_pack as builder

    model_root = model_root.resolve(strict=True)
    if model_root.drive.casefold() != "e:":
        raise WheelhouseError("QWEN_MODEL_DRIVE_INVALID", f"Qwen model must be on E:, got {model_root}")
    model_identity_sources = {
        "config.json": model_root / "config.json",
        "speech_tokenizer/config.json": model_root / "speech_tokenizer" / "config.json",
    }
    for relative, source in model_identity_sources.items():
        if not source.is_file():
            raise WheelhouseError("QWEN_MODEL_CONFIG_MISSING", f"model identity file is missing: {source}")

    python_material_root = python_material_root.resolve(strict=True)
    if python_material_root.drive.casefold() != "e:":
        raise WheelhouseError(
            "PORTABLE_PYTHON_DRIVE_INVALID",
            f"portable Python material must be on E:, got {python_material_root}",
        )
    python_source = python_material_root / "builder-input"
    source_receipt_path = python_material_root / "SOURCE_RECEIPT.json"
    component_fragment_path = python_material_root / (
        "BUILDER_COMPONENT.v2.fragment.json"
        if strict_12hz_v2
        else "BUILDER_COMPONENT.fragment.json"
    )
    for required in (python_source, source_receipt_path, component_fragment_path):
        if not required.exists():
            raise WheelhouseError(
                "PORTABLE_PYTHON_MATERIAL_MISSING", f"required material is missing: {required}"
            )
    source_receipt = json.loads(source_receipt_path.read_text(encoding="utf-8"))
    python_component = json.loads(component_fragment_path.read_text(encoding="utf-8"))
    if source_receipt.get("schema") != builder.PORTABLE_PYTHON_RECEIPT_SCHEMA:
        raise WheelhouseError("PORTABLE_PYTHON_RECEIPT_INVALID", "unexpected source receipt schema")
    if (
        python_component.get("kind") != "portable_python"
        or python_component.get("distribution") != "python.org-embeddable"
        or python_component.get("version") != "3.12.10"
    ):
        raise WheelhouseError(
            "PORTABLE_PYTHON_COMPONENT_INVALID", "portable Python component identity mismatch"
        )
    include = python_component.get("include")
    if not isinstance(include, list) or not include or any(not isinstance(item, str) for item in include):
        raise WheelhouseError("PORTABLE_PYTHON_COMPONENT_INVALID", "include allowlist is invalid")
    expected_python_tree = source_receipt.get("builder_derivative", {}).get(
        "expected_tree_sha256"
    )
    if expected_python_tree != python_component.get("expected_tree_sha256"):
        raise WheelhouseError(
            "PORTABLE_PYTHON_TREE_MISMATCH", "source receipt and component tree disagree"
        )
    actual_python_tree = builder.calculate_allowlist_tree_sha256(python_source, include)
    if actual_python_tree != expected_python_tree:
        raise WheelhouseError(
            "PORTABLE_PYTHON_TREE_MISMATCH",
            f"portable Python material changed: {actual_python_tree}",
        )
    expected_python_files = {
        path.relative_to(python_source).as_posix()
        for item in include
        for path in ([python_source / item] if (python_source / item).is_file() else (python_source / item).rglob("*"))
        if path.is_file()
    }
    actual_python_files = {
        path.relative_to(python_source).as_posix()
        for path in python_source.rglob("*")
        if path.is_file()
    }
    if actual_python_files != expected_python_files:
        raise WheelhouseError(
            "PORTABLE_PYTHON_FILE_SET_MISMATCH",
            f"extra={sorted(actual_python_files - expected_python_files)}, "
            f"missing={sorted(expected_python_files - actual_python_files)}",
        )

    if strict_12hz_v2:
        inventory_declaration = python_component.get("material_inventory")
        if not isinstance(inventory_declaration, dict):
            raise WheelhouseError(
                "PORTABLE_PYTHON_INVENTORY_INVALID",
                "strict portable smoke requires the v2 CPython material inventory",
            )
        formal_component = dict(python_component)
        formal_component["root"] = str(python_source)
        formal_component["license_files"] = [
            {**item, "path": str(python_material_root / str(item["path"]))}
            for item in python_component["license_files"]
        ]
        formal_component["receipt"] = {
            **python_component["receipt"],
            "path": str(source_receipt_path),
        }
        formal_component["material_inventory"] = {
            **inventory_declaration,
            "path": str(python_material_root / str(inventory_declaration["path"])),
        }
        try:
            formal_plan = builder._plan_component(
                python_material_root,
                formal_component,
            )
            builder._validate_portable_python_component(
                formal_plan,
                spec_dir=python_material_root,
                require_material_inventory=True,
            )
        except builder.BuildError as exc:
            raise WheelhouseError(exc.code, str(exc)) from exc

    generation_id = _validated_generation_id(generation_id)
    profile_root = (
        _strict_12hz_profile_root(root, generation_id) if strict_12hz_v2 else root
    )
    builder_inputs = (
        profile_root / "builder-inputs"
        if strict_12hz_v2
        else root / "builder-inputs-v1"
    )
    staged_site_packages = builder_inputs / "site-packages"
    core_validation_path = (
        profile_root / "evidence" / "builder-dependency-validation.core.json"
        if strict_12hz_v2
        else builder_inputs / "builder-dependency-validation.json"
    )
    final_validation_path = (
        profile_root / "evidence" / "builder-dependency-validation.json"
        if strict_12hz_v2
        else builder_inputs / "builder-dependency-validation.json"
    )
    fragment_path = builder_inputs / "builder-dependency-fragment.json"
    lock_path = profile_root / "builder-dependency-lock.json"
    inventory_path = profile_root / "THIRD_PARTY_BUILDER_INVENTORY.json"
    resolution_path = profile_root / "resolution-plan.json"
    if (
        not staged_site_packages.is_dir()
        or not core_validation_path.is_file()
        or not fragment_path.is_file()
        or not lock_path.is_file()
        or not inventory_path.is_file()
        or not resolution_path.is_file()
    ):
        raise WheelhouseError(
            "BUILDER_INPUTS_MISSING", "run --materialize-builder-inputs before portable smoke"
        )
    validation = json.loads(core_validation_path.read_text(encoding="utf-8"))
    validation_core = dict(validation)
    validation_core_sha256 = _sha256_bytes(_json_bytes(validation_core))
    if any(
        field in validation
        for field in {
            "pre_product_entry_validation_sha256",
            "pe_dependency_audit",
            "portable_product_entry_receipt",
        }
    ):
        raise WheelhouseError(
            "BUILDER_INPUTS_INVALID",
            "portable smoke requires a distinct immutable pre-product-entry validation",
        )
    dependency_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected_package_count = dependency_lock.get("package_count")
    v2_valid = (
        strict_12hz_v2
        and validation.get("schema")
        == "mentorflow.qwen-runtime-builder-dependency-validation.v2"
        and validation.get("profile") == "12hz-only"
        and expected_package_count == 48
        and validation.get("package_count") == 48
        and validation.get("removed_packages")
        == ["flatbuffers", "onnxruntime", "protobuf", "sox"]
    )
    v1_valid = (
        not strict_12hz_v2
        and expected_package_count == 52
        and validation.get("package_count") == 52
    )
    if validation.get("status") != "passed" or not (v2_valid or v1_valid):
        raise WheelhouseError(
            "BUILDER_INPUTS_INVALID",
            "portable smoke requires the matching validated dependency closure",
        )

    audit_receipts_root = (
        profile_root / "audit-receipts" if strict_12hz_v2 else root / "receipts"
    )
    audit_receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(audit_receipts_root.glob("*.json"))
    ]
    expected_members: dict[str, dict[str, Any]] = {}
    members_by_package: list[tuple[str, list[dict[str, Any]]]] = []
    for audit in audit_receipts:
        selected = [
            member for member in audit["members"] if not _is_runtime_test_member(member["path"])
        ]
        members_by_package.append((audit["name"], selected))
        for member in selected:
            relative = member["path"]
            if relative in expected_members:
                raise WheelhouseError(
                    "PORTABLE_SMOKE_MEMBER_COLLISION", f"duplicate runtime path: {relative}"
                )
            expected_members[relative] = member
    if len(expected_members) != validation.get("selected_files"):
        raise WheelhouseError(
            "PORTABLE_SMOKE_MEMBER_COUNT_MISMATCH",
            f"receipts={len(expected_members)}, validation={validation.get('selected_files')}",
        )

    configured_smoke_root = portable_smoke_root
    if configured_smoke_root is None:
        environment_smoke_root = os.environ.get("MENTORFLOW_PORTABLE_SMOKE_ROOT", "").strip()
        configured_smoke_root = Path(environment_smoke_root) if environment_smoke_root else None
    if strict_12hz_v2 and configured_smoke_root is None:
        raise WheelhouseError(
            "PORTABLE_SMOKE_ROOT_REQUIRED",
            "strict portable smoke requires --portable-smoke-root or MENTORFLOW_PORTABLE_SMOKE_ROOT",
        )
    smoke_root = Path(os.path.abspath(configured_smoke_root)) if configured_smoke_root else (
        profile_root / "portable-import-smoke"
        if strict_12hz_v2
        else root / "portable-import-smoke-v1"
    )
    if strict_12hz_v2:
        if os.name == "nt" and smoke_root.drive.casefold() != "e:":
            raise WheelhouseError(
                "PORTABLE_SMOKE_DRIVE_INVALID", f"portable smoke root must be on E:, got {smoke_root}"
            )
        if os.path.lexists(smoke_root):
            raise WheelhouseError(
                "PORTABLE_SMOKE_ROOT_EXISTS",
                f"portable smoke roots are immutable and must be unique: {smoke_root}",
            )
    elif not smoke_root.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise WheelhouseError(
            "PORTABLE_SMOKE_ROOT_INVALID", f"smoke root escapes wheelhouse: {smoke_root}"
        )
    runtime_root = smoke_root / "runtime"
    python_target = runtime_root / "python"
    site_packages_target = python_target / "Lib" / "site-packages"
    runner_source = project_root / "backend" / "runtime" / "qwen_base_clone_server.py"
    runner_target = runtime_root / "runner" / "qwen_base_clone_server.py"
    evidence_root = smoke_root / "evidence"
    overlay_source = builder_inputs / "receipts" / "qwen-12hz-source-overlay.json"
    overlay_target = smoke_root / "metadata" / "source-overlays" / "qwen-12hz-only.json"
    runtime_lock_target = smoke_root / "metadata" / "dependency-lock.json"
    if not overlay_source.is_file():
        raise WheelhouseError(
            "SOURCE_OVERLAY_CONTRACT_MISSING",
            "run --materialize-builder-inputs with the current runner before portable smoke",
        )
    install_relative_paths = tuple(
        sorted(
            {
                *(f"runtime/python/{relative}" for relative in expected_python_files),
                *(
                    f"runtime/python/Lib/site-packages/{relative}"
                    for relative in expected_members
                ),
                "runtime/runner/qwen_base_clone_server.py",
                "metadata/source-overlays/qwen-12hz-only.json",
                "metadata/dependency-lock.json",
                "evidence/builder-dependency-lock.json",
                *(
                    f"runtime/models/qwen3-tts-12hz-0.6b-base/{relative}"
                    for relative in model_identity_sources
                ),
            }
        )
    )
    path_budget = builder.calculate_install_path_budget(install_relative_paths)
    try:
        path_budget_acceptance = builder.validate_install_root_budget(
            smoke_root,
            path_budget,
            relative_paths=install_relative_paths,
        )
    except builder.BuildError as exc:
        raise WheelhouseError(exc.code, str(exc)) from exc
    required_copy_bytes = (
        sum(path.stat().st_size for path in python_source.rglob("*") if path.is_file())
        + sum(item["bytes"] for item in expected_members.values())
        + runner_source.stat().st_size
        + overlay_source.stat().st_size
        + lock_path.stat().st_size
        + sum(path.stat().st_size for path in model_identity_sources.values())
    )
    free_bytes_before = shutil.disk_usage(smoke_root.anchor).free
    if not smoke_root.exists() and free_bytes_before < required_copy_bytes + 1024 * 1024 * 1024:
        raise WheelhouseError(
            "PORTABLE_SMOKE_DISK_SPACE_INSUFFICIENT",
            f"target={smoke_root}; free={free_bytes_before}; "
            f"copy plus headroom={required_copy_bytes + 1024 * 1024 * 1024}",
        )
    print(
        f"PORTABLE_SMOKE_SPACE_OK target={smoke_root} free={free_bytes_before} "
        f"required_copy={required_copy_bytes}",
        flush=True,
    )
    try:
        assert_no_reparse_ancestors(smoke_root.parent, Path(smoke_root.anchor))
        smoke_root.mkdir(parents=True, exist_ok=not strict_12hz_v2)
        assert_no_reparse_ancestors(smoke_root, Path(smoke_root.anchor))
    except (OSError, PlainFileCopyError) as exc:
        code = exc.code if isinstance(exc, PlainFileCopyError) else "PORTABLE_SMOKE_ROOT_CREATE_FAILED"
        raise WheelhouseError(code, str(exc)) from exc
    python_target.mkdir(parents=True, exist_ok=True)
    site_packages_target.mkdir(parents=True, exist_ok=True)
    evidence_root.mkdir(parents=True, exist_ok=True)

    def copy_smoke_file(
        source: Path,
        destination: Path,
        *,
        expected_bytes: int,
        expected_sha256: str,
    ) -> None:
        if destination.is_file():
            if (
                destination.stat().st_size == expected_bytes
                and _sha256_file(destination) == expected_sha256
            ):
                return
            raise WheelhouseError(
                "PORTABLE_SMOKE_COPY_MISMATCH", f"existing smoke target differs: {destination}"
            )
        try:
            copy_plain_file_exact(
                source,
                destination,
                expected_bytes=expected_bytes,
                expected_sha256=expected_sha256,
                source_root=Path(source.anchor),
                destination_root=smoke_root,
            )
        except PlainFileCopyError as exc:
            raise WheelhouseError(exc.code, str(exc)) from exc

    for relative in sorted(expected_python_files):
        source = python_source.joinpath(*PurePosixPath(relative).parts)
        destination = python_target.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file():
            copy_smoke_file(
                source,
                destination,
                expected_bytes=source.stat().st_size,
                expected_sha256=_sha256_file(source),
            )
        if destination.stat().st_size != source.stat().st_size or _sha256_file(destination) != _sha256_file(source):
            raise WheelhouseError(
                "PORTABLE_PYTHON_COPY_MISMATCH", f"portable Python copy differs: {relative}"
            )
    copied_python_files = {
        path.relative_to(python_target).as_posix()
        for path in python_target.rglob("*")
        if path.is_file() and not path.is_relative_to(site_packages_target)
    }
    if copied_python_files != expected_python_files:
        raise WheelhouseError(
            "PORTABLE_PYTHON_COPY_FILE_SET_MISMATCH",
            f"extra={sorted(copied_python_files - expected_python_files)}, "
            f"missing={sorted(expected_python_files - copied_python_files)}",
        )

    for package_index, (package_name, members) in enumerate(members_by_package, start=1):
        for member in members:
            relative = member["path"]
            source = staged_site_packages.joinpath(*PurePosixPath(relative).parts)
            destination = site_packages_target.joinpath(*PurePosixPath(relative).parts)
            if not source.is_file() or source.stat().st_size != member["bytes"]:
                raise WheelhouseError(
                    "PORTABLE_SMOKE_SOURCE_MISMATCH", f"staged dependency changed: {relative}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file():
                copy_smoke_file(
                    source,
                    destination,
                    expected_bytes=member["bytes"],
                    expected_sha256=member["sha256"],
                )
            if (
                destination.stat().st_size != member["bytes"]
                or _sha256_file(destination) != member["sha256"]
            ):
                raise WheelhouseError(
                    "PORTABLE_SMOKE_COPY_MISMATCH", f"copied dependency differs: {relative}"
                )
        print(
            f"PORTABLE_SMOKE_COMPONENT_COPIED {package_index}/{len(members_by_package)} "
            f"{package_name} files={len(members)}",
            flush=True,
        )
    copied_dependency_files = {
        path.relative_to(site_packages_target).as_posix()
        for path in site_packages_target.rglob("*")
        if path.is_file()
    }
    if copied_dependency_files != set(expected_members):
        raise WheelhouseError(
            "PORTABLE_SMOKE_COPY_FILE_SET_MISMATCH",
            f"extra={sorted(copied_dependency_files - set(expected_members))[:20]}, "
            f"missing={sorted(set(expected_members) - copied_dependency_files)[:20]}",
        )

    runner_bytes = runner_source.read_bytes()
    copy_smoke_file(
        runner_source,
        runner_target,
        expected_bytes=len(runner_bytes),
        expected_sha256=_sha256_bytes(runner_bytes),
    )
    copy_smoke_file(
        overlay_source,
        overlay_target,
        expected_bytes=overlay_source.stat().st_size,
        expected_sha256=_sha256_file(overlay_source),
    )
    copy_smoke_file(
        lock_path,
        runtime_lock_target,
        expected_bytes=lock_path.stat().st_size,
        expected_sha256=_sha256_file(lock_path),
    )
    copied_model_identity = {}
    for relative, source in model_identity_sources.items():
        destination = smoke_root.joinpath(
            *PurePosixPath("runtime/models/qwen3-tts-12hz-0.6b-base/" + relative).parts
        )
        copy_smoke_file(
            source,
            destination,
            expected_bytes=source.stat().st_size,
            expected_sha256=_sha256_file(source),
        )
        copied_model_identity[relative] = {
            "source_sha256": _sha256_file(source),
            "copied_sha256": _sha256_file(destination),
        }
    expected_lock_target = evidence_root / "builder-dependency-lock.json"
    copy_smoke_file(
        lock_path,
        expected_lock_target,
        expected_bytes=lock_path.stat().st_size,
        expected_sha256=_sha256_file(lock_path),
    )
    probe_path = evidence_root / "portable_import_probe.py"
    probe_source = _portable_probe_source_v2() if strict_12hz_v2 else r'''from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


events = {"network": [], "listener": [], "subprocess": [], "model_load": []}


def _deny_network(self, address):
    events["network"].append(repr(address))
    raise RuntimeError("network disabled by portable import smoke")


def _deny_create_connection(address, *args, **kwargs):
    events["network"].append(repr(address))
    raise RuntimeError("network disabled by portable import smoke")


def _deny_listen(self, backlog=0):
    events["listener"].append(f"listen:{backlog}")
    raise RuntimeError("listeners disabled by portable import smoke")


def _deny_process(*args, **kwargs):
    events["subprocess"].append(repr(args[0] if args else kwargs.get("args")))
    raise RuntimeError("subprocess disabled by portable import smoke")


_original_popen = subprocess.Popen


class _GuardedPopen(_original_popen):
    def __init__(self, *args, **kwargs):
        events["subprocess"].append(repr(args[0] if args else kwargs.get("args")))
        raise RuntimeError("subprocess disabled by portable import smoke")


socket.socket.connect = _deny_network
socket.socket.connect_ex = _deny_network
socket.create_connection = _deny_create_connection
socket.socket.listen = _deny_listen
subprocess.Popen = _GuardedPopen
os.system = _deny_process

lock_path = Path(sys.argv[1]).resolve(strict=True)
site_packages = Path(sys.argv[2]).resolve(strict=True)
runner_path = Path(sys.argv[3]).resolve(strict=True)
runtime_python = Path(sys.argv[4]).resolve(strict=True)
lock = json.loads(lock_path.read_text(encoding="utf-8"))

from packaging.utils import canonicalize_name

expected = {
    canonicalize_name(item["name"]): item["version"] for item in lock["packages"]
}
actual = {}
metadata_paths = {}
for distribution in metadata.distributions(path=[str(site_packages)]):
    name = canonicalize_name(distribution.metadata["Name"])
    if name in actual:
        raise RuntimeError(f"duplicate distribution metadata: {name}")
    actual[name] = distribution.version
    distribution_path = Path(distribution._path).resolve(strict=True)
    if not distribution_path.is_relative_to(site_packages):
        raise RuntimeError(f"distribution metadata escaped portable tree: {distribution_path}")
    metadata_paths[name] = str(distribution_path)
if actual != expected:
    raise RuntimeError(
        f"distribution closure mismatch: missing={sorted(set(expected) - set(actual))}, "
        f"extra={sorted(set(actual) - set(expected))}, "
        f"versions={sorted(name for name in expected if actual.get(name) != expected[name])}"
    )

excluded_distributions = [
    "deepspeed", "flash-attn", "gradio", "gradio-client", "hf-gradio", "peft"
]
excluded_modules = ["deepspeed", "flash_attn", "gradio", "gradio_client", "peft"]
for name in excluded_distributions:
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        pass
    else:
        raise RuntimeError(f"excluded distribution is installed: {name}")
for module_name in excluded_modules:
    if importlib.util.find_spec(module_name) is not None:
        raise RuntimeError(f"excluded module is importable: {module_name}")

spec = importlib.util.spec_from_file_location("mentorflow_qwen_runner_portable_smoke", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError("production runner spec could not be created")
runner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner_module)
overlay = runner_module.install_qwen_12hz_only_runtime(runtime_python.parents[1])
if overlay["sox_loaded"] or overlay["tokenizer_25hz_loaded"]:
    raise RuntimeError(f"forbidden module was loaded during overlay installation: {overlay}")

import accelerate
import einops
import librosa
import onnxruntime
import soundfile
import torch
import torchaudio
import transformers
from qwen_tts import Qwen3TTSModel
import qwen_tts


module_paths = {}
for module in (
    accelerate, einops, librosa, onnxruntime, soundfile, torch,
    torchaudio, transformers, qwen_tts,
):
    module_path = Path(module.__file__).resolve(strict=True)
    if not module_path.is_relative_to(site_packages):
        raise RuntimeError(f"dependency imported outside portable tree: {module.__name__}={module_path}")
    module_paths[module.__name__] = str(module_path)


def _deny_model_load(cls, *args, **kwargs):
    events["model_load"].append(repr(args[0] if args else None))
    raise RuntimeError("model loading disabled by portable import smoke")


Qwen3TTSModel.from_pretrained = classmethod(_deny_model_load)
backend = runner_module.QwenModelBackend(
    pack_root=runtime_python.parents[1],
    device="cuda:0",
    minimum_driver="999.0",
)
if backend._model is not None:
    raise RuntimeError("runner unexpectedly loaded a model")
if "sox" in sys.modules or any(
    name == "qwen_tts.core.tokenizer_25hz" or name.startswith("qwen_tts.core.tokenizer_25hz.")
    for name in sys.modules
):
    raise RuntimeError("portable import crossed the locked 12Hz-only boundary")
if any(events.values()):
    raise RuntimeError(f"guarded operation was attempted: {events}")

resolved_sys_path = [str(Path(item).resolve(strict=False)) for item in sys.path]
for entry in resolved_sys_path:
    if not Path(entry).is_relative_to(runtime_python):
        raise RuntimeError(f"sys.path escaped portable Python: {entry}")
if Path(sys.executable).resolve(strict=True).parent != runtime_python:
    raise RuntimeError(f"unexpected interpreter: {sys.executable}")
if sys.flags.isolated != 1:
    raise RuntimeError("portable interpreter is not isolated")

print("PORTABLE_SMOKE_JSON " + json.dumps({
    "status": "passed",
    "python_version": sys.version.split()[0],
    "python_executable": str(Path(sys.executable).resolve(strict=True)),
    "python_prefix": str(Path(sys.prefix).resolve(strict=True)),
    "sys_path": resolved_sys_path,
    "isolated": sys.flags.isolated,
    "dont_write_bytecode": sys.dont_write_bytecode,
    "package_count": len(actual),
    "packages": dict(sorted(actual.items())),
    "metadata_paths": dict(sorted(metadata_paths.items())),
    "excluded_distributions": excluded_distributions,
    "excluded_modules": excluded_modules,
    "module_paths": dict(sorted(module_paths.items())),
    "qwen_model_symbol": Qwen3TTSModel.__name__,
    "overlay": {
        key: value if not isinstance(value, Path) else str(value)
        for key, value in overlay.items()
    },
    "runner_path": str(runner_path),
    "backend_model_loaded": backend._model is not None,
    "guarded_attempts": events,
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
}, ensure_ascii=False, sort_keys=True))
'''
    _atomic_write(probe_path, probe_source.encode("utf-8"))

    cache_root = smoke_root / "cache"
    temp_root = smoke_root / "temp"
    runtime_profile_root = smoke_root / "profile"
    for directory in (cache_root, temp_root, runtime_profile_root):
        directory.mkdir(parents=True, exist_ok=True)
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
        "PATH": os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HOME": str(cache_root / "huggingface"),
        "HUGGINGFACE_HUB_CACHE": str(cache_root / "huggingface" / "hub"),
        "TORCH_HOME": str(cache_root / "torch"),
        "TORCHINDUCTOR_CACHE_DIR": str(cache_root / "torchinductor"),
        "NUMBA_CACHE_DIR": str(cache_root / "numba"),
        "NUMBA_THREADING_LAYER": "workqueue",
        "XDG_CACHE_HOME": str(cache_root / "xdg"),
        "MPLCONFIGDIR": str(cache_root / "matplotlib"),
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "HOME": str(runtime_profile_root),
        "USERPROFILE": str(runtime_profile_root),
        "APPDATA": str(runtime_profile_root / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(runtime_profile_root / "AppData" / "Local"),
        "CUDA_VISIBLE_DEVICES": "",
        "TORCH_DEVICE_BACKEND_AUTOLOAD": "0",
        "NO_PROXY": "*",
    }
    command = [
        str(python_target / "python.exe"),
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
        str(probe_path),
        str(expected_lock_target),
        str(site_packages_target),
        str(runner_target),
        str(python_target),
    ]
    started_ns = time.time_ns()
    completed = subprocess.run(
        command,
        cwd=str(smoke_root),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        check=False,
        shell=False,
    )
    finished_ns = time.time_ns()
    stdout_path = evidence_root / "stdout.txt"
    stderr_path = evidence_root / "stderr.txt"
    _atomic_write(stdout_path, completed.stdout.encode("utf-8"))
    _atomic_write(stderr_path, completed.stderr.encode("utf-8"))
    result_line = next(
        (
            line.removeprefix("PORTABLE_SMOKE_JSON ")
            for line in completed.stdout.splitlines()
            if line.startswith("PORTABLE_SMOKE_JSON ")
        ),
        None,
    )
    result = json.loads(result_line) if result_line is not None else None
    pe_audit = (
        audit_portable_pe_dependencies(smoke_root, generation_id=generation_id)
        if strict_12hz_v2
        else None
    )
    host_external_added = (
        list(result.get("native_boundaries", {}).get("host_external_added") or [])
        if strict_12hz_v2 and isinstance(result, dict)
        else []
    )
    pe_declared_import_names = (
        {
            dependency["name"].casefold()
            for binary in pe_audit["binaries"]
            for dependency in binary["dependencies"]
        }
        if strict_12hz_v2
        else set()
    )
    host_external_dependency_matches = [
        raw_path
        for raw_path in host_external_added
        if Path(raw_path).name.casefold() in pe_declared_import_names
    ]
    host_injected_observer_records = (
        [
            _host_injected_observer_record(
                raw_path,
                pe_declared_import_names=pe_declared_import_names,
            )
            for raw_path in host_external_added
        ]
        if strict_12hz_v2
        else []
    )
    common_passed = (
        completed.returncode == 0
        and isinstance(result, dict)
        and result.get("status") == "passed"
        and result.get("package_count") == expected_package_count
        and result.get("backend_model_loaded") is False
        and result.get("overlay", {}).get("status") == "installed"
        and result.get("overlay", {}).get("sox_loaded") is False
        and result.get("overlay", {}).get("tokenizer_25hz_loaded") is False
    )
    v2_passed = (
        strict_12hz_v2
        and common_passed
        and result.get("product_entry")
        == [
            "install_qwen_12hz_only_runtime(pack_root)",
            "from qwen_tts import Qwen3TTSModel",
        ]
        and result.get("guarded_attempts")
        == {
            "bind": ["('::1', 0)"],
            "connect": [],
            "listen": [],
            "subprocess": [],
            "model_load": [],
        }
        and result.get("numba")
        == {
            "configured": "workqueue",
            "active_config": "workqueue",
            "tbbpool_loaded": False,
            "tbb": "optional-not-shipped",
        }
        and host_external_dependency_matches == []
        and len(host_injected_observer_records) == len(host_external_added)
        and pe_audit.get("status") == "passed"
        and pe_audit.get("unresolved") == []
        and pe_audit.get("tbb", {}).get("status") == "optional-not-shipped"
    )
    v1_passed = (
        not strict_12hz_v2
        and common_passed
        and result.get("guarded_attempts")
        == {"listener": [], "model_load": [], "network": [], "subprocess": []}
    )
    passed = v2_passed or v1_passed
    receipt = {
        "schema": (
            "mentorflow.qwen-runtime-portable-import-smoke.v2"
            if strict_12hz_v2
            else "mentorflow.qwen-runtime-portable-import-smoke.v1"
        ),
        "status": "passed" if passed else "failed",
        **({"profile": "12hz-only"} if strict_12hz_v2 else {}),
        **(
            {"generation_id": generation_id}
            if strict_12hz_v2 and generation_id is not None
            else {}
        ),
        "scope": "fresh copied E:-only portable Python dependency and production-runner import",
        "not_proven": [
            "CUDA driver or device availability",
            "model weight loading",
            "voice-clone prompt creation",
            "audio synthesis quality or latency",
            "service startup or loopback request handling",
            "system-call-level filesystem tracing outside redirected cache and temp paths",
            *(
                [
                    "a clean machine with no third-party process injection; this machine "
                    "executed the signed observer modules listed in native_host_injection"
                ]
                if strict_12hz_v2 and host_injected_observer_records
                else []
            ),
        ],
        "safety": {
            **(
                {"network_connect_bind_and_listener_guards": True}
                if strict_12hz_v2
                else {"network_connect_and_listener_guards": True}
            ),
            "subprocess_guards": True,
            "offline_environment_flags": True,
            "all_cache_temp_profile_targets_on_e_drive": True,
            "model_execution": False,
            "cuda_api_called_by_probe": False,
            "service_started": False,
            "synthesis_called": False,
            "hardlinks_or_symlinks_used": False,
            **(
                {
                    "expected_capability_bind": "('::1', 0)",
                    "expected_capability_bind_attempts": 1,
                    "connect_attempts": 0,
                    "listen_attempts": 0,
                    "subprocess_attempts": 0,
                    "host_external_native_dependency_edges": 0,
                    "host_injected_observer_modules_recorded": True,
                    "windows_system_boundary_recorded": True,
                    "nvidia_driver_boundary_recorded": True,
                    "numba_threading_layer": "workqueue",
                    "tbb": "optional-not-shipped",
                }
                if strict_12hz_v2
                else {}
            ),
        },
        "timing": {
            "started_unix_ns": started_ns,
            "finished_unix_ns": finished_ns,
            "duration_seconds": round((finished_ns - started_ns) / 1_000_000_000, 6),
        },
        "inputs": {
            "python_source_receipt": {
                "path": str(source_receipt_path),
                "sha256": _sha256_file(source_receipt_path),
            },
            "python_component_fragment": {
                "path": str(component_fragment_path),
                "sha256": _sha256_file(component_fragment_path),
                "tree_sha256": actual_python_tree,
            },
            "dependency_lock": {
                "path": str(lock_path),
                "sha256": _sha256_file(lock_path),
                "package_count": dependency_lock["package_count"],
            },
            "builder_validation": {
                "path": str(core_validation_path),
                "sha256": validation_core_sha256,
                "selected_files": validation["selected_files"],
                "excluded_test_files": validation["excluded_test_files"],
                "excluded_test_bytes": validation["excluded_test_bytes"],
            },
            **(
                {
                    "builder_fragment": {
                        "path": str(fragment_path),
                        "sha256": _sha256_file(fragment_path),
                    },
                    "builder_inventory": {
                        "path": str(inventory_path),
                        "sha256": _sha256_file(inventory_path),
                    },
                    "resolution_plan": {
                        "path": str(resolution_path),
                        "sha256": _sha256_file(resolution_path),
                    },
                }
                if strict_12hz_v2
                else {}
            ),
            "runner": {
                "source_path": str(runner_source),
                "copied_path": str(runner_target),
                "sha256": _sha256_bytes(runner_bytes),
            },
            "source_overlay": {
                "source_path": str(overlay_source),
                "copied_path": str(overlay_target),
                "sha256": _sha256_file(overlay_target),
            },
            "model_identity_configs": copied_model_identity,
        },
        "staging": {
            "root": str(smoke_root),
            "path_budget": path_budget,
            "install_root_path_acceptance": path_budget_acceptance,
            "free_bytes_before": free_bytes_before,
            "required_copy_bytes": required_copy_bytes,
            "python": str(python_target),
            "python_executable": str(python_target / "python.exe"),
            "python_executable_sha256": _sha256_file(python_target / "python.exe"),
            "site_packages": str(site_packages_target),
            "dependency_files": len(copied_dependency_files),
            "dependency_bytes": sum(item["bytes"] for item in expected_members.values()),
        },
        "probe": {
            "path": str(probe_path),
            "sha256": _sha256_file(probe_path),
            "command": command,
            "cwd": str(smoke_root),
            "environment": environment,
            "returncode": completed.returncode,
            "stdout": {"path": str(stdout_path), "sha256": _sha256_file(stdout_path)},
            "stderr": {"path": str(stderr_path), "sha256": _sha256_file(stderr_path)},
            "result": result,
        },
        **(
            {
                "native_host_injection": {
                    "environment_fact": (
                        "third-party signed host software injected module(s) after the "
                        "product entry began on this machine"
                    ),
                    "clean_machine_no_injection_proven": False,
                    "portable_runtime_dependency": False,
                    "acceptance_rule": (
                        "each observer must have an exact approved basename and publisher, "
                        "valid Authenticode, and no PE import or delay-import edge"
                    ),
                    "observers": host_injected_observer_records,
                    "authenticode_verifier": (
                        "PowerShell 7 Get-AuthenticodeSignature, executed by the evidence "
                        "parent after the guarded portable child exited"
                    ),
                },
                "pe_dependency_audit": {
                    "path": pe_audit["path"],
                    "sha256": pe_audit["sha256"],
                    "status": pe_audit["status"],
                    "binary_count": pe_audit["binary_count"],
                    "dependency_edge_count": pe_audit["dependency_edge_count"],
                    "windows_system_boundary_edges": len(
                        pe_audit["windows_system_boundary"]
                    ),
                    "nvidia_driver_boundary_edges": len(
                        pe_audit["nvidia_driver_boundary"]
                    ),
                    "unresolved_edges": len(pe_audit["unresolved"]),
                    "tbb": pe_audit["tbb"],
                    "host_external_native_modules_added": host_external_added,
                    "host_external_dependency_matches": host_external_dependency_matches,
                    "host_external_classification": (
                        "observer module injected by host software; not named by any "
                        "portable PE import or delay-import edge"
                    ),
                }
            }
            if strict_12hz_v2
            else {}
        ),
    }
    receipt_path = (
        profile_root / "evidence" / "portable-python-import-smoke.json"
        if strict_12hz_v2
        else profile_root / "portable-python-import-smoke.json"
    )
    _atomic_write(receipt_path, _json_bytes(receipt))
    if not passed:
        raise WheelhouseError(
            "PORTABLE_IMPORT_SMOKE_FAILED",
            f"returncode={completed.returncode}; stderr={completed.stderr[-3000:]}",
        )
    if strict_12hz_v2:
        _finalize_v2_builder_validation(
            core_validation_path,
            final_validation_path=final_validation_path,
            portable_receipt_path=receipt_path,
            pe_receipt_path=Path(str(pe_audit["path"])),
        )
    return receipt


def _validated_root(raw: str) -> Path:
    root = Path(raw).resolve(strict=False)
    if root.drive.casefold() != "e:":
        raise WheelhouseError("OUTPUT_DRIVE_INVALID", f"wheelhouse must be on E:, got {root}")
    return root


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="query official index metadata only")
    mode.add_argument("--download", action="store_true", help="download and audit the exact plan")
    mode.add_argument(
        "--license-supplements",
        action="store_true",
        help="fetch exact release-tag licences for audited wheels that omit them",
    )
    mode.add_argument(
        "--sox-import-smoke",
        action="store_true",
        help="import the official sox 1.4.1 wheel ahead of the validated Qwen environment",
    )
    mode.add_argument(
        "--validate-real-supplements",
        action="store_true",
        help="validate the two real release-tag supplements with the production builder",
    )
    mode.add_argument(
        "--audit-builder-payload",
        action="store_true",
        help="record exact wheel members conflicting with the runtime-only builder policy",
    )
    mode.add_argument(
        "--materialize-builder-inputs",
        action="store_true",
        help="extract the exact runtime-only wheel members and validate all dependency components",
    )
    mode.add_argument(
        "--materialize-12hz-v2",
        action="store_true",
        help="re-audit cached wheels and materialize the strict 12Hz-only v2 closure",
    )
    mode.add_argument(
        "--portable-import-smoke",
        action="store_true",
        help="copy and import the exact closure with the verified E:-only portable Python",
    )
    mode.add_argument(
        "--portable-12hz-v2",
        action="store_true",
        help="run the product-entry smoke against the strict 12Hz-only v2 closure",
    )
    mode.add_argument(
        "--refresh-portable-python-receipt",
        action="store_true",
        help="add live exact Authenticode evidence to the pinned portable CPython receipt",
    )
    mode.add_argument(
        "--materialize-portable-python-v2-materials",
        action="store_true",
        help="add an immutable v2 CPython inventory and component fragment without rewriting v1",
    )
    parser.add_argument("--root", default=str(WHEELHOUSE_ROOT_DEFAULT))
    parser.add_argument(
        "--generation-id",
        default=None,
        help=(
            "required for strict v2 materialize/smoke; writes under "
            "v2/generations/<id>/12hz-only and never overwrites legacy v2"
        ),
    )
    parser.add_argument(
        "--portable-python-material-root",
        default=str(PORTABLE_PYTHON_MATERIAL_ROOT_DEFAULT),
    )
    parser.add_argument(
        "--qwen-model-root",
        default=str(QWEN_MODEL_ROOT_DEFAULT),
        help="locked Qwen3-TTS-12Hz-0.6B-Base root used only for identity-config smoke copies",
    )
    parser.add_argument(
        "--portable-smoke-root",
        default=None,
        help=(
            "fresh short E: root for portable smoke; may also be supplied by "
            "MENTORFLOW_PORTABLE_SMOKE_ROOT and is mandatory for strict v2"
        ),
    )
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    root = _validated_root(arguments.root)
    generation_id = _validated_generation_id(arguments.generation_id)
    if (arguments.materialize_12hz_v2 or arguments.portable_12hz_v2) and generation_id is None:
        raise WheelhouseError(
            "GENERATION_ID_REQUIRED",
            "strict v2 materialize/smoke requires a new explicit --generation-id",
        )
    if arguments.refresh_portable_python_receipt:
        result = refresh_portable_python_authenticode_receipt(
            Path(arguments.portable_python_material_root)
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if arguments.materialize_portable_python_v2_materials:
        result = materialize_portable_python_v2_materials(
            Path(arguments.portable_python_material_root)
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if arguments.plan:
        plan = build_plan()
        _atomic_write(root / "resolution-plan.json", _json_bytes(plan))
        print(json.dumps({key: plan[key] for key in ("schema", "dependency_count", "artifact_count", "download_bytes", "download_gib", "blockers")}, indent=2))
        return 2 if plan["blockers"] else 0
    if arguments.license_supplements:
        result = fetch_license_supplements(root)
        print(
            json.dumps(
                {
                    "schema": result["schema"],
                    "supplement_count": result["supplement_count"],
                    "release_status": result["release_status"],
                },
                indent=2,
            )
        )
        return 0
    if arguments.sox_import_smoke:
        result = run_sox_override_import_smoke(root)
        print(json.dumps(result, indent=2))
        return 0
    if arguments.validate_real_supplements:
        result = validate_real_supplements_with_builder(root)
        print(json.dumps(result, indent=2))
        return 0
    if arguments.audit_builder_payload:
        result = audit_builder_payload_compatibility(root)
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "schema",
                        "status",
                        "affected_packages",
                        "forbidden_member_count",
                        "forbidden_member_bytes",
                    )
                },
                indent=2,
            )
        )
        return 3 if result["affected_packages"] else 0
    if arguments.materialize_builder_inputs:
        result = materialize_builder_dependency_inputs(root)
        print(json.dumps(result, indent=2))
        return 0
    if arguments.materialize_12hz_v2:
        result = materialize_builder_dependency_inputs(
            root,
            strict_12hz_v2=True,
            generation_id=generation_id,
        )
        print(json.dumps(result, indent=2))
        return 0
    if arguments.portable_import_smoke or arguments.portable_12hz_v2:
        result = run_portable_import_smoke(
            root,
            Path(arguments.portable_python_material_root),
            Path(arguments.qwen_model_root),
            strict_12hz_v2=arguments.portable_12hz_v2,
            generation_id=generation_id,
            portable_smoke_root=(
                Path(arguments.portable_smoke_root)
                if arguments.portable_smoke_root
                else None
            ),
        )
        print(
            json.dumps(
                {
                    "schema": result["schema"],
                    "status": result["status"],
                    "package_count": result["probe"]["result"]["package_count"],
                    "dependency_files": result["staging"]["dependency_files"],
                    "returncode": result["probe"]["returncode"],
                },
                indent=2,
            )
        )
        return 0
    plan_path = root / "resolution-plan.json"
    if not plan_path.is_file():
        raise WheelhouseError("PLAN_MISSING", f"run --plan first: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema") != PLAN_SCHEMA:
        raise WheelhouseError("PLAN_INVALID", "resolution plan schema mismatch")
    result = download_and_audit(root, plan)
    print(
        json.dumps(
            {
                "schema": LOCK_SCHEMA,
                "packages": len(result["lock"]["packages"]),
                "wheel_bytes": result["inventory"]["wheel_bytes"],
                "blockers": result["lock"]["blockers"],
            },
            indent=2,
        )
    )
    return 3 if result["lock"]["blockers"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WheelhouseError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "message": exc.message}, indent=2), file=sys.stderr)
        raise SystemExit(2)
