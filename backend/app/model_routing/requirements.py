from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .capabilities import Capability


class TaskType(str, Enum):
    TEACHER = "teacher"
    KNOWLEDGE_EXTRACT = "knowledge_extract"
    KNOWLEDGE_VERIFY = "knowledge_verify"
    PLANNING = "planning"
    ASSESSMENT = "assessment"
    FREE_CHAT = "free_chat"
    CONFLICT_ANALYSIS = "conflict_analysis"
    PDF_KNOWLEDGE_EXTRACT = "pdf_knowledge_extract"
    IMAGE_KNOWLEDGE_EXTRACT = "image_knowledge_extract"
    SUBTITLED_VIDEO_ANALYSIS = "subtitled_video_analysis"
    CODE_ANALYSIS = "code_analysis"
    VISION_DIAGRAM = "vision_diagram"
    AUDIO_SEMANTIC = "audio_semantic"


def _capability(value: Capability | str) -> Capability:
    if isinstance(value, Capability):
        return value
    return Capability(str(value).strip().lower())


@dataclass(frozen=True, slots=True)
class CapabilityFallback:
    capability: Capability
    code: str
    description: str
    pipeline: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        capability = _capability(self.capability)
        code = str(self.code or "").strip().upper()
        description = str(self.description or "").strip()
        pipeline = tuple(
            item
            for item in (str(value or "").strip() for value in self.pipeline)
            if item
        )
        if not code:
            raise ValueError("fallback code must be non-empty")
        if not description:
            raise ValueError("fallback description must be non-empty")
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "pipeline", pipeline)


@dataclass(frozen=True, slots=True)
class TaskCapabilityRequirement:
    task_type: str
    required_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    preferred_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    fallbacks: tuple[CapabilityFallback, ...] = ()
    optional_enhancement: bool = False
    independent_verification_preferred: bool = False
    minimum_context_window: int | None = None
    local_preprocessing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        task_type = str(self.task_type or "").strip().lower()
        required = frozenset(_capability(value) for value in self.required_capabilities)
        preferred = frozenset(
            _capability(value) for value in self.preferred_capabilities
        )
        fallbacks = tuple(self.fallbacks)
        local_preprocessing = tuple(
            item
            for item in (
                str(value or "").strip() for value in self.local_preprocessing
            )
            if item
        )
        if not task_type:
            raise ValueError("task_type must be non-empty")
        if required & preferred:
            raise ValueError("required and preferred capabilities must not overlap")
        fallback_capabilities = {fallback.capability for fallback in fallbacks}
        if not fallback_capabilities <= preferred:
            raise ValueError("fallbacks may only describe preferred capabilities")
        if self.minimum_context_window is not None and self.minimum_context_window <= 0:
            raise ValueError("minimum_context_window must be positive when provided")
        object.__setattr__(self, "task_type", task_type)
        object.__setattr__(self, "required_capabilities", required)
        object.__setattr__(self, "preferred_capabilities", preferred)
        object.__setattr__(self, "fallbacks", fallbacks)
        object.__setattr__(self, "local_preprocessing", local_preprocessing)

    def fallback_for(self, capability: Capability) -> CapabilityFallback | None:
        return next(
            (
                fallback
                for fallback in self.fallbacks
                if fallback.capability == capability
            ),
            None,
        )


_PROMPTED_STRUCTURE = CapabilityFallback(
    capability=Capability.STRUCTURED_OUTPUT,
    code="PROMPTED_STRUCTURE_WITH_LOCAL_VALIDATION",
    description=(
        "Provider-native structured output is unavailable; use prompted JSON "
        "and application-owned validation."
    ),
    pipeline=("prompted_json", "local_schema_validation"),
)

_GENERAL_REASONING = CapabilityFallback(
    capability=Capability.REASONING,
    code="GENERAL_TEXT_REASONING",
    description=(
        "A dedicated reasoning capability is unavailable; use bounded text "
        "reasoning and report lower confidence."
    ),
)

_LOCAL_OCR = CapabilityFallback(
    capability=Capability.VISION,
    code="LOCAL_OCR_ONLY",
    description=(
        "Vision understanding is unavailable; extract local OCR text before "
        "routing to the text Provider."
    ),
    pipeline=("local_ocr", "text_generation"),
)


