from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORY_PACK_ROOT = PROJECT_ROOT / "story_packs"
PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
REQUIRED_MANIFEST_FIELDS = {
    "story_pack_id", "version", "title", "source_work", "source_version", "language",
    "minimum_runtime_version", "supported_duration_days", "content_rating", "entry_scene_id",
    "ending_scene_ids", "checksum", "created_at", "provenance_notes",
}


class StoryPackError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _safe_pack_path(pack_id: str, root: Path = STORY_PACK_ROOT) -> Path:
    if not PACK_ID_RE.fullmatch(pack_id):
        raise StoryPackError("STORY_PACK_ID_INVALID", "Story Pack ID 格式无效")
    path = (root / pack_id).resolve()
    if path.parent != root.resolve() or not path.is_dir():
        raise StoryPackError("STORY_PACK_NOT_FOUND", "Story Pack 不存在", {"story_pack_id": pack_id})
    return path


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StoryPackError("STORY_PACK_FILE_MISSING", "Story Pack 文件缺失", {"file": path.name}) from exc
    except (OSError, yaml.YAMLError) as exc:
        raise StoryPackError("STORY_PACK_FILE_INVALID", "Story Pack 文件无法读取", {"file": path.name}) from exc
    if not isinstance(value, dict):
        raise StoryPackError("STORY_PACK_SCHEMA_INVALID", "Story Pack YAML 根节点必须是对象", {"file": path.name})
    return value


