import sys
from types import SimpleNamespace
from unittest.mock import Mock

import chromadb
import pytest

from app.db import chroma_client


@pytest.fixture
def doubles(monkeypatch):
    client = Mock()
    monkeypatch.setattr(chromadb, "HttpClient", client)
    encoder = Mock()
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=encoder))
    return SimpleNamespace(client=client, encoder=encoder)


def test_vector_store_authenticates_to_the_proxy_with_configured_token(monkeypatch, doubles):
    token = "a" * 64
    monkeypatch.setattr(chroma_client.settings, "CHROMA_AUTH_TOKEN", token)
    chroma_client.VectorStore()
    kwargs = doubles.client.call_args.kwargs
    assert (kwargs["host"], kwargs["port"]) == (chroma_client.settings.CHROMA_HOST, chroma_client.settings.CHROMA_PORT)
    assert kwargs["settings"].chroma_client_auth_provider == "chromadb.auth.token_authn.TokenAuthClientProvider"
    assert kwargs["settings"].chroma_client_auth_credentials == token


def test_vector_store_refuses_to_start_without_token(monkeypatch, doubles):
    monkeypatch.setattr(chroma_client.settings, "CHROMA_AUTH_TOKEN", "")
    with pytest.raises(RuntimeError, match="CHROMA_AUTH_TOKEN"):
        chroma_client.VectorStore()
    doubles.client.assert_not_called()
    doubles.encoder.assert_not_called()
