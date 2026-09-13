import pytest
from tools.dev_workbench import runtime_lease

@pytest.fixture(autouse=True)
def isolated_runtime_authority(tmp_path, monkeypatch):
    # Tests never read, modify or publish the real acceptance environment.
    monkeypatch.setattr(runtime_lease,'AUTHORITY',tmp_path/'coordination')
