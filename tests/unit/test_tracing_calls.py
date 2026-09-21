from uuid import uuid4

from sourcelens.persistence.models import Symbol
from sourcelens.tracing.calls import detect_calls


def make_symbol(
    file_content: str,
    snippet: str,
    name: str,
    kind: str,
    file_id,
    parent_id=None,
    qualified_name=None,
) -> Symbol:
    """Locates `snippet` inside `file_content` to derive real byte/line
    offsets, the way the actual parser would — several symbols in a test
    must share one coherent file content, not each carry their own isolated
    string, or the byte ranges used to extract call sites would be wrong.
    """
    start_byte = file_content.encode().index(snippet.encode())
    end_byte = start_byte + len(snippet.encode())
    start_line = file_content[:start_byte].count("\n") + 1
    end_line = start_line + snippet.count("\n")
    return Symbol(
        id=uuid4(),
        analysis_id=uuid4(),
        file_id=file_id,
        parent_id=parent_id,
        name=name,
        qualified_name=qualified_name or name,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
        start_byte=start_byte,
        end_byte=end_byte,
        signature=name,
    )


def test_bare_call_resolves_when_the_name_is_unique():
    file_id = uuid4()
    content = "def helper():\n    return 1\n\ndef main():\n    return helper()\n"
    callee = make_symbol(content, "def helper():\n    return 1\n", "helper", "function", file_id)
    caller = make_symbol(content, "def main():\n    return helper()\n", "main", "function", file_id)

    edges = detect_calls([callee, caller], {file_id: content})

    assert len(edges) == 1
    edge = edges[0]
    assert edge.caller_id == caller.id
    assert edge.callee_id == callee.id
    assert edge.resolution == "resolved"
    assert edge.confidence == 0.6


def test_self_qualified_call_resolves_against_sibling_method_first():
    file_id = uuid4()
    class_id = uuid4()
    content = (
        "class Service:\n"
        "    def save(self):\n"
        "        pass\n\n"
        "    def create(self):\n"
        "        self.save()\n"
    )
    save = make_symbol(
        content,
        "def save(self):\n        pass\n",
        "save",
        "method",
        file_id,
        parent_id=class_id,
        qualified_name="Service.save",
    )
    create = make_symbol(
        content,
        "def create(self):\n        self.save()\n",
        "create",
        "method",
        file_id,
        parent_id=class_id,
        qualified_name="Service.create",
    )

    edges = detect_calls([save, create], {file_id: content})

    assert len(edges) == 1
    assert edges[0].caller_id == create.id
    assert edges[0].callee_id == save.id
    assert edges[0].resolution == "resolved"
    assert edges[0].confidence == 0.8  # qualified match outranks a bare name match


def test_ambiguous_call_lists_every_candidate():
    file_id = uuid4()
    class_a = uuid4()
    class_b = uuid4()
    content = "def save_a():\n    pass\n\ndef save_b():\n    pass\n\ndef run():\n    save()\n"
    save_a = make_symbol(
        content, "def save_a():\n    pass\n", "save", "method", file_id, parent_id=class_a
    )
    save_b = make_symbol(
        content, "def save_b():\n    pass\n", "save", "method", file_id, parent_id=class_b
    )
    caller = make_symbol(content, "def run():\n    save()\n", "run", "function", file_id)

    edges = detect_calls([save_a, save_b, caller], {file_id: content})

    assert len(edges) == 2
    assert {e.resolution for e in edges} == {"ambiguous"}
    assert {e.callee_id for e in edges} == {save_a.id, save_b.id}
    assert set(edges[0].candidates) == {save_a.id, save_b.id}


def test_unmatched_call_produces_no_edge():
    file_id = uuid4()
    content = "def run():\n    requests.get('http://example.com')\n"
    caller = make_symbol(content, content, "run", "function", file_id)

    edges = detect_calls([caller], {file_id: content})

    assert edges == []


def test_keywords_and_builtins_are_not_treated_as_calls():
    file_id = uuid4()
    content = "def run():\n    if True:\n        print(len([1, 2]))\n"
    caller = make_symbol(content, content, "run", "function", file_id)

    edges = detect_calls([caller], {file_id: content})

    assert edges == []


def test_recursive_calls_are_preserved():
    file_id = uuid4()
    content = "def factorial(n):\n    return factorial(n - 1)\n"
    caller = make_symbol(content, content, "factorial", "function", file_id)

    edges = detect_calls([caller], {file_id: content})

    assert len(edges) == 1
    assert edges[0].caller_id == edges[0].callee_id == caller.id
