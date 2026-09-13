"""In-memory legacy recovery presentation contract. No data/identity/IO engine."""
from dataclasses import dataclass
from typing import Mapping

SCHEMA = 'mentorflow.legacy-recovery-preview.v1'
SUMMARY_LABELS = {'plans': '计划', 'progress': '学习进度', 'materials': '资料',
                  'learning_records': '学习记录', 'conversations': '对话', 'reviews': '复习数据'}
SOURCE_LABELS = {'primary': '主要历史数据', 'development': '历史开发副本', 'older_local': '更早的本机副本'}
CONFLICT_LABELS = {'identical': '完全相同', 'main_only': '只存在于主要数据',
                   'other_only': '只存在于其他副本', 'different': '同一记录但内容不同',
                   'path_repair': '资料路径需要修复'}
READINESS_MESSAGES = {
    'TARGET_STORAGE_STABLE': '当前账号仍运行在测试数据目录，暂不能正式迁移。',
    'IDENTITY_ISSUER_STABLE': '账号身份地址仍在迁移准备中，暂不能正式迁移。',
    'SOURCE_SELECTED': '请选择数据来源和学习空间，并等待对应的预览确认。',
    'CONFLICT_POLICY_READY': '历史副本的合并方式尚未确认，暂不能正式迁移。',
}


def _text(value, *, required=False):
    if not isinstance(value, str) or required and not value.strip():
        raise ValueError('Invalid presentation text')
    return value


def _count(value):
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError('Invalid presentation count')
    return value


@dataclass(frozen=True)
class Source:
    key: str
    kind: str
    has_differences: bool
    path: str


@dataclass(frozen=True)
class Workspace:
    source_key: str
    key: str
    name: str
    plans: int | None
    materials: int | None
    last_active: str
    ownership_proven: bool

    @property
    def selection_key(self):
        return self.source_key, self.key


@dataclass(frozen=True)
class RecoveryPreview:
    valid: bool = False
    revision: str = ''
    sequence: int = 0
    target_key: str = ''
    target_label: str = ''
    data_present: bool = False
    data_loss_confirmed: bool | None = None
    summary: tuple = ()
    sources: tuple[Source, ...] = ()
    workspaces: tuple[Workspace, ...] = ()
    conflicts: tuple = ()
    all_content_candidates_found: bool = False
    line_ending_only: int | None = None
    readiness: tuple = ()
    confirmed_sources: frozenset = frozenset()
    confirmed_workspaces: frozenset = frozenset()
    technical: tuple = ()

    @classmethod
    def from_mapping(cls, raw):
        """Unknown/malformed payloads become a controlled, non-ready preview.

        The producer owns auditing, counts, ownership and readiness. This parser
        validates their shape; it never derives those facts from source files.
        """
        try:
            if not isinstance(raw, Mapping) or raw.get('schema') != SCHEMA:
                return cls()
            sources = tuple(Source(_text(s['key'], required=True), _text(s['kind']),
                                   s.get('has_differences') is True, _text(s.get('path', '')))
                            for s in raw.get('sources', []))
            source_keys = {s.key for s in sources}
            if len(source_keys) != len(sources):
                return cls()
            workspaces = tuple(Workspace(_text(w['source_key'], required=True), _text(w['key'], required=True),
                _text(w.get('name', '未命名学习空间')), _count(w.get('plans')), _count(w.get('materials')),
                _text(w.get('last_active', '待确认')), w.get('ownership_proven') is True)
                for w in raw.get('workspaces', []))
            workspace_keys = {w.selection_key for w in workspaces}
            if len(workspace_keys) != len(workspaces) or any(w.source_key not in source_keys for w in workspaces):
                return cls()
            selection = raw.get('confirmed_selection', {})
            confirmed_sources = frozenset(_text(s, required=True) for s in selection.get('sources', []))
            confirmed_workspaces = frozenset((_text(w['source_key'], required=True), _text(w['key'], required=True))
                                            for w in selection.get('workspaces', []))
            if not confirmed_sources <= source_keys or not confirmed_workspaces <= workspace_keys:
                return cls()
            if any(source not in confirmed_sources for source, _ in confirmed_workspaces):
                return cls()
            details = raw.get('technical', {})
            # Whitelisted display-only strings. Unknown audit fields never reach UI.
            technical = tuple((key, _text(details[key])) for key in
                ('issuer_hash', 'subject_hash', 'manifest_hashes', 'ledger_status') if key in details)
            loss = raw.get('data_loss_confirmed')
            sequence = _count(raw['sequence'])
            if sequence is None:
                return cls()
            return cls(valid=True, revision=_text(raw['revision'], required=True), sequence=sequence,
                target_key=_text(raw['target_key'], required=True), target_label=_text(raw.get('target_label', '当前账号')),
                data_present=raw.get('data_present') is True,
                data_loss_confirmed=loss if type(loss) is bool else None,
                summary=tuple((key, _count(raw.get('summary', {}).get(key))) for key in SUMMARY_LABELS),
                sources=sources, workspaces=workspaces,
                conflicts=tuple((key, _count(raw.get('conflicts', {}).get(key))) for key in CONFLICT_LABELS),
                all_content_candidates_found=raw.get('all_content_candidates_found') is True,
                line_ending_only=_count(raw.get('line_ending_only')),
                readiness=tuple((key, raw.get('readiness', {}).get(key) is True) for key in READINESS_MESSAGES),
                confirmed_sources=confirmed_sources, confirmed_workspaces=confirmed_workspaces, technical=technical)
        except (KeyError, TypeError, ValueError, AttributeError):
            return cls()


