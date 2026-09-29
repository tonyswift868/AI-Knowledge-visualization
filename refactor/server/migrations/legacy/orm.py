"""Frozen metadata required by revisions 0001/0010/0011/0012; never imported by app."""
from datetime import datetime, date, timezone
from typing import Any
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
def utcnow(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
# Keep v1 knowledge-map/teacher tables on a dedicated metadata registry.  The
# 0012 migration (and legacy request bootstrap) intentionally use
# ``Base.metadata.create_all``; keeping these tables separate guarantees that
# 0013, rather than request handling, is the only creator of their schema.
class KnowledgeMapBase(DeclarativeBase): pass
MIGRATION_OWNED_KNOWLEDGE_TABLES = frozenset({
    'orm_source_revisions',
    'orm_conversion_manifests',
    'orm_knowledge_map_revisions',
    'orm_knowledge_nodes',
    'orm_knowledge_edges',
    'orm_plan_execution_tasks',
    'orm_plan_mastery_events',
    'orm_learning_resources',
    'orm_planning_assistant_cache',
    'orm_planning_resource_pools',
    'orm_planning_resource_candidates',
    'orm_planning_resource_pool_events',
    'orm_planning_resource_pool_requests',
    'orm_video_collections',
    'orm_video_episodes',
    'orm_video_subtitle_segments',
    'orm_video_collection_jobs',
    'orm_video_summaries',
})
class WorkspaceORM(Base):
    __tablename__='orm_workspaces'; id:Mapped[str]=mapped_column(String(64),primary_key=True); title:Mapped[str]=mapped_column(String(120)); description:Mapped[str]=mapped_column(Text,default=''); character_id:Mapped[str]=mapped_column(String(32),default='warm'); allow_external_knowledge:Mapped[bool]=mapped_column(Boolean,default=False); execution_mode:Mapped[str]=mapped_column(String(16),default='demo'); provider_name:Mapped[str]=mapped_column(String(64),default='fake'); provider_model:Mapped[str]=mapped_column(String(128),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); sources:Mapped[list['SourceORM']]=relationship(cascade='all,delete-orphan'); goals:Mapped[list['GoalORM']]=relationship(cascade='all,delete-orphan'); plans:Mapped[list['PlanORM']]=relationship(cascade='all,delete-orphan')
class GoalORM(Base):
    __tablename__='orm_goals'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),unique=True); goal_statement:Mapped[str]=mapped_column(Text); motivation:Mapped[str]=mapped_column(Text,default=''); deadline:Mapped[date]=mapped_column(Date); minutes_per_day:Mapped[int]=mapped_column(Integer); days_per_week:Mapped[int]=mapped_column(Integer); preferred_weekdays:Mapped[list[Any]]=mapped_column(JSON,default=list); current_level:Mapped[str]=mapped_column(String(24)); expected_output:Mapped[str]=mapped_column(Text); unavailable_dates:Mapped[list[Any]]=mapped_column(JSON,default=list); success_criteria:Mapped[list[Any]]=mapped_column(JSON,default=list); confirmed:Mapped[bool]=mapped_column(Boolean,default=False)
class SourceORM(Base):
    __tablename__='orm_sources'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),index=True); title:Mapped[str]=mapped_column(String(200)); source_type:Mapped[str]=mapped_column(String(32)); trust_level:Mapped[str]=mapped_column(String(32)); content_hash:Mapped[str]=mapped_column(String(128),index=True); status:Mapped[str]=mapped_column(String(32),default='ready'); parse_error:Mapped[str]=mapped_column(Text,default=''); original_url:Mapped[str]=mapped_column(Text,default=''); local_path:Mapped[str]=mapped_column(Text,default=''); page_count:Mapped[int]=mapped_column(Integer,default=0); archive_preview_id:Mapped[str]=mapped_column(String(64),default=''); archive_relative_path:Mapped[str]=mapped_column(Text,default=''); parent_archive_name:Mapped[str]=mapped_column(String(240),default=''); language:Mapped[str]=mapped_column(String(32),default=''); chunks:Mapped[list['ChunkORM']]=relationship(cascade='all,delete-orphan'); __table_args__=(UniqueConstraint('workspace_id','content_hash',name='uq_orm_source_hash'),)
class ChunkORM(Base):
    __tablename__='orm_chunks'; id:Mapped[str]=mapped_column(String(64),primary_key=True); source_id:Mapped[str]=mapped_column(ForeignKey('orm_sources.id',ondelete='CASCADE'),index=True); ordinal:Mapped[int]=mapped_column(Integer); page_start:Mapped[int|None]=mapped_column(Integer,nullable=True); page_end:Mapped[int|None]=mapped_column(Integer,nullable=True); heading:Mapped[str]=mapped_column(String(300),default=''); text:Mapped[str]=mapped_column(Text); normalized_text:Mapped[str]=mapped_column(Text); content_hash:Mapped[str]=mapped_column(String(128))
