"""Lossless, conservative top-level fence segmentation (not a Markdown parser).

Container-bearing or reference-definition messages are deliberately left whole:
separate QTextDocuments cannot preserve their cross-block semantics. Offsets are
Python string offsets; payloads are slices of the original, including line ends.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MarkdownBlock:
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class FencedBlock:
    source: str
    start: int
    end: int
    payload: str
    payload_start: int
    payload_end: int
    info: str
    closed: bool

    @property
    def language(self) -> str:
        return self.info.split()[0] if self.info.split() else ""


# Hard bounds keep model output scanning linear and predictable. Over-limit
# input remains in the existing native renderer; nothing is discarded.
_MAX_CHARS = 1_000_000
_MAX_LINES = 30_000
_CONTAINER = re.compile(r"^(?: {0,3}(?:>|[-+*](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$))| {4}| {0,3}\t)")
# A reference label may span lines or contain escaped closing brackets. Treat
# any bracket-led line as a candidate instead of partially parsing labels;
# whole-document fallback preserves native cross-document reference semantics.
_REFERENCE = re.compile(r"^ {0,3}\[")


def _fence(line: str) -> tuple[str, int, str] | None:
    body = line.rstrip("\r\n")
    indent = len(body) - len(body.lstrip(" "))
    if indent > 3 or indent == len(body):
        return None
    char = body[indent]
    if char not in "`~":
        return None
    end = indent
    while end < len(body) and body[end] == char:
        end += 1
    length = end - indent
    if length < 3:
        return None
    return char, length, body[end:]


def segment_agent_content(source: str) -> list[MarkdownBlock | FencedBlock]:
    """Lift only reliable top-level fences, otherwise retain the whole message.

    Lists, quotes, indented code and reference definitions outside fences cause
    whole-message fallback, including lazy continuations. Within a fence those
    same characters are literal payload, as are shorter/different closing runs.
    """
    whole = [MarkdownBlock(source, 0, len(source))]
    if len(source) > _MAX_CHARS or any(char in source for char in "\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
        return whole
    lines = source.splitlines(keepends=True)
    if len(lines) > _MAX_LINES:
        return whole
    blocks: list[MarkdownBlock | FencedBlock] = []
    offset = 0
    text_start = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if _CONTAINER.match(line) or _REFERENCE.match(line):
            return whole
        opener = _fence(line)
        if opener is None or (opener[0] == "`" and "`" in opener[2]):
            offset += len(line)
            i += 1
            continue
        char, length, info = opener
        if text_start < offset:
            blocks.append(MarkdownBlock(source[text_start:offset], text_start, offset))
        start = offset
        offset += len(line)
        payload_start = offset
        i += 1
        closed = False
        while i < len(lines):
            closer = _fence(lines[i])
            if (closer is not None and closer[0] == char and closer[1] >= length
                    and not closer[2].strip(" \t")):
                closed = True
                break
            offset += len(lines[i])
            i += 1
        payload_end = offset
        if closed:
            offset += len(lines[i])
            i += 1
        blocks.append(FencedBlock(
            source[start:offset], start, offset, source[payload_start:payload_end],
            payload_start, payload_end, info.strip(), closed,
        ))
        text_start = offset
    if text_start < len(source):
        blocks.append(MarkdownBlock(source[text_start:], text_start, len(source)))
    return blocks or whole