def _structured_text_requirement(
    task_type: TaskType,
    *,
    independent_verification_preferred: bool = False,
    local_preprocessing: tuple[str, ...] = (),
) -> TaskCapabilityRequirement:
    return TaskCapabilityRequirement(
        task_type=task_type.value,
        required_capabilities=frozenset({Capability.TEXT_GENERATION}),
        preferred_capabilities=frozenset({Capability.STRUCTURED_OUTPUT}),
        fallbacks=(_PROMPTED_STRUCTURE,),
        independent_verification_preferred=independent_verification_preferred,
        local_preprocessing=local_preprocessing,
    )


DEFAULT_TASK_REQUIREMENTS: Mapping[str, TaskCapabilityRequirement] = MappingProxyType(
    {
        TaskType.TEACHER.value: TaskCapabilityRequirement(
            task_type=TaskType.TEACHER.value,
            required_capabilities=frozenset({Capability.TEXT_GENERATION}),
        ),
        TaskType.KNOWLEDGE_EXTRACT.value: _structured_text_requirement(
            TaskType.KNOWLEDGE_EXTRACT
        ),
        TaskType.KNOWLEDGE_VERIFY.value: _structured_text_requirement(
            TaskType.KNOWLEDGE_VERIFY,
            independent_verification_preferred=True,
        ),
        TaskType.PLANNING.value: _structured_text_requirement(TaskType.PLANNING),
        TaskType.ASSESSMENT.value: _structured_text_requirement(TaskType.ASSESSMENT),
        TaskType.FREE_CHAT.value: TaskCapabilityRequirement(
            task_type=TaskType.FREE_CHAT.value,
            required_capabilities=frozenset({Capability.TEXT_GENERATION}),
        ),
        TaskType.CONFLICT_ANALYSIS.value: TaskCapabilityRequirement(
            task_type=TaskType.CONFLICT_ANALYSIS.value,
            required_capabilities=frozenset({Capability.TEXT_GENERATION}),
            preferred_capabilities=frozenset({Capability.REASONING}),
            fallbacks=(_GENERAL_REASONING,),
        ),
        TaskType.PDF_KNOWLEDGE_EXTRACT.value: _structured_text_requirement(
            TaskType.PDF_KNOWLEDGE_EXTRACT,
            local_preprocessing=("local_pdf_parser_or_ocr", "text_generation"),
        ),
        TaskType.IMAGE_KNOWLEDGE_EXTRACT.value: TaskCapabilityRequirement(
            task_type=TaskType.IMAGE_KNOWLEDGE_EXTRACT.value,
            required_capabilities=frozenset({Capability.TEXT_GENERATION}),
            preferred_capabilities=frozenset(
                {Capability.STRUCTURED_OUTPUT, Capability.VISION}
            ),
            fallbacks=(_PROMPTED_STRUCTURE, _LOCAL_OCR),
        ),
        TaskType.SUBTITLED_VIDEO_ANALYSIS.value: _structured_text_requirement(
            TaskType.SUBTITLED_VIDEO_ANALYSIS,
            local_preprocessing=("subtitle_to_text", "text_generation"),
        ),
        TaskType.CODE_ANALYSIS.value: _structured_text_requirement(
            TaskType.CODE_ANALYSIS,
            local_preprocessing=("local_code_parser", "text_generation"),
        ),
        TaskType.VISION_DIAGRAM.value: TaskCapabilityRequirement(
            task_type=TaskType.VISION_DIAGRAM.value,
            required_capabilities=frozenset(
                {Capability.TEXT_GENERATION, Capability.VISION}
            ),
            optional_enhancement=True,
        ),
        TaskType.AUDIO_SEMANTIC.value: TaskCapabilityRequirement(
            task_type=TaskType.AUDIO_SEMANTIC.value,
            required_capabilities=frozenset(
                {Capability.TEXT_GENERATION, Capability.AUDIO}
            ),
            optional_enhancement=True,
        ),
    }
)


def requirement_for(
    task_type: TaskType | str,
    *,
    requirements: Mapping[str, TaskCapabilityRequirement] = DEFAULT_TASK_REQUIREMENTS,
) -> TaskCapabilityRequirement:
    key = task_type.value if isinstance(task_type, TaskType) else str(task_type).strip().lower()
    try:
        return requirements[key]
    except KeyError as exc:
        raise ValueError(f"unknown task requirement: {key or '<empty>'}") from exc
