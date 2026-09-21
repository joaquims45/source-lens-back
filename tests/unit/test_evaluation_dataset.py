import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sourcelens.evaluation.dataset import load_dataset


def test_load_dataset_parses_questions():
    with TemporaryDirectory() as workdir:
        path = Path(workdir) / "dataset.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "question": "Where is auth?",
                        "expected_files": ["src/auth/auth.service.ts"],
                    },
                    {
                        "question": "Where is billing?",
                        "expected_files": ["src/billing/billing.ts"],
                    },
                ]
            )
        )

        questions = load_dataset(path)

    assert [item.question for item in questions] == ["Where is auth?", "Where is billing?"]
    assert questions[0].expected_files == ["src/auth/auth.service.ts"]
