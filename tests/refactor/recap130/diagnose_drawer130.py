"""Compare the failing existing drawer assertion with the unchanged baseline.

No assertion is removed or weakened. Only capture its DOM bounds on failure.
"""
import json, os, subprocess, sys, types
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))


def probe130(root):
    source=(REPO/'tests/refactor/teacher77/test_teacher77_drawer_browser.py').read_text(encoding='utf-8')
    marker="        assert js('(()=>{const composer=document.querySelector(\".class-main .composer\").getBoundingClientRect();"
    start=source.index(marker)
    capture='''        evidence=json.loads(js('JSON.stringify((()=>{const composer=document.querySelector(".class-main .composer").getBoundingClientRect(),send=[...document.querySelectorAll(".class-main .composer button")].find(b=>b.textContent.includes("发送")).getBoundingClientRect(),rail=document.querySelector(".teacher-rail").getBoundingClientRect();return {width:innerWidth,height:innerHeight,composer:{bottom:composer.bottom,right:composer.right},send:{bottom:send.bottom,right:send.right},rail:{left:rail.left}}})())'))
        (root/'bounds130.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        view.grab().save(str(root/'bounds130.png'))
'''
    source=source[:start]+capture+source[start:]
    module=types.ModuleType('teacher77_probe130');module.__file__=str(REPO/'tests/refactor/teacher77/test_teacher77_drawer_browser.py')
    exec(compile(source,module.__file__,'exec'),module.__dict__)
    module.browser(root)


def baseline_fixture130(root):
    from tests.refactor.teacher77.test_teacher77_drawer_browser import build_fixture
    from tests.refactor.recap130.test_recap130_browser import baseline130
    build_fixture(root)
    classroom=baseline130('refactor/learning/web/src/pages/Classroom.tsx',root)
    hook=baseline130('refactor/learning/web/src/useRoundScroll.ts',root)
    web=REPO/'refactor/learning/web';bridge=REPO/'tests/refactor/teacher77/teacher77_bridge.ts'
    aliases=[dict(find='./pages/Classroom',replacement=classroom.as_posix()),dict(find=(web/'src/useRoundScroll').as_posix(),replacement=hook.as_posix())]
    aliases.extend(dict(find=name,replacement=bridge.as_posix()) for name in ('../bridge','./bridge',(web/'src/bridge').as_posix()))
    config='import base from '+json.dumps((web/'vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',resolve:{alias:'+json.dumps(aliases)+'},build:{outDir:"dist",emptyOutDir:true,target:"esnext"}}'
    (root/'vite.config.mjs').write_text(config,encoding='utf-8')
    deps=Path('D:/MentorFlow/.local/web-deps')
    subprocess.run(['D:/MentorFlow/.local/node/node.exe',str(deps/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=dict(os.environ,MENTORFLOW_WEB_DEPS=deps.as_posix()),check=True,capture_output=True,timeout=90)


if __name__=='__main__':
    root=Path(sys.argv[2])
    if sys.argv[1]=='--build-baseline':baseline_fixture130(root)
    else:probe130(root)
