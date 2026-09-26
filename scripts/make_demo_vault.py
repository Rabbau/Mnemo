"""Generates demo-vault/: a small but realistic Obsidian vault (numbered folders, Templater templates, links).

Used for trying Mnemo locally and for the GitHub Pages demo (scripts/build_demo.py).
Run from the project root:  python scripts/make_demo_vault.py
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "demo-vault"


def concept(created, tags, simple, points, how, code, related, tech):
    return f"""---
tags: [концепт{", " + ", ".join(tags) if tags else ""}]
создано: {created}
---

## 🔍 Что это простыми словами
{simple}

## 📌 Ключевые моменты
{chr(10).join("- " + p for p in points)}

## 🧩 Как это работает
{how}

## 💻 Пример кода
```python
{code}
```

## 🔗 Связанные концепты
{chr(10).join(f"- [[{r}]]" for r in related)}

## 📦 Используется в технологиях
{chr(10).join(f"- [[{t}]]" for t in tech)}
"""


def tech(created, status, why, concepts, code, pros, cons, moc, extra=""):
    return f"""---
tags: [технология]
статус: {status}
создано: {created}
---

## 🎯 Зачем мне это
{why}

## 🧠 Ключевые концепты
{chr(10).join(f"- [[{c}]]" for c in concepts)}

## 💻 Примеры кода
```python
{code}
```

## ⚡ Плюсы и минусы
| Плюсы | Минусы |
|-------|--------|
| {pros} | {cons} |

## 🗺️ MOC
- [[{moc}]]
{extra}"""


def problem(created, status, what, techs, tried, solution, insight, related):
    return f"""---
tags: [проблема]
статус: {status}
создано: {created}
---

## ❓ В чём проблема
{what}

## 🌍 Контекст
- Технология: {", ".join(f"[[{t}]]" for t in techs)}

## 🔬 Что пробовал
{chr(10).join(f"- [x] {t}" for t in tried)}

## ✅ Решение
{solution}

## 💡 Что понял в итоге
{insight}

## 🔗 Связанные концепты
{chr(10).join(f"- [[{r}]]" for r in related)}
"""


NOTES = {
    # ------------------------------------------------------------ 000 Inbox
    "000 Inbox/Прочитать про CRDT.md": """---
tags: [инбокс]
статус: не разобрано
создано: 2026-09-18
---

Как Figma и Notion синхронизируют правки нескольких людей без конфликтов? Говорят, [[CRDT]]. Связано с [[Согласованность данных]].
""",
    "000 Inbox/Бот напоминаний о воде.md": """---
tags: [инбокс, идея]
статус: не разобрано
создано: 2026-09-22
---

Мини-бот: раз в два часа пишет «попей воды». Можно сделать на базе [[Телеграм-бот трекер привычек]] и [[APScheduler]].
""",
    # ------------------------------------------------------------ 100 MOC
    "100 MOC/MOC - Python.md": """---
tags: [moc]
создано: 2025-11-03
---

> Карта знаний по Python

## 🟢 Основы
- [[Python]]
- [[Замыкание]]
- [[Декораторы]]

## 🟡 Продвинутые темы
- [[Асинхронность в Python]]
- [[Event Loop]]
- [[Dependency Injection]]

## 🔵 Связанные технологии
- [[FastAPI]]
- [[SQLAlchemy]]
- [[APScheduler]]

## 🔴 Проблемы и решения
- [[RuntimeError Event loop is closed]]
""",
    "100 MOC/MOC - Backend.md": """---
tags: [moc]
создано: 2025-12-10
---

> Всё, что нужно бэкенд-разработчику

## 🟢 Основы
- [[REST]]
- [[Транзакции и ACID]]
- [[Индексы в базе данных]]

## 🟡 Продвинутые темы
- [[Кеширование]]
- [[Согласованность данных]]
- [[Очереди сообщений]]

