"""Minimal async client for the Ollama HTTP API."""
import json
import re

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=4.0, read=900.0)


class OllamaError(Exception):
    pass


def _explain(e: Exception, url: str) -> OllamaError:
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return OllamaError(f"Не удалось подключиться к Ollama по адресу {url}. Запущена ли Ollama?")
    if isinstance(e, httpx.TimeoutException):
        return OllamaError("Ollama не ответила вовремя (таймаут).")
    return OllamaError(str(e))


def _client(timeout) -> httpx.AsyncClient:
    # Ollama is a local service: never route it through system/env proxies (they may hang on localhost)
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


class Ollama:
    def __init__(self, url: str):
        url = (url or "http://127.0.0.1:11434").strip().rstrip("/")
        # "localhost" resolves to ::1 first on Windows, where Ollama doesn't listen -> connect stalls
        self.url = re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", url, flags=re.I)

    async def _check(self, r: httpx.Response):
        if r.status_code >= 400:
            body = (await r.aread()).decode("utf-8", "replace")
            try:
                msg = json.loads(body).get("error", body)
            except ValueError:
                msg = body
            if "not found" in msg and "model" in msg:
                msg += " — скачай модель командой: ollama pull <имя>"
            raise OllamaError(msg)

    async def version(self) -> str:
        try:
            async with _client(httpx.Timeout(5.0, connect=3.0)) as c:
                r = await c.get(f"{self.url}/api/version")
                await self._check(r)
                return r.json().get("version", "?")
        except httpx.HTTPError as e:
            raise _explain(e, self.url)

    async def models(self) -> list:
        try:
            async with _client(httpx.Timeout(10.0, connect=3.0)) as c:
                r = await c.get(f"{self.url}/api/tags")
                await self._check(r)
        except httpx.HTTPError as e:
            raise _explain(e, self.url)
        out = []
        for m in r.json().get("models", []):
            name = m.get("name", "")
            details = m.get("details") or {}
            fams = " ".join((details.get("families") or []) + [details.get("family") or ""]).lower()
            out.append({
                "name": name,
                "size": m.get("size", 0),
                "params": details.get("parameter_size", ""),
                "embedding": "bert" in fams or "embed" in name.lower() or "nomic" in fams,
            })
        return out

    async def chat_stream(self, model: str, messages: list, options: dict | None = None, fmt=None):
        payload = {"model": model, "messages": messages, "stream": True, "options": options or {}}
        if fmt:
            payload["format"] = fmt
        try:
            async with _client(TIMEOUT) as c:
                async with c.stream("POST", f"{self.url}/api/chat", json=payload) as r:
                    await self._check(r)
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        d = json.loads(line)
                        if d.get("error"):
                            raise OllamaError(d["error"])
                        chunk = (d.get("message") or {}).get("content", "")
                        if chunk:
                            yield chunk
                        if d.get("done"):
                            break
        except httpx.HTTPError as e:
            raise _explain(e, self.url)

    async def chat(self, model: str, messages: list, options: dict | None = None, fmt=None) -> str:
        parts = []
        async for chunk in self.chat_stream(model, messages, options, fmt):
            parts.append(chunk)
        return "".join(parts)

    async def embed(self, model: str, texts: list) -> list:
        try:
            async with _client(TIMEOUT) as c:
                r = await c.post(f"{self.url}/api/embed", json={"model": model, "input": texts})
                if r.status_code == 404 and "model" not in r.text:
                    # older Ollama without /api/embed
                    out = []
                    for t in texts:
                        r2 = await c.post(f"{self.url}/api/embeddings", json={"model": model, "prompt": t})
                        await self._check(r2)
                        out.append(r2.json()["embedding"])
                    return out
                await self._check(r)
                return r.json()["embeddings"]
        except httpx.HTTPError as e:
            raise _explain(e, self.url)
