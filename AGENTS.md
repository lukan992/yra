# AGENTS.md

## Non-negotiable rule

Do not edit any files inside `prompts/` unless the user explicitly says:
"разрешаю редактировать prompts".

You may read prompt files, but you must not modify, reformat, replace, delete, or rename them.

If implementation requires prompt changes, describe the needed changes in the final answer instead of editing files.

## Project context

This is a FastAPI MVP for a legal pretrial-claim generation pipeline.

The backend may be changed freely, including:
- `app/services/`
- `app/api/`
- `app/db/`
- `app/repositories/`
- `app/schemas/`
- Docker and config files

Protected:
- `prompts/`