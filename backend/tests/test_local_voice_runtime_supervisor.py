from __future__ import annotations

import itertools
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from app import local_voice_runtime_supervisor as runtime


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.return_code: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.poll_failures = 0
        self.poll_always_raises = False

    def poll(self) -> int | None:
        if self.poll_always_raises:
            raise RuntimeError("poll_handle_failed")
        if self.poll_failures:
            self.poll_failures -= 1
            raise RuntimeError("poll_handle_failed")
        return self.return_code

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.return_code = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.return_code = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.return_code is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.return_code


class FakeLauncher:
    def __init__(self) -> None:
        self.calls = 0
        self.environments: list[dict[str, str]] = []
        self.processes: list[FakeProcess] = []

    def launch(
        self,
        plan: runtime.LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
    ) -> FakeProcess:
        self.calls += 1
        self.environments.append(dict(environment))
        process = FakeProcess(43_200 + self.calls)
        self.processes.append(process)
        return process


class FakeTreeOwner:
    def __init__(self) -> None:
        self.launch_calls = 0
        self.terminate_calls = 0
        self.release_calls = 0
        self.fail_termination = False
        self.fail_release = False
        self.invalidate_poll_on_release = False
        self.raise_after_termination = False
        self.terminate_entered: threading.Event | None = None
        self.allow_termination: threading.Event | None = None

    def launch(
        self,
        launcher: runtime.RuntimeLauncher,
        plan: runtime.LocalVoiceRuntimePlan,
        *,
        environment: Mapping[str, str],
        pre_resume=None,
    ) -> runtime.OwnedProcess:
        self.launch_calls += 1
        if pre_resume is not None:
            pre_resume()
        return launcher.launch(plan, environment=environment)

    def owns_pid(self, process: runtime.OwnedProcess, pid: int) -> bool:
        return process.poll() is None and int(process.pid) == int(pid)

    def terminate(self, process: runtime.OwnedProcess, *, timeout_seconds: float) -> bool:
        self.terminate_calls += 1
        if self.terminate_entered is not None:
            self.terminate_entered.set()
        if self.allow_termination is not None:
            assert self.allow_termination.wait(timeout=2)
        if self.fail_termination:
            raise RuntimeError("job_object_close_failed")
        process.terminate()
        process.wait(timeout=timeout_seconds)
        if self.raise_after_termination:
            raise RuntimeError("job_object_closed_then_report_failed")
        return process.poll() is not None

    def release(self, process: runtime.OwnedProcess) -> None:
        assert process.poll() is not None
        self.release_calls += 1
        if self.fail_release:
            raise RuntimeError("owner_release_failed")
        if self.invalidate_poll_on_release:
            process.poll_always_raises = True


class FakeLaunchAttestation:
    def __init__(self, *, fail_seal: bool = False, release_failures: int = 0) -> None:
        self.fail_seal = fail_seal
        self.release_failures = release_failures
        self.invalid = False
        self.seal_calls = 0
        self.assert_calls = 0
        self.release_calls = 0

    def seal_for_launch(self) -> None:
        self.seal_calls += 1
        if self.fail_seal:
            raise runtime.LocalVoiceRuntimeError(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED",
                "fixture launch attestation changed",
            )

    def assert_valid(self) -> None:
        self.assert_calls += 1
        if self.invalid:
            raise runtime.LocalVoiceRuntimeError(
                "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED",
                "fixture launch attestation changed",
            )

    def release(self) -> None:
        self.release_calls += 1
        if self.release_failures:
            self.release_failures -= 1
            raise RuntimeError("attestation_release_failed")


