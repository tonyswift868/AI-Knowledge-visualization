import os
import sys
import traceback

import bpy


export_dir = os.path.abspath(sys.argv[-2])
output_path = os.path.abspath(sys.argv[-1])
os.makedirs(os.path.dirname(output_path), exist_ok=True)

try:
    settings = bpy.context.scene.kkbp
    settings.import_dir = export_dir
    settings.use_rigify = False
    settings.use_outline = True
    settings.fix_seams = True
    settings.separate_clothes = False
    print("KKBP_IMPORT_RESULT", bpy.ops.kkbp.kkbpimport("EXEC_DEFAULT"))
    bpy.ops.wm.save_as_mainfile(filepath=output_path)
except Exception:
    traceback.print_exc()
    bpy.ops.wm.save_as_mainfile(filepath=output_path.replace(".blend", "_error.blend"))
    raise
finally:
    bpy.ops.wm.quit_blender()
