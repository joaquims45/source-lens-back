import pytest

from sourcelens.intelligence.parser import parse


def test_typescript_classes_interfaces_exports_and_aliases():
    source = """import { User as Person } from "./user";
export interface Store { save(user: Person): void; }
export type ID = string;
export class AuthService {
  login(user: Person) { return user; }
}
export const run = (value: ID) => value;
"""
    result = parse(source, "typescript")
    assert result.status == "ok"
    symbols = {symbol.qualified_name: symbol for symbol in result.symbols}
    assert symbols["Store"].kind == "interface"
    assert symbols["ID"].kind == "type"
    assert symbols["AuthService.login"].kind == "method"
    assert symbols["run"].exported
    assert result.imports[0].module == "./user"
    assert "User as Person" in result.imports[0].content


@pytest.mark.parametrize("language", ["tsx", "javascript"])
def test_jsx_and_arrow_functions(language):
    result = parse("export const App = () => <main>Hello</main>;", language)
    assert result.status == "ok"
    assert result.symbols[0].name == "App"


def test_crlf_ranges():
    result = parse("class A {\r\n  run() {}\r\n}\r\n", "javascript")
    assert [(s.start_line, s.end_line) for s in result.symbols] == [(1, 3), (2, 2)]
