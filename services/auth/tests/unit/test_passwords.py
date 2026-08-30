import pytest
from auth import passwords


def test_hash_and_verify_round_trip():
    password_hash = passwords.hash_password("correct horse battery staple")

    assert passwords.verify_password(password_hash, "correct horse battery staple") is True


def test_verify_rejects_wrong_password():
    password_hash = passwords.hash_password("correct horse battery staple")

    assert passwords.verify_password(password_hash, "wrong password") is False


def test_hash_is_not_the_plaintext():
    password_hash = passwords.hash_password("hunter2")

    assert "hunter2" not in password_hash


def test_needs_rehash_is_false_for_a_hash_just_produced():
    password_hash = passwords.hash_password("correct horse battery staple")

    assert passwords.needs_rehash(password_hash) is False


def test_dummy_verify_does_not_raise():
    passwords.dummy_verify()


def test_hash_password_rejects_a_password_over_the_max_length():
    too_long = "a" * (passwords.MAX_PASSWORD_LENGTH + 1)

    with pytest.raises(passwords.PasswordTooLong):
        passwords.hash_password(too_long)


def test_hash_password_accepts_a_password_at_the_max_length():
    at_max = "a" * passwords.MAX_PASSWORD_LENGTH

    passwords.hash_password(at_max)
