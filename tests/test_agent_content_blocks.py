"""Exact offsets and conservative scope of assistant fence segmentation."""
import pytest

from app.ui.agent_content_blocks import FencedBlock, MarkdownBlock, segment_agent_content


@pytest.mark.parametrize("source,payload,language,closed", [
    ("```python\n  x = 1\n\n```\n", "  x = 1\n\n", "python", True),
    ("````markdown\n# A\n```python\nx\n```\n````\n", "# A\n```python\nx\n```\n", "markdown", True),
    ("~~~md\r\n# A\r\n~~~~ \t\r\n", "# A\r\n", "md", True),
    ("  ```\r\n  a\r\n\r\n   ```\r\n", "  a\r\n\r\n", "", True),
    ("```\n```", "", "", True),
    ("```", "", "", False),
    ("```json\n  a\n", "  a\n", "json", False),
    ("```foo\na", "a", "foo", False),
    ("````\na\n```\nb\n~~~~\n````tail\nc\n`````\n", "a\n```\nb\n~~~~\n````tail\nc\n", "", True),
    ("~~~unknown\nx\n```\n~~~", "x\n```\n", "unknown", True),
    ("```https://example.com\nx\n```", "x\n", "https://example.com", True),
])
def test_lossless_fences(source, payload, language, closed):
    blocks = segment_agent_content(source)
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, FencedBlock)
    assert block.payload == payload
    assert block.language == language
    assert block.closed is closed
    assert source[block.payload_start:block.payload_end] == payload
    assert source[block.start:block.end] == block.source == source


def test_multi_block_order_offsets_and_internal_literal_containers():
    source = "before\r\n\r\n```py\r\n- literal\r\n> literal\r\n```\r\nmiddle\n~~~md\n# H\n~~~\nafter\n"
    blocks = segment_agent_content(source)
    assert [type(b) for b in blocks] == [MarkdownBlock, FencedBlock, MarkdownBlock, FencedBlock, MarkdownBlock]
    assert "".join(b.source for b in blocks) == source
    assert blocks[1].payload == "- literal\r\n> literal\r\n"
    assert blocks[3].payload == "# H\n"
    for block in blocks:
        assert source[block.start:block.end] == block.source


@pytest.mark.parametrize("source", [
    "inline `x` and ```y```", "``not a fence\n", "```bad`info\nx\n```bad`info\n",
    "    ```python\nx\n    ```", "\t```\nx\n\t```",
    "> ```md\n> # H\n> ```\n\n```py\nx\n```",
    "- item\n\n  ```py\n  x\n  ```", "1. first\n\n```py\nx\n```\n\n2. second",
    "- first\n  continuation\n```py\nx\n```", "[link][r]\n\n```\nx\n```\n\n[r]: https://example.com",
    "```\nx\n```\n\n    indented", "```\nx\n```\n\n> quote",
    "See [multi\nline].\n\n```py\nx\n```\n\n[multi\nline]: https://example.com\n",
    "See [a\\]b].\n\n```py\nx\n```\n\n[a\\]b]: https://example.com\n",
])
def test_uncertain_or_cross_document_semantics_stay_whole(source):
    assert segment_agent_content(source) == [MarkdownBlock(source, 0, len(source))]


def test_backtick_invalid_info_does_not_hide_later_valid_fence():
    source = "```bad`info\nparagraph\n\n```ok\nx\n```\n"
    blocks = segment_agent_content(source)
    assert isinstance(blocks[0], MarkdownBlock)
    assert blocks[0].source.startswith("```bad`info")
    assert blocks[1].payload == "x\n"


def test_bounded_scanner_preserves_over_limit_source():
    source = "```\n" + "x" * 1_000_000
    assert segment_agent_content(source) == [MarkdownBlock(source, 0, len(source))]
    source = "\n" * 30_001
    assert segment_agent_content(source) == [MarkdownBlock(source, 0, len(source))]


@pytest.mark.parametrize("source", ["   \t```\nx\n```", "text\u2028```\nx\n```"])
def test_nonstandard_indent_or_linebreak_falls_back(source):
    assert segment_agent_content(source) == [MarkdownBlock(source, 0, len(source))]
