# Legal Claim Pipeline MVP

FastAPI backend для внутреннего прототипа генерации JSON досудебной претензии по ЗоЗПП. LLM вызывается только через LiteLLM, история обращений и запусков pipeline сохраняется в PostgreSQL.

## Запуск

1. Скопируйте пример окружения:

```bash
cp .env.example .env
```

2. Заполните `.env`. Минимально нужны:

```env
APP_PORT=8000
POSTGRES_DB=legal_mvp
POSTGRES_USER=legal_mvp
POSTGRES_PASSWORD=legal_mvp
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://legal_mvp:legal_mvp@postgres:5432/legal_mvp
LITELLM_BASE_URL=http://your-litellm-host:4000
LITELLM_API_KEY=your-key
LITELLM_MAIN_MODEL=your-main-model
LITELLM_VALIDATOR_MODEL=your-validator-model
LITELLM_TIMEOUT_SECONDS=120
LITELLM_MAX_RETRIES=2
```

3. Запустите сервисы:

```bash
docker compose up --build
```

4. Создайте таблицы:

```bash
docker compose exec backend python -m app.db.init_db
```

FastAPI docs будут доступны по адресу `http://localhost:8000/docs`, если `APP_PORT=8000`.

## API

```bash
curl -X POST http://localhost:8000/api/v1/claims/analyze \
  -H "Content-Type: application/json" \
  -d '{"user_text":"Купил телефон, через неделю он сломался, магазин отказывается вернуть деньги"}'
```

Ответ всегда JSON:

```json
{
  "status": "claim_generated",
  "request_id": "uuid",
  "run_id": "uuid",
  "case_type": "consumer_goods",
  "summary": "...",
  "facts": {},
  "missing_fields": [],
  "clarifying_questions": [],
  "used_laws": [],
  "claim_json": {},
  "validation": {},
  "error": null
}
```

## База законов

Таблица `law_articles` уже создана для будущего заполнения статьями закона:

- `law_name`
- `article_number`
- `article_title`
- `article_text`
- `tags`
- `is_active`

Пример ручного заполнения:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO law_articles (id, law_name, article_number, article_title, article_text, tags, is_active)
VALUES (
  gen_random_uuid(),
  'Закон РФ "О защите прав потребителей"',
  '18',
  'Права потребителя при обнаружении в товаре недостатков',
  'Потребитель в случае обнаружения в товаре недостатков вправе потребовать замены товара, возврата уплаченной суммы и иных предусмотренных законом способов защиты.',
  '["недостаток товара", "возврат денег", "ремонт", "замена"]'::jsonb,
  true
);
```

Сейчас БД законов является заглушкой. Если `law_articles` пустая или поиск не нашел релевантные статьи, pipeline вернет:

```json
{
  "status": "error",
  "error": {
    "code": "LEGAL_CONTEXT_NOT_FOUND",
    "message": "Не найдены релевантные нормы права в БД. Заполните таблицу law_articles."
  }
}
```

## Проверка

```bash
uv run python -m compileall app
```

При поднятом PostgreSQL:

```bash
uv run python -m app.db.init_db
```
