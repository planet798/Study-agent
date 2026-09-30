"""Transient memory candidate contract and conservative standalone validation."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

EXTRACTOR_VERSION = 1
MAX_CANDIDATES = 3
MAX_OUTPUT_CHARS = 65_536
MemoryKind = Literal["long_term_preference", "stable_background"]
KINDS = frozenset({"long_term_preference", "stable_background"})
_MODEL_FIELDS = frozenset({"content", "kind", "evidence", "reason"})
VALIDATION_CODES = frozenset({
    "text_type", "unsafe_text", "empty_text", "text_too_long", "invalid_source_ids",
    "source_excluded", "no_explicit_long_term_evidence", "duplicate_json_key",
    "invalid_json_constant", "output_size_or_type", "invalid_json", "unsupported_structure",
    "too_many_candidates", "candidate_fields", "invalid_kind", "evidence_not_found",
    "evidence_not_long_term", "candidate_excluded", "content_not_supported",
    "unexpected_tool_calls", "incomplete_response", "validation_failed",
})

# Deliberately conservative screening, not a comprehensive privacy/NLP classifier.
# Suspicious source messages are withheld from the extraction model altogether.
_HARD_SECRET_OR_IDENTIFIER = re.compile(
    r"api[ _-]?key|password|passwd|\btoken\b|\bsecret\b|credential|"
    r"\bsk-[a-z0-9_-]+|-----BEGIN .*PRIVATE KEY|bearer\s+\S+|"
    r"密码|密钥|口令|令牌|身份证|护照|住址|家庭地址|手机号|银行卡|"
    r"银行账号|银行账户|信用卡|社保|"
    r"\b(?:passport|ssn|bank account|home address|phone number)\b|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)\d{7,}(?!\d)", re.IGNORECASE,
)
# Sensitive *topics* are permitted. These patterns require a personal disclosure
# relationship, including application/model paraphrases referring to 'the user'.
_SENSITIVE_PERSONAL_DISCLOSURE = re.compile(
    r"(?:我|用户|使用者)\s*(?:本人\s*)?(?:长期|一直|曾经|已经|目前|现在|正在)?\s*"
    r"(?:患有|患上|感染|得了|被诊断为|被诊断患有|确诊为|确诊患有)|"
    r"(?:我|用户|使用者)\s*(?:的\s*)?(?:个人\s*)?"
    r"(?:病史|诊断|宗教(?:信仰)?|信仰|政治(?:立场|倾向|观点)|种族|民族|性取向|性生活|收入|财产)"
    r"\s*(?:是|为|包括|记录了|显示|[:：=…])|"
    r"(?:我|用户|使用者)\s*(?:目前\s*)?是[^。！？!?；;\n\r]{0,30}(?:患者|病人|信徒|教徒)|"
    r"\b(?:I|the user|user)\s+(?:(?:currently|regularly|usually|previously|personally|also)\s+)?"
    r"(?:have|has|suffer from|suffers from|"
    r"was diagnosed with|am diagnosed with|is diagnosed with|am living with|is living with)\s+"
    r"(?:diabetes|cancer|depression|HIV|a medical condition)\b"
    r"(?!\s*(?:datasets?\b|data\b|examples?\b|classification\b))|"
    r"\b(?:I am|the user is|user is)\s+(?:HIV[- ]positive|diabetic|"
    r"(?:a\s+)?(?:cancer|diabetes)\s+patient|(?:a\s+)?(?:religious\s+)?believer)\b|"
    r"\b(?:my|the user's|user's)\s+(?:medical history|(?:medical\s+)?diagnosis|religion|"
    r"religious beliefs|faith|political affiliation|political views|race|ethnicity|"
    r"sexual orientation|sex life|income|salary|assets)\s*(?:is\b|are\b|includes\b|[:=])",
    re.IGNORECASE,
)


def _has_private_information(text: str) -> bool:
    # Also inspect whitespace-normalized text so a line break inside a personal
    # assertion cannot hide it from source/content/evidence/reason screening.
    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    return bool(_HARD_SECRET_OR_IDENTIFIER.search(normalized)
                or _SENSITIVE_PERSONAL_DISCLOSURE.search(normalized))


_QUOTED = re.compile(r'引用|原文|转述|[“”「」『』]|"[^"\n]*"|\'[^\'\n]+\'|`|(?m:^\s*>)|\b(?:quoted|quotation)\b', re.IGNORECASE)
_THIRD_PERSON = re.compile(r"我(?:的)?(?:朋友|同学|同事|家人|老师)|他|她|他们|她们|\b(?:my friend|he|she|they)\b", re.IGNORECASE)
_TEMPORARY = re.compile(
    r"这次|本次|这(?:个|项)项目|当前项目|本项目|今天|今日|今晚|暂时|临时|"
    r"这道题|这题|一次性|\b(?:this time|this project|today|tonight|temporary|for now)\b", re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"[?？]|你觉得|是不是|也许|可能|假如|如果|以后就这样|之前那个|还是按之前|"
    r"据此|推断|基础(?:薄弱|差)|能力(?:差|弱)|数学(?:差|不好)|智商|"
    r"Mastery|Capability|\b(?:maybe|perhaps|if|infer|guess|as before|same as before)\b", re.IGNORECASE,
)
_PREFERENCE = re.compile(
    r"以后|今后|默认|偏好|喜欢|一贯|通常|平时|长期|"
    r"\b(?:prefer|preference|by default|default|always|usually|from now on|in future)\b", re.IGNORECASE,
)
_BACKGROUND = re.compile(
    r"长期|一直|日常|通常|平时|主要使用|\b(?:long.term|usually|primarily|regularly)\b", re.IGNORECASE,
)
_SELF = re.compile(r"我|\b(?:I|my)\b", re.IGNORECASE)
_IDENTITY = re.compile(r"我(?:目前)?是|\bI am\b", re.IGNORECASE)
_NEGATION = re.compile(
    r"并非|不是|不喜欢|不偏好|不使用|不采用|不再|不想|不愿|不用|没有|从未|未曾|"
    r"\b(?:not|never|don't|do not)\b", re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    content: str
    kind: MemoryKind
    source_session_id: int
    source_message_id: int
    evidence: str
    reason: str
    normalized_key: str
    extractor_version: int
    possible_conflict: bool = False  # Reserved; existing memories are not consulted in E-A.


class MemoryCandidateValidationError(ValueError):
    """Only a finite application-owned code; never include model/user text."""

    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in VALIDATION_CODES else "validation_failed"
        super().__init__(self.code)


def normalized_memory_key(content: str) -> str:
    """NFC + conservative whitespace normalization; preserve case/punctuation."""
    return " ".join(unicodedata.normalize("NFC", content).strip().split())


def _text(value, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise MemoryCandidateValidationError("text_type")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in "\n\r\t"
           for char in value):
        raise MemoryCandidateValidationError("unsafe_text")
    value = value.strip()
    if not value:
        raise MemoryCandidateValidationError("empty_text")
    if maximum is not None and len(value) > maximum:
        raise MemoryCandidateValidationError("text_too_long")
    return value


def _source_ids(session_id, message_id):
    if any(type(value) is not int or value <= 0 for value in (session_id, message_id)):
        raise MemoryCandidateValidationError("invalid_source_ids")


def _statements(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|[\n\r]", text) if part.strip()]


def _qualifies(statement: str, kind: str) -> bool:
    if (_TEMPORARY.search(statement) or _UNCERTAIN.search(statement)
            or _NEGATION.search(statement)):
        return False
    if kind == "long_term_preference":
        # Explicit future/default instruction may use an implicit first-person subject.
        return bool(_PREFERENCE.search(statement))
    return bool(_SELF.search(statement) and (_BACKGROUND.search(statement) or _IDENTITY.search(statement)))


def validate_source(user_message: str, source_session_id: int, source_message_id: int) -> str:
    """Only one raw user message; no lookup, provenance claims come from the caller."""
    _source_ids(source_session_id, source_message_id)
    text = _text(user_message)
    if _has_private_information(text) or _QUOTED.search(text) or _THIRD_PERSON.search(text):
        raise MemoryCandidateValidationError("source_excluded")
    if not any(_qualifies(part, kind) for part in _statements(text) for kind in KINDS):
        raise MemoryCandidateValidationError("no_explicit_long_term_evidence")
    return user_message


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise MemoryCandidateValidationError("duplicate_json_key")
        obj[key] = value
    return obj


def _invalid_constant(_value):
    raise MemoryCandidateValidationError("invalid_json_constant")


def parse_memory_candidates(
    raw: str, *, user_message: str, source_session_id: int, source_message_id: int,
) -> list[MemoryCandidate]:
    """Strict JSON array; an invalid item rejects the entire response, no repair.

    Only extractive, whitespace/NFC-equivalent content is accepted in E-A.
    Screening is deliberately incomplete: later user confirmation remains required,
    and literal substrings alone cannot prove every possible semantic interpretation.
    """
    source = validate_source(user_message, source_session_id, source_message_id)
    if not isinstance(raw, str) or len(raw) > MAX_OUTPUT_CHARS:
        raise MemoryCandidateValidationError("output_size_or_type")
    try:
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except MemoryCandidateValidationError:
        raise
    except (ValueError, RecursionError):
        raise MemoryCandidateValidationError("invalid_json") from None
    if type(data) is not list:
        raise MemoryCandidateValidationError("unsupported_structure")
    if len(data) > MAX_CANDIDATES:
        raise MemoryCandidateValidationError("too_many_candidates")
    candidates = []
    seen = set()
    for item in data:
        if type(item) is not dict or set(item) != _MODEL_FIELDS:
            raise MemoryCandidateValidationError("candidate_fields")
        kind = item["kind"]
        if not isinstance(kind, str) or kind not in KINDS:
            raise MemoryCandidateValidationError("invalid_kind")
        content = _text(item["content"], 1000)
        evidence = _text(item["evidence"], 1000)
        reason = _text(item["reason"], 500)
        if any(_has_private_information(text) for text in (content, evidence, reason)):
            raise MemoryCandidateValidationError("candidate_excluded")
        if evidence not in source:
            raise MemoryCandidateValidationError("evidence_not_found")
        # Check the full statement too: a model cannot omit 'this time' or a
        # negation from a substring and thereby turn it into a lasting preference.
        if not _qualifies(evidence, kind) or not any(
            evidence in part and _qualifies(part, kind) for part in _statements(source)
        ):
            raise MemoryCandidateValidationError("evidence_not_long_term")
        if (_QUOTED.search(content) or _THIRD_PERSON.search(content) or _TEMPORARY.search(content)
                or _UNCERTAIN.search(content) or _NEGATION.search(content)):
            raise MemoryCandidateValidationError("candidate_excluded")
        key = normalized_memory_key(content)
        # E-A is intentionally extractive: whitespace/NFC-equivalent excerpts
        # only, not unverifiable model paraphrases or additional claims.
        if key not in normalized_memory_key(evidence):
            raise MemoryCandidateValidationError("content_not_supported")
        if key in seen:
            continue
        seen.add(key)
        candidates.append(MemoryCandidate(
            content=content, kind=kind, source_session_id=source_session_id,
            source_message_id=source_message_id, evidence=evidence, reason=reason,
            normalized_key=key, extractor_version=EXTRACTOR_VERSION,
        ))
    return candidates
