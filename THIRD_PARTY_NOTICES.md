# Сторонние компоненты

Mnemo распространяется под лицензией MIT (см. [LICENSE](LICENSE)). В `frontend/vendor/` (и в копии для демо в `docs/demo/vendor/`) лежат неизменённые сборки следующих библиотек, каждая под своей лицензией:

| Библиотека | Версия | Лицензия | Исходники |
|---|---|---|---|
| Chart.js | 4.4.1 | MIT | https://github.com/chartjs/Chart.js |
| force-graph | 1.43.5 | MIT | https://github.com/vasturiano/force-graph |
| marked | 12.0.2 | MIT | https://github.com/markedjs/marked |
| DOMPurify | 3.1.6 | Apache-2.0 или MPL-2.0 (на выбор) | https://github.com/cure53/DOMPurify |

Python-зависимости (`requirements.txt`) ставятся через pip и в репозиторий не входят: FastAPI (MIT), Uvicorn (BSD-3-Clause), HTTPX (BSD-3-Clause), PyYAML (MIT), NumPy (BSD-3-Clause).

«Obsidian» — товарный знак Dynalist Inc. Mnemo — независимый проект, он не связан с Obsidian и не одобрен его разработчиками.