class FakeListenerVerifier:
    def __init__(self, *, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[tuple[str, int, int]] = []

    def verify(self, *, host: str, port: int, listener_pid: int) -> bool:
        self.calls.append((host, port, listener_pid))
        return self.accepted


class MatchingProbe:
    def __init__(self, launcher: FakeLauncher) -> None:
        self.launcher = launcher
        self.nonces: list[str] = []

    def probe(
        self,
        plan: runtime.LocalVoiceRuntimePlan,
        *,
        nonce: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.nonces.append(nonce)
        secret = self.launcher.environments[-1][runtime.INSTANCE_SECRET_ENV]
        pid = self.launcher.processes[-1].pid
        return signed_health(plan, secret=secret, nonce=nonce, pid=pid)


def signed_health(
    plan: runtime.LocalVoiceRuntimePlan,
    *,
    secret: str,
    nonce: str,
    pid: int,
    ready: bool = True,
) -> dict[str, Any]:
    return {
        "schema": runtime.RUNTIME_HEALTH_SCHEMA,
        "runtime_id": plan.runtime_id,
        "service_id": plan.service_id,
        "pid": pid,
        "listener_pid": pid,
        "ready": ready,
        "proof": runtime._hmac_proof(
            secret,
            nonce=nonce,
            service_id=plan.service_id,
            runtime_id=plan.runtime_id,
            pid=pid,
            ready=ready,
        ),
    }


def trusted_plan(
    tmp_path: Path,
    *,
    executable_sha256: str | None = None,
    entry_environment: Mapping[str, str] | None = None,
) -> tuple[runtime.TrustedRuntimeCatalog, runtime.LocalVoiceRuntimePlan]:
    root = tmp_path / "runtime"
    root.mkdir(parents=True)
    executable = root / "voice-runtime.exe"
    executable.write_bytes(b"fixture-runtime-binary")
    pinned_digest = executable_sha256 or runtime.hashlib.sha256(
        b"fixture-runtime-binary"
    ).hexdigest()
    entry = runtime.RuntimeCatalogEntry(
        catalog_id="fixture-qwen",
        runtime_id="mentorflow-qwen",
        service_id="mentorflow.teacher-voice",
        executable_relative=executable.name,
        arguments=("teacher_voice_server.py", "--config", "runtime.json"),
        working_directory_relative=".",
        environment=dict(entry_environment or {"HF_HUB_OFFLINE": "1"}),
        executable_sha256=pinned_digest,
    )
    catalog = runtime.TrustedRuntimeCatalog([entry])
    plan = catalog.issue_plan(
        "fixture-qwen",
        runtime_root=root,
        port=18_892,
        startup_timeout_seconds=1.0,
        probe_timeout_seconds=0.1,
        idle_ttl_seconds=10.0,
        reaper_interval_seconds=1.0,
    )
    return catalog, plan


def supervisor_fixture(tmp_path: Path, **changes: Any):
    catalog, plan = trusted_plan(tmp_path)
    clock = FakeClock()
    launcher = FakeLauncher()
    tree_owner = FakeTreeOwner()
    listener = FakeListenerVerifier()
    nonce_counter = itertools.count(1)
    values: dict[str, Any] = {
        "catalog": catalog,
        "launcher": launcher,
        "identity_probe": MatchingProbe(launcher),
        "listener_verifier": listener,
        "process_tree_owner": tree_owner,
        "port_in_use": lambda _host, _port: False,
        "clock": clock,
        "sleep": clock.sleep,
        "secret_factory": lambda: "per-launch-secret",
        "nonce_factory": lambda: f"nonce-{next(nonce_counter)}",
        "platform_name": "nt",
    }
    values.update(changes)
    supervisor = runtime.LocalVoiceRuntimeSupervisor(plan, **values)
    # Tests drive idle reap deterministically and never create a daemon thread.
    supervisor.start_reaper = lambda: None  # type: ignore[method-assign]
    return supervisor, catalog, plan, launcher, tree_owner, listener, clock


def test_unattested_plan_is_rejected(tmp_path: Path):
    catalog, plan = trusted_plan(tmp_path)
    forged = replace(plan, _catalog_token=object())
    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        runtime.LocalVoiceRuntimeSupervisor(forged, catalog=catalog)
    assert raised.value.code == "VOICE_RUNTIME_PLAN_UNTRUSTED"

    # Copying the opaque token through dataclasses.replace is insufficient:
    # the catalog attestation binds every command and lifecycle field.
    command_tamper = replace(plan, arguments=("attacker-controlled.py",))
    with pytest.raises(runtime.LocalVoiceRuntimeError) as tampered:
        runtime.LocalVoiceRuntimeSupervisor(command_tamper, catalog=catalog)
    assert tampered.value.code == "VOICE_RUNTIME_PLAN_UNTRUSTED"

    supervisor = runtime.LocalVoiceRuntimeSupervisor(
        plan,
        catalog=catalog,
        listener_verifier=FakeListenerVerifier(),
        process_tree_owner=FakeTreeOwner(),
        port_in_use=lambda _host, _port: False,
        platform_name="nt",
    )
    supervisor.plan = command_tamper
    with pytest.raises(runtime.LocalVoiceRuntimeError) as late_tamper:
        supervisor.ensure_running()
    assert late_tamper.value.code == "VOICE_RUNTIME_PLAN_UNTRUSTED"


def test_catalog_rejects_script_executable_and_python_environment():
    with pytest.raises(runtime.LocalVoiceRuntimeError) as script:
        runtime.TrustedRuntimeCatalog(
            [
                runtime.RuntimeCatalogEntry(
                    catalog_id="bad",
                    runtime_id="bad",
                    service_id="bad",
                    executable_relative="start.ps1",
                    arguments=(),
                    working_directory_relative=".",
                )
            ]
        )
    assert script.value.code == "VOICE_RUNTIME_SCRIPT_EXECUTABLE_FORBIDDEN"

    with pytest.raises(runtime.LocalVoiceRuntimeError) as environment:
        runtime.TrustedRuntimeCatalog(
            [
                runtime.RuntimeCatalogEntry(
                    catalog_id="bad",
                    runtime_id="bad",
                    service_id="bad",
                    executable_relative="runtime.exe",
                    arguments=(),
                    working_directory_relative=".",
                    environment={"PYTHONPATH": "untrusted"},
                )
            ]
        )
    assert environment.value.code == "VOICE_RUNTIME_ENVIRONMENT_FORBIDDEN"

    with pytest.raises(runtime.LocalVoiceRuntimeError) as unknown_environment:
        runtime.TrustedRuntimeCatalog(
            [
                runtime.RuntimeCatalogEntry(
                    catalog_id="bad",
                    runtime_id="bad",
                    service_id="bad",
                    executable_relative="runtime.exe",
                    arguments=(),
                    working_directory_relative=".",
                    environment={"OPENAI_API_KEY": "must-not-enter-child"},
                    executable_sha256="0" * 64,
                )
            ]
        )
    assert unknown_environment.value.code == "VOICE_RUNTIME_ENVIRONMENT_FORBIDDEN"

    with pytest.raises(runtime.LocalVoiceRuntimeError) as missing_hash:
        runtime.TrustedRuntimeCatalog(
            [
                runtime.RuntimeCatalogEntry(
                    catalog_id="unhashed",
                    runtime_id="unhashed",
                    service_id="unhashed",
                    executable_relative="runtime.exe",
                    arguments=(),
                    working_directory_relative=".",
                )
            ]
        )
    assert missing_hash.value.code == "VOICE_RUNTIME_EXECUTABLE_HASH_REQUIRED"


def test_windows_requires_listener_verifier_and_tree_owner_before_launch(tmp_path: Path):
    catalog, plan = trusted_plan(tmp_path)
    launcher = FakeLauncher()
    tree_owner = FakeTreeOwner()
    with pytest.raises(runtime.LocalVoiceRuntimeError) as no_listener:
        runtime.LocalVoiceRuntimeSupervisor(
            plan,
            catalog=catalog,
            launcher=launcher,
            process_tree_owner=tree_owner,
            platform_name="nt",
        ).ensure_running()
    assert no_listener.value.code == "VOICE_RUNTIME_LISTENER_VERIFIER_REQUIRED"
    assert launcher.calls == 0

    with pytest.raises(runtime.LocalVoiceRuntimeError) as no_job:
        runtime.LocalVoiceRuntimeSupervisor(
            plan,
            catalog=catalog,
            launcher=launcher,
            listener_verifier=FakeListenerVerifier(),
            platform_name="nt",
        ).ensure_running()
    assert no_job.value.code == "VOICE_RUNTIME_WINDOWS_JOB_OWNER_REQUIRED"
    assert launcher.calls == 0


def test_first_use_single_flight_reuses_process_and_uses_fresh_nonce(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(tmp_path)
    barrier = threading.Barrier(5)
    results: list[dict[str, Any]] = []

    def run() -> None:
        barrier.wait()
        results.append(supervisor.ensure_running())

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    probe = supervisor._identity_probe
    assert isinstance(probe, MatchingProbe)
    assert launcher.calls == 1
    assert len(results) == 4
    assert sum(bool(item["started"]) for item in results) == 1
    assert len(set(probe.nonces)) == 4
    supervisor.shutdown()


def test_application_before_launch_verifier_runs_once_per_child_generation(tmp_path: Path):
    verification_calls: list[int] = []

    def verify_before_launch() -> None:
        verification_calls.append(len(verification_calls) + 1)

    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        before_launch=verify_before_launch,
    )
    first = supervisor.ensure_running()
    same_generation = supervisor.ensure_running()
    assert first["generation"] == same_generation["generation"]
    assert verification_calls == [1]
    assert launcher.calls == 1

    launcher.processes[0].return_code = 0
    replacement = supervisor.ensure_running()
    assert replacement["generation"] > first["generation"]
    assert verification_calls == [1, 2]
    assert launcher.calls == 2
    supervisor.shutdown()


def test_application_before_launch_verifier_rejects_tampered_replacement_before_spawn(
    tmp_path: Path,
):
    state = {"tampered": False}
    verification_calls = 0

    def verify_before_launch() -> None:
        nonlocal verification_calls
        verification_calls += 1
        if state["tampered"]:
            raise RuntimeError("verified engine bytes changed")

    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        before_launch=verify_before_launch,
    )
    supervisor.ensure_running()
    assert verification_calls == 1
    launcher.processes[0].return_code = 0
    state["tampered"] = True

    with pytest.raises(runtime.LocalVoiceRuntimeError) as blocked:
        supervisor.ensure_running()
    assert blocked.value.code == "VOICE_RUNTIME_PRELAUNCH_VERIFY_FAILED"
    assert verification_calls == 2
    assert launcher.calls == 1
    assert supervisor.status()["owned"] is False


def test_launch_attestation_is_generation_scoped_and_released_on_every_exit(tmp_path: Path):
    leases: list[FakeLaunchAttestation] = []

    def acquire_attestation() -> FakeLaunchAttestation:
        lease = FakeLaunchAttestation()
        leases.append(lease)
        return lease

    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        before_launch=acquire_attestation,
    )
    first = supervisor.ensure_running()
    repeated = supervisor.ensure_running()
    assert repeated["generation"] == first["generation"]
    assert len(leases) == 1
    assert leases[0].seal_calls == 1
    assert leases[0].assert_calls >= 3
    assert leases[0].release_calls == 0

    launcher.processes[0].return_code = 0
    replacement = supervisor.ensure_running()
    assert replacement["generation"] > first["generation"]
    assert len(leases) == 2
    assert leases[0].release_calls == 1
    assert leases[1].seal_calls == 1
    assert leases[1].release_calls == 0

    supervisor.shutdown()
    assert leases[1].release_calls == 1


