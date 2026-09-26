---
tags: [технология]
статус: изучаю
создано: 2025-12-12
---

## 🎯 Зачем мне это
Быстрый асинхронный фреймворк для REST API в пет-проектах.

## 🧠 Ключевые концепты
- [[REST]]
- [[Dependency Injection]]
- [[Асинхронность в Python]]

## 💻 Примеры кода
```python
@app.get('/notes/{note_id}')
async def get_note(note_id: int, db = Depends(get_db)):
    return await db.get(Note, note_id)
```

## ⚡ Плюсы и минусы
| Плюсы | Минусы |
|-------|--------|
| Автодокументация, валидация через Pydantic | Меньше «батареек», чем в Django |

## 🗺️ MOC
- [[MOC - Backend]]
