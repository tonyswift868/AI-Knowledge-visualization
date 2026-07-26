import json
import os
import sys

import bpy
from PIL import Image


output_path = os.path.abspath(sys.argv[-1])
sidecar_path = os.path.splitext(output_path)[0] + ".materials.json"
texture_dir = os.path.join(os.path.dirname(output_path), "textures-2k")
os.makedirs(texture_dir, exist_ok=True)
image_cache = {}

atlas = next(collection for collection in bpy.data.collections if collection.name.endswith(" atlas"))
atlas.hide_viewport = False
atlas.hide_render = False


def reveal(layer):
    if layer.collection == atlas or atlas in layer.collection.children_recursive:
        layer.exclude = False
        layer.hide_viewport = False
    for child in layer.children:
        reveal(child)


def source_image(material, node_name):
    node = material.node_tree.nodes.get(node_name) if material and material.use_nodes else None
    return node.image if node and getattr(node, "image", None) else None


def web_image(source):
    if source is None:
        return None
    source_path = os.path.abspath(bpy.path.abspath(source.filepath))
    key = os.path.normcase(source_path)
    if key in image_cache:
        return image_cache[key]
    with Image.open(source_path) as image:
        if max(image.size) <= 2048:
            image_cache[key] = source
            return source
        resized = image.copy()
        resized.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        target = os.path.join(texture_dir, os.path.splitext(os.path.basename(source_path))[0] + ".web.png")
        resized.save(target, format="PNG", optimize=True)
    loaded = bpy.data.images.load(target, check_existing=True)
    image_cache[key] = loaded
    return loaded


def web_material(name, light, dark, normal, uses_alpha):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.surface_render_method = "DITHERED"
    material.use_transparency_overlap = False
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.inputs["Roughness"].default_value = 0.72
    principled.inputs["Metallic"].default_value = 0.0
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    color = nodes.new("ShaderNodeTexImage")
    color.name = "KKS Light Atlas"
    color.image = light
    material.node_tree.links.new(color.outputs["Color"], principled.inputs["Base Color"])
    if dark:
        shade = nodes.new("ShaderNodeTexImage")
        shade.name = "KKS Dark Atlas"
        shade.image = dark
        material.node_tree.links.new(shade.outputs["Color"], principled.inputs["Emission Color"])
        principled.inputs["Emission Strength"].default_value = 1.0
    if normal:
        normal_texture = nodes.new("ShaderNodeTexImage")
        normal_texture.name = "KKS Normal Atlas"
        normal_texture.image = normal
        normal_texture.image.colorspace_settings.name = "Non-Color"
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.name = "KKS Normal Map"
        material.node_tree.links.new(normal_texture.outputs["Color"], normal_map.inputs["Color"])
        material.node_tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    if uses_alpha:
        material.node_tree.links.new(color.outputs["Alpha"], principled.inputs["Alpha"])
    return material


reveal(bpy.context.view_layer.layer_collection)
meshes = [
    obj for obj in atlas.all_objects
    if obj.type == "MESH" and obj.name.startswith(("Body ", "Outfit 00 ", "Hair Outfit 00 "))
]
armature = next(obj for obj in atlas.all_objects if obj.type == "ARMATURE")
sidecar = {
    "schema": "mentorflow.kks-material-atlas.v1",
    "source": "KKBP Importer 9.4.2",
    "objects": [],
}

for obj in meshes:
    groups = {}
    remap = {}
    for old_index, source in enumerate(list(obj.data.materials)):
        light = web_image(source_image(source, "_light.png"))
        dark = web_image(source_image(source, "_dark.png"))
        normal = web_image(source_image(source, "_NMP_CNV.png"))
        if light is None:
            raise RuntimeError(f"missing baked light texture for {obj.name}: {source.name}")
        alpha = getattr(source, "surface_render_method", "DITHERED") == "BLENDED" or bool(source.get("hair"))
        if "hitomi_L" in source.name:
            group_key = ("eye-left", light.filepath, True)
        elif "hitomi_R" in source.name:
            group_key = ("eye-right", light.filepath, True)
        else:
            group_key = ("atlas-alpha" if alpha else "atlas-opaque", light.filepath, alpha)
        if group_key not in groups:
            groups[group_key] = {
                "material": web_material(f"{obj.name} {group_key[0]}", light, dark, normal, alpha),
                "light": bpy.path.abspath(light.filepath),
                "dark": bpy.path.abspath(dark.filepath) if dark else None,
                "normal": bpy.path.abspath(normal.filepath) if normal else None,
                "alphaMethod": "MASK" if alpha else "OPAQUE",
                "sources": [],
            }
        groups[group_key]["sources"].append(source.name)
        remap[old_index] = list(groups).index(group_key)
    polygon_materials = [remap[polygon.material_index] for polygon in obj.data.polygons]
    obj.data.materials.clear()
    for group in groups.values():
        obj.data.materials.append(group["material"])
    for polygon, material_index in zip(obj.data.polygons, polygon_materials):
        polygon.material_index = material_index
    sidecar["objects"].append({
        "name": obj.name,
        "materials": [
            {key: value for key, value in group.items() if key != "material"}
            for group in groups.values()
        ],
    })

bpy.ops.object.select_all(action="DESELECT")
armature.hide_set(False)
armature.select_set(True)
for obj in meshes:
    obj.hide_set(False)
    obj.select_set(True)
bpy.context.view_layer.objects.active = armature
with open(sidecar_path, "w", encoding="utf-8") as stream:
    json.dump(sidecar, stream, ensure_ascii=False, indent=2)

bpy.ops.export_scene.gltf(
    filepath=output_path,
    export_format="GLB",
    use_selection=True,
    export_animations=False,
    export_skins=True,
    export_def_bones=False,
    export_morph=True,
    export_morph_normal=True,
    export_morph_tangent=False,
    export_materials="EXPORT",
    export_image_format="WEBP",
    export_image_add_webp=False,
    export_image_webp_fallback=False,
    export_image_quality=88,
    export_meshopt_compression_enable=True,
    export_meshopt_extension="EXT_meshopt_compression",
    export_apply=False,
    export_yup=True,
)
print("KKS_WEB_GLB", output_path, os.path.getsize(output_path), len(armature.data.bones))