class FeasibilityORM(Base):
    __tablename__='orm_feasibility'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); payload:Mapped[dict[str,Any]]=mapped_column(JSON); confirmed:Mapped[bool]=mapped_column(Boolean,default=False); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class PlanORM(Base):
    __tablename__='orm_plans'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),index=True); version:Mapped[int]=mapped_column(Integer); title:Mapped[str]=mapped_column(String(200)); status:Mapped[str]=mapped_column(String(24),default='draft'); deadline:Mapped[date]=mapped_column(Date); capacity_status:Mapped[str]=mapped_column(String(24),default='sufficient'); scheduled_minutes:Mapped[int]=mapped_column(Integer,default=0); unscheduled_minutes:Mapped[int]=mapped_column(Integer,default=0); diff:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); stale:Mapped[bool]=mapped_column(Boolean,default=False); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); milestones:Mapped[list['MilestoneORM']]=relationship(cascade='all,delete-orphan'); days:Mapped[list['DayORM']]=relationship(cascade='all,delete-orphan')
class MilestoneORM(Base):
    __tablename__='orm_milestones'; id:Mapped[str]=mapped_column(String(64),primary_key=True); plan_id:Mapped[str]=mapped_column(ForeignKey('orm_plans.id',ondelete='CASCADE')); ordinal:Mapped[int]=mapped_column(Integer); title:Mapped[str]=mapped_column(String(160)); objective:Mapped[str]=mapped_column(Text); modules:Mapped[list['ModuleORM']]=relationship(cascade='all,delete-orphan')
class ModuleORM(Base):
    __tablename__='orm_modules'; id:Mapped[str]=mapped_column(String(64),primary_key=True); milestone_id:Mapped[str]=mapped_column(ForeignKey('orm_milestones.id',ondelete='CASCADE')); ordinal:Mapped[int]=mapped_column(Integer); title:Mapped[str]=mapped_column(String(160)); objective:Mapped[str]=mapped_column(Text); lessons:Mapped[list['LessonORM']]=relationship(cascade='all,delete-orphan')
class LessonORM(Base):
    __tablename__='orm_lessons'; id:Mapped[str]=mapped_column(String(64),primary_key=True); module_id:Mapped[str]=mapped_column(ForeignKey('orm_modules.id',ondelete='CASCADE')); ordinal:Mapped[int]=mapped_column(Integer); title:Mapped[str]=mapped_column(String(200)); objective:Mapped[str]=mapped_column(Text); estimated_minutes:Mapped[int]=mapped_column(Integer); completion_criteria:Mapped[str]=mapped_column(Text); blocks:Mapped[list['BlockORM']]=relationship(cascade='all,delete-orphan')
class BlockORM(Base):
    __tablename__='orm_blocks'; id:Mapped[str]=mapped_column(String(64),primary_key=True); lesson_id:Mapped[str]=mapped_column(ForeignKey('orm_lessons.id',ondelete='CASCADE')); ordinal:Mapped[int]=mapped_column(Integer); block_type:Mapped[str]=mapped_column(String(32)); content:Mapped[str]=mapped_column(Text); rubric:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); evidence_chunk_ids:Mapped[list[Any]]=mapped_column(JSON,default=list)
class DayORM(Base):
    __tablename__='orm_days'; id:Mapped[str]=mapped_column(String(64),primary_key=True); plan_id:Mapped[str]=mapped_column(ForeignKey('orm_plans.id',ondelete='CASCADE')); day:Mapped[date]=mapped_column(Date,index=True); planned_minutes:Mapped[int]=mapped_column(Integer); tasks:Mapped[list['TaskORM']]=relationship(cascade='all,delete-orphan')
class TaskORM(Base):
    __tablename__='orm_tasks'; id:Mapped[str]=mapped_column(String(64),primary_key=True); day_id:Mapped[str]=mapped_column(ForeignKey('orm_days.id',ondelete='CASCADE')); lesson_id:Mapped[str|None]=mapped_column(String(64),nullable=True); title:Mapped[str]=mapped_column(String(240)); task_type:Mapped[str]=mapped_column(String(32)); estimated_minutes:Mapped[int]=mapped_column(Integer); status:Mapped[str]=mapped_column(String(24),default='not_started'); started_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True); completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True); actual_minutes:Mapped[int]=mapped_column(Integer,default=0); evidence:Mapped[str]=mapped_column(Text,default='')
class ProgressORM(Base):
    __tablename__='orm_progress'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); lesson_id:Mapped[str]=mapped_column(String(64),index=True); mastery:Mapped[float]=mapped_column(Float,default=0); attempts:Mapped[int]=mapped_column(Integer,default=0); independent_attempts:Mapped[int]=mapped_column(Integer,default=0); hint_attempts:Mapped[int]=mapped_column(Integer,default=0); next_review:Mapped[date|None]=mapped_column(Date,nullable=True); __table_args__=(UniqueConstraint('workspace_id','lesson_id',name='uq_orm_progress'),)
class MasteryEventORM(Base):
    __tablename__='orm_mastery_events'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64)); lesson_id:Mapped[str]=mapped_column(String(64)); score:Mapped[float]=mapped_column(Float); delta:Mapped[float]=mapped_column(Float); used_hint:Mapped[bool]=mapped_column(Boolean); independent:Mapped[bool]=mapped_column(Boolean); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class MistakeORM(Base):
    __tablename__='orm_mistakes'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64)); lesson_id:Mapped[str]=mapped_column(String(64)); criterion:Mapped[str]=mapped_column(Text); occurrences:Mapped[int]=mapped_column(Integer,default=1); status:Mapped[str]=mapped_column(String(24),default='open')
