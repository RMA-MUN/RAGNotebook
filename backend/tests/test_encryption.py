import pytest

from app.utils.encryption import decrypt_secret, encrypt_secret


def test_encrypt_decrypt_roundtrip():
    token = encrypt_secret("sk-1234567890")
    assert token != "sk-1234567890"
    assert decrypt_secret(token) == "sk-1234567890"


def test_empty_is_identity():
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_missing_secret_key_raises(monkeypatch):
    from app.core.settings import settings
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    from app.utils.encryption import encrypt_secret
    with pytest.raises(RuntimeError):
        encrypt_secret("sk-x")
