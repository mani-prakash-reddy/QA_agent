import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from app.core.config import settings
from app.qa.llm.fake import ScriptedChatModel
from app.qa.llm.gateway import LLMConfigError, SecretGuard, get_chat_model
from app.qa.security.crypto import CryptoError, decrypt_json, encrypt_json
from app.qa.security.scanner import SecretLeakError, SecretScanner


def test_crypto_roundtrip_and_ciphertext_hides_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    token = encrypt_json({"username": "admin", "password": "S3cret!pw"})
    assert "S3cret" not in token
    assert decrypt_json(token)["password"] == "S3cret!pw"


def test_crypto_wrong_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    token = encrypt_json({"a": "b"})
    monkeypatch.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(Fernet.generate_key().decode())
    )
    with pytest.raises(CryptoError):
        decrypt_json(token)


def test_crypto_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(old))
    token = encrypt_json({"a": "b"})
    monkeypatch.setattr(
        settings, "CREDENTIAL_ENCRYPTION_KEY", SecretStr(f"{new},{old}")
    )
    assert decrypt_json(token) == {"a": "b"}


def test_crypto_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", None)
    with pytest.raises(CryptoError):
        encrypt_json({})


def test_scanner_redacts_and_ignores_short_values() -> None:
    s = SecretScanner(["hunter2-long", "ab"])
    assert s.contains_secret("pw=hunter2-long")
    assert not s.contains_secret("ab")  # too short -> would false-positive everywhere
    assert s.redact("x hunter2-long y") == "x [REDACTED] y"
    with pytest.raises(SecretLeakError) as exc:
        s.assert_clean("hunter2-long", "test")
    assert "hunter2-long" not in str(exc.value)


def test_canary_secret_never_reaches_the_llm() -> None:
    canary = "CANARY-password-12345"
    guard = SecretGuard(SecretScanner([canary]))
    model = ScriptedChatModel(script=[AIMessage(content="ok")])
    assert model.invoke("hello", config={"callbacks": [guard]}).content == "ok"
    with pytest.raises(SecretLeakError):
        model.invoke(f"log in with {canary}", config={"callbacks": [guard]})


def test_confidential_project_refuses_free_models() -> None:
    with pytest.raises(LLMConfigError):
        get_chat_model(
            "explorer", overrides={"explorer": "x/y:free"}, confidential=True
        )


def test_azure_embeddings_are_preferred_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    from app.core.config import settings
    from app.qa.llm.gateway import get_embeddings

    monkeypatch.setattr(settings, "AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setattr(settings, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "ada")
    monkeypatch.setattr(settings, "AZURE_OPENAI_API_KEY", SecretStr("k-not-real"))
    assert settings.embedding_model_id == "azure:ada"
    emb = get_embeddings(
        confidential=True
    )  # an Azure deployment is fine for confidential data
    assert type(emb).__name__ == "AzureOpenAIEmbeddings"
    monkeypatch.setattr(settings, "AZURE_OPENAI_API_KEY", None)
    assert settings.embedding_model_id == settings.EMBEDDING_MODEL
