import os,sys,json
from pathlib import Path
R=Path(r"E:/Codex/Data/worktrees/2735/MentorFlow");D=Path(__file__).parent
assert D == Path(r'D:/CodexScratch/MentorFlow/MF-MAIN3-A-GATE1-FINAL-INTEGRATION-20260916-36/A'), 'Copy this audit runner to its assigned scratch before execution'
sys.path[:0]=[str(R),str(R/'backend')]
os.chdir(R)
os.environ.update(PYTHONDONTWRITEBYTECODE='1',QT_QPA_PLATFORM='offscreen',TEMP=str(D/'tests'),TMP=str(D/'tests'),MENTORFLOW_USER_DATA_ROOT=str(D/'tests/user-data'),MENTORFLOW_DB=str(D/'tests/bootstrap.db'),MENTORFLOW_ORM_DB=str(D/'tests/bootstrap-orm.db'),MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY='1',MENTORFLOW_TEST_SCRATCH_ROOT=str(D))
from scripts import test_storage_policy as policy
# Test-process-only relocation of the existing named assignment. No shipped
# policy bytes change; all canonicalization/isolation/deletion checks still run.
policy.ASSIGNED_ROOT=D
policy.AUTHORIZED_ROOTS=frozenset((*policy.AUTHORIZED_ROOTS,D))
import pytest
class Assignment:
 @pytest.fixture(autouse=True)
 def assigned(self,request,monkeypatch):
  selected=policy.GUI_AB_ROOT if request.node.name=='test_negative_profile_is_only_explicit_dispatch33_device' else D
  monkeypatch.setenv(policy.SCRATCH_ENV,str(selected))
  monkeypatch.setenv(policy.TEST_MODE_ENV,'1')
args=['-q','-o','cache_dir='+str(D/'cache/pytest'),'--basetemp='+str(D/'tests/run2'),'--junitxml='+str(D/'focused2.xml')]
args+=['desktop/tests/test_upload_source_paths.py','backend/tests/test_material_long_account_paths.py','backend/tests/test_material_path_convergence.py','tools/dev_workbench/test_dispatch33_storage.py','tools/dev_workbench/test_ephemeral_storage.py','desktop/tests/test_stable_account_storage.py','desktop/tests/test_product_plan_projection.py','backend/tests/test_review_campaign_discovery.py','desktop/tests/test_rehydration_integration.py','backend/tests/test_product_sync_regression.py','desktop/tests/test_product_sync_bridge.py']
args+=['tools/dev_workbench/test_devices.py::'+n for n in ['test_durable_roots_reset_only_one_device','test_protected_and_broad_roots_refused','test_device_environment_cannot_become_a_real_migration_process','test_existing_unowned_root_and_forged_marker_refused','test_junction_and_hardlink_are_not_launchable','test_restart_validates_long_nested_payload_without_weakening_links']]
if __name__ == '__main__':
 sys.exit(pytest.main(args,plugins=[Assignment()]))
