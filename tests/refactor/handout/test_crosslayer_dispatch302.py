from pathlib import Path

from scripts.handout.crosslayer_dispatch import extract


def put(root: Path, name: str, text: str):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_dispatch302_classroom_registry_and_getattr_are_finite(tmp_path):
    put(tmp_path, "refactor/learning/app/classroom.py", '''
class Classroom:
    def reply(self, course, state, ctx, text): pass
    def recall_reveal(self, course, state, ctx, qid, answer): pass
    def start_test(self, course, state, ctx, kind): pass
    def start_check(self, course, state, ctx, kind): pass
    def perform(self, plan_id, action, ctx):
        return self.run(plan_id, lambda c,s,x: getattr(self, action)(c,s,x), ctx)
    def _wait(self, course, state, action):
        if action not in ('start_test', 'start_check'):
            raise AssertionError()
    def _resume_materials(self, course, state, ctx):
        getattr(self, wait['action'])(course, state, ctx)
    def retry(self, course, state, ctx):
        getattr(self, failure['action'])(course, state, ctx)
def register(bridge):
    def invoke(action, params): pass
    def reveal(params): return invoke('recall_reveal', params)
    methods = {
        'classroom.reply': lambda p,c: invoke('reply',p),
        'class.recall_reveal': reveal,
        'classroom.test': lambda p,c: invoke('start_test',p),
    }
''')
    result = extract(tmp_path)
    edges = {(r["name"], r["target"]["function"]) for r in result["connections"]
             if r["source"]["function"].startswith("Classroom.perform")}
    assert ("classroom.reply", "Classroom.reply") in edges
    assert ("class.recall_reveal", "Classroom.recall_reveal") in edges
    assert ("classroom.test", "Classroom.start_test") in edges
    waits = {r["name"] for r in result["connections"] if r["name"].startswith("material_wait.action=")}
    assert waits == {"material_wait.action=start_check", "material_wait.action=start_test"}
    assert {r["name"] for r in result["dynamic"]} == {"material_wait.action", "failure.action"}


def test_dispatch302_failure_replay_uses_invoke_action_not_bridge_alias(tmp_path):
    put(tmp_path, "refactor/learning/app/classroom.py", '''
class Classroom:
    def start_test(self, course, state, ctx): pass
    def perform(self, action):
        self._local.action = (action, [], "request")
    def run(self, state):
        state["failure"] = dict(action=action[0], args=action[1])
    def retry(self, state):
        return getattr(self, failure["action"])(state)
def register(bridge):
    def invoke(action, params): return room.perform(action)
    methods = {
        "classroom.test": lambda p,c: invoke("start_test",p),
    }
''')
    result = extract(tmp_path)
    failure = next(row for row in result["connections"] if row["name"] == "failure.action=start_test")
    assert failure["target"]["function"] == "Classroom.start_test"
    assert any("classroom.test" in step["function"] for step in failure["via"])
    assert any("invoke(start_test)" == step["function"] for step in failure["via"])
    assert not any(row["name"] == "failure.action=classroom.test" for row in result["connections"])


def test_dispatch302_qapplication_updater_and_operation_branch(tmp_path):
    put(tmp_path, "refactor/learning/app/handlers.py", '''
def update_action(action):
    updater = getattr(QApplication.instance(), "_mentorflow_updates", None)
    value = updater.snapshot() if action == "status" else updater.run(action)
def register(bridge):
    for action in ("status", "install"):
        bridge.register("update." + action, lambda p,c: update_action(action))
''')
    put(tmp_path, "refactor/desktop/account_shell_main.py", '''
def main():
    app = QApplication([])
    app._mentorflow_updates = DesktopUpdates(app)
''')
    put(tmp_path, "refactor/desktop/updates.py", '''
class DesktopUpdates:
    def snapshot(self): pass
    def run(self, action):
        def work():
            operation = self.core.repair_installation if self.state.get('shell') == 'repair' else self.core.update_to_latest
            result = operation()
''')
    put(tmp_path, "refactor/desktop/delivery/per_user_update_core.py", '''
def repair_installation(): pass
def update_to_latest(): pass
''')
    put(tmp_path, "refactor/desktop/delivery/startup_integrity.py", "")
    result = extract(tmp_path)
    pairs = {(r["name"], r["target"]["function"]) for r in result["connections"]}
    assert ("update.status", "DesktopUpdates.snapshot") in pairs
    assert ("update.install", "DesktopUpdates.run") in pairs
    assert ("install operation (state.get('shell') == 'repair')", "repair_installation") in pairs
    assert ("install operation (not (state.get('shell') == 'repair'))", "update_to_latest") in pairs


def test_dispatch302_literal_getattr_and_cli_action_are_resolved(tmp_path):
    put(tmp_path, "refactor/learning/app/handlers.py", '''
def model_ready(current, recovered=False):
    action = getattr(current, 'on_network_restored' if recovered else 'start_model_auto_check', None)
    action()
''')
    put(tmp_path, "refactor/learning/app/services.py", '''
class AppServices:
    def on_network_restored(self): pass
    def start_model_auto_check(self): pass
''')
    result = extract(tmp_path)
    pairs = {(r["name"], r["target"]["function"]) for r in result["connections"]}
    assert ("model_ready.on_network_restored", "AppServices.on_network_restored") in pairs
    assert ("model_ready.start_model_auto_check", "AppServices.start_model_auto_check") in pairs
    # CLI action dispatch is owned by the process extractor; do not infer a
    # core call merely from a main() action guard.
    assert not any(name.startswith("--action ") for name, _target in pairs)


