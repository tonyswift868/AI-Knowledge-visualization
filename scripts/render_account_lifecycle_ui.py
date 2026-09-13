"""Render the existing 03 forms with the pinned Keycloak FreeMarker dependency.

The shared offline renderer supplies layout/messages, never identity behavior.
The output is a build input; credentials and network are owned by the native bridge.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def render(keycloak,jdk,output):
    output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    libs=keycloak/'lib/lib/main'
    theme=libs/'org.keycloak.keycloak-themes-26.7.3.jar'
    assert theme.is_file(),'Exact Keycloak 26.7.3 required'
    with tempfile.TemporaryDirectory(prefix='lifecycle-render-',dir=output.parent) as scratch:
        temp=Path(scratch)
        with zipfile.ZipFile(theme) as archive:
            for item in archive.infolist():
                if item.filename.startswith('theme/') and not item.is_dir():
                    target=temp/item.filename
                    assert target.resolve().is_relative_to(temp.resolve())
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(item))
        source=ROOT/'tests/theme/ResetPasswordThemeFixture.java'
        subprocess.run([str(jdk/'bin/javac.exe'),'-encoding','UTF-8','-cp',str(libs/'*'),'-d',str(temp),str(source)],check=True)
        subprocess.run([str(jdk/'bin/java.exe'),'-cp',str(temp)+';'+str(libs/'*'),'ResetPasswordThemeFixture',str(ROOT),str(temp/'theme'),str(temp/'rendered')],check=True)
        html=(temp/'rendered/lifecycle.html').read_text(encoding='utf-8').replace('"/resources/','"resources/')
        html=html.replace('</body>','<script src="qrc:///qtwebchannel/qwebchannel.js"></script><script type="module" src="resources/js/account-lifecycle-native.js"></script></body>')
        (output/'lifecycle.html').write_text(html,encoding='utf-8')
    assets=ROOT/'deploy/enterprise/themes/mentorflow-desktop-v1/login/resources'
    shutil.copytree(assets,output/'resources',dirs_exist_ok=True)
    inputs=[ROOT/'tests/theme/ResetPasswordThemeFixture.java',*sorted((ROOT/'deploy/enterprise/themes/mentorflow-desktop-v1/login').rglob('*'))]
    manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs if p.is_file()}
    (output/'source-inputs.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('EXISTING_03_LIFECYCLE_UI_RENDERED')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--keycloak',type=Path,required=True);p.add_argument('--jdk',type=Path,required=True)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/account-lifecycle-ui');a=p.parse_args();render(a.keycloak,a.jdk,a.output)
