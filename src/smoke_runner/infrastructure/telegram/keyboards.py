# ruff: noqa: RUF001
"""Inline keyboards and compact callback-data conventions."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from smoke_runner.infrastructure.db.gateway import HistoryItem


def dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💨 Покурил сейчас", callback_data="log:session")],
            [InlineKeyboardButton(text="☀️ Проснулся сейчас", callback_data="log:wake")],
            [
                InlineKeyboardButton(text="🕘 Другое время", callback_data="backfill"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="dashboard"),
            ],
            [
                InlineKeyboardButton(text="📚 История", callback_data="history:0"),
                InlineKeyboardButton(text="📊 Отчёты", callback_data="reports"),
            ],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        ]
    )


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вчерашний отчёт", callback_data="report:daily")],
            [InlineKeyboardButton(text="Последняя неделя", callback_data="report:weekly")],
            [InlineKeyboardButton(text="← На главную", callback_data="dashboard")],
        ]
    )


def backfill_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💨 Сессия", callback_data="bfkind:session"),
                InlineKeyboardButton(text="☀️ Пробуждение", callback_data="bfkind:wake"),
            ],
            [InlineKeyboardButton(text="← На главную", callback_data="dashboard")],
        ]
    )


def backfill_date_keyboard(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data=f"bfdate:{kind}:today"),
                InlineKeyboardButton(text="Вчера", callback_data=f"bfdate:{kind}:yesterday"),
            ],
            [InlineKeyboardButton(text="Другая дата", callback_data=f"bfdate:{kind}:custom")],
            [InlineKeyboardButton(text="← Назад", callback_data="backfill")],
        ]
    )


def confirm_event_keyboard(kind: str, timestamp: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data=f"bfok:{kind}:{timestamp}")],
            [InlineKeyboardButton(text="Отмена", callback_data="dashboard")],
        ]
    )


def ambiguous_time_keyboard(kind: str, first: int, second: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Первый вариант", callback_data=f"bfok:{kind}:{first}")],
            [InlineKeyboardButton(text="Второй вариант", callback_data=f"bfok:{kind}:{second}")],
            [InlineKeyboardButton(text="Отмена", callback_data="dashboard")],
        ]
    )


def history_keyboard(items: tuple[HistoryItem, ...], offset: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        kind = item.kind.value
        record_id = item.id
        label = "💨" if kind == "session" else "☀️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{label} Запись #{record_id}", callback_data=f"record:{kind}:{record_id}"
                )
            ]
        )
    navigation: list[InlineKeyboardButton] = []
    if offset:
        navigation.append(
            InlineKeyboardButton(text="← Новее", callback_data=f"history:{max(0, offset - 20)}")
        )
    if len(items) == 20:
        navigation.append(
            InlineKeyboardButton(text="Старее →", callback_data=f"history:{offset + 20}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="По дате", callback_data="history-date")])
    rows.append([InlineKeyboardButton(text="← На главную", callback_data="dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def record_keyboard(kind: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить время", callback_data=f"edit:{kind}:{record_id}"
                )
            ],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{kind}:{record_id}")],
            [InlineKeyboardButton(text="← К истории", callback_data="history:0")],
        ]
    )


def delete_confirm_keyboard(kind: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"delete-ok:{kind}:{record_id}"
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data=f"record:{kind}:{record_id}")],
        ]
    )


def interval_units_keyboard(notifications_enabled: bool) -> InlineKeyboardMarkup:
    status = "вкл" if notifications_enabled else "выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="В часах", callback_data="interval-unit:hour"),
                InlineKeyboardButton(text="В днях", callback_data="interval-unit:day"),
            ],
            [
                InlineKeyboardButton(
                    text=f"Уведомление о рубеже: {status}", callback_data="notify-toggle"
                )
            ],
            [InlineKeyboardButton(text="← На главную", callback_data="dashboard")],
        ]
    )


def interval_values_keyboard(unit: str) -> InlineKeyboardMarkup:
    maximum = 24 if unit == "hour" else 7
    rows: list[list[InlineKeyboardButton]] = []
    for start in range(1, maximum + 1, 4):
        rows.append(
            [
                InlineKeyboardButton(text=str(value), callback_data=f"interval:{unit}:{value}")
                for value in range(start, min(start + 4, maximum + 1))
            ]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def feedback_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Отменить запись", callback_data=f"undo:{record_id}")],
            [InlineKeyboardButton(text="На главную", callback_data="dashboard")],
        ]
    )
