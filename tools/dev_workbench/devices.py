"""Owned two-device harness. Observations are not sync or acceptance authority."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from desktop.test_device_profile import DEVICE_BASE, MARKER, PROFILE_CONTRACT, environment, plain, read_profile, validate_storage
from .targets import Refused, launch_exact, pinned_file, json_bytes, read_checked, sha256

SCENARIOS = ('SCENARIO_01_CLOUD_REHYDRATION', 'SCENARIO_02_MISSING_LOCAL_RESOURCE', 'SCENARIO_03_PORTABLE_PACK')
OBSERVATION_KEYS = {'account', 'server_workspace', 'last_sync_revision',
                    'cloud_entities_restored_count', 'missing_local_resources_count'}


@contextmanager
def directory_lease(path):
    """Deny rename/delete while traversing, including during bounded reset."""
    plain(path)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        for parent in reversed((Path(path), *Path(path).parents)):
            handle = create(str(parent), 0x80, 3, None, 3, 0x02200000, None)
            if handle == wintypes.HANDLE(-1).value: raise Refused('TEST_DEVICE_BUSY')
            handles.append(handle)
        plain(path)
        yield
    finally:
        for handle in reversed(handles): kernel.CloseHandle(handle)


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


class Devices:
    def __init__(self, base=DEVICE_BASE):
        self.base = Path(base).absolute()
        allowed = (Path('E:/Codex/TestDevices'), Path('E:/Codex/Temp'))
        if self.base.drive.upper() == 'D:':
            from scripts.test_storage_policy import require_test_path
            try: require_test_path(Path(base))
            except ValueError: raise Refused('TEST_DEVICE_UNOWNED_ROOT') from None
            allowed = (self.base.parent,)
        if '..' in self.base.parts or not any(self.base != root and self.base.is_relative_to(root) for root in allowed):
            raise Refused('TEST_DEVICE_UNOWNED_ROOT')
        self.processes = {}
        self.running_targets = {}
        self._mutex = threading.RLock()

    def root(self, device):
        if device not in ('A', 'B', 'UserTest'): raise Refused('TEST_DEVICE_UNKNOWN')
        return self.base / device

    def _owners(self):
        path = self.base / '.owners.json'
        if not path.exists(): return {}
        plain(path)
        if path.stat().st_size > 2048: raise Refused('TEST_DEVICE_UNOWNED_ROOT')
        value = json_bytes(path.read_bytes())
        if not isinstance(value, dict) or not set(value) <= {'A', 'B', 'UserTest'}:
            raise Refused('TEST_DEVICE_UNOWNED_ROOT')
        return value

    def owned(self, device):
        root = self.root(device)
        value = read_profile(root, base=self.base)
        if self._owners().get(device) != value['owner']: raise Refused('TEST_DEVICE_UNOWNED_ROOT')
        return value

    def prepare(self, device):
        with self._mutex:
            root = self.root(device)
            # Never claim or wipe an existing directory without our creation record.
            if root.exists(): return self.owned(device)
            for parent in reversed(self.base.parents):
                if not parent.exists(): parent.mkdir()
                plain(parent)
            self.base.mkdir(exist_ok=True)
            plain(self.base)
            with directory_lease(self.base):
                owners = self._owners()
                if device in owners: raise Refused('TEST_DEVICE_UNOWNED_ROOT')
                root.mkdir()
                value = dict(contract=PROFILE_CONTRACT, device=device, root=str(root),
                             owner=uuid.uuid4().hex, generation=uuid.uuid4().hex)
                atomic_json(root / MARKER, value)
                (root / '.running').write_bytes(b'0')
                self._directories(root)
                owners[device] = value['owner']; atomic_json(self.base / '.owners.json', owners)
                return value

    @staticmethod
    def _directories(root):
        for name in ('vault/prelaunch/data', 'preferences/system', 'cache', 'temp', 'profile/Local', 'profile/Roaming'):
            (root / name).mkdir(parents=True, exist_ok=True)

    def busy(self, device):
        import msvcrt
        process = self.processes.get(device)
        if process is not None and process.poll() is None: return True
        if not self.root(device).exists(): return False
        self.owned(device)
        try:
            with plain(self.root(device) / '.running').open('r+b') as lock:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        except OSError: return True

    @staticmethod
    def compatible(manifest, qualification, paths):
        try:
            if not manifest or manifest['status'] != 'CURRENT' or not qualification:
                return False
            if any(qualification.get(key) != manifest[field] for key, field in (
                    ('build_source_sha', 'source_sha'), ('build_source_tree', 'source_tree'),
                    ('artifact_sha256', 'artifact_sha256'), ('build_id', 'build_id'), ('test_id', 'test_id'))):
                return False
            if manifest['launch_type'] != 'DESKTOP_EXE' or manifest['environment'] not in ('TEST', 'STAGING'): return False
            raw = read_checked(manifest['identity_evidence_path'], paths)
            from desktop.test_device_configuration import CONTRACT
            identity = json_bytes(raw)
            legacy = identity.get('test_device_configuration', {})
            contract = identity.get('runtime_contract', legacy.get('contract') if isinstance(legacy,dict) else None)
            if not (sha256(raw) == manifest['identity_evidence_sha256'] and contract == CONTRACT and
                    identity.get('test_id',manifest['test_id']) == manifest['test_id'] and
                    identity.get('device_profile_contract') == PROFILE_CONTRACT and
                    identity.get('packaged_entrypoint') == 'desktop_entry.py'): return False
            from .runtime_lease import verified_runtime
            with verified_runtime(manifest, paths): return True
        except (KeyError, ValueError, OSError, Refused, AttributeError): return False

    @staticmethod
    def source_supported(source, build_sha=None):
        try:
            adapter = source.git('show', (build_sha or source.sha) + ':desktop/test_device_profile.py')
            entry = source.git('show', (build_sha or source.sha) + ':desktop/main.py')
            frozen = source.git('show', (build_sha or source.sha) + ':desktop_entry.py')
            return ("PROFILE_CONTRACT = '" + PROFILE_CONTRACT + "'" in adapter
                    and '_configure_test_device()' in entry
                    and 'from desktop.test_device_configuration import apply_from_environment' in frozen)
        except Refused: return False

    def reset(self, device):
        import msvcrt
        with self._mutex, directory_lease(self.root(device)):
            value = self.owned(device); root = self.root(device)
            if self.busy(device): raise Refused('TEST_DEVICE_BUSY')
            with (plain(root / '.running')).open('r+b') as lock:
                try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError: raise Refused('TEST_DEVICE_BUSY') from None
                try:
                    # Reject links anywhere before removing any contents.
                    def audit(folder):
                        with directory_lease(folder):
                            for child in folder.iterdir():
                                plain(child)
                                if child.is_dir(): audit(child)
                    audit(root)
                    def clear(folder):
                        with directory_lease(folder):
                            for child in folder.iterdir():
                                if folder == root and child.name in (MARKER, '.running'): continue
                                plain(child)
                                if child.is_dir():
                                    clear(child)
                                    # rmdir removes the entry, never traverses a replaced junction.
                                    child.rmdir()
                                else: child.unlink()
                    clear(root)
                    self._directories(root)
                    value['generation'] = uuid.uuid4().hex
                    atomic_json(root / MARKER, value)
                finally:
                    lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            # Rotate credential namespace. Never enumerate or delete real Locker entries.
            return value

    def target(self, device, manifest, scenario=SCENARIOS[0]):
        if scenario not in SCENARIOS[:2]: raise Refused('TEST_DEVICE_SCENARIO_UNAVAILABLE')
        value = self.owned(device)
        return dict(target_id='DEVICE_' + device + '_TARGET', device=device, scenario=scenario,
                    scope='DERIVED_NOT_LAUNCH_AUTHORITY', candidate_status=(manifest or {}).get('status'),
                    profile_contract=PROFILE_CONTRACT, root=str(self.root(device)), generation=value['generation'],
                    source_sha=(manifest or {}).get('source_sha'), source_tree=(manifest or {}).get('source_tree'),
                    build_source_sha=(manifest or {}).get('source_sha'), build_source_tree=(manifest or {}).get('source_tree'),
                    test_id=(manifest or {}).get('test_id'),
                    artifact_sha256=(manifest or {}).get('artifact_sha256'), build_id=(manifest or {}).get('build_id'))

    def launch(self, device, selected, source, paths, *, spawn=subprocess.Popen, cancelled=lambda: False,
               scenario=SCENARIOS[0]):
        from .authority import load_view
        with self._mutex:
            if scenario not in SCENARIOS[:2]: raise Refused('TEST_DEVICE_SCENARIO_UNAVAILABLE')
            value = self.prepare(device)
            if self.busy(device): raise Refused('TEST_DEVICE_BUSY')
            fresh = load_view(source, paths).get('active_target')
            if not fresh:
                raise Refused('TEST_DEVICE_INTEGRATED_CANDIDATE_REQUIRED')
            if not self.source_supported(source, fresh['source_sha']): raise Refused('TEST_DEVICE_CONTRACT_REQUIRED')
            if fresh.get('launch_type') != 'DESKTOP_EXE' or fresh.get('environment') not in ('TEST', 'STAGING'):
                raise Refused('TEST_DEVICE_CONTRACT_REQUIRED')
            other = 'B' if device == 'A' else 'A'
            if self.busy(other):
                running = self.running_targets.get(other)
                if not running or any(running[key] != fresh[key] for key in ('source_sha','source_tree','artifact_sha256','build_id')):
                    raise Refused('TEST_DEVICE_PAIR_BUILD_CHANGED')
            root = self.root(device)
            with pinned_file(root / MARKER):
                validate_storage(root)
                target = self.target(device, fresh, scenario)
                env = {key: val for key, val in os.environ.items() if key.upper() in {
                    'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'USERNAME', 'USERDOMAIN',
                    'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)'}}
                system = Path(os.environ['SystemRoot'])
                env.update(PATH=str(system / 'System32') + ';' + str(system))
                env.update(environment(root, value))
                env['MENTORFLOW_TEST_ARTIFACT_SHA256'] = fresh['artifact_sha256']
                env['MENTORFLOW_TEST_TARGET_ID'] = fresh['test_id']
                env['MENTORFLOW_TEST_SCENARIO'] = scenario
                # Verification and leases are still exclusively owned by launch_exact.
                process = launch_exact(selected, fresh, source.tree_for, paths, spawn=spawn, cancelled=cancelled,
                                       device_environment=env, device_contract=PROFILE_CONTRACT,
                                       authority_check=source.assert_current)
                self.processes[device] = process
                self.running_targets[device] = target
                atomic_json(root / '.target.json', target)  # Disposable, never read as launch authority.
                return process.pid

    def observation(self, device, manifest, scenario=SCENARIOS[0]):
        target = self.target(device, manifest, scenario)
        path = self.root(device) / 'observation.json'
        result = dict(target=target, scope='LOCAL_OBSERVATION_NOT_ACCEPTANCE', values={})
        if not path.exists(): return result
        try:
            plain(path)
            if path.stat().st_size > 8192: return result
            with pinned_file(path) as stream: value = json_bytes(stream.read(8193))
            if value.get('schema') == 'mentorflow.sync-observation.v1':
                if (value.get('test_id') != manifest['test_id'] or value.get('scenario') != scenario
                        or value.get('generation') != target['generation']
                        or value.get('artifact_sha256') != manifest['artifact_sha256']
                        or value.get('source') != 'SERVER_CONFIRMED'):
                    return result
                value = dict(target=target, scenario=scenario,
                             values={key: value.get(key) for key in OBSERVATION_KEYS})
            if (value.get('target') != target or value.get('scenario') != scenario
                    or set(value) != {'target', 'scenario', 'values'}): return result
            fields = value['values']
            if not isinstance(fields, dict) or not set(fields) <= OBSERVATION_KEYS: return result
            for key, val in fields.items():
                if key.endswith('_count'):
                    if type(val) is not int or not 0 <= val <= 10**9: return result
                elif not isinstance(val, str) or len(val) > 160: return result
            result.update(values=fields, scenario=value['scenario'])
        except (ValueError, OSError, Refused, TypeError): pass
        return result