## 🔵 Связанные технологии
- [[FastAPI]]
- [[PostgreSQL]]
- [[Redis]]

## 🔴 Проблемы и решения
- [[N+1 запросы в SQLAlchemy]]
""",
    "100 MOC/MOC - DevOps.md": """---
tags: [moc]
создано: 2026-02-14
---

> Деплой, контейнеры и автоматизация

## 🟢 Основы
- [[Git]]
- [[Docker]]
- [[CI-CD]]

## 🔵 Связанные технологии
- [[Kubernetes]]

## 🔴 Проблемы и решения
- [[Контейнер не видит базу данных]]
""",
    # ------------------------------------------------------------ 200 Tech
    "200 Tech/Python.md": tech("2025-10-01", "использую", "Основной язык: бэкенд, боты, скрипты автоматизации.",
                               ["Замыкание", "Декораторы", "Асинхронность в Python"],
                               "numbers = [x * x for x in range(10) if x % 2 == 0]",
                               "Простой синтаксис, огромная экосистема", "Медленнее компилируемых языков, GIL", "MOC - Python"),
    "200 Tech/FastAPI.md": tech("2025-12-12", "изучаю", "Быстрый асинхронный фреймворк для REST API в пет-проектах.",
                                ["REST", "Dependency Injection", "Асинхронность в Python"],
                                "@app.get('/notes/{note_id}')\nasync def get_note(note_id: int, db = Depends(get_db)):\n    return await db.get(Note, note_id)",
                                "Автодокументация, валидация через Pydantic", "Меньше «батареек», чем в Django", "MOC - Backend"),
    "200 Tech/PostgreSQL.md": tech("2025-12-20", "изучаю", "Главная база для проектов: надёжная и с кучей возможностей.",
                                   ["Индексы в базе данных", "Транзакции и ACID"],
                                   "CREATE INDEX idx_notes_user ON notes(user_id);",
                                   "ACID, JSONB, мощные индексы", "Нужно следить за vacuum и индексами", "MOC - Backend"),
    "200 Tech/SQLAlchemy.md": tech("2026-01-15", "изучаю", "ORM для работы с PostgreSQL из Python, в том числе асинхронно.",
                                   ["Транзакции и ACID"],
                                   "stmt = select(User).options(selectinload(User.notes))\nusers = (await session.scalars(stmt)).all()",
                                   "Мощный, гибкий, есть async", "Легко словить N+1", "MOC - Backend",
                                   "\n## ⚠️ Подводные камни\n- [[N+1 запросы в SQLAlchemy]]\n"),
    "200 Tech/Redis.md": tech("2026-03-02", "изучаю", "Кеш и очереди для телеграм-бота, чтобы не долбить базу.",
                              ["Кеширование", "Очереди сообщений"],
                              "await redis.set('user:42:streak', 7, ex=86400)",
                              "Очень быстрый, простые структуры", "Данные в памяти — нужна настройка персистентности", "MOC - Backend"),
    "200 Tech/Docker.md": tech("2026-02-15", "использую", "Одинаковое окружение локально и на сервере, деплой одной командой.",
                               ["CI-CD"],
                               "# docker compose up -d --build",
                               "Воспроизводимость, изоляция", "На Windows бывает медленно", "MOC - DevOps",
                               "\n## ⚠️ Подводные камни\n- [[Контейнер не видит базу данных]]\n"),
    "200 Tech/Git.md": tech("2025-10-05", "использую", "Контроль версий для всех проектов.",
                            ["CI-CD"], "# git switch -c feature/habits && git commit -m 'add habits'",
                            "Стандарт индустрии", "Порог входа для rebase и конфликтов", "MOC - DevOps"),
    "200 Tech/APScheduler.md": tech("2026-04-10", "изучаю", "Планировщик задач: напоминания в боте по расписанию.",
                                    ["Асинхронность в Python", "Event Loop"],
                                    "scheduler.add_job(send_reminder, 'cron', hour=9, args=[user_id])",
                                    "Cron-синтаксис, работает с asyncio", "Задачи живут в процессе — при рестарте нужен jobstore", "MOC - Python"),
    # ------------------------------------------------------------ 300 Concepts
    "300 Concepts/Замыкание.md": concept("2025-10-20", ["python"], "Функция, которая «помнит» переменные из места, где её создали, даже когда оттуда уже вышли.",
                                        ["Внутренняя функция захватывает переменные внешней", "На замыканиях построены декораторы", "nonlocal позволяет менять захваченную переменную"],
                                        "Когда внешняя функция возвращает внутреннюю, Python сохраняет ссылки на нужные переменные в `__closure__`.",
                                        "def counter():\n    n = 0\n    def inc():\n        nonlocal n\n        n += 1\n        return n\n    return inc",
                                        ["Декораторы"], ["Python"]),
    "300 Concepts/Декораторы.md": concept("2025-10-28", ["python"], "Обёртка, которая добавляет функции поведение, не меняя её код.",
                                         ["Декоратор — функция, принимающая функцию", "Работает благодаря замыканиям", "functools.wraps сохраняет имя и docstring"],
                                         "`@timer` над функцией — это то же самое, что `f = timer(f)`.",
                                         "def timer(fn):\n    @wraps(fn)\n    def wrapper(*a, **kw):\n        t = time.perf_counter()\n        result = fn(*a, **kw)\n        print(time.perf_counter() - t)\n        return result\n    return wrapper",
                                         ["Замыкание"], ["Python", "FastAPI"]),
    "300 Concepts/Асинхронность в Python.md": concept("2026-01-05", ["python", "async"], "Способ делать много операций ввода-вывода одновременно в одном потоке: пока ждём сеть, выполняем другое.",
                                                     ["async def создаёт корутину", "await отдаёт управление Event Loop", "Подходит для I/O, а не для тяжёлых вычислений"],
                                                     "Корутины переключаются в точках await, а [[Event Loop]] решает, кого запускать дальше.",
                                                     "async def main():\n    async with aiohttp.ClientSession() as s:\n        pages = await asyncio.gather(*(fetch(s, u) for u in urls))",
                                                     ["Event Loop"], ["Python", "FastAPI", "APScheduler"]),
    "300 Concepts/Event Loop.md": concept("2026-01-07", ["python", "async"], "Диспетчер, который по очереди запускает готовые к работе корутины.",
                                         ["Один цикл на поток", "Закрытый цикл нельзя переиспользовать", "asyncio.run создаёт и закрывает цикл"],
                                         "Цикл хранит очередь задач и следит за сокетами: как только данные пришли — продолжает нужную корутину.",
                                         "loop = asyncio.get_running_loop()\nloop.call_later(5, print, 'прошло 5 секунд')",
                                         ["Асинхронность в Python"], ["Python", "APScheduler"]),
    "300 Concepts/REST.md": concept("2025-12-11", ["backend", "api"], "Стиль API, где всё — ресурсы с адресами, а действия — HTTP-методы.",
                                   ["GET читает, POST создаёт, PUT/PATCH меняет, DELETE удаляет", "Сервер не хранит состояние клиента", "Коды ответа несут смысл: 201, 404, 422"],
                                   "Клиент обращается к `/notes/42`, сервер отвечает JSON-представлением ресурса.",
                                   "@app.post('/notes', status_code=201)\nasync def create_note(note: NoteIn): ...",
                                   ["Кеширование"], ["FastAPI"]),
    "300 Concepts/Транзакции и ACID.md": concept("2025-12-22", ["backend", "бд"], "Транзакция — группа операций, которая выполняется целиком или не выполняется вовсе.",
                                                ["Atomicity — всё или ничего", "Consistency — данные остаются корректными", "Isolation — транзакции не мешают друг другу", "Durability — после commit данные не потеряются"],
                                                "База пишет изменения в журнал (WAL) и применяет их только при commit.",
                                                "async with session.begin():\n    session.add(order)\n    user.balance -= order.total",
                                                ["Согласованность данных", "Индексы в базе данных"], ["PostgreSQL", "SQLAlchemy"]),
    "300 Concepts/Индексы в базе данных.md": concept("2026-01-20", ["бд"], "Как оглавление в книге: помогает найти строки, не читая всю таблицу.",
                                                    ["Чаще всего B-tree", "Ускоряют чтение, замедляют запись", "EXPLAIN ANALYZE покажет, используется ли индекс"],
                                                    "B-tree хранит отсортированные ключи и ссылки на строки, поиск идёт за логарифм.",
                                                    "# EXPLAIN ANALYZE SELECT * FROM notes WHERE user_id = 42;",
                                                    ["Транзакции и ACID"], ["PostgreSQL"]),
    "300 Concepts/Кеширование.md": concept("2026-03-05", ["backend"], "Храним результат дорогой операции рядом, чтобы в следующий раз отдать мгновенно.",
                                          ["Главный вопрос — инвалидация", "TTL спасает от устаревших данных", "Кешировать стоит то, что часто читают и редко меняют"],
                                          "Сначала ищем в кеше ([[Redis]]); если нет — считаем, кладём в кеш с TTL и отдаём.",
                                          "@lru_cache(maxsize=256)\ndef exchange_rate(currency: str) -> float: ...",
                                          ["REST", "Согласованность данных"], ["Redis"]),
    "300 Concepts/Согласованность данных.md": concept("2026-05-11", ["backend", "распределённые-системы"], "Насколько одинаково разные части системы видят одни и те же данные в один момент.",
                                                     ["Строгая согласованность — все видят последнее значение", "Eventual consistency — рано или поздно все сойдутся", "CAP-теорема: при разделении сети выбираем C или A"],
                                                     "Кеши и реплики ускоряют систему, но могут отдавать устаревшие данные.",
                                                     "# реплика может отставать: читаем свежие данные с мастера\nsession = primary_session() if need_fresh else replica_session()",
                                                     ["Кеширование", "Транзакции и ACID", "CRDT"], ["PostgreSQL", "Redis"]),
    "300 Concepts/Очереди сообщений.md": concept("2026-04-02", ["backend"], "Почтовый ящик между сервисами: один кладёт задачу, другой забирает, когда готов.",
                                                ["Развязывают сервисы по времени", "Сглаживают пики нагрузки", "Нужна идемпотентность обработчиков"],
                                                "Продюсер пишет сообщение в очередь, воркер читает и подтверждает обработку.",
                                                "await redis.lpush('tasks', json.dumps(task))\ntask = await redis.brpop('tasks')",
                                                ["Согласованность данных"], ["Redis", "Celery"]),
    "300 Concepts/Dependency Injection.md": concept("2026-02-01", ["python", "архитектура"], "Объект не создаёт свои зависимости сам, а получает их снаружи.",
                                                   ["Упрощает тестирование — подменяем зависимости", "В FastAPI это Depends()", "Меньше связанность кода"],
                                                   "Фреймворк сам вызывает `get_db()` и передаёт результат в обработчик.",
                                                   "async def get_db():\n    async with SessionLocal() as s:\n        yield s",
                                                   ["Декораторы"], ["FastAPI"]),
    "300 Concepts/CI-CD.md": concept("2026-02-20", ["devops"], "Автоматическая проверка и выкладка кода: запушил в main → тесты → деплой.",
                                    ["CI — сборка и тесты на каждый коммит", "CD — автоматическая доставка на сервер", "GitHub Actions — самый простой старт"],
                                    "Пайплайн описывается в YAML и запускается на каждый push.",
                                    "# .github/workflows/deploy.yml\n# on: push -> jobs: test -> deploy",
                                    ["Docker"], ["Git", "Docker"]),
    # ------------------------------------------------------------ 400 Problems
    "400 Problems/N+1 запросы в SQLAlchemy.md": problem("2026-01-18", "решена", "Страница со списком пользователей делала 101 запрос к базе вместо двух.",
                                                        ["SQLAlchemy", "PostgreSQL"], ["Включил echo=True и посчитал запросы", "Попробовал joinedload"],
                                                        "Использовал `selectinload(User.notes)` — связанные заметки грузятся одним запросом.",
                                                        "Ленивые связи в цикле = N+1. Всегда смотри в логи SQL, когда страница тормозит.",
                                                        ["Индексы в базе данных"]),
    "400 Problems/Контейнер не видит базу данных.md": problem("2026-02-17", "решена", "Приложение в Docker падало с `connection refused` к PostgreSQL на localhost.",
                                                               ["Docker", "PostgreSQL"], ["Проверил, что база запущена", "Пробовал 127.0.0.1 вместо localhost"],
                                                               "Внутри compose надо обращаться к сервису по имени: `DATABASE_HOST=db`.",
                                                               "localhost в контейнере — это сам контейнер, а не хост.",
                                                               ["CI-CD"]),
    "400 Problems/RuntimeError Event loop is closed.md": problem("2026-04-12", "открыта", "После остановки бота в логах `RuntimeError: Event loop is closed` от aiohttp.",
                                                                  ["Python", "APScheduler"], ["Добавил `await session.close()`", "Перенёс shutdown планировщика раньше закрытия цикла"],
                                                                  "Пока частично: сессия закрывается в on_shutdown, но ошибка иногда остаётся.",
                                                                  "Ресурсы, привязанные к циклу, надо закрывать до того, как закроется сам [[Event Loop]].",
                                                                  ["Асинхронность в Python", "Event Loop"]),
    # ------------------------------------------------------------ 500 Resource
    "500 Resource/Книга — Грокаем алгоритмы.md": """---