def test_launch_attestation_pre_resume_failure_releases_without_spawn(tmp_path: Path):
    lease = FakeLaunchAttestation(fail_seal=True)
    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        before_launch=lambda: lease,
    )

    with pytest.raises(runtime.LocalVoiceRuntimeError) as blocked:
        supervisor.ensure_running()

    assert blocked.value.code == "VOICE_ENGINE_LAUNCH_ATTESTATION_CHANGED"
    assert lease.seal_calls == 1
    assert lease.assert_calls == 0
    assert lease.release_calls == 1
    assert launcher.calls == 0
    assert supervisor.status()["owned"] is False


def test_partial_launch_attestation_contract_is_released_before_rejection(tmp_path: Path):
    class PartialAttestation:
        def __init__(self) -> None:
            self.release_calls = 0

        def release(self) -> None:
            self.release_calls += 1

    candidate = PartialAttestation()
    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        before_launch=lambda: candidate,
    )

    with pytest.raises(runtime.LocalVoiceRuntimeError) as blocked:
        supervisor.ensure_running()

    assert blocked.value.code == "VOICE_RUNTIME_PRELAUNCH_VERIFY_FAILED"
    assert candidate.release_calls == 1
    assert launcher.calls == 0


def test_replayed_old_hmac_is_rejected(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(tmp_path)

    class ReplayProbe(MatchingProbe):
        cached: Mapping[str, Any] | None = None

        def probe(self, plan, *, nonce, timeout_seconds):  # type: ignore[no-untyped-def]
            if self.cached is None:
                self.cached = super().probe(plan, nonce=nonce, timeout_seconds=timeout_seconds)
            return self.cached

    supervisor._identity_probe = ReplayProbe(launcher)
    supervisor.ensure_running()
    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.ensure_running()
    assert raised.value.code == "VOICE_RUNTIME_IDENTITY_MISMATCH"
    assert supervisor.status()["owned"] is True
    supervisor.shutdown()


def test_wrong_pid_and_os_listener_mismatch_are_rejected_and_rolled_back(tmp_path: Path):
    supervisor, _catalog, plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)

    class WrongPidProbe:
        def probe(self, _plan, *, nonce, timeout_seconds):  # type: ignore[no-untyped-def]
            secret = launcher.environments[-1][runtime.INSTANCE_SECRET_ENV]
            return signed_health(plan, secret=secret, nonce=nonce, pid=99_999)

    supervisor._identity_probe = WrongPidProbe()
    with pytest.raises(runtime.LocalVoiceRuntimeError) as wrong_pid:
        supervisor.ensure_running()
    assert wrong_pid.value.code == "VOICE_RUNTIME_IDENTITY_MISMATCH"
    assert owner.terminate_calls == 1
    assert supervisor.status()["owned"] is False

    second, *_ = supervisor_fixture(tmp_path / "second", listener_verifier=FakeListenerVerifier(accepted=False))
    with pytest.raises(runtime.LocalVoiceRuntimeError) as listener_mismatch:
        second.ensure_running()
    assert listener_mismatch.value.code == "VOICE_RUNTIME_IDENTITY_MISMATCH"
    assert second.status()["owned"] is False