class RecoverySelection:
    """Explicit choices only; readiness must match a fresh producer projection."""
    def __init__(self, preview):
        self.preview = preview
        self.sources = set()
        self.workspaces = set()
        self.preview_confirmed = False

    def replace_preview(self, preview):
        if preview.valid and self.preview.valid and preview.target_key == self.preview.target_key and preview.sequence <= self.preview.sequence:
            return False
        if preview.target_key != self.preview.target_key:
            self.sources.clear()
            self.workspaces.clear()
        self.preview = preview
        self.sources &= {s.key for s in preview.sources}
        self.workspaces &= {w.selection_key for w in preview.workspaces if w.source_key in self.sources}
        self.preview_confirmed = False
        return True

    def select_source(self, key, selected):
        if key not in {s.key for s in self.preview.sources}:
            return
        if selected:
            self.sources.add(key)
        else:
            self.sources.discard(key)
            self.workspaces = {w for w in self.workspaces if w[0] != key}
        self.preview_confirmed = False

    def select_workspace(self, key, selected):
        if key not in {w.selection_key for w in self.preview.workspaces if w.source_key in self.sources}:
            return
        if selected:
            self.workspaces.add(key)
        else:
            self.workspaces.discard(key)
        self.preview_confirmed = False

    @property
    def blockers(self):
        p = self.preview
        if not p.valid:
            return ['暂时无法读取迁移预览，请获取新的预览后重试。']
        result = []
        if not p.data_present:
            result.append('旧数据是否可用仍待确认，暂不能正式迁移。')
        if p.data_loss_confirmed is not False:
            result.append('数据完整性仍需进一步核实，暂不能正式迁移。')
        flags = dict(p.readiness)
        for key, message in READINESS_MESSAGES.items():
            ready = flags.get(key) is True
            if key == 'SOURCE_SELECTED':
                ready = ready and bool(self.sources) and bool(self.workspaces)
                ready = ready and self.sources == p.confirmed_sources and self.workspaces == p.confirmed_workspaces
            if not ready:
                result.append(message)
        selected = [w for w in p.workspaces if w.selection_key in self.workspaces]
        if any(not w.ownership_proven for w in selected):
            result.append('所选学习空间的归属还待确认，暂不能正式迁移。')
        return result

    @property
    def can_start(self):
        return not self.blockers

    def confirm_preview(self):
        # Deliberately no executor, endpoint, filesystem or credential callback.
        if not self.can_start:
            return False
        self.preview_confirmed = True
        return True

    def selection_contract(self):
        return {'preview_revision': self.preview.revision, 'preview_sequence': self.preview.sequence,
                'target_key': self.preview.target_key, 'sources': sorted(self.sources),
                'workspaces': [{'source_key': source, 'key': key} for source, key in sorted(self.workspaces)]}
