# Explicit TEST-ONLY repack using the already qualified dependency closure.
# All absolute paths below are build inputs, never runtime location authority.
import ast
import os
from pathlib import Path
from PyInstaller.building.build_main import Analysis
root = Path(os.environ['MF_UAT_PROJECT'])
work = Path(os.environ['MF_UAT_BUILD'])
saved = ast.literal_eval(Path(os.environ['MF_UAT_ANALYSIS_TOC']).read_text(encoding='utf-8'))
fields = {definition[0]: value for definition, value in zip(Analysis._GUTS, saved)}
pure = list(fields['pure'])
names = {row[0] for row in pure}
for name in ('desktop.update_tree_security', 'scripts.runtime_path_budget', 'desktop.uat_health_contract'):
    if name not in names:
        pure.append((name, str(root/(name.replace('.', '/')+'.py')), 'PYMODULE'))
pure.append(('mentorflow_uat_build_authority', str(work/'mentorflow_uat_build_authority.py'), 'PYMODULE'))
pyz = PYZ(pure)
exe = EXE(pyz, fields['scripts'], [], exclude_binaries=True, name='MentorFlow',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, uac_admin=False, icon=str(root/'release/native/MentorFlow.ico'))
