from __future__ import annotations

import ctypes
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

from app import local_voice_runtime_supervisor as supervisor_module
from app import qwen_engine_launch_attestation as launch_attestation
from app import teacher_voice_pack as voice_pack
from app import windows_voice_runtime_ownership as windows_runtime
from app.windows_install_path_budget import calculate_windows_install_path_budget


class FakeProcess:
    def __init__(self, pid: int = 41_001, handle: int = 81_001) -> None:
        self.pid = pid
        self._handle = handle
        self.return_code: int | None = None
        self.kill_calls = 0
        self.native_close_calls = 0
        self.native_handle_closed = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.return_code = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.return_code = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.return_code is None:
            raise subprocess.TimeoutExpired("fake-child", timeout)
        return self.return_code

    def close_native_handle(self) -> None:
        if self.native_handle_closed:
            return
        self.native_close_calls += 1
        self.native_handle_closed = True


class FakeLauncher:
    def __init__(self, process: FakeProcess | None = None, *, fail: bool = False) -> None:
        self.process = process or FakeProcess()
        self.fail = fail
        self.calls = 0

    def launch(self, plan: Any, *, environment: Mapping[str, str]) -> FakeProcess:
        self.calls += 1
        if self.fail:
            raise OSError("launch failed")
        return self.process


class FakeWindowsApi:
    def __init__(
        self,
        *,
        listener_snapshots: Sequence[Sequence[windows_runtime.WindowsTcpListener]] = ((),),
        fail_at: str = "",
    ) -> None:
        self.listener_snapshots = list(listener_snapshots)
        self.fail_at = fail_at
        self.events: list[tuple[Any, ...]] = []
        self.close_calls: list[int] = []
        self.assigned: dict[int, FakeProcess] = {}
        self._next_handle = 701
        self.process = FakeProcess()
        self.thread_handle = 901
        self.attribute_deletes = 0
        self.query_count = 0
        self.fail_close_once = False

    def create_job_object(self) -> int:
        self.events.append(("create",))
        if self.fail_at == "create":
            raise OSError("create failed")
        result = self._next_handle
        self._next_handle += 1
        return result

    def set_kill_on_job_close(self, job_handle: int) -> None:
        self.events.append(("configure", job_handle))
        if self.fail_at == "configure":
            raise OSError("configure failed")

    def create_job_attribute_list(
        self, job_handle: int
    ) -> windows_runtime.WindowsJobAttributeList:
        self.events.append(("attribute", job_handle))
        if self.fail_at == "attribute":
            raise OSError("attribute failed")
        return windows_runtime.WindowsJobAttributeList(
            buffer=object(),
            job_handles=(job_handle,),
            pointer=801,
        )

    def delete_job_attribute_list(
        self, attributes: windows_runtime.WindowsJobAttributeList
    ) -> None:
        if attributes.deleted:
            return
        self.events.append(("delete_attribute", attributes.pointer))
        self.attribute_deletes += 1
        attributes.deleted = True

    def create_process_suspended(
        self,
        attributes: windows_runtime.WindowsJobAttributeList,
        plan: Any,
        environment: Mapping[str, str],
    ) -> windows_runtime.SuspendedWindowsChild:
        job_handle = int(attributes.job_handles[0])  # type: ignore[index]
        self.events.append(("create_suspended", job_handle, self.process.pid))
        if self.fail_at == "create_process":
            raise OSError("CreateProcessW failed")
        self.assigned[job_handle] = self.process
        return windows_runtime.SuspendedWindowsChild(
            process=self.process,
            thread_handle=self.thread_handle,
        )

    def resume_thread(self, thread_handle: int) -> None:
        self.events.append(("resume", thread_handle))
        if self.fail_at == "resume":
            raise OSError("ResumeThread failed")

    def close_handle(self, handle: int) -> None:
        self.events.append(("close", handle))
        self.close_calls.append(handle)
        if self.fail_at == "thread_close" and handle == self.thread_handle:
            self.fail_at = ""
            raise OSError("thread close failed")
        if self.fail_close_once:
            self.fail_close_once = False
            raise OSError("close failed once")
        process = self.assigned.pop(handle, None)
        if process is not None:
            process.return_code = 1

    def ipv4_tcp_listeners(self) -> Sequence[windows_runtime.WindowsTcpListener]:
        self.query_count += 1
        if self.fail_at == "tcp_table":
            raise OSError("tcp table unavailable")
        index = min(self.query_count - 1, len(self.listener_snapshots) - 1)
        return self.listener_snapshots[index]