tags: [ресурс, книга]
статус: прочитано
создано: 2025-11-15
---

## 📌 О чём
Алгоритмы с картинками: бинарный поиск, сортировки, графы, динамическое программирование.

## 💡 Что забрал себе
- Big O на пальцах
- Поиск в ширину пригодился для [[MOC - Python]]-задачек на собеседованиях
- Хеш-таблицы объясняют, почему [[Кеширование]] такое быстрое
""",
    "500 Resource/Курс по FastAPI.md": """---
tags: [ресурс, курс]
статус: в процессе
создано: 2026-01-02
---

## 📌 О чём
Практический курс: от первого эндпоинта до деплоя в Docker.

## 💡 Что забрал себе
- [[Dependency Injection]] через Depends
- Асинхронная работа с базой через [[SQLAlchemy]]
- Деплой: [[Docker]] + [[CI-CD]]
""",
    # ------------------------------------------------------------ 600 Personal
    "600 Personal/610 Idea/Трекер чтения книг.md": """---
tags: [идея]
статус: сырая
создано: 2026-06-03
---

## 💭 Мысль
Бот, который спрашивает «сколько страниц прочитал сегодня?» и строит график. Можно переиспользовать код [[Телеграм-бот трекер привычек]].

## 🔗 Связи
- [[MOC - Ideas]]
""",
    "600 Personal/630 Goals (цели)/Стать backend-разработчиком.md": """---
