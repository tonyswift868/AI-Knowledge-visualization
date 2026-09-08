# Real desktop entry; reuse only the already installed dependency closure.
import ast
import os
from pathlib import Path
from PyInstaller.building.build_main import Analysis
root=Path(os.environ['MF_DELIVERY_PROJECT'])
work=Path(os.environ['MF_DELIVERY_BUILD'])
saved=ast.literal_eval(Path(os.environ['MF_DELIVERY_ANALYSIS']).read_text(encoding='utf-8'))
fields={definition[0]:value for definition,value in zip(Analysis._GUTS,saved)}
blocked={'scripts.measure_installed_login_gate','scripts.test_login_form_probe','desktop_update_uat_entry',
         'mentorflow_uat_build_authority','mentorflow_delivery_authority'}
pure=[]
for name,path,kind in fields['pure']:
    if name in blocked:continue
    if name.startswith('scripts.test_') or '.tests.' in name or name.endswith('.tests'):continue
    local=root/(name.replace('.','/')+'.py')
    package=root/name.replace('.','/')/'__init__.py'
    if local.is_file():path=str(local)
    elif package.is_file():path=str(package)
    pure.append((name,path,kind))
names={row[0] for row in pure}
extra=['desktop.gui_update_runtime','desktop.update_bridge','desktop.update_request','desktop.windows_identity',
       'desktop.windows_process_peer','desktop.windows_update_child','desktop.windows_update_pipe',
       'desktop.update_tree_security','scripts.install_product','scripts.install_receipt_state','scripts.runtime_path_budget',
       'scripts.update_health','scripts.update_source_reader','scripts.install_location']
for name in extra:
    if name not in names:pure.append((name,str(root/(name.replace('.','/')+'.py')),'PYMODULE'))
pure.append(('mentorflow_delivery_authority',str(work/'mentorflow_delivery_authority.py'),'PYMODULE'))
hooks=[row for row in fields['scripts'] if Path(row[1]).name.startswith('pyi_rth_')]
scripts=hooks+[('desktop_gui_entry',str(root/'desktop_gui_entry.py'),'PYSOURCE')]
pyz=PYZ(pure)
exe=EXE(pyz,scripts,[],exclude_binaries=True,name='MentorFlow',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,uac_admin=False,
    icon=str(root/'release/native/MentorFlow.ico'))
