# План реализации

Статус: согласовано, этапы 0–3 завершены, этап 4 не начат, версия 1.0
Основание: требования 1.2, архитектура 1.1

## 1. Принцип исполнения

Каждый пакет работ заканчивается работающим кодом, тестами и обновлённой
документацией. Агент не начинает Telegram UI до прохождения domain acceptance
tests и не добавляет новую инфраструктуру без ADR.

Основная команда проверки в конце каждого пакета:

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## 2. Этап 0 — каркас проекта

### WP-00. Python project

Статус: завершено 2026-08-14.

Результат:

- `pyproject.toml`, Python 3.14, `uv.lock`;
- src layout и пустые слои из architecture;
- Ruff, mypy, pytest/pytest-asyncio/Hypothesis;
- `.env.example`, `.gitignore`;
- CLI `python -m smoke_runner --help`;
- CI-команда или локальный `make check`/script без зависимости от Docker.

Приёмка:

- `uv sync --locked` работает на чистой машине;
- smoke test импортирует package;
- test suite запускается без bot token.

## 3. Этап 1 — доменное ядро

Этот этап не импортирует aiogram, SQLAlchemy или Matplotlib.

### WP-10. Timeline и interval policy

Статус: завершено 2026-08-14.

Реализовать:

- UTC value objects и injected `Clock` protocol;
- validation интервала 1–24 часа / 1–7 дней;
- timeline sessions + interval changes;
- кусочное правило target из требований 1.2;
- backfill/edit/delete recomputation;
- reaction class.

Обязательные acceptance examples:

```text
target 12:00, I=1h, session 11:50 -> next 12:50
target 12:00, I=1h, session 12:05 -> next 13:00
target 12:00, I=1h, session 12:14:59 -> next 13:00
target 12:00, I=1h, session 12:15 -> next 13:15
```

Hypothesis invariants:

- next target всегда позже basis session;
- earliness и lateness не могут быть положительными одновременно;
- несколько малых lateness не накапливают drift;
- вставка и последующее удаление backfill восстанавливают исходный timeline.

### WP-11. Метрики, wake cycles и streaks

Статус: завершено 2026-08-14.

Реализовать daily/weekly aggregates, partial first week, actual gaps, wake-to-first,
last session before next wake и серии дней без нарушений.

Приёмка:

- Sunday–Saturday period и Sunday 09:00 schedule проверены на timezone boundary;
- неоднозначное и несуществующее локальное время на DST-переходах обработано
  явно;
- zero-session day продолжает streak;
- missing data отличается от нуля;
- partial week не сравнивает totals с full week;
- backfill violation пересчитывает streak.

### WP-12. Feedback и report snapshots

Статус: завершено 2026-08-14.

Реализовать:

- классы реакции и ротацию шаблонов;
- immutable `DailyReportSnapshot`, `WeeklyReportSnapshot`;
- `ReportCommentaryInput` без Telegram identifiers;
- `DisabledReportCommentator`.

Приёмка: каждый текстовый факт выводится из typed snapshot, renderer ничего не
пересчитывает.

## 4. Этап 2 — persistence

### WP-20. SQLAlchemy models и Alembic

Статус: завершено 2026-08-14.

- Реализовать схему из [data-model.md](data-model.md).
- Добавить первую migration с constraints/indexes.
- Настроить async engine, one-slot pool и SQLite pragmas.
- Добавить repository protocols и adapters.

Приёмка:

- migration upgrade на пустой базе;
- повторный upgrade идемпотентен;
- foreign key и unique constraints доказаны тестами;
- два concurrent invite redemption не создают двух пользователей;
- каждая async task получает отдельный AsyncSession object.

### WP-21. Idempotent use cases

Статус: завершено 2026-08-14.

Реализовать транзакции log now/backfill/edit/delete/wake/change interval и
processed update guard.

Приёмка: повтор одного update ничего не меняет; ошибка в середине rollback-ит и
update marker, и предметную запись.

## 5. Этап 3 — доступ и Telegram MVP

### WP-30. Bootstrap и приглашения

Статус: завершено 2026-08-15.

- Pydantic settings;
- bootstrap admin;
- `/start <invite>` и ручной ввод;
- `/invite`, `/users`, `/revoke` для admin;
- private-chat и authorization middleware.

Security acceptance:

- invite code не хранится/не логируется открыто;
- одноразовость и expiry;
- отозванный пользователь не проходит middleware;
- callback чужой записи возвращает отказ без утечки данных.

### WP-31. Главный экран и логирование

Статус: завершено 2026-08-15.

- dashboard renderer;
- «Покурил сейчас» одним нажатием;
- «Проснулся сейчас»;
- поддерживающая реакция и undo;
- change interval hours/days.

Приёмка: основной happy path занимает одно нажатие и не ждёт подтверждения.

### WP-32. Backfill, история и редактирование

Статус: завершено 2026-08-15.

- FSM today/yesterday/date/time confirmation;
- последние 20, pagination и выбор даты;
- record detail, edit, delete;
- один editable screen message.