def test_preoccupied_or_reused_port_is_never_adopted(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        port_in_use=lambda _host, _port: True,
    )
    with pytest.raises(runtime.LocalVoiceRuntimeError) as occupied:
        supervisor.ensure_running()
    assert occupied.value.code == "VOICE_RUNTIME_PORT_OCCUPIED"
    assert launcher.calls == 0
    assert owner.terminate_calls == 0


def test_unexpected_probe_error_always_rolls_back_spawned_tree(tmp_path: Path):
    supervisor, _catalog, _plan, _launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)

    class BrokenProbe:
        def probe(self, plan, *, nonce, timeout_seconds):  # type: ignore[no-untyped-def]
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

    supervisor._identity_probe = BrokenProbe()
    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.ensure_running()
    assert raised.value.code == "VOICE_RUNTIME_PROBE_FAILED"
    assert owner.terminate_calls == 1
    assert supervisor.status()["owned"] is False


def test_clock_failure_after_launch_rolls_back_persisted_handle(tmp_path: Path):
    def broken_clock() -> float:
        raise RuntimeError("clock_failed_after_launch")

    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        clock=broken_clock,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.ensure_running()

    assert raised.value.code == "VOICE_RUNTIME_START_FAILED"
    assert launcher.calls == 1
    assert owner.terminate_calls == 1
    assert supervisor.status()["owned"] is False


