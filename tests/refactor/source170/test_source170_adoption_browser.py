"""Exercise NewPlan's source adoption UI using a synthetic bridge only."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.refactor.plan37.test_plan37_browser_contract import browser


def build_fixture(root):
    repo = Path(__file__).resolve().parents[3]
    here = Path(__file__).parent
    deps = Path("D:/MentorFlow/.local/web-deps")
    node = Path("D:/MentorFlow/.local/node/node.exe")
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    (root / "entry.tsx").write_text('import ' + json.dumps((repo / "refactor/learning/web/src/theme.css").as_posix()) + ';await import(' + json.dumps((here / "source170_browser.tsx").as_posix()) + ');', encoding="utf-8")
    config = '''import path from "node:path"
import { pathToFileURL } from "node:url"
const deps = DEPS
const { default: react } = await import(pathToFileURL(path.join(deps, "node_modules/@vitejs/plugin-react/dist/index.js")))
export default { root: ROOT, base: "./", resolve: { alias: [{find:"../bridge",replacement:BRIDGE}] }, plugins:[react(), {name:"deps",enforce:"pre",async resolveId(source,importer,options) { if (!importer || source.startsWith(".") || source.startsWith("/") || source.includes(":")) return null; if (importer.includes("node_modules")) return null; return this.resolve(source,path.join(deps,"package.json"),{...options,skipSelf:true}) }}],build:{outDir:"dist",emptyOutDir:true} }
'''.replace("DEPS", json.dumps(deps.as_posix())).replace("ROOT", json.dumps(root.as_posix())).replace("BRIDGE", json.dumps((here / "source170_bridge.ts").as_posix()))
    (root / "vite.config.mjs").write_text(config, encoding="utf-8")
    result = subprocess.run([str(node), str(deps / "node_modules/vite/bin/vite.js"), "build", "--config", str(root / "vite.config.mjs")], env=dict(os.environ, MENTORFLOW_WEB_DEPS=deps.as_posix()), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_source170_user_materials_are_adopted_and_can_continue(tmp_path):
    build_fixture(tmp_path)
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "plan37/test_plan37_browser_contract.py"), str(tmp_path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    audit = json.loads((tmp_path / "browser-result.json").read_text(encoding="utf-8"))
    assert audit["ok"], audit
