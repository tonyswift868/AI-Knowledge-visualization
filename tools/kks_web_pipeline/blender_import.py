"""Blender-side KKBP import stage. Invoked by build_kks_web_model.py."""

from __future__ import annotations

import os
import sys
import traceback

import bpy


export_dir = os.path.abspath(sys.argv[-2])
output_blend = os.path.abspath(sys.argv[-1])
os.makedirs(os.path.dirname(output_blend), exist_ok=True)
error_blend = os.path.splitext(output_blend)[0] + ".error.blend"

try:
    settings = bpy.context.scene.kkbp
    settings.import_dir = export_dir
    settings.use_rigify = False
    settings.use_outline = True
    settings.fix_seams = True
    settings.separate_clothes = False
    result = bpy.ops.kkbp.kkbpimport("EXEC_DEFAULT")
    if "FINISHED" not in result:
        raise RuntimeError(f"KKBP import returned {result}")
    bpy.ops.wm.save_as_mainfile(filepath=output_blend)
    print("KKBP_WEB_IMPORT_COMPLETE", output_blend)
except Exception:
    traceback.print_exc()
    bpy.ops.wm.save_as_mainfile(filepath=error_blend)
    raise
finally:
    bpy.ops.wm.quit_blender()
