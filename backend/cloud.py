"""Client for OpenAI-compatible chat APIs: OpenRouter, DeepSeek, OpenAI and any compatible server.

Exposes the same chat interface as the Ollama client (chat_stream / chat / models), so AI features
don't care which provider is active. Embeddings for search stay on local Ollama.
"""
from __future__ import annotations

import json

import httpx

from .ollama_client import OllamaError

TIMEOUT = httpx.Timeout(30.0, connect=10.0, read=300.0)

PRESETS = {
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "keys_url": "https://openrouter.ai/keys", "example": "deepseek/deepseek-chat"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com",
                 "keys_url": "https://platform.deepseek.com/api_keys", "example": "deepseek-chat"},
    "openai": {"label": "OpenAI (ChatGPT)", "base_url": "https://api.openai.com/v1",
               "keys_url": "https://platform.openai.com/api-keys", "example": "gpt-4o-mini"},
    "custom": {"label": "Другой OpenAI-совместимый", "base_url": "", "keys_url": "", "example": ""},
}


class CloudError(OllamaError):
    """Subclass of OllamaError so every place that handles model errors handles these too."""


def _explain_status(code: int, body: str, provider: str) -> CloudError:
    try:
        data = json.loads(body)
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else (err or data.get("message") or body)
    except ValueError:
        msg = body
    msg = str(msg)[:400]
    label = PRESETS.get(provider, {}).get("label", provider)
    hints = {
        401: "Неверный или отозванный API-ключ.",
        402: "На счёте провайдера закончились деньги.",
        403: "Доступ запрещён (ключ без прав или провайдер недоступен из твоего региона — попробуй через VPN/прокси).",
        404: "Модель не найдена — проверь её имя.",
        429: "Слишком много запросов или исчерпан лимит — подожди немного.",
    }
    return CloudError(f"{label}: {hints.get(code, f'ошибка {code}.')} {msg}".strip())


class CloudLLM:
    def __init__(self, provider: str, base_url: str, api_key: str):
        self.provider = provider if provider in PRESETS else "custom"
        self.url = (base_url or PRESETS[self.provider]["base_url"]).rstrip("/")
        self.api_key = api_key or ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.provider == "openrouter":
            # optional attribution headers recommended by OpenRouter
            h["HTTP-Referer"] = "http://127.0.0.1:8765"
            h["X-Title"] = "Mnemo"
        return h

    def _client(self) -> httpx.AsyncClient:
        # unlike local Ollama, cloud calls DO honour system proxies (needed e.g. for OpenAI from some regions)
        return httpx.AsyncClient(timeout=TIMEOUT, trust_env=True)

    def _check_ready(self):
        if not self.url:
            raise CloudError("Не указан адрес API провайдера.")
        if not self.api_key and self.provider != "custom":
            raise CloudError("Не указан API-ключ. Добавь его в Настройках.")

    async def models(self) -> list:
        self._check_ready()
        try:
            async with self._client() as c:
                r = await c.get(f"{self.url}/models", headers=self._headers())
        except httpx.HTTPError as e:
            raise CloudError(f"Не удалось подключиться к {self.url}: {type(e).__name__}")
        if r.status_code >= 400:
            raise _explain_status(r.status_code, r.text, self.provider)
        out = []
        for m in r.json().get("data", []):
            pricing = m.get("pricing") or {}
            free = str(pricing.get("prompt", "")) in ("0", "0.0") and str(pricing.get("completion", "")) in ("0", "0.0")
            out.append({"name": m.get("id", ""), "free": free or m.get("id", "").endswith(":free"),
                        "context": m.get("context_length"), "embedding": False})
        return sorted(out, key=lambda m: m["name"])

    async def verify_key(self) -> None:
        """OpenRouter's /models is public, so check the key explicitly there."""
        self._check_ready()
        if self.provider != "openrouter":
            return
        try:
            async with self._client() as c:
                r = await c.get(f"{self.url}/key", headers=self._headers())
        except httpx.HTTPError as e:
            raise CloudError(f"Не удалось подключиться к {self.url}: {type(e).__name__}")
        if r.status_code >= 400:
            raise _explain_status(r.status_code, r.text, self.provider)

    def _payload(self, model, messages, options, fmt, stream):
        options = options or {}
        payload = {"model": model, "messages": messages, "stream": stream}
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if fmt == "json":
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def chat_stream(self, model: str, messages: list, options: dict | None = None, fmt=None):
        self._check_ready()
        payload = self._payload(model, messages, options, fmt, True)
        try:
            async with self._client() as c:
                async with c.stream("POST", f"{self.url}/chat/completions", json=payload, headers=self._headers()) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode("utf-8", "replace")
                        if fmt and r.status_code == 400 and "response_format" in body:
                            # model without JSON mode: retry without it (prompts already ask for JSON)
                            async for chunk in self.chat_stream(model, messages, options, None):
                                yield chunk
                            return
                        raise _explain_status(r.status_code, body, self.provider)
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue  # SSE comments like ": OPENROUTER PROCESSING"
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        d = json.loads(data)
                        if d.get("error"):
                            err = d["error"]
                            raise CloudError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                        choices = d.get("choices") or []
                        delta = (choices[0].get("delta") or {}) if choices else {}
                        # reasoning models also send "reasoning_content" — only the answer is shown
                        if delta.get("content"):
                            yield delta["content"]
        except httpx.HTTPError as e:
            raise CloudError(f"Сетевая ошибка при обращении к {self.url}: {type(e).__name__}. "
                             "Проверь интернет/прокси.")

    async def chat(self, model: str, messages: list, options: dict | None = None, fmt=None) -> str:
        parts = []
        async for chunk in self.chat_stream(model, messages, options, fmt):
            parts.append(chunk)
        return "".join(parts)
