from pickley import flattened


def test_flattened():
    assert len(flattened(None)) == 0
    assert len(flattened("")) == 0
    assert flattened("a b") == ["a b"]
    assert flattened("a b", separator=" ") == ["a", "b"]
    assert flattened(["a b"]) == ["a b"]
    assert flattened(["a b", ["a b c"]]) == ["a b", "a b c"]
    assert flattened(["a b", ["a b c"]], separator=" ") == ["a", "b", "c"]
    assert flattened(["a b", ["a b c"], "a"], separator=" ", unique=False) == ["a", "b", "a", "b", "c", "a"]

    assert flattened(["a b", [None, "-i", None]]) == ["a b", "-i"]
    assert flattened(["a b", [None, "-i", None]], unique=False) == ["a b"]
