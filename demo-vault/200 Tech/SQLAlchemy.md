---
tags: [технология]
статус: изучаю
создано: 2026-01-15
---

## 🎯 Зачем мне это
ORM для работы с PostgreSQL из Python, в том числе асинхронно.

## 🧠 Ключевые концепты
- [[Транзакции и ACID]]

## 💻 Примеры кода
```python
stmt = select(User).options(selectinload(User.notes))
users = (await session.scalars(stmt)).all()
```

## ⚡ Плюсы и минусы
| Плюсы | Минусы |
|-------|--------|
| Мощный, гибкий, есть async | Легко словить N+1 |

## 🗺️ MOC
- [[MOC - Backend]]

## ⚠️ Подводные камни
- [[N+1 запросы в SQLAlchemy]]
