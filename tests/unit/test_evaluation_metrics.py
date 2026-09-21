from sourcelens.evaluation.metrics import recall_at_k, reciprocal_rank


def test_recall_at_k_counts_each_expected_file_once():
    retrieved = ["a.py", "b.py", "a.py", "c.py"]
    assert recall_at_k(retrieved, ["a.py", "b.py"], k=4) == 1.0
    assert recall_at_k(retrieved, ["a.py", "z.py"], k=4) == 0.5


def test_recall_at_k_respects_the_cutoff():
    retrieved = ["a.py", "b.py", "c.py"]
    assert recall_at_k(retrieved, ["c.py"], k=2) == 0.0
    assert recall_at_k(retrieved, ["c.py"], k=3) == 1.0


def test_recall_at_k_with_no_expected_files_is_zero():
    assert recall_at_k(["a.py"], [], k=5) == 0.0


def test_reciprocal_rank_of_first_hit():
    assert reciprocal_rank(["x.py", "a.py", "b.py"], ["a.py"]) == 0.5
    assert reciprocal_rank(["a.py"], ["a.py"]) == 1.0


def test_reciprocal_rank_is_zero_when_nothing_matches():
    assert reciprocal_rank(["x.py", "y.py"], ["a.py"]) == 0.0
