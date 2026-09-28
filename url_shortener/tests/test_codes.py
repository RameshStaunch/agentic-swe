from shortener.codes import ALPHABET, CODE_LENGTH, new_code, valid_alias


def test_new_code_shape():
    code = new_code()
    assert len(code) == CODE_LENGTH
    assert set(code) <= set(ALPHABET)


def test_new_codes_are_distinct():
    assert len({new_code() for _ in range(10_000)}) == 10_000


def test_valid_alias():
    assert valid_alias("my-link_1")
    assert not valid_alias("ab")
    assert not valid_alias("has space")
    assert not valid_alias("x" * 33)
    assert not valid_alias("links")
    assert not valid_alias("HEALTHZ")
