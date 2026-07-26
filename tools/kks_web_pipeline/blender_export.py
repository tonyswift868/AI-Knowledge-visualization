"""Blender-side WebP/Meshopt GLB export. Invoked by build_kks_web_model.py."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import bpy
from PIL import Image


output_path = os.path.abspath(sys.argv[-2])
max_texture_dimension = int(sys.argv[-1])
sidecar_path = os.path.splitext(output_path)[0] + ".materials.json"
os.makedirs(os.path.dirname(output_path), exist_ok=True)
web_texture_dir = os.path.join(os.path.dirname(output_path), "textures")
os.makedirs(web_texture_dir, exist_ok=True)
web_image_cache = {}

atlas_collection = next(
    collection for collection in bpy.data.collections if collection.name.endswith(" atlas")
)
atlas_collection.hide_viewport = False
atlas_collection.hide_render = False


def reveal_layer(layer):
    if layer.collection == atlas_collection or atlas_collection in layer.collection.children_recursive:
        layer.exclude = False
        layer.hide_viewport = False
    for child in layer.children:
        reveal_layer(child)


def image_from_node(material, node_name):
    node = material.node_tree.nodes.get(node_name) if material and material.use_nodes else None
    return node.image if node and getattr(node, "image", None) else None


def web_image(source):
    if source is None:
        return None
    source_path = os.path.normcase(os.path.abspath(bpy.path.abspath(source.filepath)))
    if source_path in web_image_cache:
        return web_image_cache[source_path]
    with Image.open(source_path) as image:
        if max(image.size) <= max_texture_dimension:
            web_image_cache[source_path] = source
            return source
        resized = image.copy()
        resized.thumbnail(
            (max_texture_dimension, max_texture_dimension),
            Image.Resampling.LANCZOS,
        )
        stem = os.path.splitext(os.path.basename(source_path))[0]
        digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:10]
        target_path = os.path.join(web_texture_dir, f"{stem}-{digest}.png")
        resized.save(target_path, format="PNG", optimize=True)
    loaded = bpy.data.images.load(target_path, check_existing=True)
    web_image_cache[source_path] = loaded
    print("DOWNSCALED_TEXTURE", source_path, "->", target_path, tuple(loaded.size))
    return loaded


def make_web_material(name, light_image, dark_image, normal_image, uses_alpha):
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
    if principled.inputs.get("Specular IOR Level"):
        principled.inputs["Specular IOR Level"].default_value = 0.22
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    color = nodes.new("ShaderNodeTexImage")
    color.name = "KKS Light Atlas"
    color.image = light_image
    color.interpolation = "Linear"
    material.node_tree.links.new(color.outputs["Color"], principled.inputs["Base Color"])
    # glTF has no native slot for KKS's dark toon atlas.  Store it in the
    # standard emissive texture slot so GLTFLoader transports and caches it;
    # the browser KKS material consumes it as shade color, not as emission.
    if dark_image:
        shade = nodes.new("ShaderNodeTexImage")
        shade.name = "KKS Dark Atlas"
        shade.image = dark_image
        shade.interpolation = "Linear"
        material.node_tree.links.new(
            shade.outputs["Color"], principled.inputs["Emission Color"]
        )
        principled.inputs["Emission Strength"].default_value = 1.0
    if normal_image:
        normal_texture = nodes.new("ShaderNodeTexImage")
        normal_texture.name = "KKS Normal Atlas"
        normal_texture.image = normal_image
        normal_texture.image.colorspace_settings.name = "Non-Color"
        normal_texture.interpolation = "Linear"
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.name = "KKS Normal Map"
        material.node_tree.links.new(
            normal_texture.outputs["Color"], normal_map.inputs["Color"]
        )
        material.node_tree.links.new(
            normal_map.outputs["Normal"], principled.inputs["Normal"]
        )
    if uses_alpha:
        material.node_tree.links.new(color.outputs["Alpha"], principled.inputs["Alpha"])
    return material


def semantic_material_role(source_name, object_name, uses_alpha):
    """Keep KKS face layers separate after atlas baking.

    KKBP places many facial surfaces in one texture atlas, but they still have
    different depth/alpha contracts.  Merging eyebrows, nose lines, eyelines,
    sclera and skin into one glTF primitive is what produced the mask-like and
    missing-feature faces in the browser.
    """

    name = source_name.casefold()
    if "hitomi_l" in name:
        return "eye-left"
    if "hitomi_r" in name:
        return "eye-right"
    if "mayuge" in name:
        return "face-brow"
    if "noseline" in name:
        return "face-nose-line"
    if "eyeline" in name:
        return "face-eye-line"
    if "sirome" in name:
        return "face-eye-white"
    if "tooth" in name:
        return "face-teeth"
    if "tang" in name:
        return "face-tongue"
    if "cf_m_face" in name:
        return "face-skin"
    if "cf_m_body" in name:
        return "body-skin"
    if "hair" in object_name.casefold() or "hair" in name:
        return "hair-alpha" if uses_alpha else "hair-opaque"
    return "atlas-alpha" if uses_alpha else "atlas-opaque"


reveal_layer(bpy.context.view_layer.layer_collection)
main_meshes = [
    obj
    for obj in atlas_collection.all_objects
    if obj.type == "MESH"
    and obj.name.startswith(("Body ", "Outfit 00 ", "Hair Outfit 00 "))
]
armature = next(obj for obj in atlas_collection.all_objects if obj.type == "ARMATURE")
sidecar = {
    "schema": "mentorflow.kks-material-atlas.v1",
    "source": "KKBP Importer 9.4.2",
    "shading": {
        "recommended": "MToon",
        "lightTextureRole": "baseColor",
        "darkTextureRole": "shadeMultiply",
        "normalTextureRole": "normal",
    },
    "objects": [],
}

for obj in main_meshes:
    source_materials = list(obj.data.materials)
    groups = {}
    remap = {}
    for old_index, source in enumerate(source_materials):
        light = web_image(image_from_node(source, "_light.png"))
        dark = web_image(image_from_node(source, "_dark.png"))
        normal = web_image(image_from_node(source, "_NMP_CNV.png"))
        if light is None:
            raise RuntimeError(f"missing baked light texture for {obj.name}: {source.name}")
        source_method = getattr(source, "surface_render_method", "DITHERED")
        # KK's DITHERED materials are alpha-tested, not opaque.  The body
        # atlas uses transparent packing cells; exporting those cells as
        # OPAQUE turns the empty RGBA pixels into jagged black legs/holes in
        # the browser.  Preserve the source render contract in glTF MASK
        # materials so the shared preview does not need card-specific hacks.
        uses_alpha = source_method in {"BLENDED", "DITHERED"} or bool(source.get("hair"))
        role = semantic_material_role(source.name, obj.name, uses_alpha)
        group_key = (role, light.filepath, uses_alpha)
        if group_key not in groups:
            groups[group_key] = {
                "material": make_web_material(
                    f"{obj.name} {group_key[0]}",
                    light,
                    dark,
                    normal,
                    uses_alpha,
                ),
                "light": bpy.path.abspath(light.filepath),
                "dark": bpy.path.abspath(dark.filepath) if dark else None,
                "normal": bpy.path.abspath(normal.filepath) if normal else None,
                "alphaMethod": "MASK" if uses_alpha else "OPAQUE",
                "sources": [],
            }
        groups[group_key]["sources"].append(source.name)
        remap[old_index] = list(groups).index(group_key)

    remapped_indices = [remap[polygon.material_index] for polygon in obj.data.polygons]
    obj.data.materials.clear()
    for group in groups.values():
        obj.data.materials.append(group["material"])
    for polygon, material_index in zip(obj.data.polygons, remapped_indices):
        polygon.material_index = material_index
    sidecar["objects"].append(
        {
            "name": obj.name,
            "materials": [
                {key: value for key, value in group.items() if key != "material"}
                for group in groups.values()
            ],
        }
    )

bpy.ops.object.select_all(action="DESELECT")
armature.hide_set(False)
armature.select_set(True)
for obj in main_meshes:
    obj.hide_set(False)
    obj.select_set(True)
bpy.context.view_layer.objects.active = armature
with open(sidecar_path, "w", encoding="utf-8") as stream:
    json.dump(sidecar, stream, ensure_ascii=False, indent=2)

print("ATLAS_MESHES", [(obj.name, len(obj.data.vertices)) for obj in main_meshes])
print("ATLAS_BONES", len(armature.data.bones))
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
print("EXPORTED_KKS_WEB_GLB", output_path, os.path.getsize(output_path))
print("EXPORTED_KKS_MATERIAL_SIDECAR", sidecar_path, os.path.getsize(sidecar_path))
