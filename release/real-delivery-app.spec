# Real desktop entry; reuse only the already installed dependency closure.
import ast
import os
from pathlib import Path
from PyInstaller.building.build_main import Analysis
root=Path(os.environ['MF_DELIVERY_PROJECT'])
work=Path(os.environ['MF_DELIVERY_BUILD'])
if os.environ.get('MF_DELIVERY_ANALYSIS'):
    saved=ast.literal_eval(Path(os.environ['MF_DELIVERY_ANALYSIS']).read_text(encoding='utf-8'))
    fields={definition[0]:value for definition,value in zip(Analysis._GUTS,saved)}
else:
    # Frozen 07's PYZ/EXE TOCs retain the dependency closure even if an older
    # Analysis scratch was retired. All project modules are rebound below.
    pyz_seed=ast.literal_eval(Path(os.environ['MF_DELIVERY_SEED_PYZ_TOC']).read_text(encoding='utf-8'))
    exe_seed=ast.literal_eval(Path(os.environ['MF_DELIVERY_SEED_EXE_TOC']).read_text(encoding='utf-8'))
    def script_rows(value):
        if isinstance(value,(list,tuple)):
            if len(value)==3 and all(isinstance(v,str) for v in value) and value[2]=='PYSOURCE':
                yield value
            else:
                for child in value:yield from script_rows(child)
    fields={'pure':pyz_seed[1], 'scripts':list(script_rows(exe_seed))}
    if not fields['pure'] or not fields['scripts']:raise ValueError('empty frozen dependency closure')
seed_pyz=os.environ.get('MF_DELIVERY_SEED_PYZ_TOC')
if seed_pyz:
    # Preserve the accepted desktop's actual module closure, not merely the
    # older Analysis used to obtain the installed toolchain/runtime hooks.
    fields['pure']=ast.literal_eval(Path(seed_pyz).read_text(encoding='utf-8'))[1]
runtime_analysis=os.environ.get('MF_DELIVERY_RUNTIME_ANALYSIS')
runtime_pure=[]
if runtime_analysis:
    runtime_saved=ast.literal_eval(Path(runtime_analysis).read_text(encoding='utf-8'))
    runtime_fields={definition[0]:value for definition,value in zip(Analysis._GUTS,runtime_saved)}
    runtime_pure=runtime_fields['pure']
blocked={'scripts.measure_installed_login_gate','scripts.test_login_form_probe','desktop_update_uat_entry',
         'mentorflow_uat_build_authority','mentorflow_delivery_authority'}
pure=[]
seen=set()
for name,path,kind in [*fields['pure'],*runtime_pure]:
    if name in blocked:continue
    if name in seen:continue
    if name.startswith('scripts.test_') or '.tests.' in name or name.endswith('.tests'):continue
    local=root/(name.replace('.','/')+'.py')
    package=root/name.replace('.','/')/'__init__.py'
    if local.is_file():path=str(local)
    elif package.is_file():path=str(package)
    pure.append((name,path,kind))
    seen.add(name)
names={row[0] for row in pure}
extra=['desktop.native_auth','desktop.gui_update_runtime','desktop.update_bridge','desktop.update_request','desktop.windows_identity',
       'desktop.windows_process_peer','desktop.windows_update_child','desktop.windows_update_pipe',
       'desktop.update_tree_security','scripts.install_product','scripts.install_receipt_state','scripts.runtime_path_budget',
       'scripts.update_health','scripts.update_source_reader','scripts.install_location','scripts.per_user_install',
       'scripts.per_user_update_core','scripts.per_user_runtime_session','scripts.per_user_launcher',
       'desktop.update_controller','desktop.update_lifecycle','desktop.update_session','desktop.update_panel']
for name in extra:
    if name not in names:pure.append((name,str(root/(name.replace('.','/')+'.py')),'PYMODULE'))
# The seed contains old app.* paths outside root; bind only this delivery's
# authentication modules explicitly, including the newly added migration module.
import sys
sys.path.insert(0, str(root))
from scripts.build_e108_user_candidate import auth_source_modules
auth_sources = auth_source_modules()
pure = [row for row in pure if row[0] not in auth_sources]
pure.extend((name, str(path), 'PYMODULE') for name, path in auth_sources.items())
pure.append(('mentorflow_delivery_authority',str(work/'mentorflow_delivery_authority.py'),'PYMODULE'))
hooks=[row for row in fields['scripts'] if Path(row[1]).name.startswith('pyi_rth_')]
scripts=hooks+[('desktop_gui_entry',str(root/'desktop_gui_entry.py'),'PYSOURCE')]
pyz=PYZ(pure)
exe=EXE(pyz,scripts,[],exclude_binaries=True,name='MentorFlow',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,uac_admin=False,
    icon=str(root/'release/native/MentorFlow.ico'))
