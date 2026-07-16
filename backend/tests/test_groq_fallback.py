import json

import httpx

from app import llm


def _handler(status_by_model):
    def handle(request):
        m = json.loads(request.content)["model"]
        st = status_by_model.get(m, 200)
        if st >= 400:
            return httpx.Response(st, json={"error": {"message": f"rate limit {m}"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": f"A civil noise complaint from {m}."}}]})

    return handle


def _patch(monkeypatch, handler):
    orig = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        kw.pop("timeout", None)
        kw["transport"] = httpx.MockTransport(handler)
        orig(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


async def test_falls_back_to_secondary_on_429(monkeypatch):
    _patch(monkeypatch, _handler({"llama-3.3-70b-versatile": 429}))
    out = await llm.generate_with_groq("k", "llama-3.3-70b-versatile", "p", fallback_model="llama-3.1-8b-instant")
    assert out and "llama-3.1-8b-instant" in out


async def test_none_when_all_rate_limited(monkeypatch):
    _patch(monkeypatch, _handler({"llama-3.3-70b-versatile": 429, "llama-3.1-8b-instant": 429}))
    assert await llm.generate_with_groq("k", "llama-3.3-70b-versatile", "p", fallback_model="llama-3.1-8b-instant") is None


async def test_primary_used_when_available(monkeypatch):
    _patch(monkeypatch, _handler({}))
    out = await llm.generate_with_groq("k", "llama-3.3-70b-versatile", "p", fallback_model="llama-3.1-8b-instant")
    assert out and "llama-3.3-70b-versatile" in out


async def test_no_key_returns_none():
    assert await llm.generate_with_groq(None, "llama-3.3-70b-versatile", "p") is None