class ReviewORM(Base):
    __tablename__='orm_reviews'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64)); lesson_id:Mapped[str]=mapped_column(String(64)); due_date:Mapped[date]=mapped_column(Date,index=True); interval_days:Mapped[int]=mapped_column(Integer,default=1); status:Mapped[str]=mapped_column(String(24),default='due')
class JobORM(Base):
    __tablename__='orm_jobs'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64)); job_type:Mapped[str]=mapped_column(String(32)); status:Mapped[str]=mapped_column(String(24),default='queued'); stage:Mapped[str]=mapped_column(String(32),default='validating'); progress:Mapped[int]=mapped_column(Integer,default=0); message:Mapped[str]=mapped_column(Text,default=''); error:Mapped[str]=mapped_column(Text,default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class SessionORM(Base):
    __tablename__='orm_sessions'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),index=True); lesson_id:Mapped[str]=mapped_column(String(64),default=''); status:Mapped[str]=mapped_column(String(24),default='active'); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class MessageORM(Base):
    __tablename__='orm_messages'; id:Mapped[str]=mapped_column(String(64),primary_key=True); session_id:Mapped[str]=mapped_column(ForeignKey('orm_sessions.id',ondelete='CASCADE'),index=True); role:Mapped[str]=mapped_column(String(24)); content:Mapped[str]=mapped_column(Text); grounding:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class CitationORM(Base):
    __tablename__='orm_citations'; id:Mapped[str]=mapped_column(String(64),primary_key=True); message_id:Mapped[str]=mapped_column(ForeignKey('orm_messages.id',ondelete='CASCADE'),index=True); source_id:Mapped[str]=mapped_column(String(64)); chunk_id:Mapped[str]=mapped_column(String(64)); page_start:Mapped[int|None]=mapped_column(Integer,nullable=True); page_end:Mapped[int|None]=mapped_column(Integer,nullable=True); quote:Mapped[str]=mapped_column(Text)
class ConflictORM(Base):
    __tablename__='orm_conflicts'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),index=True); topic:Mapped[str]=mapped_column(String(240)); source_a_id:Mapped[str]=mapped_column(String(64)); chunk_a_id:Mapped[str]=mapped_column(String(64),default=''); source_b_id:Mapped[str]=mapped_column(String(64)); chunk_b_id:Mapped[str]=mapped_column(String(64),default=''); summary:Mapped[str]=mapped_column(Text,default=''); evidence_a:Mapped[str]=mapped_column(Text); evidence_b:Mapped[str]=mapped_column(Text); status:Mapped[str]=mapped_column(String(24),default='unresolved'); resolution:Mapped[str]=mapped_column(Text,default=''); selected_source_id:Mapped[str]=mapped_column(String(64),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); resolved_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class CharacterORM(Base):
    __tablename__='orm_characters'; id:Mapped[str]=mapped_column(String(32),primary_key=True); name:Mapped[str]=mapped_column(String(80)); tone:Mapped[str]=mapped_column(String(32)); pace:Mapped[str]=mapped_column(String(32))
class ReviewAttemptORM(Base):
    __tablename__='orm_review_attempts'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); lesson_id:Mapped[str]=mapped_column(String(64)); score:Mapped[float]=mapped_column(Float); feedback:Mapped[str]=mapped_column(Text,default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class NarrativeCampaignORM(Base):
    __tablename__='orm_narrative_campaigns'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); story_pack_id:Mapped[str]=mapped_column(String(64)); story_pack_version:Mapped[str]=mapped_column(String(32)); current_arc_id:Mapped[str]=mapped_column(String(128),default=''); current_scene_id:Mapped[str]=mapped_column(String(128)); current_story_stage:Mapped[str]=mapped_column(String(64),default='intro'); state:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); revision:Mapped[int]=mapped_column(Integer,default=0); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class NarrativeEventORM(Base):
    __tablename__='orm_narrative_events'; id:Mapped[str]=mapped_column(String(64),primary_key=True); event_id:Mapped[str]=mapped_column(String(64),unique=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),index=True); source_type:Mapped[str]=mapped_column(String(64)); source_id:Mapped[str]=mapped_column(String(128),default=''); event_type:Mapped[str]=mapped_column(String(80)); occurred_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); event_payload:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); idempotency_key:Mapped[str]=mapped_column(String(160)); generation_meta:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); __table_args__=(UniqueConstraint('campaign_id','idempotency_key',name='uq_orm_narrative_event_idempotency'),)
class NarrativeVoiceCacheORM(Base):
    __tablename__='orm_narrative_voice_cache'; cache_key:Mapped[str]=mapped_column(String(128),primary_key=True); text_hash:Mapped[str]=mapped_column(String(128)); voice_profile_id:Mapped[str]=mapped_column(String(80)); provider:Mapped[str]=mapped_column(String(32),default='fake'); audio_uri:Mapped[str]=mapped_column(Text,default=''); status:Mapped[str]=mapped_column(String(32),default='text_fallback'); duration_ms:Mapped[int]=mapped_column(Integer,default=0); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class LearningRunORM(Base):
    __tablename__='orm_learning_runs'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); task_id:Mapped[str]=mapped_column(String(64),index=True); lesson_id:Mapped[str]=mapped_column(String(64),default=''); phase:Mapped[str]=mapped_column(String(40),default='explanation'); status:Mapped[str]=mapped_column(String(24),default='active'); state:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); revision:Mapped[int]=mapped_column(Integer,default=0); last_idempotency_key:Mapped[str]=mapped_column(String(160),default=''); last_response:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class LearningRunMessageORM(Base):
    __tablename__='orm_learning_run_messages'; id:Mapped[str]=mapped_column(String(64),primary_key=True); run_id:Mapped[str]=mapped_column(ForeignKey('orm_learning_runs.id',ondelete='CASCADE'),index=True); role:Mapped[str]=mapped_column(String(24)); phase:Mapped[str]=mapped_column(String(40)); content:Mapped[str]=mapped_column(Text); grounding:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); idempotency_key:Mapped[str]=mapped_column(String(160),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('run_id','idempotency_key',name='uq_orm_learning_run_message_idempotency'),)
