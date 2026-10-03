"""Structural root-fence segmentation; clipboard offsets always refer to raw source.

markdown-it identifies ownership, never renders HTML or resolves resources. Qt
remains the display authority. Python character offsets are not Qt UTF-16 offsets.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

try:
    from markdown_it import MarkdownIt
    from markdown_it.rules_block import reference
except ImportError as exc:
    raise ImportError(
        "Agent Markdown panels require markdown-it-py>=4.0,<5.0. "
        "Update the project venv: .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
    ) from exc


@dataclass(frozen=True)
class MarkdownBlock:
    source: str
    start: int
    end: int
    # Derived rendering input only: never part of source or clipboard payload.
    reference_context: str = ""


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


_MAX_CHARS = 1_000_000
_MAX_LINES = 30_000
_MAX_DEPTH = 64


def _reference_with_source(state, start, end, silent):
    """Retain definition spelling, including duplicates, with container prefixes removed.

    getLines uses the parser's temporary container offsets, not a label/URL regex.
    This text is only render context; raw maps remain the source authority.
    """
    if not reference(state, start, end, silent):
        return False
    if not silent:
        state.tokens[-1].meta["source"] = state.getLines(start, state.line, state.blkIndent, False)
    return True


def _parser():
    parser = MarkdownIt("commonmark", {
        "html": False, "inline_definitions": True, "maxNesting": _MAX_DEPTH,
    }).enable(["table", "strikethrough"])
    # Qt can represent even file/javascript references. Collect their structure
    # too; navigation/resource safety belongs to the native view, not this parser.
    parser.validateLink = lambda _url: True
    parser.block.ruler.at("reference", _reference_with_source)
    return parser


def _fence(line: str) -> tuple[str, int, str] | None:
    body = line.rstrip("\r\n")
    match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", body)
    if not match:
        return None
    run, info = match.groups()
    return run[0], len(run), info


def segment_agent_content(source: str) -> list[MarkdownBlock | FencedBlock]:
    """Parse the complete reply once; lift only parser-owned root fences.

    Lists, quotes and indented code retain their contiguous native Markdown
    regions. Parse/map failures and explicit bounds retain the complete message.
    """
    whole = [MarkdownBlock(source, 0, len(source))]
    if len(source) > _MAX_CHARS or any(c in source for c in "\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
        return whole
    lines = source.splitlines(keepends=True)
    if len(lines) > _MAX_LINES:
        return whole
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    try:
        env = {}
        tokens = _parser().parse(source, env)
        if any(token.level >= _MAX_DEPTH - 1 for token in tokens):
            return whole
        roots = [token for token in tokens if token.type == "fence" and token.level == 0]
        if not roots:
            return whole
        definitions = []
        for token in tokens:
            if token.type != "definition":
                continue
            first, last = token.map
            if not 0 <= first < last <= len(lines):
                return whole
            # Root definitions keep original CRLF/Unicode spelling. Nested
            # definitions use parser-stripped container syntax for Qt context.
            definitions.append(source[offsets[first]:offsets[last]] if token.level == 0
                               else token.meta["source"])
        context = "\n\n".join(definitions)
        blocks: list[MarkdownBlock | FencedBlock] = []
        text_start = 0
        for token in roots:
            first, last = token.map
            if not 0 <= first < last <= len(lines):
                return whole
            start, end = offsets[first], offsets[last]
            if start < text_start:
                return whole
            opener = _fence(lines[first])
            if opener is None or (opener[0] == "`" and "`" in opener[2]):
                return whole
            char, length, info = opener
            payload_start = offsets[first + 1]
            payload_end = end
            closed = False
            # Inspect only parser-approved bounds; shorter inner fences remain
            # payload, even when the parser normalized indent/CRLF/content.
            for index in range(first + 1, last):
                closer = _fence(lines[index])
                if (closer and closer[0] == char and closer[1] >= length
                        and not closer[2].strip(" \t")):
                    if index != last - 1:
                        return whole
                    closed = True
                    payload_end = offsets[index]
                    break
            if text_start < start:
                blocks.append(MarkdownBlock(source[text_start:start], text_start, start, context))
            blocks.append(FencedBlock(source[start:end], start, end,
                                      source[payload_start:payload_end], payload_start,
                                      payload_end, info.strip(), closed))
            text_start = end
        if text_start < len(source):
            blocks.append(MarkdownBlock(source[text_start:], text_start, len(source), context))
        return blocks
    except Exception:  # one malformed output must not break the conversation
        return whole
