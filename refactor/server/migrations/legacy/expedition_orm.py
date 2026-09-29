"""SQLAlchemy persistence models for the global Learning Expedition subsystem.

The expedition is deliberately scoped to the local profile rather than a
Workspace.  Learning rewards retain workspace/run provenance in the ledger,
while combat state, collections and lineups live in the shared expedition
profile.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .orm import Base


def expedition_now() -> datetime:
    return datetime.now(timezone.utc)


class ExpeditionProfileORM(Base):
    __tablename__ = "orm_expedition_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    training_exp: Mapped[int] = mapped_column(Integer, default=0)
    training_level: Mapped[int] = mapped_column(Integer, default=1)
    training_spent_exp: Mapped[int] = mapped_column(Integer, default=0)
    crystals: Mapped[int] = mapped_column(Integer, default=0)
    total_verified_minutes: Mapped[int] = mapped_column(Integer, default=0)
    current_stage: Mapped[int] = mapped_column(Integer, default=1)
    active_lineup_id: Mapped[str] = mapped_column(String(64), default="")
    gacha_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class VerifiedLearningRewardLedgerORM(Base):
    __tablename__ = "orm_verified_learning_reward_ledger"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    learning_run_id: Mapped[str] = mapped_column(String(64), index=True)
    standard_minutes: Mapped[int] = mapped_column(Integer)
    released_minutes: Mapped[int] = mapped_column(Integer, default=0)
    frozen_minutes: Mapped[int] = mapped_column(Integer, default=0)
    reward_type: Mapped[str] = mapped_column(String(40), default="verified_learning")
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class TrainingSlotORM(Base):
    __tablename__ = "orm_training_slots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    slot_id: Mapped[str] = mapped_column(String(16))
    level: Mapped[int] = mapped_column(Integer, default=1)
    spent_exp: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("profile_id", "slot_id", name="uq_expedition_training_slot"),)


class HeroDefinitionORM(Base):
    __tablename__ = "orm_expedition_hero_definitions"
    hero_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    rarity: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(80))
    base_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normal_attack: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active_skill: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    passive_one: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    passive_two: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    awakening: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    weakness: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    asset_id: Mapped[str] = mapped_column(String(128), default="")


class HeroOwnershipORM(Base):
    __tablename__ = "orm_expedition_hero_ownership"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    hero_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_hero_definitions.hero_id"), index=True)
    stars: Mapped[int] = mapped_column(Integer, default=1)
    fragments: Mapped[int] = mapped_column(Integer, default=0)
    awakened: Mapped[bool] = mapped_column(Boolean, default=False)
    owned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    __table_args__ = (UniqueConstraint("profile_id", "hero_id", name="uq_expedition_hero_owned"),)


class HeroStarProgressORM(Base):
    __tablename__ = "orm_expedition_hero_star_progress"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ownership_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_hero_ownership.id", ondelete="CASCADE"), unique=True)
    current_stars: Mapped[int] = mapped_column(Integer, default=1)
    fragments: Mapped[int] = mapped_column(Integer, default=0)


class HeroAwakeningORM(Base):
    __tablename__ = "orm_expedition_hero_awakening"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ownership_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_hero_ownership.id", ondelete="CASCADE"), unique=True)
    unlocked: Mapped[bool] = mapped_column(Boolean, default=False)
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EquipmentDefinitionORM(Base):
    __tablename__ = "orm_expedition_equipment_definitions"
    equipment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slot: Mapped[str] = mapped_column(String(16))
    quality: Mapped[str] = mapped_column(String(16))
    base_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    effect: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    asset_id: Mapped[str] = mapped_column(String(128), default="")


class EquipmentInstanceORM(Base):
    __tablename__ = "orm_expedition_equipment_instances"
    instance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    equipment_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_equipment_definitions.equipment_id"), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    equipped_hero_id: Mapped[str] = mapped_column(String(64), default="")
    revision: Mapped[int] = mapped_column(Integer, default=0)


class PetDefinitionORM(Base):
    __tablename__ = "orm_expedition_pet_definitions"
    pet_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    passive: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    once_per_battle: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    asset_id: Mapped[str] = mapped_column(String(128), default="")


class PetOwnershipORM(Base):
    __tablename__ = "orm_expedition_pet_ownership"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    pet_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_pet_definitions.pet_id"), index=True)
    equipped_hero_id: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("profile_id", "pet_id", name="uq_expedition_pet_owned"),)


class LineupPresetORM(Base):
    __tablename__ = "orm_expedition_lineup_presets"
    lineup_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    formation_type: Mapped[str] = mapped_column(String(32), default="grid_3x3")
    stance: Mapped[str] = mapped_column(String(16), default="balanced")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class LineupMemberORM(Base):
    __tablename__ = "orm_expedition_lineup_members"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lineup_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_lineup_presets.lineup_id", ondelete="CASCADE"), index=True)
    slot_id: Mapped[str] = mapped_column(String(16))
    hero_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("lineup_id", "slot_id", name="uq_expedition_lineup_slot"), UniqueConstraint("lineup_id", "hero_id", name="uq_expedition_lineup_hero"))


class GachaBannerORM(Base):
    __tablename__ = "orm_expedition_gacha_banners"
    banner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    banner_type: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(120))
    cost_single: Mapped[int] = mapped_column(Integer)
    cost_ten: Mapped[int] = mapped_column(Integer)
    pity_limit: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class GachaPullRecordORM(Base):
    __tablename__ = "orm_expedition_gacha_pull_records"
    pull_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    banner_id: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(Integer)
    results: Mapped[list[Any]] = mapped_column(JSON, default=list)
    seed: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class ExpeditionStageProgressORM(Base):
    __tablename__ = "orm_expedition_stage_progress"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    stage_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="available")
    best_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_battle_id: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("profile_id", "stage_number", name="uq_expedition_stage_progress"),)


class ExpeditionStageDefinitionCacheORM(Base):
    __tablename__ = "orm_expedition_stage_definition_cache"
    stage_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class BattleRecordORM(Base):
    __tablename__ = "orm_expedition_battle_records"
    battle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    stage_number: Mapped[int] = mapped_column(Integer)
    lineup_id: Mapped[str] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="completed")
    winner: Mapped[str] = mapped_column(String(16), default="draw")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class BattleEventLogORM(Base):
    __tablename__ = "orm_expedition_battle_event_logs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    battle_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_battle_records.battle_id", ondelete="CASCADE"), index=True)
    event_index: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64), default="")
    target_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    source_skill_id: Mapped[str] = mapped_column(String(80), default="")
    numeric_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    is_miss: Mapped[bool] = mapped_column(Boolean, default=False)
    animation_hint: Mapped[str] = mapped_column(String(64), default="")
    timestamp_offset: Mapped[int] = mapped_column(Integer, default=0)
    rng_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MilestoneRewardClaimORM(Base):
    __tablename__ = "orm_expedition_milestone_reward_claims"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    milestone_id: Mapped[str] = mapped_column(String(80))
    reward: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    __table_args__ = (UniqueConstraint("profile_id", "milestone_id", name="uq_expedition_milestone_claim"),)


class SkinOwnershipORM(Base):
    __tablename__ = "orm_expedition_skin_ownership"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_profiles.id", ondelete="CASCADE"), index=True)
    skin_id: Mapped[str] = mapped_column(String(80))
    hero_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("profile_id", "skin_id", name="uq_expedition_skin_owned"),)


class ExpeditionAssetRegistryORM(Base):
    __tablename__ = "orm_expedition_asset_registry"
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    character_id: Mapped[str] = mapped_column(String(64), default="")
    path: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="generated")
    author: Mapped[str] = mapped_column(String(120), default="MentorFlow")
    license: Mapped[str] = mapped_column(String(80), default="original")
    generated_by: Mapped[str] = mapped_column(String(120), default="programmatic_svg")
    attribution: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(128), default="")


class ExpeditionArtPackORM(Base):
    """Logical art-pack identity; versions are immutable once installed."""

    __tablename__ = "orm_expedition_art_packs"
    pack_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    source: Mapped[str] = mapped_column(String(64), default="upstream_approved_art")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)


class ExpeditionArtPackVersionORM(Base):
    __tablename__ = "orm_expedition_art_pack_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[str] = mapped_column(ForeignKey("orm_expedition_art_packs.pack_id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="PREVIEWED")
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_hash: Mapped[str] = mapped_column(String(128), default="")
    hero_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default="upstream_approved_art")
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    asset_path: Mapped[str] = mapped_column(Text, default="")
    previous_active_pack_id: Mapped[str] = mapped_column(String(96), default="")
    previous_active_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    __table_args__ = (UniqueConstraint("pack_id", "version", name="uq_expedition_art_pack_version"),)


class ExpeditionHeroAssetORM(Base):
    __tablename__ = "orm_expedition_hero_assets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(96), index=True)
    pack_version: Mapped[str] = mapped_column(String(32), index=True)
    hero_id: Mapped[str] = mapped_column(String(64), index=True)
    asset_kind: Mapped[str] = mapped_column(String(32))
    asset_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(80), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    focal_point: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    offset: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(64), default="upstream_approved_art")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=expedition_now)
    __table_args__ = (UniqueConstraint("pack_id", "pack_version", "hero_id", "asset_kind", name="uq_expedition_hero_pack_asset"),)


class ExpeditionArtReviewORM(Base):
    __tablename__ = "orm_expedition_art_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(96), index=True)
    pack_version: Mapped[str] = mapped_column(String(32), index=True)
    hero_id: Mapped[str] = mapped_column(String(64))
    asset_kind: Mapped[str] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("pack_id", "pack_version", "hero_id", "asset_kind", name="uq_expedition_art_review"),)


class ExpeditionActiveArtPackORM(Base):
    __tablename__ = "orm_expedition_active_art_pack"
    singleton_id: Mapped[str] = mapped_column(String(16), primary_key=True, default="active")
    pack_id: Mapped[str] = mapped_column(String(96), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    previous_pack_id: Mapped[str] = mapped_column(String(96), default="")
    previous_version: Mapped[str] = mapped_column(String(32), default="")