def compute_pack_checksum(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.yaml":
            manifest = _read_yaml(path)
            manifest["checksum"] = ""
            data = yaml.safe_dump(manifest, sort_keys=True, allow_unicode=True).encode("utf-8")
        else:
            data = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _assert_safe_reference(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise StoryPackError("STORY_PACK_REFERENCE_INVALID", "Story Pack 引用必须是字符串", {"field": field})
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise StoryPackError("STORY_PACK_PATH_UNSAFE", "Story Pack 不允许绝对路径或目录逃逸", {"field": field})
    if any(token in value.lower() for token in ("<script", "javascript:", "data:text/html")):
        raise StoryPackError("STORY_PACK_EXECUTABLE_CONTENT", "Story Pack 不允许可执行内容", {"field": field})


def _resolve_pack_asset(root: Path | None, raw_path: Any, field: str) -> Path | None:
    if raw_path is None:
        return None
    _assert_safe_reference(raw_path, field)
    if root is None:
        return None
    candidate = (root / str(raw_path)).resolve()
    if root.resolve() not in candidate.parents:
        raise StoryPackError("STORY_PACK_PATH_UNSAFE", "资产路径超出 Story Pack 目录", {"field": field})
    return candidate


def _validate_pack_file_reference(root: Path | None, raw_path: Any, field: str) -> None:
    if raw_path is None:
        return
    candidate = _resolve_pack_asset(root, raw_path, field)
    if candidate is not None and not candidate.is_file():
        raise StoryPackError("STORY_PACK_FILE_MISSING", "Story Pack 引用的文件不存在", {"field": field, "path": str(raw_path)})


def _scene_edges(scene: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for choice in scene.get("choices", []) or []:
        if isinstance(choice, dict) and choice.get("next_scene_id"):
            targets.append(str(choice["next_scene_id"]))
    for rule in scene.get("next_scene_rules", []) or []:
        if isinstance(rule, dict) and rule.get("target_scene_id"):
            targets.append(str(rule["target_scene_id"]))
    return targets


def validate_story_pack(pack: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    manifest = pack.get("manifest")
    if not isinstance(manifest, dict):
        raise StoryPackError("STORY_PACK_SCHEMA_INVALID", "manifest 缺失")
    missing = sorted(REQUIRED_MANIFEST_FIELDS - set(manifest))
    if missing:
        raise StoryPackError("STORY_PACK_SCHEMA_INVALID", "manifest 缺少必填字段", {"missing": missing})
    if manifest.get("story_pack_id") != pack.get("story_pack_id"):
        raise StoryPackError("STORY_PACK_ID_MISMATCH", "manifest 与目录 ID 不一致")
    characters = pack.get("characters", {})
    scenes = pack.get("scenes", {})
    if not characters or not scenes:
        raise StoryPackError("STORY_PACK_SCHEMA_INVALID", "Story Pack 至少需要角色和场景")
    pack_root = root or (Path(pack["root"]) if pack.get("root") else None)
    assets = {item.get("asset_id"): item for item in (pack.get("asset_manifest", {}).get("assets", []) or []) if isinstance(item, dict)}
    voice_profiles = {item.get("voice_profile_id"): item for item in (pack.get("voice_manifest", {}).get("voice_profiles", []) or []) if isinstance(item, dict)}
    for asset_id, asset in assets.items():
        if not asset_id or asset.get("kind") not in {"background", "character", "music", "voice", "icon"}:
            raise StoryPackError("STORY_PACK_ASSET_KIND_INVALID", "资产 kind 无效", {"asset_id": asset_id})
        asset_path = _resolve_pack_asset(pack_root, asset.get("path"), f"asset_manifest.assets[{asset_id}].path")
        if asset.get("status") == "ready" and asset_path is not None and not asset_path.is_file():
            raise StoryPackError("STORY_PACK_ASSET_MISSING", "ready 资产文件不存在", {"asset_id": asset_id})
    for profile_id, profile in voice_profiles.items():
        if not profile_id or profile.get("provider") not in {"browser_speech", "fake", "external"}:
            raise StoryPackError("STORY_PACK_VOICE_PROFILE_INVALID", "voice profile 不存在或 provider 无效", {"voice_profile_id": profile_id})
        if profile.get("pronunciation_dictionary_ref"):
            pronunciation_ref = profile["pronunciation_dictionary_ref"]
            if pronunciation_ref in assets:
                if assets[pronunciation_ref].get("kind") not in {"icon"}:
                    raise StoryPackError("STORY_PACK_ASSET_KIND_INVALID", "发音词典引用必须指向可读资源", {"voice_profile_id": profile_id, "asset_id": pronunciation_ref})
                _validate_pack_file_reference(pack_root, assets[pronunciation_ref].get("path"), f"voice_profiles[{profile_id}].pronunciation_dictionary_ref")
            else:
                _validate_pack_file_reference(pack_root, pronunciation_ref, f"voice_profiles[{profile_id}].pronunciation_dictionary_ref")
    bindings = {item.get("binding_id"): item for item in (pack.get("learning_bindings", {}).get("bindings", []) or []) if isinstance(item, dict)}
    for scene_id, scene in scenes.items():
        if scene.get("scene_id") != scene_id:
            raise StoryPackError("STORY_PACK_SCENE_ID_MISMATCH", "场景 ID 与文件内容不一致", {"scene_id": scene_id})
        if scene.get("scene_type") not in {"fixed_main_story", "fixed_side_story", "safe_dynamic_interaction", "learning_session", "review_session", "assessment", "ending", "postgame"}:
            raise StoryPackError("STORY_PACK_SCENE_TYPE_INVALID", "场景类型不受支持", {"scene_id": scene_id})
        for target in _scene_edges(scene):
            if target not in scenes:
                raise StoryPackError("STORY_PACK_REFERENCE_INVALID", "场景引用不存在", {"scene_id": scene_id, "target": target})
        if scene.get("background_ref") and scene["background_ref"] not in assets:
            raise StoryPackError("STORY_PACK_ASSET_REFERENCE_INVALID", "背景资源引用不存在", {"scene_id": scene_id})
        if scene.get("background_ref") and assets[scene["background_ref"]].get("kind") != "background":
            raise StoryPackError("STORY_PACK_ASSET_KIND_INVALID", "background_ref 必须引用 background 资产", {"scene_id": scene_id})
        if scene.get("music_ref"):
            if scene["music_ref"] not in assets or assets[scene["music_ref"]].get("kind") != "music":
                raise StoryPackError("STORY_PACK_ASSET_REFERENCE_INVALID", "music_ref 必须引用 music 资产", {"scene_id": scene_id})
        for voice_ref in scene.get("voice_line_refs", []) or []:
            if isinstance(voice_ref, dict):
                profile_ref = voice_ref.get("voice_profile_ref")
                asset_ref = voice_ref.get("asset_id")
                if profile_ref and profile_ref not in voice_profiles:
                    raise StoryPackError("STORY_PACK_VOICE_REFERENCE_INVALID", "voice_line_refs 的 voice profile 不存在", {"scene_id": scene_id, "voice_ref": profile_ref})
                if asset_ref and (asset_ref not in assets or assets[asset_ref].get("kind") != "voice"):
                    raise StoryPackError("STORY_PACK_ASSET_KIND_INVALID", "voice_line_refs 的 asset 必须是 voice 资产", {"scene_id": scene_id, "asset_id": asset_ref})
                if not profile_ref and not asset_ref:
                    raise StoryPackError("STORY_PACK_VOICE_REFERENCE_INVALID", "voice_line_refs 必须提供 voice profile 或 voice asset", {"scene_id": scene_id})
            elif voice_ref and voice_ref not in voice_profiles and (voice_ref not in assets or assets[voice_ref].get("kind") != "voice"):
                raise StoryPackError("STORY_PACK_VOICE_REFERENCE_INVALID", "voice_line_refs 引用不存在或类型错误", {"scene_id": scene_id, "voice_ref": voice_ref})
        for layer in scene.get("character_layers", []) or []:
            if layer.get("character_id") not in characters:
                raise StoryPackError("STORY_PACK_CHARACTER_REFERENCE_INVALID", "角色引用不存在", {"scene_id": scene_id, "character_id": layer.get("character_id")})
            _assert_safe_reference(layer.get("position"), "character_layers.position")
            if layer.get("asset_ref"):
                if layer["asset_ref"] not in assets or assets[layer["asset_ref"]].get("kind") != "character":
                    raise StoryPackError("STORY_PACK_ASSET_KIND_INVALID", "角色层必须引用 character 资产", {"scene_id": scene_id, "asset_id": layer.get("asset_ref")})
        for line in scene.get("speaker_lines", []) or []:
            if line.get("speaker_id") not in characters:
                raise StoryPackError("STORY_PACK_CHARACTER_REFERENCE_INVALID", "台词角色引用不存在", {"scene_id": scene_id, "speaker_id": line.get("speaker_id")})
            if line.get("voice_profile_ref") and line["voice_profile_ref"] not in voice_profiles:
                raise StoryPackError("STORY_PACK_VOICE_REFERENCE_INVALID", "speaker line voice profile 不存在", {"scene_id": scene_id, "voice_profile_ref": line.get("voice_profile_ref")})
        if scene.get("learning_gate_ref") and scene["learning_gate_ref"] not in bindings:
            raise StoryPackError("STORY_PACK_LEARNING_GATE_INVALID", "学习门禁引用不存在", {"scene_id": scene_id})
        if scene.get("scene_type") in {"fixed_main_story", "fixed_side_story", "ending", "postgame"} and scene.get("immutable") is not True:
            raise StoryPackError("STORY_PACK_IMMUTABLE_REQUIRED", "固定或结局场景必须 immutable", {"scene_id": scene_id})
    for character_id, character in characters.items():
        if character.get("character_id") != character_id:
            raise StoryPackError("STORY_PACK_CHARACTER_ID_MISMATCH", "角色 ID 与文件内容不一致", {"character_id": character_id})
        if not isinstance(character.get("can_teach"), bool):
            raise StoryPackError("STORY_PACK_CHARACTER_SCHEMA_INVALID", "角色 can_teach 必须是布尔值", {"character_id": character_id})
        for asset_id in character.get("visual_asset_refs", []) or []:
            if asset_id not in assets or assets[asset_id].get("kind") != "character":
                raise StoryPackError("STORY_PACK_ASSET_REFERENCE_INVALID", "角色资源引用不存在", {"character_id": character_id, "asset_id": asset_id})
        if character.get("voice_profile_ref") and character["voice_profile_ref"] not in voice_profiles:
            raise StoryPackError("STORY_PACK_VOICE_REFERENCE_INVALID", "角色 voice profile 不存在", {"character_id": character_id, "voice_profile_ref": character.get("voice_profile_ref")})
        _assert_safe_reference(character.get("voice_profile_ref"), "voice_profile_ref")
    entry = manifest.get("entry_scene_id")
    endings = manifest.get("ending_scene_ids") or []
    if entry not in scenes or any(ending not in scenes for ending in endings):
        raise StoryPackError("STORY_PACK_ENTRY_OR_ENDING_INVALID", "入口或结局场景引用不存在")
    graph = {scene_id: _scene_edges(scene) for scene_id, scene in scenes.items()}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise StoryPackError("STORY_PACK_ANCHOR_CYCLE", "主线场景存在循环", {"scene_id": node})
        if node in visited:
            return
        visiting.add(node)
        for target in graph[node]:
            visit(target)
        visiting.remove(node)
        visited.add(node)

    visit(entry)
    reachable = visited
    if any(ending not in reachable for ending in endings):
        raise StoryPackError("STORY_PACK_ENDING_UNREACHABLE", "存在不可达结局")
    if root is not None:
        declared = str(manifest.get("checksum", ""))
        actual = compute_pack_checksum(root)
        if declared != actual:
            raise StoryPackError("STORY_PACK_CHECKSUM_MISMATCH", "Story Pack checksum 不匹配", {"declared": declared, "actual": actual})
    return {"valid": True, "scene_count": len(scenes), "character_count": len(characters), "reachable_scene_count": len(reachable), "checksum": manifest.get("checksum")}


def load_story_pack(pack_id: str, root: Path = STORY_PACK_ROOT) -> dict[str, Any]:
    pack_root = _safe_pack_path(pack_id, root)
    manifest = _read_yaml(pack_root / "manifest.yaml")
    story_bible = _read_yaml(pack_root / "story_bible.yaml")
    learning_bindings = _read_yaml(pack_root / "learning_bindings.yaml")
    asset_manifest = _read_yaml(pack_root / "asset_manifest.yaml")
    voice_manifest = _read_yaml(pack_root / "voice_manifest.yaml")
    localization_path = pack_root / "localization" / "zh-CN.yaml"
    localization = _read_yaml(localization_path) if localization_path.exists() else {}
    characters = {path.stem: _read_yaml(path) for path in sorted((pack_root / "characters").glob("*.yaml"))}
    scenes = {path.stem: _read_yaml(path) for path in sorted((pack_root / "scenes").rglob("*.yaml"))}
    pack = {"story_pack_id": pack_id, "root": str(pack_root), "manifest": manifest, "story_bible": story_bible, "learning_bindings": learning_bindings, "asset_manifest": asset_manifest, "voice_manifest": voice_manifest, "localization": localization, "characters": characters, "scenes": scenes}
    validate_story_pack(pack, pack_root)
    return pack


def list_story_packs(root: Path = STORY_PACK_ROOT) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    result: list[dict[str, Any]] = []
    for directory in sorted(item for item in root.iterdir() if item.is_dir() and PACK_ID_RE.fullmatch(item.name)):
        try:
            pack = load_story_pack(directory.name, root)
            result.append({"story_pack_id": directory.name, **pack["manifest"], "validation": {"valid": True}})
        except StoryPackError as exc:
            result.append({"story_pack_id": directory.name, "validation": {"valid": False, "code": exc.code, "message": exc.message}})
    return result


def public_character(character: dict[str, Any], story_stage: str = "intro") -> dict[str, Any]:
    result = {key: value for key, value in character.items() if key not in {"true_identity", "false_beliefs_by_story_stage", "secrets_by_story_stage", "forbidden_speech", "knowledge_by_story_stage"}}
    stages = ["intro", "first_pass", "review", "truth", "postgame"]
    knowledge = character.get("knowledge_by_story_stage", {}) or {}
    if story_stage == "all":
        allowed = knowledge
    else:
        index = stages.index(story_stage) if story_stage in stages else 0
        allowed = {key: value for key, value in knowledge.items() if key == "all" or key in stages[: index + 1]}
    result["knowledge_by_story_stage"] = allowed
    return result


def public_scene(pack: dict[str, Any], scene_id: str, story_stage: str = "intro") -> dict[str, Any]:
    scene = pack["scenes"].get(scene_id)
    if scene is None:
        raise StoryPackError("SCENE_NOT_FOUND", "场景不存在", {"scene_id": scene_id})
    result = {key: value for key, value in scene.items() if key not in {"entry_conditions", "next_scene_rules"}}
    result["background_asset_url"] = f"/api/story-packs/{pack['story_pack_id']}/assets/{scene['background_ref']}" if scene.get("background_ref") else None
    result["characters"] = []
    for layer in scene.get("character_layers", []) or []:
        character = public_character(pack["characters"][layer["character_id"]], story_stage)
        character.update({"position": layer.get("position"), "expression": layer.get("expression"), "asset_ref": layer.get("asset_ref")})
        if layer.get("asset_ref"):
            character["visual_asset_url"] = f"/api/story-packs/{pack['story_pack_id']}/assets/{layer['asset_ref']}"
        result["characters"].append(character)
    result["speaker_lines"] = [{"speaker_id": line.get("speaker_id"), "text": line.get("text", "")} for line in scene.get("speaker_lines", [])]
    return result
