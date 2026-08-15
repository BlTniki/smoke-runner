# Архитектурные решения

Статус: согласовано, версия 1.2

## ADR-001: модульный монолит в одном процессе

**Решение:** aiogram polling, scheduler и application services запускаются одним
Python-процессом.

**Причина:** личный VPS, небольшой круг пользователей и SQLite не оправдывают
network services. Один процесс упрощает транзакции, deploy и backup.

**Следствие:** допускается ровно один replica. Переход к нескольким экземплярам
потребует PostgreSQL и внешней координации jobs.

## ADR-002: aiogram 3 и asyncio

**Решение:** использовать aiogram 3.x, routers/middleware и long polling.

**Причина:** нативная async-модель соответствует Telegram I/O и позволяет в том
же event loop держать scheduler. Framework предоставляет FSM и inline keyboards.

**Следствие:** доменная логика не должна зависеть от aiogram types; handlers
только переводят Telegram input в application commands.

## ADR-003: SQLAlchemy async поверх SQLite без WAL

**Решение:** стабильный SQLAlchemy 2.0 + aiosqlite, pool из одного connection, rollback
journal `DELETE`, `synchronous=FULL`.

**Причина:** типизированная схема и транзакции полезны уже в MVP; один connection
соответствует single-process модели. Ветка 2.1 на старте реализации доступна
только как prerelease, поэтому в основу не берётся. WAL не нужен при текущей
нагрузке.

**Следствие:** все DB-транзакции короткие, Telegram и Matplotlib выполняются
после их завершения. При реальной нехватке concurrency база мигрирует на
PostgreSQL.

## ADR-004: DB-driven scheduler вместо APScheduler

**Решение:** один собственный scheduler loop читает due state из предметных
таблиц SQLite.

**Причина:** target меняется после ранней/поздней/backfill session. Отдельное job
store дублировало бы состояние и создавало гонки.

**Следствие:** scheduler обязан иметь recovery, claim states, heartbeat и fake
clock tests.

## ADR-005: метрики рассчитываются, а не сохраняются

**Решение:** sessions, wakes и interval changes — факты; violations, targets,
streaks и report aggregates строятся pure functions.

**Причина:** задняя запись может изменить всю последующую сетку. Сохранённые
производные значения потребовали бы сложной инвалидации.

**Следствие:** MVP использует O(N) timeline одного пользователя. Кэш возможен
позже как полностью пересоздаваемая проекция.

## ADR-006: разные приоритеты доставки

**Решение:** milestone предпочитает at-most-once, report — ограниченный retry с
приоритетом доставки, dashboard edit можно повторять.

**Причина:** SQLite и Telegram Bot API не образуют общей транзакции. Двойная
похвала хуже редкого пропуска; пропущенный отчёт хуже редкого дубля.

**Следствие:** crash-window явно документируется и тестируется. Exactly-once не
обещается как математическая гарантия.

## ADR-007: uv и Python 3.14

**Решение:** native-first разработка через `uv`, production из того же lockfile;
runtime Python 3.14.

**Причина:** Docker запрещён на рабочем ноутбуке, но воспроизводимость нужна.
`uv` создаёт обычный `.venv`, фиксирует transitive dependencies и работает в
Docker build.

**Следствие:** `uv.lock` обязателен в репозитории; CI/deploy используют
`--locked`/`--frozen`, а обновления зависимостей выполняются отдельным PR.

## ADR-008: AI как необязательный port

**Решение:** MVP содержит только `ReportCommentator` protocol и disabled adapter.

**Причина:** агент не должен влиять на расчёты и доступность отчётов.

**Следствие:** будущий provider подключается отдельным adapter с opt-in, timeout
и fallback без изменения domain/application слоёв.

## ADR-009: первый администратор через терминальный bootstrap-код

**Решение:** Telegram ID администратора необязателен. Если admin ещё не создан,
процесс печатает один короткоживущий код, хранит только его HMAC digest и
атомарно привязывает первого отправителя к единственной роли `admin`.

**Причина:** Telegram Bot API не раскрывает ID владельца до первого сообщения
боту. Предварительное требование ID делает первый запуск искусственно сложным.

**Следствие:** в БД существует singleton bootstrap-code slot и unique index на
роль admin. Перезапуск до привязки ротирует код; после привязки bootstrap
автоматически закрывается. Для unattended deploy остаётся optional ID setting.
