"""Blender-side KKBP bake/atlas stage. Invoked by build_kks_web_model.py."""

from __future__ import annotations

import os
import sys
import traceback

import bpy


atlas_dir = os.path.abspath(sys.argv[-2])
output_blend = os.path.abspath(sys.argv[-1])
os.makedirs(atlas_dir, exist_ok=True)
os.makedirs(os.path.dirname(output_blend), exist_ok=True)
error_blend = os.path.splitext(output_blend)[0] + ".error.blend"

settings = bpy.context.scene.kkbp
settings.import_dir = atlas_dir
settings.use_rigify = False
settings.prep_dropdown = "no_change"
settings.simp_dropdown = "C"
settings.atlas_dropdown = "skip_eyes"

try:
    bake_result = bpy.ops.kkbp.bakematerials("EXEC_DEFAULT")
    if "FINISHED" not in bake_result:
        raise RuntimeError(f"KKBP bake returned {bake_result}")
    prep_result = bpy.ops.kkbp.exportprep("EXEC_DEFAULT")
    if "FINISHED" not in prep_result:
        raise RuntimeError(f"KKBP export prep returned {prep_result}")
    bpy.ops.wm.save_as_mainfile(filepath=output_blend)
    print("KKBP_WEB_ATLAS_COMPLETE", output_blend)
except Exception:
    traceback.print_exc()
    bpy.ops.wm.save_as_mainfile(filepath=error_blend)
    raise
finally:
    bpy.ops.wm.quit_blender()