Приёмка: restart может сбросить незаконченную FSM-форму, но подтверждённые данные
и экран восстанавливаются.

## 6. Этап 4 — scheduler и уведомления

### WP-40. Durable scheduler loop

- nearest-due sleep + asyncio wake event;
- claim/recovery/heartbeat;
- milestone rows и supersede;
- 15-minute catch-up;
- graceful shutdown.

Приёмка fake-clock tests:

- ранняя session отменяет прежний target;
- session в малом lateness сохраняет сетку;
- stale milestone не отправляется;
- restart не дублирует claimed milestone.

### WP-41. Active dashboard refresh

- refresh раз в 5 минут;
- active window 30 минут;
- immediate event/target refresh;
- history screen не затирается scheduler-ом.

Приёмка: одно открытие создаёт не больше шести background edits.

## 7. Этап 5 — отчёты и графики

### WP-50. Daily report

- Sunday-independent daily boundary в user timezone;
- 09:00 delivery;
- сравнение с предыдущим днём;
- шаблонное поддерживающее резюме;
- ручной rebuild/resend.

### WP-51. Weekly report

- Sunday–Saturday boundary;
- first partial week;
- core и дополнительные метрики;
- streaks и сравнение;
- две chart specs.

### WP-52. Matplotlib renderer

- Agg backend;
- PNG в memory buffer;
- читаемость на ширине телефона;
- отдельные axes для разных единиц;
- визуальная маркировка missing/partial data;
- выполнение вне event loop.

Приёмка включает golden/snapshot images для zero, missing, partial и long-history
fixtures плюс ручной просмотр на телефоне.

### WP-53. Report delivery recovery

- unique deliveries;
- immutable snapshot и отдельные text/chart delivery parts;
- bounded retry;
- startup catch-up;
- хранение message ids;
- documented crash-window tests.

## 8. Этап 6 — эксплуатация

### WP-60. Backup/restore

- online SQLite backup command;
- retention 30 дней;
- integrity check;
- restore runbook и автоматизированный restore smoke test.

### WP-61. Native и container deploy

- native `uv run` инструкции;
- multi-stage Dockerfile;
- non-root/read-only rootfs;
- `/data` volume;
- graceful signals и one-replica guard;
- пример `systemd` как fallback.

### WP-62. Observability

- JSON logging/redaction;
- scheduler heartbeat;
- healthcheck CLI;
- operator runbook: restart, resend report, revoke user, backup, restore.

## 9. Этап 7 — пилот

1. Создать отдельного test bot.
2. Пройти acceptance checklist двумя Telegram accounts.
3. Восстановить production-like DB из backup.
4. Запустить семидневный личный пилот.
5. Зафиксировать UX-проблемы отдельным документом, не менять формулы молча.
6. После пилота пометить MVP release candidate.

## 10. Возможное распараллеливание агентов

После WP-00:

- агент A: WP-10 timeline;
- агент B: WP-11 metrics/streaks;
- агент C: WP-20 schema/repositories.

Точки синхронизации:

1. typed domain models и repository protocols согласуются до параллельной работы;
2. WP-10 и WP-11 merge до WP-21;
3. WP-20 merge до Telegram/application integration;
4. один агент отвечает за финальную сквозную приёмку, чтобы не размыть владение.

Telegram routers лучше делить по feature (`auth`, `logging`, `history`,
`settings`, `reports`), а не по техническим слоям.

## 11. Трассировка требований

| Требование | Основной пакет |
|---|---|
| FR-01 | WP-21, WP-31 |
| FR-02 | WP-21, WP-32 |
| FR-03 | WP-11, WP-21, WP-31, WP-32 |
| FR-04 | WP-10, WP-21, WP-31 |
| FR-05 | WP-10, WP-31, WP-41 |
| FR-06 | WP-10, WP-21 |
| FR-07 | WP-21, WP-32 |
| FR-08 | WP-10, WP-11, WP-21 |
| FR-09 | WP-50, WP-53 |
| FR-10 | WP-51, WP-52, WP-53 |
| FR-11 | WP-50, WP-51, WP-53 |
| FR-12 | WP-30 |
| FR-13 | WP-21 |
| FR-14 | WP-40 |
| FR-15 | WP-12, WP-31 |
| FR-16 | WP-20, WP-21, WP-30 |
| FR-17 | WP-30 |
| FR-18 | WP-41 |
| FR-19 | WP-12 |
| FR-20 | WP-11, WP-50, WP-51 |
| FR-21 | WP-11, WP-51, WP-52 |
| FR-22 | WP-11, WP-51, WP-52 |
| FR-23 | WP-10, WP-40 |

## 12. Definition of Done MVP

- Все FR-01–FR-23 имеют проходящий acceptance test.
- `ruff`, `mypy`, unit/integration/acceptance tests зелёные.
- Нет TODO, меняющих продуктовые формулы.
- Два пользователя изолированы тестом и ручной проверкой.
- Scheduler/reports переживают restart-сценарии.
- Backup восстановлен в отдельную базу.
- Docker и native run используют один lockfile.
- Runbook позволяет владельцу самостоятельно обновить и восстановить бота.
