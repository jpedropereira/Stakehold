from auth import tokens


def test_generate_token_produces_distinct_tokens():
    assert tokens.generate_token() != tokens.generate_token()


def test_hash_token_is_32_bytes():
    assert len(tokens.hash_token(tokens.generate_token())) == 32


def test_hash_token_is_stable_for_the_same_input():
    token = tokens.generate_token()

    assert tokens.hash_token(token) == tokens.hash_token(token)


def test_hash_token_differs_for_different_tokens():
    assert tokens.hash_token("token-a") != tokens.hash_token("token-b")


def test_hash_token_never_equals_the_raw_token_bytes():
    token = tokens.generate_token()

    assert tokens.hash_token(token) != token.encode("utf-8")
