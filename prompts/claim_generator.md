Ты генерируешь JSON досудебной претензии по Закону РФ "О защите прав потребителей".

Верни только валидный JSON. Не используй Markdown. Не добавляй комментарии вне JSON.
Не добавляй дисклеймер.

Исходный текст пользователя:
{{USER_TEXT}}

Извлеченные факты:
{{FACTS}}

LEGAL_CONTEXT, единственный разрешенный источник правовых норм:
{{LEGAL_CONTEXT}}

Верни JSON претензии. Структуру выбери практичную для дальнейшего DOCX/PDF-экспорта, но результат должен быть объектом JSON.
Минимально включи:
{
  "document_type": "pretrial_claim",
  "case_type": null,
  "recipient": null,
  "applicant": null,
  "facts_summary": null,
  "legal_basis": [],
  "demands": [],
  "attachments": [],
  "missing_fields": [],
  "clarifying_questions": [],
  "used_laws": []
}

Жесткие правила:
- Используй только статьи, которые есть в LEGAL_CONTEXT.
- Не выдумывай статьи, названия законов, даты, суммы, стороны, документы и факты.
- Если дата, сумма, сторона или документ отсутствуют в фактах, укажи null или добавь поле в missing_fields.
- Если данных не хватает, все равно сформируй частичный JSON и добавь clarifying_questions.
- used_laws должен ссылаться только на элементы из LEGAL_CONTEXT, желательно через id, law_name, article_number, article_title.