class TeacherMemoryORM(Base):
    __tablename__='orm_teacher_memories'; workspace_id:Mapped[str]=mapped_column(String(64),primary_key=True); memory:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); revision:Mapped[int]=mapped_column(Integer,default=0); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class NarrativeDialogueORM(Base):
    __tablename__='orm_narrative_dialogues'; id:Mapped[str]=mapped_column(String(64),primary_key=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),index=True); scene_id:Mapped[str]=mapped_column(String(128)); line_id:Mapped[str]=mapped_column(String(128),default=''); role:Mapped[str]=mapped_column(String(24)); character_id:Mapped[str]=mapped_column(String(64),default=''); route:Mapped[str]=mapped_column(String(32)); content:Mapped[str]=mapped_column(Text); generation_meta:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); idempotency_key:Mapped[str]=mapped_column(String(160),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('campaign_id','idempotency_key',name='uq_orm_narrative_dialogue_idempotency'),)
class TaskSemanticProfileORM(Base):
    __tablename__='orm_task_semantic_profiles'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),index=True); version:Mapped[int]=mapped_column(Integer,default=1); profile:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); generation_meta:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); idempotency_key:Mapped[str]=mapped_column(String(160),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('workspace_id','version',name='uq_orm_task_profile_version'),)
class FusionAdapterVersionORM(Base):
    __tablename__='orm_fusion_adapter_versions'; id:Mapped[str]=mapped_column(String(64),primary_key=True); adapter_id:Mapped[str]=mapped_column(String(80)); version:Mapped[str]=mapped_column(String(24)); definition:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('adapter_id','version',name='uq_orm_fusion_adapter_version'),)
class CampaignAdaptationBlueprintORM(Base):
    __tablename__='orm_campaign_adaptation_blueprints'; id:Mapped[str]=mapped_column(String(64),primary_key=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),index=True); task_profile_id:Mapped[str]=mapped_column(String(64)); story_pack_id:Mapped[str]=mapped_column(String(64)); version:Mapped[int]=mapped_column(Integer,default=1); status:Mapped[str]=mapped_column(String(24),default='draft'); blueprint:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); generation_meta:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); idempotency_key:Mapped[str]=mapped_column(String(160),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('campaign_id','version',name='uq_orm_campaign_blueprint_version'),)
class SceneContractORM(Base):
    __tablename__='orm_scene_contracts'; id:Mapped[str]=mapped_column(String(64),primary_key=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),index=True); blueprint_id:Mapped[str]=mapped_column(String(64)); story_anchor_before:Mapped[str]=mapped_column(String(128),default=''); story_anchor_after:Mapped[str]=mapped_column(String(128),default=''); version:Mapped[int]=mapped_column(Integer,default=1); status:Mapped[str]=mapped_column(String(24),default='validated'); contract:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); validator_result:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); idempotency_key:Mapped[str]=mapped_column(String(160),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('campaign_id','idempotency_key',name='uq_orm_scene_contract_idempotency'),)
class LearningArtifactORM(Base):
    __tablename__='orm_learning_artifacts'; id:Mapped[str]=mapped_column(String(64),primary_key=True); workspace_id:Mapped[str]=mapped_column(ForeignKey('orm_workspaces.id',ondelete='CASCADE'),index=True); campaign_id:Mapped[str]=mapped_column(String(64),default='',index=True); learning_run_id:Mapped[str]=mapped_column(String(64),default='',index=True); task_id:Mapped[str]=mapped_column(String(64),default='',index=True); artifact_type:Mapped[str]=mapped_column(String(40)); content:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); source_refs:Mapped[list[Any]]=mapped_column(JSON,default=list); evaluation_result:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); status:Mapped[str]=mapped_column(String(24),default='provisional'); verified:Mapped[bool]=mapped_column(Boolean,default=False); hint_level:Mapped[str]=mapped_column(String(24),default='none'); revision:Mapped[int]=mapped_column(Integer,default=0); idempotency_key:Mapped[str]=mapped_column(String(160),default=''); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('workspace_id','idempotency_key',name='uq_orm_learning_artifact_idempotency'),)