def test_reaper_start_failure_is_part_of_startup_transaction(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)

    def broken_reaper() -> None:
        raise RuntimeError("reaper_thread_failed")

    supervisor.start_reaper = broken_reaper  # type: ignore[method-assign]
    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.ensure_running()

    assert raised.value.code == "VOICE_RUNTIME_START_FAILED"
    assert launcher.calls == 1
    assert owner.terminate_calls == 1
    assert supervisor.status()["owned"] is False


def test_startup_poll_failure_rolls_back_instead_of_losing_handle(tmp_path: Path):
    class PollFailLauncher(FakeLauncher):
        def launch(self, plan, *, environment):  # type: ignore[no-untyped-def]
            process = super().launch(plan, environment=environment)
            process.poll_failures = 1
            return process

    launcher = PollFailLauncher()
    supervisor, _catalog, _plan, _unused, owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        launcher=launcher,
        identity_probe=MatchingProbe(launcher),
    )

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.ensure_running()

    assert raised.value.code == "VOICE_RUNTIME_STARTUP_POLL_FAILED"
    assert owner.terminate_calls == 1
    assert supervisor.status()["owned"] is False


def test_unconfirmed_termination_keeps_handle_and_failed_state(tmp_path: Path):
    supervisor, _catalog, _plan, _launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    owner.fail_termination = True

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.shutdown()

    assert raised.value.code == "VOICE_RUNTIME_TERMINATION_UNCONFIRMED"
    status = supervisor.status()
    assert status["owned"] is True
    assert status["state"] == "failed"
    assert status["last_error"]["code"] == "VOICE_RUNTIME_TERMINATION_UNCONFIRMED"


def test_termination_exception_is_safe_when_retained_handle_confirms_exit(tmp_path: Path):
    supervisor, _catalog, _plan, _launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    owner.raise_after_termination = True

    stopped = supervisor.shutdown()

    assert stopped["stopped"] is True
    assert supervisor.status()["owned"] is False


