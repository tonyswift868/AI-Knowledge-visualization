import os
import sys
import traceback

import bpy


work_dir = os.path.abspath(sys.argv[-2])
output_blend = os.path.abspath(sys.argv[-1])
os.makedirs(work_dir, exist_ok=True)

settings = bpy.context.scene.kkbp
settings.import_dir = work_dir
settings.use_rigify = False
settings.prep_dropdown = "no_change"
settings.simp_dropdown = "C"
settings.atlas_dropdown = "skip_eyes"

try:
    print("KKBP_BAKE_RESULT", bpy.ops.kkbp.bakematerials("EXEC_DEFAULT"))
    print("KKBP_EXPORT_PREP_RESULT", bpy.ops.kkbp.exportprep("EXEC_DEFAULT"))
    bpy.ops.wm.save_as_mainfile(filepath=output_blend)
except Exception:
    traceback.print_exc()
    bpy.ops.wm.save_as_mainfile(filepath=output_blend.replace(".blend", "_error.blend"))
    raise
finally:
    bpy.ops.wm.quit_blender()