class StoryObjectORM(Base):
    __tablename__='orm_story_objects'; id:Mapped[str]=mapped_column(String(64),primary_key=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),index=True); source_artifact_id:Mapped[str]=mapped_column(ForeignKey('orm_learning_artifacts.id',ondelete='CASCADE'),index=True); object_type:Mapped[str]=mapped_column(String(40)); display_name:Mapped[str]=mapped_column(String(200)); story_description:Mapped[str]=mapped_column(Text); verified_facts:Mapped[list[Any]]=mapped_column(JSON,default=list); uncertainties:Mapped[list[Any]]=mapped_column(JSON,default=list); world_effects:Mapped[list[Any]]=mapped_column(JSON,default=list); character_visibility:Mapped[list[Any]]=mapped_column(JSON,default=list); status:Mapped[str]=mapped_column(String(24),default='provisional'); revision:Mapped[int]=mapped_column(Integer,default=0); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class NarrativeContinuitySnapshotORM(Base):
    __tablename__='orm_narrative_continuity_snapshots'; id:Mapped[str]=mapped_column(String(64),primary_key=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),unique=True); snapshot:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); revision:Mapped[int]=mapped_column(Integer,default=0); validator_warnings:Mapped[list[Any]]=mapped_column(JSON,default=list); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class GeneratedNarrativeSceneORM(Base):
    __tablename__='orm_generated_narrative_scenes'; id:Mapped[str]=mapped_column(String(64),primary_key=True); campaign_id:Mapped[str]=mapped_column(ForeignKey('orm_narrative_campaigns.id',ondelete='CASCADE'),index=True); contract_id:Mapped[str]=mapped_column(ForeignKey('orm_scene_contracts.id',ondelete='CASCADE'),index=True); scene:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); validator_result:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); generation_meta:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); status:Mapped[str]=mapped_column(String(24),default='ready'); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('campaign_id','contract_id',name='uq_orm_generated_scene_contract'),)
class SourceRevisionORM(Base):
    __tablename__='orm_source_revisions'; id:Mapped[str]=mapped_column(String(96),primary_key=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); source_id:Mapped[str]=mapped_column(String(64),index=True); revision:Mapped[int]=mapped_column(Integer); source_hash:Mapped[str]=mapped_column(String(128)); status:Mapped[str]=mapped_column(String(24),default='ready'); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('source_id','revision',name='uq_orm_source_revision'),)
class ConversionManifestORM(Base):
    __tablename__='orm_conversion_manifests'; id:Mapped[str]=mapped_column(String(96),primary_key=True); source_revision_id:Mapped[str]=mapped_column(ForeignKey('orm_source_revisions.id',ondelete='CASCADE'),index=True); adapter_name:Mapped[str]=mapped_column(String(80)); adapter_version:Mapped[str]=mapped_column(String(32)); mime_type:Mapped[str]=mapped_column(String(120),default=''); source_path:Mapped[str]=mapped_column(Text,default=''); content_hash:Mapped[str]=mapped_column(String(128)); metadata_json:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); status:Mapped[str]=mapped_column(String(24),default='ready'); failure_json:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
class KnowledgeMapRevisionORM(Base):
    __tablename__='orm_knowledge_map_revisions'; id:Mapped[str]=mapped_column(String(96),primary_key=True); map_id:Mapped[str]=mapped_column(String(64),index=True); workspace_id:Mapped[str]=mapped_column(String(64),index=True); revision:Mapped[int]=mapped_column(Integer); is_current:Mapped[bool]=mapped_column(Boolean,default=True,index=True); status:Mapped[str]=mapped_column(String(24),default='staged',index=True); generation_mode:Mapped[str]=mapped_column(String(40)); provider_used:Mapped[bool]=mapped_column(Boolean,default=False); source_revisions:Mapped[list[Any]]=mapped_column(JSON,default=list); anchor_material_id:Mapped[str|None]=mapped_column(String(160),nullable=True,index=True); supporting_material_ids:Mapped[list[Any]]=mapped_column(JSON,default=list); scope_policy:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); role_provenance:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); source_fingerprints:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); quality:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); fingerprint:Mapped[str]=mapped_column(String(128)); warnings:Mapped[list[Any]]=mapped_column(JSON,default=list); staging_token:Mapped[str]=mapped_column(String(128),default=''); staged_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); activation_id:Mapped[str|None]=mapped_column(String(96),nullable=True); activated_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True); failure_json:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow); __table_args__=(UniqueConstraint('workspace_id','revision',name='uq_orm_knowledge_map_revision'),Index('ix_orm_knowledge_map_revision_workspace_status','workspace_id','status'))
class KnowledgeNodeORM(Base):
    __tablename__='orm_knowledge_nodes'; id:Mapped[str]=mapped_column(String(160),primary_key=True); map_revision_id:Mapped[str]=mapped_column(ForeignKey('orm_knowledge_map_revisions.id',ondelete='CASCADE'),index=True); node_id:Mapped[str]=mapped_column(String(64),index=True); source_id:Mapped[str]=mapped_column(String(64),index=True); semantic_key:Mapped[str]=mapped_column(String(240)); label:Mapped[str]=mapped_column(String(240)); summary:Mapped[str]=mapped_column(Text); kind:Mapped[str]=mapped_column(String(40)); revision:Mapped[int]=mapped_column(Integer); status:Mapped[str]=mapped_column(String(24)); content_hash:Mapped[str]=mapped_column(String(128)); source_refs:Mapped[list[Any]]=mapped_column(JSON,default=list); history:Mapped[dict[str,Any]]=mapped_column(JSON,default=dict); __table_args__=(UniqueConstraint('map_revision_id','node_id',name='uq_orm_knowledge_node_revision'),)
