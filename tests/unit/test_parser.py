from sourcelens.intelligence.parser import parse


def test_python_hierarchy_decorators_unicode_and_imports():
    source = (
        "from pathlib import Path\n\nclass Café:\n    @staticmethod\n    def run(x):\n"
        "        def nested():\n            return x\n        return nested()\n"
    )
    result = parse(source, "python")
    assert result.status == "ok"
    assert [(symbol.qualified_name, symbol.kind) for symbol in result.symbols] == [
        ("Café", "class"),
        ("Café.run", "method"),
        ("Café.run.nested", "function"),
    ]
    method = result.symbols[1]
    assert (method.start_line, method.end_line) == (4, 8)
    assert source.encode()[method.start_byte : method.end_byte].decode().startswith("@staticmethod")
    assert result.imports[0].module == "pathlib"


def test_errors_are_visible_and_unsupported_is_explicit():
    assert parse("def broken(\n", "python").status == "partial"
    assert parse("hello", "rust").status == "unsupported"
