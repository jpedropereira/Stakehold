from shared.env import bool_env, float_env, int_env


def test_int_env_reads_set_value(monkeypatch):
    monkeypatch.setenv("X_INT", "42")

    assert int_env("X_INT", 1) == 42


def test_int_env_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("X_INT", raising=False)

    assert int_env("X_INT", 7) == 7


def test_int_env_falls_back_to_default_when_empty(monkeypatch):
    monkeypatch.setenv("X_INT", "")

    assert int_env("X_INT", 7) == 7


def test_float_env_reads_set_value(monkeypatch):
    monkeypatch.setenv("X_FLOAT", "1.5")

    assert float_env("X_FLOAT", 0.0) == 1.5


def test_float_env_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("X_FLOAT", raising=False)

    assert float_env("X_FLOAT", 2.5) == 2.5


def test_bool_env_true_values(monkeypatch):
    for value in ("1", "true", "True", "yes", "anything-else"):
        monkeypatch.setenv("X_BOOL", value)
        assert bool_env("X_BOOL", False) is True


def test_bool_env_false_values(monkeypatch):
    for value in ("0", "false", "False", "no", "NO"):
        monkeypatch.setenv("X_BOOL", value)
        assert bool_env("X_BOOL", True) is False


def test_bool_env_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("X_BOOL", raising=False)

    assert bool_env("X_BOOL", True) is True
    assert bool_env("X_BOOL", False) is False


def test_bool_env_falls_back_to_default_when_empty_or_whitespace(monkeypatch):
    monkeypatch.setenv("X_BOOL", "")
    assert bool_env("X_BOOL", True) is True

    monkeypatch.setenv("X_BOOL", "   ")
    assert bool_env("X_BOOL", True) is True
