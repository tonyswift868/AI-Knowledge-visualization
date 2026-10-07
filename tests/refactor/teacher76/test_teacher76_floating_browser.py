"""Batch 77 supersedes the floating window; keep its real-renderer regression checks."""
import importlib.util
from pathlib import Path

def test_teacher76_real_floating_window_and_model_lifecycle(tmp_path):
    path=Path(__file__).parents[1]/"teacher77/test_teacher77_drawer_browser.py"
    spec=importlib.util.spec_from_file_location("teacher77_replacing_layout",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.test_teacher77_real_drawer_physics_and_expression_lifecycle(tmp_path)