def listener(port: int, pid: int, host: str = "127.0.0.1") -> windows_runtime.WindowsTcpListener:
    return windows_runtime.WindowsTcpListener(
        local_address=host,
        local_port=port,
        owning_pid=pid,
    )


def test_ctypes_job_and_tcp_structures_encode_required_fields() -> None:
    information = windows_runtime._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = (
        windows_runtime.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    assert (
        information.BasicLimitInformation.LimitFlags
        == windows_runtime.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    assert ctypes.sizeof(information) >= ctypes.sizeof(
        windows_runtime._JOBOBJECT_BASIC_LIMIT_INFORMATION
    )

    row = windows_runtime._MIB_TCPROW_OWNER_PID()
    row.dwLocalAddr = int.from_bytes(socket.inet_aton("127.0.0.1"), "little")
    row.dwLocalPort = socket.htons(24_321)
    row.dwOwningPid = 55_501
    header = (1).to_bytes(ctypes.sizeof(windows_runtime.wintypes.DWORD), "little")
    raw = header + bytes(row)
    buffer = ctypes.create_string_buffer(raw, len(raw))

    parsed = windows_runtime.CtypesWindowsRuntimeApi._parse_tcp_table(buffer, len(raw))
    assert parsed == [listener(24_321, 55_501)]


def test_job_owner_creates_child_atomically_in_preconfigured_job_and_closes_once() -> None:
    api = FakeWindowsApi()
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    launcher = windows_runtime.WindowsAtomicRuntimeLauncher(api)

    process = owner.launch(launcher, object(), environment={})  # type: ignore[arg-type]

    assert api.events[:7] == [
        ("create",),
        ("configure", 701),
        ("attribute", 701),
        ("create_suspended", 701, process.pid),
        ("delete_attribute", 801),
        ("resume", 901),
        ("close", 901),
    ]
    assert owner.owns_pid(process, process.pid) is True
    assert owner.owns_pid(process, process.pid + 1) is False
    assert owner.terminate(process, timeout_seconds=0.1) is True
    assert owner.terminate(process, timeout_seconds=0.1) is True
    owner.release(process)
    owner.close()
    assert api.close_calls == [901, 701]
    assert process.native_close_calls == 1


@pytest.mark.skipif(os.name != "nt", reason="requires Windows share-deny handles")
def test_unlisted_file_in_suspended_child_gate_prevents_resume_and_transport(tmp_path: Path) -> None:
    root = tmp_path / "engine"
    root.mkdir()
    signed_file = root / "signed.bin"
    signed_file.write_bytes(b"signed-engine-fixture")
    dependency = voice_pack.QwenBaseCloneEngineDependency(
        kind=voice_pack.QWEN_BASE_CLONE_ENGINE_KIND,
        catalog_id="fixture-qwen-engine",
        version="0.1.0",
        manifest_sha256="d" * 64,
    )
    capability = voice_pack.VerifiedQwenEngineRegistrationCapability(
        voice_pack._QWEN_ENGINE_CAPABILITY_TOKEN,
        dependency,
        root,
        "e" * 64,
        (("signed.bin", signed_file.stat().st_size, hashlib.sha256(signed_file.read_bytes()).hexdigest()),),
        (),
        install_path_budget=calculate_windows_install_path_budget(("signed.bin",)),
    )
    lease = launch_attestation.acquire_qwen_engine_launch_attestation(capability)
    api = FakeWindowsApi()
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    launcher = windows_runtime.WindowsAtomicRuntimeLauncher(api)
    transport_calls: list[str] = []

    def suspended_child_gate() -> None:
        api.events.append(("pre_resume_gate",))
        (root / "unlisted-after-create.txt").write_text("changed", encoding="utf-8")
        lease.seal_for_launch()

    def launch_then_transport() -> None:
        owner.launch(
            launcher,
            object(),
            environment={},
            pre_resume=suspended_child_gate,
        )  # type: ignore[arg-type]
        transport_calls.append("sent")

    try:
        with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as blocked:
            launch_then_transport()
    finally:
        lease.release()
        owner.close()

    assert blocked.value.code == "VOICE_RUNTIME_PRELAUNCH_VERIFY_FAILED"
    assert ("pre_resume_gate",) in api.events
    assert not any(event[0] == "resume" for event in api.events)
    assert transport_calls == []
    assert api.process.kill_calls == 1
    assert api.process.native_close_calls == 1
    assert owner.owns_pid(api.process, api.process.pid) is False


@pytest.mark.parametrize(
    (
        "fail_at",
        "expected_child_creations",
        "expected_attribute_deletes",
        "expected_kills",
        "expected_native_closes",
    ),
    [
        ("create", 0, 0, 0, 0),
        ("configure", 0, 0, 0, 0),
        ("attribute", 0, 0, 0, 0),
        ("create_process", 1, 1, 0, 0),
        ("resume", 1, 1, 1, 1),
        ("thread_close", 1, 1, 1, 1),
    ],
)
def test_job_setup_errors_fail_closed_and_cleanup(
    fail_at: str,
    expected_child_creations: int,
    expected_attribute_deletes: int,
    expected_kills: int,
    expected_native_closes: int,
) -> None:
    api = FakeWindowsApi(fail_at=fail_at)
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    launcher = windows_runtime.WindowsAtomicRuntimeLauncher(api)

    with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as raised:
        owner.launch(launcher, object(), environment={})  # type: ignore[arg-type]

    assert raised.value.code.startswith("VOICE_RUNTIME_WINDOWS_")
    assert sum(event[0] == "create_suspended" for event in api.events) == expected_child_creations
    assert api.attribute_deletes == expected_attribute_deletes
    assert api.process.kill_calls == expected_kills
    assert api.process.native_close_calls == expected_native_closes
    assert owner.owns_pid(api.process, api.process.pid) is False
    # Job handle 701 is always closed if it was successfully created.  A
    # created child also has its thread handle closed exactly once.
    assert api.close_calls.count(701) == (0 if fail_at == "create" else 1)
    assert api.close_calls.count(901) == (
        2 if fail_at == "thread_close" else (1 if fail_at == "resume" else 0)
    )


def test_non_atomic_launcher_is_rejected_before_job_or_child_creation() -> None:
    api = FakeWindowsApi()
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    unsafe_launcher = FakeLauncher()

    with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as raised:
        owner.launch(unsafe_launcher, object(), environment={})  # type: ignore[arg-type]

    assert raised.value.code == "VOICE_RUNTIME_ATOMIC_LAUNCHER_REQUIRED"
    assert unsafe_launcher.calls == 0
    assert api.events == []


def test_job_close_failure_retains_record_for_safe_retry() -> None:
    api = FakeWindowsApi()
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    process = owner.launch(
        windows_runtime.WindowsAtomicRuntimeLauncher(api),
        object(),
        environment={},
    )  # type: ignore[arg-type]
    api.fail_close_once = True

    with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as raised:
        owner.terminate(process, timeout_seconds=0.1)

    assert raised.value.code == "VOICE_RUNTIME_WINDOWS_JOB_CLOSE_FAILED"
    assert owner.owns_pid(process, process.pid) is True
    assert owner.terminate(process, timeout_seconds=0.1) is True
    owner.release(process)
    assert api.close_calls == [901, 701, 701]


def test_natural_exit_release_closes_job_handle_idempotently() -> None:
    api = FakeWindowsApi()
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    process = owner.launch(
        windows_runtime.WindowsAtomicRuntimeLauncher(api),
        object(),
        environment={},
    )  # type: ignore[arg-type]
    process.return_code = 0

    owner.release(process)
    owner.release(process)

    assert api.close_calls == [901, 701]
    assert owner.owns_pid(process, process.pid) is False
    assert process.native_close_calls == 1


def test_listener_verifier_retries_and_requires_exact_loopback_pid() -> None:
    port = 24_321
    pid = 55_501
    api = FakeWindowsApi(
        listener_snapshots=(
            (),
            (listener(port, pid + 1),),
            (listener(port, pid),),
        )
    )
    sleeps: list[float] = []
    verifier = windows_runtime.WindowsTcpListenerOwnershipVerifier(
        api,
        attempts=3,
        retry_delay_seconds=0.01,
        sleep=sleeps.append,
    )

    assert verifier.verify(host="127.0.0.1", port=port, listener_pid=pid) is True
    assert api.query_count == 3
    assert sleeps == [0.01, 0.01]

    wrong = FakeWindowsApi(listener_snapshots=((listener(port, pid + 1),),))
    assert windows_runtime.WindowsTcpListenerOwnershipVerifier(
        wrong, attempts=2, retry_delay_seconds=0, sleep=lambda _seconds: None
    ).verify(host="127.0.0.1", port=port, listener_pid=pid) is False

    missing = FakeWindowsApi(listener_snapshots=((),))
    assert windows_runtime.WindowsTcpListenerOwnershipVerifier(
        missing, attempts=2, retry_delay_seconds=0, sleep=lambda _seconds: None
    ).verify(host="127.0.0.1", port=port, listener_pid=pid) is False

    ambiguous = FakeWindowsApi(
        listener_snapshots=((listener(port, pid), listener(port, pid + 1)),)
    )
    assert windows_runtime.WindowsTcpListenerOwnershipVerifier(
        ambiguous, attempts=1, retry_delay_seconds=0
    ).verify(host="127.0.0.1", port=port, listener_pid=pid) is False


def test_listener_verifier_system_error_is_fail_closed() -> None:
    verifier = windows_runtime.WindowsTcpListenerOwnershipVerifier(
        FakeWindowsApi(fail_at="tcp_table"),
        attempts=3,
        retry_delay_seconds=0,
    )
    with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as raised:
        verifier.verify(host="127.0.0.1", port=24_321, listener_pid=55_501)
    assert raised.value.code == "VOICE_RUNTIME_TCP_TABLE_QUERY_FAILED"


def test_production_dependency_factory_is_explicitly_windows_only() -> None:
    with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as raised:
        windows_runtime.create_production_windows_runtime_ownership(
            platform_name="posix"
        )
    assert raised.value.code == "VOICE_RUNTIME_PLATFORM_UNSUPPORTED"

    api = FakeWindowsApi()
    dependencies = windows_runtime.create_production_windows_runtime_ownership(
        platform_name="nt",
        api=api,
    )
    assert isinstance(
        dependencies.process_tree_owner,
        windows_runtime.WindowsJobObjectProcessTreeOwner,
    )
    assert isinstance(
        dependencies.listener_verifier,
        windows_runtime.WindowsTcpListenerOwnershipVerifier,
    )
    assert isinstance(
        dependencies.launcher,
        windows_runtime.WindowsAtomicRuntimeLauncher,
    )

    with pytest.raises(supervisor_module.LocalVoiceRuntimeError) as supervisor_raised:
        supervisor_module.create_production_local_voice_runtime_supervisor(
            None,  # type: ignore[arg-type]
            catalog=None,  # type: ignore[arg-type]
            platform_name="posix",
        )
    assert supervisor_raised.value.code == "VOICE_RUNTIME_PLATFORM_UNSUPPORTED"


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows Job and TCP APIs")
def test_real_windows_job_and_tcp_listener_acceptance_has_no_residual_child() -> None:
    reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reserved.bind(("127.0.0.1", 0))
    port = int(reserved.getsockname()[1])
    reserved.close()
    assert port not in {8_000, 8_892, 9_880}

    helper = (
        "import socket,sys,time;"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        "s.bind(('127.0.0.1',int(sys.argv[1])));"
        "s.listen(1);"
        "time.sleep(20)"
    )

    api = windows_runtime.CtypesWindowsRuntimeApi()
    owner = windows_runtime.WindowsJobObjectProcessTreeOwner(api)
    launcher = windows_runtime.WindowsAtomicRuntimeLauncher(api)
    verifier = windows_runtime.WindowsTcpListenerOwnershipVerifier(
        api,
        attempts=2,
        retry_delay_seconds=0.02,
    )
    process: supervisor_module.OwnedProcess | None = None
    try:
        executable = Path(str(getattr(sys, "_base_executable", sys.executable)))
        plan = SimpleNamespace(
            executable=executable,
            arguments=("-c", helper, str(port)),
            working_directory=executable.parent,
        )
        process = owner.launch(
            launcher,
            plan,
            environment=dict(os.environ),
        )  # type: ignore[arg-type]
        deadline = time.monotonic() + 5.0
        verified = False
        observed: list[windows_runtime.WindowsTcpListener] = []
        while time.monotonic() < deadline and process.poll() is None:
            observed = [
                row
                for row in api.ipv4_tcp_listeners()
                if row.local_port == port or row.owning_pid == process.pid
            ]
            if verifier.verify(host="127.0.0.1", port=port, listener_pid=process.pid):
                verified = True
                break
            time.sleep(0.03)
        assert verified is True, {
            "port": port,
            "pid": process.pid,
            "poll": process.poll(),
            "observed": observed,
        }
        assert owner.owns_pid(process, process.pid) is True
    finally:
        if process is not None:
            owner.terminate(process, timeout_seconds=5.0)
            process.wait(timeout=5.0)
            owner.release(process)
        owner.close()

    assert process is not None
    assert process.poll() is not None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as residual_probe:
        residual_probe.settimeout(0.2)
        assert residual_probe.connect_ex(("127.0.0.1", port)) != 0
