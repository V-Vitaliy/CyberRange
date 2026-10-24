# Backend tests

- `unit/` — настройки и очередь запросов.
- `api/` — HTTP/WebSocket-маршруты, загрузка файлов и жизненный цикл приложения.
- `services/` — доступ к данным, переключаемые защиты, RAG, SOC и email-инструменты.
- `test_admin_setup.py` — подготовка БД на заглушках; также запускается отдельно через стандартный `unittest` без backend-зависимостей.

Тесты используют заглушки внешних сервисов: PostgreSQL, Redis, Chroma, LLM и SMTP
запускать не нужно. Они не подтверждают работу приложения с реальными сервисами.
SQLi-проверка помечена `xfail`, пока отсутствует метод поиска в репозитории.

Из корня проекта, в активированном виртуальном окружении:

```bash
python -m pip install -r backend/requirements-test.txt
PYTHONPATH=backend python -m pytest -q -ra backend/tests
```

Отдельная группа запускается тем же способом:

```bash
PYTHONPATH=backend python -m pytest -q -ra backend/tests/services
```

CI использует рекурсивный запуск `backend/tests` и подхватывает все группы.
Frontend-тесты остаются в `frontend/tests`; запуск из корня:

```bash
node --test frontend/tests/*.test.mjs
```