class KnowledgeEdgeORM(Base):
    __tablename__='orm_knowledge_edges'; id:Mapped[str]=mapped_column(String(160),primary_key=True); map_revision_id:Mapped[str]=mapped_column(ForeignKey('orm_knowledge_map_revisions.id',ondelete='CASCADE'),index=True); edge_id:Mapped[str]=mapped_column(String(64),index=True); source_node_id:Mapped[str]=mapped_column(String(64)); target_node_id:Mapped[str]=mapped_column(String(64)); relation:Mapped[str]=mapped_column(String(40)); source_refs:Mapped[list[Any]]=mapped_column(JSON,default=list); __table_args__=(UniqueConstraint('map_revision_id','edge_id',name='uq_orm_knowledge_edge_revision'),)


class KnowledgeMapJobORM(KnowledgeMapBase):
    """Durable, retryable work item for a knowledge-map revision."""

    __tablename__ = 'orm_knowledge_map_jobs'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    map_id: Mapped[str] = mapped_column(String(64), index=True)
    # Database-level FK is owned by Alembic 0013.  The target belongs to the
    # legacy metadata registry, so this mapper deliberately keeps only the
    # scalar column instead of an unresolved cross-metadata ForeignKey.
    map_revision_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(32), default='queued', index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Versioned, durable progress projection.  Keeping this separate from
    # result/failure payloads lets clients poll truthful work-unit checkpoints
    # while a staged revision is still being built.
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_knowledge_map_job_idempotency'),
        Index('ix_orm_knowledge_map_job_workspace_status', 'workspace_id', 'status'),
        Index('ix_orm_knowledge_map_job_revision_status', 'map_revision_id', 'status'),
    )


class PlanningAssistantCacheORM(KnowledgeMapBase):
    """Durable local cache for cost-bearing planning-provider responses."""

    __tablename__ = "orm_planning_assistant_cache"
    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    cache_kind: Mapped[str] = mapped_column(String(40), index=True)
    provider_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    request_hash: Mapped[str] = mapped_column(String(128), index=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_version: Mapped[str] = mapped_column(String(40), default="planning-assistant.v1")
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanningResourcePoolORM(KnowledgeMapBase):
    """Durable server-owned state for one workspace planning session."""

    __tablename__ = "orm_planning_resource_pools"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    planning_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_size: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    anchor_candidate_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("workspace_id", "planning_session_id", name="uq_orm_planning_resource_pool_session"),
        Index("ix_orm_planning_resource_pool_workspace_revision", "workspace_id", "revision"),
    )


class PlanningResourceCandidateORM(KnowledgeMapBase):
    """Stable candidate record; inactive rows are retained for audit history."""

    __tablename__ = "orm_planning_resource_candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pool_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(String(180), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(180), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    selection_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unselected", index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    retained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    direct_user_reference: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("pool_id", "stable_id", name="uq_orm_planning_resource_candidate_identity"),
        Index("ix_orm_planning_resource_candidate_pool_active", "pool_id", "active", "ordinal"),
    )


class PlanningResourcePoolEventORM(KnowledgeMapBase):
    """Append-only decision/replenishment audit trail."""

    __tablename__ = "orm_planning_resource_pool_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pool_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(180), nullable=False)
    before_state: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    after_state: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    client_request_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_orm_planning_resource_pool_event_pool_revision", "pool_id", "revision"),)


class PlanningResourcePoolRequestORM(KnowledgeMapBase):
    """Idempotency ledger for explicit pool mutations."""

    __tablename__ = "orm_planning_resource_pool_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pool_id: Mapped[str] = mapped_column(String(64), index=True)
    client_request_id: Mapped[str] = mapped_column(String(160), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("pool_id", "client_request_id", name="uq_orm_planning_resource_pool_request"),
    )


class MaterialDispositionORM(KnowledgeMapBase):
    """Explicit workspace decision for source material used by map generation."""

    __tablename__ = 'orm_material_dispositions'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    source_revision_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    map_revision_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    material_key: Mapped[str] = mapped_column(String(160))
    disposition: Mapped[str] = mapped_column(String(32), default='pending', index=True)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    reason: Mapped[str] = mapped_column(Text, default='')
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'map_revision_id', 'material_key', name='uq_orm_material_disposition_revision'),
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_material_disposition_idempotency'),
        Index('ix_orm_material_disposition_workspace_status', 'workspace_id', 'status'),
    )


class ReviewCandidateORM(KnowledgeMapBase):
    """Review queue item kept separate from immutable map revision contents."""

    __tablename__ = 'orm_knowledge_review_candidates'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    map_revision_id: Mapped[str] = mapped_column(String(96), index=True)
    candidate_key: Mapped[str] = mapped_column(String(160))
    candidate_type: Mapped[str] = mapped_column(String(40), default='node')
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    edge_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reviewer_id: Mapped[str] = mapped_column(String(64), default='')
    decision_reason: Mapped[str] = mapped_column(Text, default='')
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('map_revision_id', 'candidate_key', name='uq_orm_knowledge_review_candidate_key'),
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_knowledge_review_candidate_idempotency'),
        Index('ix_orm_knowledge_review_candidate_workspace_status', 'workspace_id', 'status'),
        Index('ix_orm_knowledge_review_candidate_revision_status', 'map_revision_id', 'status'),
    )