tags: [цель]
статус: в процессе
создано: 2025-10-01
срок: 2026-12-31
---

## 🎯 Цель
Найти первую работу backend-разработчиком на Python до конца 2026 года.

## 🪜 Шаги
- [x] Основы [[Python]]
- [x] [[FastAPI]] и [[REST]]
- [ ] Уверенно [[PostgreSQL]] и [[Индексы в базе данных]]
- [ ] Два пет-проекта в портфолио: [[Телеграм-бот трекер привычек]], [[API для заметок]]
- [ ] [[Docker]] и [[CI-CD]]
""",
    # ------------------------------------------------------------ 700 Project
    "700 Project/Телеграм-бот трекер привычек.md": """---
tags: [проект]
статус: в работе
создано: 2026-03-01
---

## 🎯 Цель проекта
Бот, который напоминает о привычках и считает серии дней подряд.

## 🧱 Стек
- [[Python]], aiogram
- [[APScheduler]] для напоминаний
- [[Redis]] для серий, [[PostgreSQL]] для истории

## ✅ Задачи
- [x] Команды /start и /habits
- [x] Напоминания по расписанию
- [ ] Починить [[RuntimeError Event loop is closed]]
- [ ] Деплой в [[Docker]]
""",
    "700 Project/API для заметок.md": """---
