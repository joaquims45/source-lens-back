import json
from pathlib import Path

from pydantic import BaseModel


class EvalQuestion(BaseModel):
    question: str
    expected_files: list[str]


def load_dataset(path: str | Path) -> list[EvalQuestion]:
    data = json.loads(Path(path).read_text())
    return [EvalQuestion.model_validate(item) for item in data]