class PlanActivationRecordORM(KnowledgeMapBase):
    """Append-only activation audit for draft and active learning plans."""

    __tablename__ = 'orm_plan_activation_records'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    previous_plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default='draft', index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default='')
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'plan_id', 'plan_revision', name='uq_orm_plan_activation_revision'),
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_plan_activation_idempotency'),
        Index('ix_orm_plan_activation_workspace_status', 'workspace_id', 'status'),
    )


class PlanExecutionTaskORM(KnowledgeMapBase):
    """Mutable execution state for one immutable canonical plan snapshot.

    This table deliberately does not project canonical nodes into the legacy
    ``active_knowledge_*`` registry.  ``concept_snapshot`` keeps the exact
    revision-bound teaching fields while ``knowledge_point_id`` remains the
    stable canonical node id.
    """

    __tablename__ = 'orm_plan_execution_tasks'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    map_revision_id: Mapped[str] = mapped_column(String(96), index=True)
    knowledge_point_id: Mapped[str] = mapped_column(String(64), index=True)
    day_date: Mapped[str] = mapped_column(String(10), index=True)
    original_day_date: Mapped[str] = mapped_column(String(10), default='')
    day_index: Mapped[int] = mapped_column(Integer)
    task_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    objective: Mapped[str] = mapped_column(Text)
    source_evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    acceptance_condition: Mapped[str] = mapped_column(Text, default='')
    concept_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_task_minutes: Mapped[int] = mapped_column(Integer)
    completed_task_minutes: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_elapsed_minutes: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default='planned', index=True)
    task_type: Mapped[str] = mapped_column(String(32), default='initial_learning')
    learning_phase: Mapped[str] = mapped_column(String(40), default='understand')
    point_sequence: Mapped[int] = mapped_column(Integer, default=0)
    phase_part_index: Mapped[int] = mapped_column(Integer, default=1)
    phase_part_count: Mapped[int] = mapped_column(Integer, default=1)
    linked_review_item_id: Mapped[str] = mapped_column(String(96), default='')
    review_cycle: Mapped[int] = mapped_column(Integer, default=0)
    origin_task_id: Mapped[str] = mapped_column(String(96), default='')
    not_before_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_reason: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('plan_id', 'id', name='uq_orm_plan_execution_task'),
        Index('ix_orm_plan_execution_route', 'plan_id', 'task_type', 'point_sequence', 'day_index', 'task_order'),
    )


class PlanMasteryEventORM(KnowledgeMapBase):
    """Append-only mastery/manual-skip evidence tied to one map revision."""

    __tablename__ = 'orm_plan_mastery_events'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(96), index=True)
    map_revision_id: Mapped[str] = mapped_column(String(96), index=True)
    knowledge_point_id: Mapped[str] = mapped_column(String(64), index=True)
    evidence_hash: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_plan_mastery_event_idempotency'),
        Index('ix_orm_plan_mastery_node_revision', 'map_revision_id', 'knowledge_point_id'),
    )


class LearningResourceORM(KnowledgeMapBase):
    """External resource selected independently for a canonical map revision."""

    __tablename__ = 'orm_learning_resources'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    map_revision_id: Mapped[str] = mapped_column(String(96), index=True)
    knowledge_point_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default='')
    url: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(240), default='')
    provider_label: Mapped[str] = mapped_column(String(160), default='')
    authority: Mapped[str] = mapped_column(String(64), default='')
    source: Mapped[str] = mapped_column(String(64), default='')
    search_query: Mapped[str] = mapped_column(Text, default='')
    discovery_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default='active', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('map_revision_id', 'knowledge_point_id', 'url', name='uq_orm_learning_resource_revision_url'),
        Index('ix_orm_learning_resource_workspace_revision', 'workspace_id', 'map_revision_id', 'status'),
    )


class LearnerPreferenceORM(KnowledgeMapBase):
    """One explicit, auditable learner preference per workspace and key."""

    __tablename__ = 'orm_learner_preferences'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    preference_key: Mapped[str] = mapped_column(String(120))
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(24), default='explicit')
    status: Mapped[str] = mapped_column(String(24), default='active', index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'preference_key', name='uq_orm_learner_preference_key'),
        Index('ix_orm_learner_preference_workspace_status', 'workspace_id', 'status'),
    )


class TeacherTurnORM(KnowledgeMapBase):
    """Durable teacher response/turn, including grounding and chosen action."""

    __tablename__ = 'orm_teacher_turns'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    learning_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    turn_key: Mapped[str] = mapped_column(String(160))
    turn_number: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(24), default='teacher')
    content: Mapped[str] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(String(40), default='')
    action_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    grounding_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'turn_key', name='uq_orm_teacher_turn_key'),
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_teacher_turn_idempotency'),
        Index('ix_orm_teacher_turn_workspace_created', 'workspace_id', 'created_at'),
    )