def test_termination_poll_errors_still_apply_owner_strategy_and_finish(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    launcher.processes[0].poll_failures = 2

    stopped = supervisor.stop_owned(force=True)

    assert stopped["stopped"] is True
    assert owner.terminate_calls == 1
    assert supervisor.status()["state"] == "stopped"


def test_persistent_poll_error_never_clears_unconfirmed_handle_or_stays_stopping(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    process = launcher.processes[0]
    process.poll_always_raises = True
    owner.fail_termination = True

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.stop_owned(force=True)

    assert raised.value.code == "VOICE_RUNTIME_TERMINATION_UNCONFIRMED"
    status = supervisor.status()
    assert status["owned"] is True
    assert status["process_state"] == "unknown"
    assert status["state"] == "failed"
    assert owner.terminate_calls == 1


def test_stopping_rejects_new_lease_without_restarting(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    owner.terminate_entered = threading.Event()
    owner.allow_termination = threading.Event()
    stop_result: list[dict[str, Any]] = []

    stop_thread = threading.Thread(
        target=lambda: stop_result.append(supervisor.stop_owned(force=True))
    )
    stop_thread.start()
    assert owner.terminate_entered.wait(timeout=2)

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        with supervisor.synthesis_lease():
            pass
    assert raised.value.code == "VOICE_RUNTIME_STOPPING"

    owner.allow_termination.set()
    stop_thread.join(timeout=2)
    assert stop_result[0]["stopped"] is True
    assert launcher.calls == 1


def test_generation_tag_prevents_old_finally_from_releasing_new_lease(tmp_path: Path):
    supervisor, _catalog, _plan, _launcher, _owner, _listener, _clock = supervisor_fixture(tmp_path)
    old_token = supervisor._acquire_synthesis_lease()
    old_generation = old_token.generation
    supervisor.stop_owned(force=True)
    new_token = supervisor._acquire_synthesis_lease()
    assert new_token.generation > old_generation
    assert supervisor.status()["active_leases"] == 1

    supervisor._release_synthesis_lease(old_token)
    assert supervisor.status()["generation"] == new_token.generation
    assert supervisor.status()["active_leases"] == 1
    supervisor._release_synthesis_lease(new_token)
    assert supervisor.status()["active_leases"] == 0
    supervisor.shutdown()


def test_idle_ttl_never_stops_active_generation(tmp_path: Path):
    supervisor, _catalog, _plan, _launcher, owner, _listener, clock = supervisor_fixture(tmp_path)
    with supervisor.synthesis_lease():
        clock.value += 20
        assert supervisor.reap_if_idle() == {
            "stopped": False,
            "reason": "active_synthesis",
        }
    clock.value += 11
    assert supervisor.reap_if_idle()["reason"] == "idle_ttl"
    assert owner.terminate_calls == 1


def test_launch_environment_is_scrubbed_and_secret_is_not_health_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "untrusted-path")
    monkeypatch.setenv("PYTHONHOME", "untrusted-home")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-child")
    supervisor, _catalog, _plan, launcher, _owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    launched = launcher.environments[0]
    assert "PYTHONPATH" not in launched
    assert "PYTHONHOME" not in launched
    assert "OPENAI_API_KEY" not in launched
    assert launched["PYTHONNOUSERSITE"] == "1"
    assert launched[runtime.INSTANCE_SECRET_ENV] == "per-launch-secret"

    monkeypatch.setenv(runtime.INSTANCE_SECRET_ENV, "per-launch-secret")
    health = runtime.runtime_health_payload(
        runtime_id="mentorflow-qwen",
        service_id="mentorflow.teacher-voice",
        nonce="one-time-nonce",
        ready=True,
        listener_pid=launcher.processes[0].pid,
    )
    assert "instance_secret" not in health
    assert "instance_token_sha256" not in health
    assert health["proof"]
    supervisor.shutdown()


def test_prelaunch_hash_and_reparse_revalidation_fail_closed(tmp_path: Path, monkeypatch):
    digest = runtime.hashlib.sha256(b"fixture-runtime-binary").hexdigest()
    catalog, plan = trusted_plan(tmp_path, executable_sha256=digest)
    launcher = FakeLauncher()
    supervisor = runtime.LocalVoiceRuntimeSupervisor(
        plan,
        catalog=catalog,
        launcher=launcher,
        identity_probe=MatchingProbe(launcher),
        listener_verifier=FakeListenerVerifier(),
        process_tree_owner=FakeTreeOwner(),
        port_in_use=lambda _host, _port: False,
        platform_name="nt",
    )
    plan.executable.write_bytes(b"replaced-after-plan-issue")
    with pytest.raises(runtime.LocalVoiceRuntimeError) as changed:
        supervisor.ensure_running()
    assert changed.value.code == "VOICE_RUNTIME_EXECUTABLE_HASH_MISMATCH"
    assert launcher.calls == 0

    second, *_ = supervisor_fixture(tmp_path / "second")
    original_check = runtime._is_reparse_point
    monkeypatch.setattr(
        runtime,
        "_is_reparse_point",
        lambda path: path == second.plan.executable or original_check(path),
    )
    with pytest.raises(runtime.LocalVoiceRuntimeError) as reparse:
        second.ensure_running()
    assert reparse.value.code == "VOICE_RUNTIME_REPARSE_FORBIDDEN"


def test_shutdown_terminates_only_catalog_owned_tree(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    started = supervisor.ensure_running()
    stopped = supervisor.shutdown()
    assert stopped["stopped"] is True
    assert stopped["owned_pid"] == started["pid"]
    assert stopped["external_processes_terminated"] is False
    assert owner.terminate_calls == 1
    assert launcher.processes[0].terminate_calls == 1


def test_natural_child_exit_releases_retained_owner_handle(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    launcher.processes[0].return_code = 0

    status = supervisor.status()

    assert status["owned"] is False
    assert status["state"] == "stopped"
    assert owner.release_calls == 1
    assert owner.terminate_calls == 0


def test_attestation_cleanup_retries_before_owner_closes_authoritative_process_handle(
    tmp_path: Path,
):
    lease = FakeLaunchAttestation(release_failures=1)
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(
        tmp_path,
        before_launch=lambda: lease,
    )
    supervisor.ensure_running()
    launcher.processes[0].return_code = 0
    owner.invalidate_poll_on_release = True

    first = supervisor.status()

    assert first["owned"] is True
    assert first["state"] == "failed"
    assert first["process_state"] == "unknown"
    assert lease.release_calls == 1
    assert owner.release_calls == 0

    recovered = supervisor.status()

    assert recovered["owned"] is False
    assert recovered["state"] == "stopped"
    assert lease.release_calls == 2
    assert owner.release_calls == 1


def test_status_release_failure_is_failed_and_retained_for_retry(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    launcher.processes[0].return_code = 0
    owner.fail_release = True

    failed = supervisor.status()

    assert failed["owned"] is True
    assert failed["state"] == "failed"
    assert failed["process_state"] == "unknown"
    assert failed["last_error"]["code"] == "VOICE_RUNTIME_OWNER_RELEASE_FAILED"
    owner.fail_release = False
    recovered = supervisor.status()
    assert recovered["owned"] is False
    assert recovered["state"] == "stopped"
    assert owner.release_calls == 2


def test_stop_owned_natural_exit_release_failure_retains_for_retry(tmp_path: Path):
    supervisor, _catalog, _plan, launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    launcher.processes[0].return_code = 0
    owner.fail_release = True

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.stop_owned(force=True)

    assert raised.value.code == "VOICE_RUNTIME_OWNER_RELEASE_FAILED"
    assert supervisor.status()["owned"] is True
    owner.fail_release = False
    stopped = supervisor.stop_owned(force=True)
    assert stopped == {"stopped": False, "reason": "no_owned_process"}
    assert supervisor.status()["owned"] is False


def test_terminate_release_failure_retains_for_shutdown_retry(tmp_path: Path):
    supervisor, _catalog, _plan, _launcher, owner, _listener, _clock = supervisor_fixture(tmp_path)
    supervisor.ensure_running()
    owner.fail_release = True

    with pytest.raises(runtime.LocalVoiceRuntimeError) as raised:
        supervisor.shutdown()

    assert raised.value.code == "VOICE_RUNTIME_OWNER_RELEASE_FAILED"
    failed = supervisor.status()
    assert failed["owned"] is True
    assert failed["state"] == "failed"
    owner.fail_release = False
    stopped = supervisor.shutdown()
    assert stopped == {"stopped": False, "reason": "no_owned_process"}
    assert supervisor.status()["owned"] is False
