"""Free "web chat" mode: no API key and no local model.

Instead of calling a model, the app shows the user a ready prompt to paste into chat.deepseek.com / chatgpt.com
and waits for the pasted answer. Works with every AI feature through replay:

1. A request runs normally. The first model call raises ManualNeeded with the prompt text.
2. The browser shows the prompt, the user pastes the answer, and the SAME request is sent again with
   manual_answers=[answer]. Retrieval is deterministic, so the replay reaches the same call, which now
   returns the answer, and the next call (if any) raises ManualNeeded(step=1) — and so on.
"""
from __future__ import annotations

import contextvars

from .ollama_client import OllamaError

_session: contextvars.ContextVar[dict | None] = contextvars.ContextVar("manual_session", default=None)

LABEL = "Веб-чат (вручную)"


class ManualNeeded(Exception):
    def __init__(self, info: dict):
        super().__init__(info["title"])
        self.info = info


def start_session(answers: list | None) -> None:
    """Call at the start of every request that may reach the model."""
    _session.set({"answers": [str(a) for a in (answers or [])], "i": 0})


def _purpose(messages: list, fmt) -> tuple[str, bool]:
    text = "\n".join(m.get("content", "") for m in messages)
    if "--- ШАБЛОН ---" in text:
        name = text.split("Шаблон «", 1)[-1].split("»", 1)[0] if "Шаблон «" in text else ""
        title = text.split("Название заметки: ", 1)[-1].split("\n", 1)[0] if "Название заметки: " in text else ""
        return f"Заполнение шаблона «{name}» для заметки «{title}»", True
    if "=== Запрос пользователя ===" in text:
        return "План действий агента", False
    if fmt == "json":
        return "Подсказки тегов, связей и папки", False
    if "быстро найти или вспомнить" in text:
        return "Быстрый ответ по заметкам", False
    return "Ответ ИИ", False


def render_prompt(messages: list, fmt=None) -> str:
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    parts = []
    if system:
        parts.append("=== ИНСТРУКЦИЯ ===\n" + system)
    if len(convo) > 1:
        who = {"user": "Пользователь", "assistant": "Ассистент"}
        parts.append("=== ИСТОРИЯ ДИАЛОГА ===\n" + "\n\n".join(f"{who[m['role']]}: {m['content']}" for m in convo[:-1]))
    if convo:
        parts.append("=== ЗАПРОС ===\n" + convo[-1]["content"])
    if fmt == "json":
        parts.append("ВАЖНО: ответь ТОЛЬКО одним JSON-объектом (можно в блоке ```json), без пояснений до и после.")
    return "\n\n".join(parts)


class ManualLLM:
    provider = "manual"
    url = ""

    async def models(self) -> list:
        return []

    async def chat_stream(self, model: str, messages: list, options: dict | None = None, fmt=None):
        sess = _session.get()
        if sess is None:
            raise OllamaError("Режим веб-чата: запрос без сессии (внутренняя ошибка)")
        i = sess["i"]
        sess["i"] += 1
        if i < len(sess["answers"]):
            yield sess["answers"][i]
            return
        title, skippable = _purpose(messages, fmt)
        raise ManualNeeded({
            "step": i + 1, "title": title, "skippable": skippable, "expects": "json" if fmt == "json" else "text",
            "prompt": render_prompt(messages, fmt),
        })

    async def chat(self, model: str, messages: list, options: dict | None = None, fmt=None) -> str:
        parts = []
        async for chunk in self.chat_stream(model, messages, options, fmt):
            parts.append(chunk)
        return "".join(parts)