tags: [проект]
статус: идея
создано: 2026-05-20
---

## 🎯 Цель проекта
REST API для заметок с тегами и полнотекстовым поиском — для портфолио.

## 🧱 Стек
- [[FastAPI]], [[SQLAlchemy]], [[PostgreSQL]]
- Поиск: tsvector + [[Индексы в базе данных]]
- [[Кеширование]] популярных запросов в [[Redis]]
""",
    # ------------------------------------------------------------ 800 Daily
    "800 Daily/2026-09-20.md": """---
date: 2026-09-20
tags:
  - дейли
---

# 20 сентября 2026

## 🧠 Фокус дня
> Разобраться с [[N+1 запросы в SQLAlchemy]] в API

## 📝 Заметки дня
- selectinload спас: 101 запрос → 2
""",
    "800 Daily/2026-09-23.md": """---
date: 2026-09-23
tags:
  - дейли
---

# 23 сентября 2026

## 🧠 Фокус дня
> Докер для бота

## 📝 Заметки дня
- Опять [[Контейнер не видит базу данных]] — забыл, что хост называется db
""",
    "800 Daily/2026-09-25.md": """---
date: 2026-09-25
tags:
  - дейли
---

# 25 сентября 2026

## 📝 Заметки дня
- Прочитал про [[Согласованность данных]] и CAP. Надо разобраться с [[CRDT]].
""",
    # ------------------------------------------------------------ misc
    "Черновик без ссылок.md": "Список покупок: кофе, батарейки, блокнот.\n",
}

TEMPLATES = {
    "Concept Template": """<%*