def test_dispatch302_app_route_table_connects_page_navigation_to_component(tmp_path):
    put(tmp_path, "refactor/learning/web/src/App.tsx", '''
import { Plans as PlanList } from "./pages/Plans"
import { PlanDetail } from "./pages/PlanDetail"
function App() {
  const Page = { plans: PlanList, plan: PlanDetail }[s.route]
  return <Page />
}
''')
    put(tmp_path, "refactor/learning/web/src/store.ts", '''
export const useApp = create((set) => ({
  go: (route, tab) => set((s) => ({ route, settingsTab: tab ?? s.settingsTab })),
}))
''')
    put(tmp_path, "refactor/learning/web/src/pages/Plans.tsx", '''
import { useApp } from "../store"
export function Plans() {
  const { set, go } = useApp()
  const open = (p) => { set({ planId: p.id }); go("classroom") }
  const view = (p) => { set({ planId: p.id }); go("plan") }
}
''')
    put(tmp_path, "refactor/learning/web/src/pages/PlanDetail.tsx", '''
export function PlanDetail() { return null }
''')
    result = extract(tmp_path)
    edge = next(row for row in result["connections"] if row["kind"] == "dispatch" and row["name"] == "go(plan)")
    assert edge["source"] == {"file": "refactor/learning/web/src/pages/Plans.tsx", "line": 6, "function": "view"}
    assert edge["target"] == {"file": "refactor/learning/web/src/pages/PlanDetail.tsx", "line": 2, "function": "PlanDetail"}
    assert [step["function"] for step in edge["via"]] == [
        "store.go route -> set({ route })",
        "App route table plan: PlanDetail",
        "App render <Page />",
        "App import PlanDetail from ./pages/PlanDetail",
    ]


def test_dispatch302_unknown_literal_route_is_not_guessed(tmp_path):
    put(tmp_path, "refactor/learning/web/src/App.tsx", '''
import { Plans } from "./pages/Plans"
import { PlanDetail } from "./pages/PlanDetail"
function App() { const Page = { plans: Plans, plan: PlanDetail }[s.route]; return <Page /> }
''')
    put(tmp_path, "refactor/learning/web/src/store.ts", "go: (route) => set((s) => ({ route }))")
    put(tmp_path, "refactor/learning/web/src/pages/Plans.tsx", '''
import { useApp } from "../store"
export function Plans() { const go = useApp((s) => s.go); go("missing-route") }
''')
    put(tmp_path, "refactor/learning/web/src/pages/PlanDetail.tsx", "export function PlanDetail() {}")
    result = extract(tmp_path)
    assert not any(row["name"] == "go(missing-route)" for row in result["connections"])
    assert any(row["name"] == "go('missing-route')" for row in result["unresolved"])


def test_dispatch302_unbound_go_receiver_is_not_assumed_to_be_store(tmp_path):
    put(tmp_path, "refactor/learning/web/src/App.tsx", '''
import { Plans } from "./pages/Plans"
import { PlanDetail } from "./pages/PlanDetail"
function App() { const Page = { plans: Plans, plan: PlanDetail }[s.route]; return <Page /> }
''')
    put(tmp_path, "refactor/learning/web/src/store.ts", "go: (route) => set((s) => ({ route }))")
    put(tmp_path, "refactor/learning/web/src/pages/Plans.tsx", '''
export function Plans() { go("plan") }
''')
    put(tmp_path, "refactor/learning/web/src/pages/PlanDetail.tsx", "export function PlanDetail() {}")
    result = extract(tmp_path)
    assert not any(row["name"] == "go(plan)" for row in result["connections"])
    assert any("useApp().go" in row["reason"] for row in result["unresolved"])


def test_dispatch302_navigation_after_helper_stays_in_enclosing_component(tmp_path):
    put(tmp_path, "refactor/learning/web/src/App.tsx", '''
import { Plans } from "./pages/Plans"
import { PlanDetail } from "./pages/PlanDetail"
function App() { const Page = { plans: Plans, plan: PlanDetail }[s.route]; return <Page /> }
''')
    put(tmp_path, "refactor/learning/web/src/store.ts", "go: (route) => set((s) => ({ route }))")
    put(tmp_path, "refactor/learning/web/src/pages/Plans.tsx", '''
import { useApp } from "../store"
export function Plans() {
  const go = useApp((s) => s.go)
  function closeDialog() { return null }
  return <button onClick={() => go("plan")}/>
}
''')
    put(tmp_path, "refactor/learning/web/src/pages/PlanDetail.tsx", "export function PlanDetail() {}")
    result = extract(tmp_path)
    edge = next(row for row in result["connections"] if row["name"] == "go(plan)")
    assert edge["source"]["function"] == "Plans"
