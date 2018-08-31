from pickley import system


def test_flattened():
    assert len(system.flattened(None)) == 0
    assert len(system.flattened("")) == 0
    assert system.flattened("a b") == ["a b"]
    assert system.flattened("a b", separator=" ") == ["a", "b"]
    assert system.flattened(["a b"]) == ["a b"]
    assert system.flattened(["a b", ["a b c"]]) == ["a b", "a b c"]
    assert system.flattened(["a b", ["a b c"]], separator=" ") == ["a", "b", "c"]
    assert system.flattened(["a b", ["a b c"], "a"], separator=" ", unique=False) == ["a", "b", "a", "b", "c", "a"]

    assert system.flattened(["a b", [None, "-i", None]]) == ["a b", "-i"]
    assert system.flattened(["a b", [None, "-i", None]], unique=False) == ["a b"]
