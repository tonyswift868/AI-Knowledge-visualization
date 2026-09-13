"""Persistence and API-facing application service for Learning Expedition."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .runtime_resources import project_resource_root
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .expedition_data import BOSSES, ENEMIES, EQUIPMENT, HEROES, PETS, STANCES
from .expedition_engine import DIFFICULTY_VERSION, BattleEngine, stage_difficulty, stage_enemy_ids, stable_seed
from .expedition_art_pack import hero_assets as resolve_hero_assets
from .expedition_orm import (
    BattleEventLogORM, BattleRecordORM, EquipmentDefinitionORM, EquipmentInstanceORM,
    ExpeditionAssetRegistryORM, ExpeditionProfileORM, ExpeditionStageDefinitionCacheORM,
    ExpeditionStageProgressORM, GachaBannerORM, GachaPullRecordORM, HeroAwakeningORM,
    HeroDefinitionORM, HeroOwnershipORM, HeroStarProgressORM, LineupMemberORM,
    LineupPresetORM, MilestoneRewardClaimORM, PetDefinitionORM, PetOwnershipORM,
    TrainingSlotORM, VerifiedLearningRewardLedgerORM,
)
from .orm import SessionLocal, init_orm

ROOT = project_resource_root()
PROFILE_ID = "expedition_profile_local"
USER_KEY = "local"
ASSET_ROOT = ROOT / "frontend" / "public" / "assets" / "expedition"
_REWARD_LOCK = Lock()
_SEED_LOCK = Lock()
try:
    _REFRESH_ZONE = ZoneInfo("Asia/Shanghai")
except Exception:  # Windows installations may not ship the IANA tzdata bundle.
    _REFRESH_ZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
_REFRESH_HOUR = 0


class ExpeditionError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None):
        self.code, self.message, self.status_code, self.details = code, message, status_code, details or {}
        super().__init__(message)


def _id(prefix: str) -> str: return f"{prefix}_{uuid.uuid4().hex[:18]}"
def _now() -> datetime: return datetime.now(timezone.utc)


def _cycle_key(value: datetime | None = None) -> str:
    """The reward ledger rolls over at the configured local midnight."""
    current = value or _now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(_REFRESH_ZONE).date().isoformat()


def _reward_is_measured(row: VerifiedLearningRewardLedgerORM) -> bool:
    """Reject legacy Learning Run rewards that copied planned task minutes.

    Older rows have no duration source and therefore cannot prove that any
    active learning occurred. Explicit trusted grants remain valid.
    """
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    source = str(metadata.get("duration_source") or "")
    return bool(source) or not str(row.learning_run_id or "").startswith("learning_run_")


def _verified_total_minutes(session, profile: ExpeditionProfileORM) -> int:
    return sum(
        max(0, int(row.released_minutes or 0))
        for row in session.query(VerifiedLearningRewardLedgerORM).filter_by(profile_id=profile.id).all()
        if _reward_is_measured(row)
    )


def _cycle_minutes(session, profile: ExpeditionProfileORM, cycle_key: str | None = None) -> int:
    target = cycle_key or _cycle_key()
    total = 0
    for row in session.query(VerifiedLearningRewardLedgerORM).filter_by(profile_id=profile.id).all():
        created = row.created_at
        if created is None:
            continue
        if _reward_is_measured(row) and _cycle_key(created) == target:
            total += max(0, int(row.released_minutes or 0))
    return total


def _ticket_state(profile: ExpeditionProfileORM) -> dict[str, Any]:
    state = dict(profile.gacha_state or {})
    if state.get("ticket_cycle_key") != _cycle_key():
        state["ticket_cycle_key"] = _cycle_key()
        state["tickets"] = {"equipment": 0, "character": 0}
        state["claimed_tickets"] = {"equipment": 0, "character": 0}
    state.setdefault("tickets", {"equipment": 0, "character": 0})
    state.setdefault("claimed_tickets", {"equipment": 0, "character": 0})
    return state


def _profile(session) -> ExpeditionProfileORM:
    profile = session.query(ExpeditionProfileORM).filter_by(user_key=USER_KEY).first()
    if not profile:
        # The initial query and a subsequent add/flush are not atomic: two
        # first requests can both observe an empty table and race on the
        # unique user_key constraint.  Let SQLite arbitrate the single local
        # row, then re-read it from this session.  No exception is swallowed,
        # so an unexpected constraint or database failure remains visible.
        session.execute(
            sqlite_insert(ExpeditionProfileORM)
            .values(id=PROFILE_ID, user_key=USER_KEY, gacha_state={})
            .on_conflict_do_nothing(index_elements=["user_key"])
        )
        profile = session.query(ExpeditionProfileORM).filter_by(user_key=USER_KEY).one()
    return profile


def _seed(session) -> ExpeditionProfileORM:
    # A process-local lock keeps the rest of the first-request seed graph
    # (definitions, slots, starter ownership and lineup) from racing after
    # the profile upsert.  The profile INSERT remains atomic for callers in
    # separate processes, while all in-process request workers serialize here.
    with _SEED_LOCK:
        return _seed_locked(session)


def _seed_locked(session) -> ExpeditionProfileORM:
    profile = _profile(session)
    for item in HEROES:
        if not session.get(HeroDefinitionORM, item["hero_id"]):
            session.add(HeroDefinitionORM(hero_id=item["hero_id"], name=item["name"], rarity=item["rarity"], role=item["role"], base_stats=item["base_stats"], normal_attack=item["normal_attack"], active_skill=item["active_skill"], passive_one=item["passive_one"], passive_two=item["passive_two"], awakening=item["awakening"], weakness=item["weakness"], tags=item["tags"], asset_id=item["asset_id"]))
    for item in EQUIPMENT:
        if not session.get(EquipmentDefinitionORM, item["equipment_id"]):
            session.add(EquipmentDefinitionORM(equipment_id=item["equipment_id"], name=item["name"], slot=item["slot"], quality=item["quality"], base_stats=item["base_stats"], effect=item["effect"], asset_id=f"equipment:{item['equipment_id']}"))
    for item in PETS:
        if not session.get(PetDefinitionORM, item["pet_id"]):
            session.add(PetDefinitionORM(pet_id=item["pet_id"], name=item["name"], passive=item["passive"], once_per_battle=item["once_per_battle"], asset_id=f"pet:{item['pet_id']}"))
    banners = [("character_standard", "character", "角色招募", 30, 270, 30, {"guarantee_epic_ten": True}), ("equipment_standard", "equipment", "装备招募", 15, 135, 20, {"guarantee_epic_ten": True})]
    for banner_id, kind, name, single, ten, pity, config in banners:
        if not session.get(GachaBannerORM, banner_id): session.add(GachaBannerORM(banner_id=banner_id, banner_type=kind, name=name, cost_single=single, cost_ten=ten, pity_limit=pity, config=config))
    for index in range(1, 6):
        slot_id = f"slot_{index}"
        if not session.query(TrainingSlotORM).filter_by(profile_id=profile.id, slot_id=slot_id).first(): session.add(TrainingSlotORM(id=_id("slot"), profile_id=profile.id, slot_id=slot_id, level=1, spent_exp=0))
    starter_heroes = ("jinxiao", "leishan", "gulu", "shuangli", "zhenchui")
    for hero_id in starter_heroes:
        if not session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=hero_id).first():
            own = HeroOwnershipORM(id=_id("hero"), profile_id=profile.id, hero_id=hero_id, stars=1, fragments=0, awakened=False); session.add(own); session.flush(); session.add(HeroStarProgressORM(id=_id("star"), ownership_id=own.id, current_stars=1, fragments=0)); session.add(HeroAwakeningORM(id=_id("awakening"), ownership_id=own.id, unlocked=False))
    if not session.query(LineupPresetORM).filter_by(profile_id=profile.id).first():
        lineup = LineupPresetORM(lineup_id=_id("lineup"), profile_id=profile.id, name="初始远征队", stance="balanced", active=True); session.add(lineup); session.flush()
        for index, hero_id in enumerate(starter_heroes):
            session.add(LineupMemberORM(id=_id("member"), lineup_id=lineup.lineup_id, slot_id=f"cell_{index}", hero_id=hero_id))
        profile.active_lineup_id = lineup.lineup_id
    else:
        active_lineup = session.get(LineupPresetORM, profile.active_lineup_id) if profile.active_lineup_id else None
        if active_lineup and active_lineup.name == "初始远征队":
            existing_members = session.query(LineupMemberORM).filter_by(lineup_id=active_lineup.lineup_id).order_by(LineupMemberORM.slot_id).all()
            if len(existing_members) == 5 and sum(bool(member.hero_id and not member.hero_id.startswith("__empty_")) for member in existing_members) < 5:
                for member, hero_id in zip(existing_members, starter_heroes):
                    member.hero_id = hero_id
        if active_lineup:
            active_lineup.formation_type = "grid_3x3"
            existing_members = session.query(LineupMemberORM).filter_by(lineup_id=active_lineup.lineup_id).order_by(LineupMemberORM.slot_id).all()
            aliases = {f"slot_{index + 1}": f"cell_{index}" for index in range(5)}
            for member in existing_members:
                if member.slot_id in aliases:
                    member.slot_id = aliases[member.slot_id]
    _register_assets(session)
    session.flush(); return profile


def _register_assets(session) -> None:
    if not ASSET_ROOT.exists(): return
    for path in ASSET_ROOT.rglob("*.svg"):
        relative = path.relative_to(ROOT / "frontend" / "public").as_posix()
        asset_id = f"hero:{path.parent.name}:{path.stem}"
        if not session.get(ExpeditionAssetRegistryORM, asset_id):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            session.add(ExpeditionAssetRegistryORM(asset_id=asset_id, asset_type=path.stem, character_id=path.parent.name, path=f"/{relative}", source="generated", author="MentorFlow", license="original", generated_by="programmatic_svg", attribution="Original MentorFlow generated asset", sha256=digest))


def _public_profile(session, profile: ExpeditionProfileORM) -> dict[str, Any]:
    heroes = session.query(HeroOwnershipORM).filter_by(profile_id=profile.id).all()
    slots = session.query(TrainingSlotORM).filter_by(profile_id=profile.id).order_by(TrainingSlotORM.slot_id).all()
    state = _ticket_state(profile)
    cycle_minutes = _cycle_minutes(session, profile)
    return {"profile_id": profile.id, "training_exp": profile.training_exp, "crystals": profile.crystals, "total_verified_minutes": _verified_total_minutes(session, profile), "current_stage": profile.current_stage, "active_lineup_id": profile.active_lineup_id, "revision": profile.revision, "heroes_owned": len(heroes), "training": {"level": int(profile.training_level or 1), "available_exp": int(profile.training_exp or 0), "spent_exp": int(profile.training_spent_exp or 0), "max_level": 100, "shared": True}, "training_slots": [{"slot_id": s.slot_id, "level": int(profile.training_level or 1), "spent_exp": int(profile.training_spent_exp or 0), "max_level": 100, "shared": True} for s in slots], "daily_verified_minutes": cycle_minutes, "reward_cycle_key": _cycle_key(), "tickets": state.get("tickets", {}), "gacha_state": state}


def get_profile() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); session.commit(); return _public_profile(session, profile)


def _publish_verified_learning_reward(workspace_id: str, learning_run_id: str, standard_minutes: int, *, phase: str = "first_pass_complete", released: bool = True, duration_source: str = "verified_explicit", activity_seconds: int | None = None) -> dict[str, Any]:
    """Only called by the trusted Learning Run terminal path, never by client input."""
    minutes = max(0, min(24 * 60, int(standard_minutes)))
    if minutes <= 0: return {"granted": 0, "reason": "no_standard_minutes"}
    init_orm()
    key = f"learning_run:{learning_run_id}:phase:{phase}"
    with SessionLocal() as session:
        profile = _seed(session)
        existing = session.query(VerifiedLearningRewardLedgerORM).filter_by(idempotency_key=key).first()
        if existing: return {"granted": existing.released_minutes, "ledger_id": existing.id, "idempotent": True}
        released_minutes = minutes if released else 0
        ledger = VerifiedLearningRewardLedgerORM(id=_id("reward"), profile_id=profile.id, workspace_id=workspace_id, learning_run_id=learning_run_id, standard_minutes=minutes, released_minutes=released_minutes, frozen_minutes=minutes - released_minutes, idempotency_key=key, metadata_json={"phase": phase, "formula": "1 verified active minute = 1 exp + 1 crystal", "duration_source": duration_source, "activity_seconds": activity_seconds})
        session.add(ledger); profile.training_exp += released_minutes; profile.crystals += released_minutes; profile.total_verified_minutes += released_minutes; profile.revision += 1; profile.updated_at = _now(); session.commit()
        return {"granted": released_minutes, "ledger_id": ledger.id, "training_exp": profile.training_exp, "crystals": profile.crystals, "idempotent": False}


def publish_verified_learning_reward(workspace_id: str, learning_run_id: str, standard_minutes: int, *, phase: str = "first_pass_complete", released: bool = True, duration_source: str = "verified_explicit", activity_seconds: int | None = None) -> dict[str, Any]:
    # The unique ledger key is the cross-process guard; this lock also keeps
    # concurrent in-process Learning Run completions from racing SQLite writes.
    with _REWARD_LOCK:
        return _publish_verified_learning_reward(workspace_id, learning_run_id, standard_minutes, phase=phase, released=released, duration_source=duration_source, activity_seconds=activity_seconds)


def get_ledger() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); rows = session.query(VerifiedLearningRewardLedgerORM).filter_by(profile_id=profile.id).order_by(VerifiedLearningRewardLedgerORM.created_at.desc()).all(); session.commit()
        return {"items": [{"ledger_id": row.id, "workspace_id": row.workspace_id, "learning_run_id": row.learning_run_id, "standard_minutes": row.standard_minutes, "released_minutes": row.released_minutes if _reward_is_measured(row) else 0, "frozen_minutes": row.frozen_minutes if _reward_is_measured(row) else row.standard_minutes, "duration_verified": _reward_is_measured(row), "created_at": row.created_at.isoformat()} for row in rows]}


def list_heroes() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); owns = {row.hero_id: row for row in session.query(HeroOwnershipORM).filter_by(profile_id=profile.id).all()}; session.commit()
        return {"items": [{"hero_id": item["hero_id"], "name": item["name"], "rarity": item["rarity"], "role": item["role"], "tags": item["tags"], "asset_id": item["asset_id"], "owned": item["hero_id"] in owns, "stars": owns[item["hero_id"]].stars if item["hero_id"] in owns else 0, "weakness": item["weakness"], "active_skill": item["active_skill"], "passive_one": item["passive_one"], "passive_two": item["passive_two"], "awakening": item["awakening"], "assets": resolve_hero_assets(item["hero_id"])["assets"]} for item in HEROES]}


def hero_detail(hero_id: str) -> dict[str, Any]:
    item = next((item for item in HEROES if item["hero_id"] == hero_id), None)
    if not item: raise ExpeditionError("HERO_NOT_FOUND", "角色不存在", 404)
    result = next((row for row in list_heroes()["items"] if row["hero_id"] == hero_id), {})
    resolved = resolve_hero_assets(hero_id)
    return {**item, **result, "assets": resolved["assets"], "portrait_url": resolved["assets"]["portrait"]["url"], "skill_cut_in_url": resolved["assets"]["skill_cutin"]["url"], "skill_icon_url": resolved["assets"]["skill_icon"]["url"], "fallback_used": resolved["fallback_used"]}


def star_up(hero_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); own = session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=hero_id).first()
        if not own: raise ExpeditionError("HERO_NOT_OWNED", "尚未拥有该角色", 409)
        if own.stars >= 5: raise ExpeditionError("STAR_MAX", "角色已达到五星")
        cost = 20 * (2 ** (own.stars - 1))
        if own.fragments < cost: raise ExpeditionError("INSUFFICIENT_FRAGMENTS", "角色碎片不足", 409, {"required": cost, "available": own.fragments})
        own.fragments -= cost; own.stars += 1; progress = session.query(HeroStarProgressORM).filter_by(ownership_id=own.id).first(); progress.fragments = own.fragments; progress.current_stars = own.stars; session.commit(); return hero_detail(hero_id)


def awaken(hero_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); own = session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=hero_id).first()
        if not own: raise ExpeditionError("HERO_NOT_OWNED", "尚未拥有该角色", 409)
        if own.stars < 5: raise ExpeditionError("AWAKENING_LOCKED", "五星后才能觉醒", 409)
        own.awakened = True; marker = session.query(HeroAwakeningORM).filter_by(ownership_id=own.id).first(); marker.unlocked = True; marker.unlocked_at = _now(); session.commit(); return hero_detail(hero_id)


def training_slots() -> dict[str, Any]:
    profile = get_profile()
    return {"items": profile["training_slots"], "training": profile["training"], "formula": "all heroes inherit one shared training level; no position-specific progression"}


def upgrade_slot(slot_id: str, body: dict[str, Any]) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session)
        if body.get("expected_revision") is not None and int(body["expected_revision"]) != profile.revision: raise ExpeditionError("REVISION_CONFLICT", "训练状态已更新，请刷新", 409)
        current_level = int(profile.training_level or 1)
        levels = max(1, min(100 - current_level, int(body.get("levels", 1))))
        if current_level >= 100: raise ExpeditionError("TRAINING_MAX", "全局训练等级已达到上限", 409)
        cost = sum(100 + (current_level + n) * 20 for n in range(levels))
        if profile.training_exp < cost: raise ExpeditionError("INSUFFICIENT_EXP", "训练经验不足", 409, {"required": cost, "available": profile.training_exp})
        profile.training_exp -= cost; profile.training_level = current_level + levels; profile.training_spent_exp = int(profile.training_spent_exp or 0) + cost; profile.revision += 1; profile.updated_at = _now(); session.commit(); return get_profile()


def reset_slot(slot_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session)
        spent = int(profile.training_spent_exp or 0)
        profile.training_exp += spent; profile.training_level = 1; profile.training_spent_exp = 0; profile.revision += 1; profile.updated_at = _now(); session.commit(); return get_profile()


def _lineup_public(session, row: LineupPresetORM) -> dict[str, Any]:
    members = session.query(LineupMemberORM).filter_by(lineup_id=row.lineup_id).order_by(LineupMemberORM.slot_id).all()
    return {"lineup_id": row.lineup_id, "name": row.name, "stance": row.stance, "active": row.active, "revision": row.revision, "formation_type": row.formation_type or "grid_3x3", "members": [{"slot_id": m.slot_id, "hero_id": "" if m.hero_id.startswith("__empty_") else m.hero_id} for m in members]}


def lineups() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); rows = session.query(LineupPresetORM).filter_by(profile_id=profile.id).all(); session.commit(); return {"items": [_lineup_public(session, row) for row in rows], "formation": {"type": "grid_3x3", "cells": [f"cell_{index}" for index in range(9)], "rules": "从九宫格九个位置中自由选择五个角色；行列决定站位与行动顺序"}}


def save_lineup(body: dict[str, Any], lineup_id: str | None = None) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); row = session.get(LineupPresetORM, lineup_id) if lineup_id else None
        if row and row.profile_id != profile.id: raise ExpeditionError("LINEUP_NOT_FOUND", "阵容不存在", 404)
        members = body.get("members")
        if not isinstance(members, list) or len(members) != 5: raise ExpeditionError("FORMATION_REQUIRES_FIVE", "阵容必须包含 5 个角色")
        hero_ids = [str(item.get("hero_id") or "") for item in members]
        aliases = {f"slot_{index + 1}": f"cell_{index}" for index in range(5)}
        cell_ids = [aliases.get(str(item.get("slot_id") or ""), str(item.get("slot_id") or "")) for item in members]
        if len(set(cell_ids)) != len(cell_ids) or any(cell_id not in {f"cell_{index}" for index in range(9)} for cell_id in cell_ids):
            raise ExpeditionError("INVALID_FORMATION_CELL", "站位必须来自九宫格的 9 个合法位置")
        owned = {x.hero_id for x in session.query(HeroOwnershipORM).filter_by(profile_id=profile.id).all()}
        filled = [x for x in hero_ids if x]
        if len(set(filled)) != len(filled): raise ExpeditionError("DUPLICATE_HERO", "同一阵容不能重复角色")
        if any(hero_id not in owned for hero_id in filled): raise ExpeditionError("HERO_NOT_OWNED", "只能使用已拥有角色")
        if not row: row = LineupPresetORM(lineup_id=_id("lineup"), profile_id=profile.id, name=str(body.get("name") or "未命名阵容"), formation_type="grid_3x3", stance=str(body.get("stance") or "balanced")); session.add(row); session.flush()
        else:
            if body.get("expected_revision") is not None and int(body["expected_revision"]) != row.revision: raise ExpeditionError("REVISION_CONFLICT", "阵容已更新，请刷新", 409)
            session.query(LineupMemberORM).filter_by(lineup_id=row.lineup_id).delete(); row.name = str(body.get("name") or row.name); row.stance = str(body.get("stance") or row.stance); row.revision += 1
        row.formation_type = "grid_3x3"
        for item, slot_id in zip(members, cell_ids):
            hero_id = str(item.get("hero_id") or f"__empty_{slot_id}")
            session.add(LineupMemberORM(id=_id("member"), lineup_id=row.lineup_id, slot_id=slot_id, hero_id=hero_id))
        session.commit(); return {"item": _lineup_public(session, row)}


def activate_lineup(lineup_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); row = session.get(LineupPresetORM, lineup_id)
        if not row or row.profile_id != profile.id: raise ExpeditionError("LINEUP_NOT_FOUND", "阵容不存在", 404)
        for item in session.query(LineupPresetORM).filter_by(profile_id=profile.id).all(): item.active = item.lineup_id == lineup_id
        profile.active_lineup_id = lineup_id; profile.revision += 1; session.commit(); return {"item": _lineup_public(session, row)}


def delete_lineup(lineup_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); row = session.get(LineupPresetORM, lineup_id)
        if not row or row.profile_id != profile.id: raise ExpeditionError("LINEUP_NOT_FOUND", "阵容不存在", 404)
        if row.active: raise ExpeditionError("ACTIVE_LINEUP_LOCKED", "当前阵容不能删除")
        session.delete(row); session.commit(); return lineups()


def equipment() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); instances = session.query(EquipmentInstanceORM).filter_by(profile_id=profile.id).all(); session.commit(); return {"definitions": EQUIPMENT, "instances": [{"instance_id": i.instance_id, "equipment_id": i.equipment_id, "level": i.level, "equipped_hero_id": i.equipped_hero_id} for i in instances]}


def equip_equipment(instance_id: str, hero_id: str | None) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); item = session.get(EquipmentInstanceORM, instance_id)
        if not item or item.profile_id != profile.id: raise ExpeditionError("EQUIPMENT_NOT_FOUND", "装备不存在", 404)
        if hero_id:
            if not session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=hero_id).first(): raise ExpeditionError("HERO_NOT_OWNED", "角色未拥有", 409)
            definition = session.get(EquipmentDefinitionORM, item.equipment_id)
            for other in session.query(EquipmentInstanceORM).filter_by(profile_id=profile.id, equipped_hero_id=hero_id).all():
                other_def = session.get(EquipmentDefinitionORM, other.equipment_id)
                if other_def and definition and other_def.slot == definition.slot: other.equipped_hero_id = ""
        item.equipped_hero_id = hero_id or ""; item.revision += 1; session.commit(); return equipment()


def unequip_equipment(instance_id: str) -> dict[str, Any]: return equip_equipment(instance_id, None)


def upgrade_equipment(instance_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); item = session.get(EquipmentInstanceORM, instance_id)
        if not item or item.profile_id != profile.id: raise ExpeditionError("EQUIPMENT_NOT_FOUND", "装备不存在", 404)
        item.level = min(100, item.level + 1); item.revision += 1; session.commit(); return equipment()


def reset_equipment(instance_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); item = session.get(EquipmentInstanceORM, instance_id)
        if not item or item.profile_id != profile.id: raise ExpeditionError("EQUIPMENT_NOT_FOUND", "装备不存在", 404)
        item.level = 1; item.equipped_hero_id = ""; item.revision += 1; session.commit(); return equipment()


def pets() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); owns = session.query(PetOwnershipORM).filter_by(profile_id=profile.id).all(); session.commit(); return {"definitions": PETS, "owned": [{"pet_id": p.pet_id, "equipped_hero_id": p.equipped_hero_id} for p in owns]}


def equip_pet(pet_id: str, hero_id: str | None) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); item = session.query(PetOwnershipORM).filter_by(profile_id=profile.id, pet_id=pet_id).first()
        if not item: raise ExpeditionError("PET_NOT_OWNED", "尚未拥有该宠物", 409)
        if hero_id and not session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=hero_id).first(): raise ExpeditionError("HERO_NOT_OWNED", "角色未拥有", 409)
        item.equipped_hero_id = hero_id or ""; session.commit(); return pets()


def gacha_banners() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        _seed(session); rows = session.query(GachaBannerORM).all(); session.commit(); return {"items": [{"banner_id": row.banner_id, "banner_type": row.banner_type, "name": row.name, "cost_single": row.cost_single, "cost_ten": row.cost_ten, "pity_limit": row.pity_limit, "config": row.config} for row in rows]}


def gacha_pull(body: dict[str, Any]) -> dict[str, Any]:
    init_orm(); banner_id = str(body.get("banner_id") or "character_standard"); count = int(body.get("count") or 1); idem = str(body.get("idempotency_key") or "").strip(); currency = str(body.get("currency") or "crystals")
    if count not in {1, 10}: raise ExpeditionError("INVALID_PULL_COUNT", "只能单抽或十连")
    if currency not in {"crystals", "ticket"}: raise ExpeditionError("INVALID_PULL_CURRENCY", "抽卡货币不受支持")
    if currency == "ticket" and count != 10: raise ExpeditionError("TICKET_ONLY_TEN_PULL", "学习券只能用于十连")
    if not idem: raise ExpeditionError("IDEMPOTENCY_REQUIRED", "抽卡必须提供幂等键")
    with SessionLocal() as session:
        profile = _seed(session); banner = session.get(GachaBannerORM, banner_id)
        if not banner: raise ExpeditionError("BANNER_NOT_FOUND", "卡池不存在", 404)
        previous = session.query(GachaPullRecordORM).filter_by(idempotency_key=idem).first()
        if previous: return {"pull_id": previous.pull_id, "results": previous.results, "cost": previous.cost, "idempotent": True}
        cost = banner.cost_ten if count == 10 else banner.cost_single
        state = _ticket_state(profile)
        ticket_type = "character" if banner.banner_type == "character" else "equipment"
        if currency == "ticket":
            available = int((state.get("tickets") or {}).get(ticket_type, 0))
            if available < 1: raise ExpeditionError("INSUFFICIENT_TICKETS", "当前刷新周期没有可用的十连券", 409, {"ticket_type": ticket_type, "available": available})
        elif profile.crystals < cost:
            raise ExpeditionError("INSUFFICIENT_CRYSTALS", "学习水晶不足", 409, {"required": cost, "available": profile.crystals})
        seed = stable_seed(profile.id, banner_id, idem, profile.revision); rng = __import__("random").Random(seed); pity = int(state.get(banner_id, 0)); results = []
        if banner.banner_type == "character":
            pools = {rarity: [h["hero_id"] for h in HEROES if h["rarity"] == rarity] for rarity in ("rare", "epic", "legendary")}
            for index in range(count):
                rarity = "legendary" if rng.random() < .05 or pity + index + 1 >= banner.pity_limit else ("epic" if rng.random() < .30 else "rare")
                if count == 10 and index == 0 and rarity == "rare": rarity = "epic"
                hero_id = rng.choice(pools[rarity] or pools["rare"]); results.append({"type": "hero", "hero_id": hero_id, "rarity": rarity, "new": False})
            state[banner_id] = pity + count
        else:
            pool = [item["equipment_id"] for item in EQUIPMENT]; results = [{"type": "equipment", "equipment_id": rng.choice(pool), "rarity": "epic" if count == 10 or rng.random() < .25 else "rare"} for _ in range(count)]; state[banner_id] = pity + count
        if currency == "ticket":
            state["tickets"][ticket_type] = int(state["tickets"].get(ticket_type, 0)) - 1
        else:
            profile.crystals -= cost
        profile.gacha_state = state; profile.revision += 1
        for result in results:
            if result["type"] == "hero":
                own = session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=result["hero_id"]).first()
                if own: own.fragments += 10; result["new"] = False
                else:
                    own = HeroOwnershipORM(id=_id("hero"), profile_id=profile.id, hero_id=result["hero_id"], stars=1, fragments=0, awakened=False); session.add(own); session.flush(); session.add(HeroStarProgressORM(id=_id("star"), ownership_id=own.id, current_stars=1, fragments=0)); session.add(HeroAwakeningORM(id=_id("awakening"), ownership_id=own.id, unlocked=False)); result["new"] = True
            else: session.add(EquipmentInstanceORM(instance_id=_id("equipment"), profile_id=profile.id, equipment_id=result["equipment_id"], level=1))
        record = GachaPullRecordORM(pull_id=_id("pull"), profile_id=profile.id, banner_id=banner_id, count=count, cost=0 if currency == "ticket" else cost, results=results, seed=seed, idempotency_key=idem); session.add(record); session.commit(); return {"pull_id": record.pull_id, "results": results, "cost": 0 if currency == "ticket" else cost, "currency": currency, "tickets": state.get("tickets", {}), "crystals": profile.crystals, "idempotent": False}


def gacha_history() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); rows = session.query(GachaPullRecordORM).filter_by(profile_id=profile.id).order_by(GachaPullRecordORM.created_at.desc()).limit(50).all(); session.commit(); return {"items": [{"pull_id": row.pull_id, "banner_id": row.banner_id, "count": row.count, "cost": row.cost, "results": row.results, "created_at": row.created_at.isoformat()} for row in rows]}


def stage_definition(number: int) -> dict[str, Any]:
    if number < 1: raise ExpeditionError("INVALID_STAGE", "关卡编号必须为正数")
    seed = stable_seed("stage", number); cached = None
    init_orm()
    with SessionLocal() as session:
        cached = session.get(ExpeditionStageDefinitionCacheORM, number)
        if cached and isinstance(cached.definition, dict) and cached.definition.get("difficulty_version") == DIFFICULTY_VERSION and isinstance(cached.definition.get("difficulty"), dict):
            result = cached.definition
            session.commit()
            return result
        enemy_ids = stage_enemy_ids(number)
        boss = BOSSES[(number // 10 - 1) % len(BOSSES)] if number % 10 == 0 else None
        result = {"stage_number": number, "seed": seed, "chapter": (number - 1) // 50 + 1, "theme": ["雾港档案", "星轨温室", "逆光剧场", "深层回廊"][(number - 1) // 50 % 4], "affix": ["无", "灼热回响", "脆弱护盾", "能量潮汐"][(number - 1) % 4], "enemy_ids": enemy_ids, "boss": boss, "difficulty": stage_difficulty(number), "difficulty_version": DIFFICULTY_VERSION, "recommended_level": 1 + (number - 1) // 5, "rewards": {"tickets": 1 + number // 10, "equipment_material": 2 + number // 5, "pet_egg": number % 25 == 0}}
        if cached:
            cached.seed = seed
            cached.definition = result
        else:
            session.add(ExpeditionStageDefinitionCacheORM(stage_number=number, seed=seed, definition=result))
        session.commit(); return result


def stages() -> dict[str, Any]:
    profile = get_profile(); return {"current_stage": profile["current_stage"], "current": stage_definition(profile["current_stage"]), "infinite": True, "rules": "每10关首领，每50关章节机制，每100关背景池"}


def challenge_stage(number: int, body: dict[str, Any]) -> dict[str, Any]:
    definition = stage_definition(number)
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); lineup = session.get(LineupPresetORM, profile.active_lineup_id)
        if not lineup: raise ExpeditionError("LINEUP_REQUIRED", "请先设置阵容")
        members = session.query(LineupMemberORM).filter_by(lineup_id=lineup.lineup_id).order_by(LineupMemberORM.slot_id).all(); active_members = [m for m in members if m.hero_id and not m.hero_id.startswith("__empty_")]; hero_ids = [m.hero_id for m in active_members]
        if len(hero_ids) != 5: raise ExpeditionError("FORMATION_REQUIRES_FIVE", "挑战关卡前需要完整的五人阵容")
        definitions = {h["hero_id"]: h for h in HEROES}; heroes = [{**definitions[m.hero_id], "cell_id": m.slot_id} for m in active_members]
        seed = stable_seed(profile.id, number, lineup.lineup_id, lineup.revision, tuple(hero_ids), definition["seed"]); result = BattleEngine(seed=seed, heroes=heroes, enemy_ids=definition["enemy_ids"], stance=lineup.stance, difficulty=definition.get("difficulty"), training_level=int(profile.training_level or 1)).run()
        battle_id = _id("battle"); record = BattleRecordORM(battle_id=battle_id, profile_id=profile.id, stage_number=number, lineup_id=lineup.lineup_id, seed=seed, status="completed", winner=result["winner"], result={k: v for k, v in result.items() if k != "events"}); session.add(record)
        for event in result["events"]: session.add(BattleEventLogORM(id=_id("event"), battle_id=battle_id, event_index=event["event_index"], event_type=event["event_type"], actor_id=event["actor_id"], target_ids=event["target_ids"], source_skill_id=event["source_skill_id"], numeric_values=event["numeric_values"], status_ids=event["status_ids"], is_critical=event["is_critical"], is_miss=event["is_miss"], animation_hint=event["animation_hint"], timestamp_offset=event["timestamp_offset"], rng_trace=event["rng_trace"]))
        progress = session.query(ExpeditionStageProgressORM).filter_by(profile_id=profile.id, stage_number=number).first() or ExpeditionStageProgressORM(id=_id("stage"), profile_id=profile.id, stage_number=number, status="available"); progress.status = "cleared" if result["winner"] == "ally" else "failed"; progress.best_result = result["units"]; progress.last_battle_id = battle_id; session.add(progress)
        if result["winner"] == "ally" and number >= profile.current_stage: profile.current_stage = number + 1
        profile.revision += 1; session.commit(); return {"battle_id": battle_id, "stage": definition, "result": {k: v for k, v in result.items() if k != "events"}, "event_count": len(result["events"])}


def claim_stage(number: int) -> dict[str, Any]:
    definition = stage_definition(number)
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); progress = session.query(ExpeditionStageProgressORM).filter_by(profile_id=profile.id, stage_number=number).first()
        if not progress or progress.status != "cleared": raise ExpeditionError("STAGE_NOT_CLEARED", "请先完成关卡", 409)
        if progress.best_result.get("claimed"): return {"stage_number": number, "reward": progress.best_result["reward"], "idempotent": True}
        reward = definition["rewards"]
        if reward.get("pet_egg"):
            pet_id = PETS[(number // 25 - 1) % len(PETS)]["pet_id"]
            if not session.query(PetOwnershipORM).filter_by(profile_id=profile.id, pet_id=pet_id).first(): session.add(PetOwnershipORM(id=_id("pet"), profile_id=profile.id, pet_id=pet_id)); reward = {**reward, "pet_id": pet_id}
        progress.best_result = {**progress.best_result, "claimed": True, "reward": reward}; session.commit(); return {"stage_number": number, "reward": reward, "idempotent": False}


def battle(battle_id: str, events: bool = False) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        row = session.get(BattleRecordORM, battle_id)
        if not row: raise ExpeditionError("BATTLE_NOT_FOUND", "战斗不存在", 404)
        payload = {"battle_id": row.battle_id, "stage_number": row.stage_number, "lineup_id": row.lineup_id, "seed": row.seed, "status": row.status, "winner": row.winner, "result": row.result, "created_at": row.created_at.isoformat()}
        if events:
            units = {str(item.get("id")): item for item in (row.result.get("units") or []) if isinstance(item, dict)}
            payload["events"] = [_event_public(x, units) for x in session.query(BattleEventLogORM).filter_by(battle_id=battle_id).order_by(BattleEventLogORM.event_index).all()]
        session.commit(); return payload


def _event_public(row: BattleEventLogORM, units: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    units = units or {}
    return {
        "event_index": row.event_index,
        "event_type": row.event_type,
        "actor_id": row.actor_id,
        "target_ids": row.target_ids,
        "source_skill_id": row.source_skill_id,
        "numeric_values": row.numeric_values,
        "status_ids": row.status_ids,
        "is_critical": row.is_critical,
        "is_miss": row.is_miss,
        "animation_hint": row.animation_hint,
        "timestamp_offset": row.timestamp_offset,
        "rng_trace": row.rng_trace,
        "actor_hero_id": units.get(row.actor_id, {}).get("hero_id", ""),
        "target_hero_ids": [units.get(target_id, {}).get("hero_id", target_id) for target_id in (row.target_ids or [])],
    }


def claim_milestone(milestone_id: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session)
        required = {"verified_60": 60, "verified_180": 180}.get(milestone_id)
        if required is None: raise ExpeditionError("MILESTONE_NOT_FOUND", "里程碑不存在", 404)
        ticket_type = "equipment" if required == 60 else "character"
        cycle_minutes = _cycle_minutes(session, profile)
        state = _ticket_state(profile)
        earned = cycle_minutes // required
        claimed = int(state["claimed_tickets"].get(ticket_type, 0))
        grant = earned - claimed
        if grant <= 0:
            raise ExpeditionError("MILESTONE_LOCKED", "当前刷新周期还没有新的可兑换十连券", 409, {"required": required, "current": cycle_minutes, "ticket_type": ticket_type})
        state["claimed_tickets"][ticket_type] = earned
        state["tickets"][ticket_type] = int(state["tickets"].get(ticket_type, 0)) + grant
        profile.gacha_state = state; profile.revision += 1; profile.updated_at = _now(); session.commit()
        return {"milestone_id": milestone_id, "reward": {"type": "learning_ticket", "ticket_type": ticket_type, "count": grant, "cycle_key": _cycle_key()}, "tickets": state["tickets"], "idempotent": False}


def milestones() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        profile = _seed(session); state = _ticket_state(profile); cycle_minutes = _cycle_minutes(session, profile)
        items = []
        for milestone_id, required, ticket_type, label in (("verified_60", 60, "equipment", "60 分钟 → 1 张装备十连券"), ("verified_180", 180, "character", "180 分钟 → 1 张角色十连券")):
            earned = cycle_minutes // required
            claimed = int(state["claimed_tickets"].get(ticket_type, 0))
            items.append({"milestone_id": milestone_id, "label": label, "required_minutes": required, "ticket_type": ticket_type, "earned_count": earned, "claimable_count": max(0, earned - claimed), "claimed": earned > 0 and earned == claimed})
        session.commit()
        return {"items": items, "total_verified_minutes": _verified_total_minutes(session, profile), "daily_verified_minutes": cycle_minutes, "reward_cycle_key": _cycle_key(), "tickets": state.get("tickets", {}), "rules": "每天 00:00（Asia/Shanghai）刷新；只统计 Learning Run 通过验证后的真实活跃学习分钟"}


def assets(asset_id: str) -> dict[str, Any]:
    if any(part in asset_id for part in ("..", "/", "\\")): raise ExpeditionError("INVALID_ASSET_ID", "资源标识非法", 400)
    init_orm()
    with SessionLocal() as session:
        _seed(session); row = session.get(ExpeditionAssetRegistryORM, asset_id)
        if not row: raise ExpeditionError("ASSET_NOT_FOUND", "资源不存在", 404)
        session.commit(); return {"asset_id": row.asset_id, "asset_type": row.asset_type, "character_id": row.character_id, "path": row.path, "source": row.source, "author": row.author, "license": row.license, "generated_by": row.generated_by, "attribution": row.attribution, "sha256": row.sha256}