/*
  КОГДА ИСПОЛЬЗОВАТЬ:
  Встретил незнакомый термин или концепт, который хочешь понять глубоко.
  Примеры: замыкание, event loop, SOLID. Концепт — это ИДЕЯ, технология — ИНСТРУМЕНТ.
*/
-%>
---
tags: [концепт]
создано: <% tp.date.now("YYYY-MM-DD") %>
---

# <% tp.file.title %>

## 🔍 Что это простыми словами
> Объясни как будто человек не знает ничего

## 📌 Ключевые моменты
-

## 🧩 Как это работает
> Чуть глубже — механика, детали

## 💻 Пример кода
```python

```

## 🔗 Связанные концепты
- [[]]

## 📦 Используется в технологиях
- [[]]
""",
    "Technology Template": """<%*
/*
  КОГДА ИСПОЛЬЗОВАТЬ:
  Начинаешь изучать инструмент, язык, фреймворк или библиотеку.
  Одна заметка на технологию — постоянно дополняешь.
*/
-%>
---
tags: [технология]
статус: изучаю
создано: <% tp.date.now("YYYY-MM-DD") %>
---

# <% tp.file.title %>

## 🎯 Зачем мне это
> Для какого проекта или задачи

## 🧠 Ключевые концепты
- [[]]

## 💻 Примеры кода
```python

```

## ⚡ Плюсы и минусы
| Плюсы | Минусы |
|-------|--------|
|       |        |