class TeacherEventORM(KnowledgeMapBase):
    """Append-only event log associated with a persisted teacher turn."""

    __tablename__ = 'orm_teacher_events'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    teacher_turn_id: Mapped[str | None] = mapped_column(
        ForeignKey('orm_teacher_turns.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'event_key', name='uq_orm_teacher_event_key'),
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_teacher_event_idempotency'),
        Index('ix_orm_teacher_event_workspace_status', 'workspace_id', 'status'),
    )


class VideoCollectionORM(KnowledgeMapBase):
    """Durable source collection and manifest revision projection.

    The collection record intentionally contains no provider credentials or
    raw transcript data.  ``stable_id`` is derived by the service from the
    provider identity (or canonical URL hash) and is safe to replay.
    """

    __tablename__ = 'orm_video_collections'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stable_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default='')
    provider_collection_id: Mapped[str] = mapped_column(String(240), nullable=False, default='')
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, default='')
    canonical_url_hash: Mapped[str] = mapped_column(String(128), nullable=False, default='', index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, default='')
    title: Mapped[str] = mapped_column(String(300), nullable=False, default='')
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='active', index=True)
    source_manifest_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reported_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enumerated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'stable_id', name='uq_orm_video_collection_workspace_stable_id'),
        UniqueConstraint('workspace_id', 'input_fingerprint', name='uq_orm_video_collection_workspace_input'),
        UniqueConstraint('workspace_id', 'idempotency_key', name='uq_orm_video_collection_workspace_idempotency'),
        Index('ix_orm_video_collection_provider_identity', 'provider', 'provider_collection_id'),
        Index('ix_orm_video_collection_workspace_status', 'workspace_id', 'status'),
        Index('ix_orm_video_collection_manifest_revision', 'id', 'source_manifest_revision'),
    )


class VideoEpisodeORM(KnowledgeMapBase):
    """Manifest entry and durable per-episode checkpoint."""

    __tablename__ = 'orm_video_episodes'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey('orm_video_collections.id', ondelete='CASCADE'), index=True
    )
    stable_id: Mapped[str] = mapped_column(String(180), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default='')
    provider_episode_id: Mapped[str] = mapped_column(String(240), nullable=False, default='')
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, default='')
    canonical_url_hash: Mapped[str] = mapped_column(String(128), nullable=False, default='', index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    title: Mapped[str] = mapped_column(String(300), nullable=False, default='')
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default='unprocessed', index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    processing_token: Mapped[str] = mapped_column(String(96), nullable=False, default='', index=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    failure_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_manifest_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint('collection_id', 'stable_id', name='uq_orm_video_episode_stable_id'),
        UniqueConstraint('collection_id', 'input_fingerprint', name='uq_orm_video_episode_input_fingerprint'),
        UniqueConstraint('collection_id', 'ordinal', name='uq_orm_video_episode_ordinal'),
        Index('ix_orm_video_episode_collection_active_ordinal', 'collection_id', 'active', 'ordinal'),
        Index('ix_orm_video_episode_collection_status', 'collection_id', 'status'),
        Index('ix_orm_video_episode_collection_selected_status', 'collection_id', 'selected', 'status'),
    )


class VideoSubtitleSegmentORM(KnowledgeMapBase):
    """One bounded subtitle segment; no episode-wide concatenation column."""

    __tablename__ = 'orm_video_subtitle_segments'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey('orm_video_episodes.id', ondelete='CASCADE'), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint('episode_id', 'ordinal', name='uq_orm_video_segment_episode_ordinal'),
        UniqueConstraint('episode_id', 'input_fingerprint', name='uq_orm_video_segment_episode_fingerprint'),
        Index('ix_orm_video_segment_episode_time', 'episode_id', 'start_ms', 'end_ms'),
    )


class VideoCollectionJobORM(KnowledgeMapBase):
    """Collection-level durable orchestration state and request ledger."""

    __tablename__ = 'orm_video_collection_jobs'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey('orm_video_collections.id', ondelete='CASCADE'), index=True
    )
    job_type: Mapped[str] = mapped_column(String(48), nullable=False, default='transcribe')
    status: Mapped[str] = mapped_column(String(40), nullable=False, default='queued', index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    failure_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint('collection_id', 'idempotency_key', name='uq_orm_video_job_idempotency'),
        Index('ix_orm_video_job_collection_status', 'collection_id', 'status'),
        Index('ix_orm_video_job_collection_updated', 'collection_id', 'updated_at'),
    )


class VideoSummaryORM(KnowledgeMapBase):
    """Provider output with auditable evidence references.

    ``evidence_json`` contains only segment/summary ids and bounded timestamp
    and quote-hash claims.  Collection summaries therefore never need to
    duplicate a raw transcript.
    """

    __tablename__ = 'orm_video_summaries'
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey('orm_video_collections.id', ondelete='CASCADE'), index=True
    )
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey('orm_video_episodes.id', ondelete='CASCADE'), nullable=True, index=True
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False, default='episode', index=True)
    scope_id: Mapped[str] = mapped_column(String(180), nullable=False, default='')
    batch_key: Mapped[str] = mapped_column(String(180), nullable=False, default='')
    input_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default='')
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='ready', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint(
            'collection_id', 'scope_type', 'scope_id', 'input_fingerprint', 'provider_fingerprint',
            name='uq_orm_video_summary_scope_input_provider',
        ),
        Index('ix_orm_video_summary_collection_scope', 'collection_id', 'scope_type', 'scope_id'),
    )


# Short aliases keep imports ergonomic for callers that use the domain nouns.
MapJobORM = KnowledgeMapJobORM
KnowledgeMaterialDispositionORM = MaterialDispositionORM
KnowledgeReviewCandidateORM = ReviewCandidateORM
