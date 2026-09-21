from sourcelens.intelligence.chunking import chunks
from sourcelens.intelligence.parser import parse


def test_semantic_chunks_preserve_source_and_coverage():
    content = (
        "class Service:\n    def a(self):\n        return 1\n    def b(self):\n        return 2\n"
    )
    parsed = parse(content, "python")
    result = chunks(content, "src/service.py", "python", parsed)
    assert [chunk.kind for chunk in result] == ["class", "method", "method"]
    for chunk in result:
        assert content.encode()[chunk.start_byte : chunk.end_byte].decode() == chunk.content
        assert chunk.token_count <= 1200
    covered = set()
    for chunk in result:
        positions = set(range(chunk.start_byte, chunk.end_byte))
        assert not covered & positions
        covered |= positions
    assert all(index in covered for index, char in enumerate(content) if not char.isspace())


def test_oversized_unicode_statement_is_lossless():
    content = 'def large():\n    return "' + "á" * 4000 + '"\n'
    result = chunks(content, "a.py", "python", parse(content, "python"), max_tokens=200)
    assert any(chunk.split_reason == "oversized_syntax_node" for chunk in result)
    assert all(chunk.token_count <= 200 for chunk in result)
    assert "".join(chunk.content for chunk in result).strip() == content.strip()
    assert chunks("text", "readme.md", "markdown", parse("text", "markdown")) == []