## 🗺️ MOC
- [[]]
""",
    "Problem Template": """<%*
/*
  КОГДА ИСПОЛЬЗОВАТЬ:
  Словил баг, непонятную ошибку, застрял на задаче. Создавай сразу, не жди решения.
  Самое ценное — раздел «Что понял в итоге».
*/
-%>
---
tags: [проблема]
статус: открыта
создано: <% tp.date.now("YYYY-MM-DD") %>
---

# <% tp.file.title %>

## ❓ В чём проблема
> Опиши максимально конкретно

## 🌍 Контекст
- Технология: [[]]

## 🔬 Что пробовал
- [ ]

## ✅ Решение
> Заполни, когда найдёшь

## 💡 Что понял в итоге
> Главный инсайт

## 🔗 Связанные концепты
- [[]]
""",
    "Daily Note Template": """---
date: <% tp.date.now("YYYY-MM-DD") %>
tags:
  - дейли
---
<%*
/*
  КОГДА ИСПОЛЬЗОВАТЬ:
  Каждый день: утром поставить фокус, вечером зафиксировать, что было.
*/
-%>
# <% tp.date.now("DD MMMM YYYY") %>

## 🧠 Фокус дня
> Одна главная вещь на сегодня

## 📝 Заметки дня
-

## 💡 Идеи и мысли
-
""",
    "Inbox Template": """<%*
/*
  КОГДА ИСПОЛЬЗОВАТЬ:
  Любая мысль на ходу — записать за 5 секунд. Раз в неделю разбираешь папку.
*/
-%>
---
tags: [инбокс]
статус: не разобрано
создано: <% tp.date.now("YYYY-MM-DD") %>
---

# <% tp.file.title %>

> Запиши быстро, не думай об оформлении.
""",
}

TEMPLATER = {
    "templates_folder": "/template",
    "trigger_on_file_creation": True,
    "enable_folder_templates": True,
    "folder_templates": [
        {"folder": "000 Inbox", "template": "template/Inbox Template.md"},
        {"folder": "200 Tech", "template": "template/Technology Template.md"},
        {"folder": "300 Concepts", "template": "template/Concept Template.md"},
        {"folder": "400 Problems", "template": "template/Problem Template.md"},
        {"folder": "800 Daily", "template": "template/Daily Note Template.md"},
    ],
}

README = """---
tags: [readme]
---

# Демо-хранилище Mnemo

Пример базы знаний начинающего бэкенд-разработчика: папки с номерами, шаблоны [Templater](https://github.com/SilentVoid13/Templater), MOC-хабы, заметки-проблемы и дневник.

| Папка | Что хранится | Шаблон |
|---|---|---|
| 000 Inbox | Всё, что записал на ходу | Inbox Template |
| 100 MOC | Навигационные хабы | — |
| 200 Tech | Инструменты и библиотеки | Technology Template |
| 300 Concepts | Идеи и принципы | Concept Template |
| 400 Problems | Баги и их решения | Problem Template |
| 500 Resource | Книги и курсы | — |
| 600 Personal | Идеи и цели | — |
| 700 Project | Пет-проекты | — |
| 800 Daily | Ежедневные заметки | Daily Note Template |

Начни с [[MOC - Python]] или [[MOC - Backend]].
"""


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    for rel, text in NOTES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (ROOT / "README.md").write_text(README, encoding="utf-8")
    for name, text in TEMPLATES.items():
        p = ROOT / "template" / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    plugin = ROOT / ".obsidian" / "plugins" / "templater-obsidian"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "data.json").write_text(json.dumps(TEMPLATER, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / ".obsidian" / "community-plugins.json").write_text('["templater-obsidian"]', encoding="utf-8")
    print(f"demo vault: {len(NOTES) + 1} notes, {len(TEMPLATES)} templates -> {ROOT}")


if __name__ == "__main__":
    main()
