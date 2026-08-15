# ruff: noqa: RUF001
"""Stage-three Telegram router: auth, tracking, history and settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from smoke_runner.application.models import (
    ChangeIntervalCommand,
    DeleteEventCommand,
    EditEventCommand,
    EventSource,
    LogEventCommand,
)
from smoke_runner.application.security import (
    AdminBootstrapService,
    AuthenticatedUser,
    InviteService,
)
from smoke_runner.application.tracking import (
    EventOutsideTrackedPeriodError,
    RecordNotFoundError,
    TrackingError,
    TrackingService,
    WakeAlreadyExistsError,
)
from smoke_runner.domain.clock import Clock, UtcInstant
from smoke_runner.domain.feedback import RandomTemplateIndexChooser, choose_feedback_template
from smoke_runner.domain.local_time import (
    AmbiguousLocalTimeError,
    NonexistentLocalTimeError,
    local_day_period,
    resolve_local_datetime,
)
from smoke_runner.domain.models import TargetInterval
from smoke_runner.domain.timeline import build_timeline
from smoke_runner.infrastructure.db.gateway import DatabaseGateway, HistoryKind
from smoke_runner.infrastructure.telegram.keyboards import (
    ambiguous_time_keyboard,
    backfill_date_keyboard,
    backfill_kind_keyboard,
    confirm_event_keyboard,
    dashboard_keyboard,
    delete_confirm_keyboard,
    feedback_keyboard,
    history_keyboard,
    interval_units_keyboard,
    interval_values_keyboard,
    record_keyboard,
)
from smoke_runner.infrastructure.telegram.presenters import (
    format_interval,
    format_local,
    render_dashboard,
    render_history,
    render_record,
    render_session_feedback,
)
from smoke_runner.infrastructure.telegram.screens import ScreenManager


@dataclass(frozen=True, slots=True)
class BotServices:
    gateway: DatabaseGateway
    admin_bootstrap_service: AdminBootstrapService
    invite_service: InviteService
    tracking: TrackingService
    screens: ScreenManager
    clock: Clock


class Authenticated(Filter):
    def __init__(self, expected: bool = True) -> None:
        self._expected = expected

    async def __call__(
        self,
        event: TelegramObject,
        auth_user: AuthenticatedUser | None = None,
    ) -> bool:
        del event
        return (auth_user is not None) is self._expected


class BackfillFlow(StatesGroup):
    date = State()
    time = State()


class HistoryFlow(StatesGroup):
    date = State()


class EditFlow(StatesGroup):
    datetime = State()


def build_router(services: BotServices) -> Router:
    router = Router(name="smoke-runner")

    @router.message(CommandStart())
    async def start(
        message: Message,
        command: CommandObject,
        auth_user: AuthenticatedUser | None,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        await state.clear()
        user = auth_user
        if user is None and command.args:
            if message.from_user is None:
                return
            user = await _redeem_access_code(
                services,
                command.args,
                telegram_user_id=message.from_user.id,
                telegram_private_chat_id=message.chat.id,
            )
        if user is None:
            await message.answer(
                "Для доступа нужен одноразовый код приглашения. "
                "Отправь его сюда обычным сообщением."
            )
            return
        await _show_dashboard(services, bot, user, message.chat.id)

    @router.message(Command("invite"), Authenticated())
    async def create_invite(
        message: Message,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        if not auth_user.is_admin:
            await message.answer("Эта команда доступна только администратору.")
            return
        code = await services.invite_service.create(auth_user)
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=message.chat.id,
            text=(
                "Одноразовое приглашение создано. Оно действует ограниченное время:\n\n"
                f"{code}\n\n"
                "Можно отправить код или ссылку вида /start <код>. После первого входа код сгорит."
            ),
            reply_markup=dashboard_keyboard(),
            screen_kind="settings",
        )

    @router.message(Command("users"), Authenticated())
    async def list_users(
        message: Message,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        if not auth_user.is_admin:
            await message.answer("Эта команда доступна только администратору.")
            return
        users = await services.gateway.list_users(auth_user.id)
        lines = ["Пользователи", ""]
        lines.extend(
            f"#{user.id} · Telegram {user.telegram_user_id} · {user.role} · {user.status}"
            for user in users
        )
        lines.append("")
        lines.append("Отозвать доступ: /revoke <внутренний номер>")
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=message.chat.id,
            text="\n".join(lines),
            reply_markup=dashboard_keyboard(),
            screen_kind="settings",
        )

    @router.message(Command("revoke"), Authenticated())
    async def revoke_user(
        message: Message,
        command: CommandObject,
        auth_user: AuthenticatedUser,
    ) -> None:
        if not auth_user.is_admin:
            await message.answer("Эта команда доступна только администратору.")
            return
        try:
            target_id = int(command.args or "")
        except ValueError:
            await message.answer("Формат: /revoke <внутренний номер из /users>")
            return
        revoked = await services.gateway.revoke_user(
            admin_user_id=auth_user.id,
            target_user_id=target_id,
            now=services.clock.now(),
        )
        await message.answer("Доступ отозван." if revoked else "Активный участник не найден.")

    @router.message(Authenticated(False), F.text)
    async def redeem_manual(message: Message, bot: Bot, state: FSMContext) -> None:
        await state.clear()
        if message.from_user is None:
            return
        user = await _redeem_access_code(
            services,
            message.text or "",
            telegram_user_id=message.from_user.id,
            telegram_private_chat_id=message.chat.id,
        )
        if user is None:
            await message.answer("Код неверный, уже использован или просрочен.")
            return
        await _show_dashboard(services, bot, user, message.chat.id)

    @router.callback_query(Authenticated(False))
    async def reject_unauthorized_callback(callback: CallbackQuery) -> None:
        await callback.answer(
            "Доступ неактивен. Отправь /start и код приглашения.", show_alert=True
        )

    @router.callback_query(Authenticated(), F.data == "dashboard")
    async def dashboard(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        await callback.answer()
        await state.clear()
        await _show_dashboard(services, bot, auth_user, _callback_chat_id(callback))

    @router.callback_query(Authenticated(), F.data.startswith("log:"))
    async def log_now(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        event_update: Update,
        bot: Bot,
    ) -> None:
        await callback.answer()
        now = services.clock.now()
        kind = (callback.data or "").split(":", 1)[1]
        try:
            if kind == "session":
                result = await services.tracking.log_session(
                    LogEventCommand(
                        user_id=auth_user.id,
                        telegram_update_id=event_update.update_id,
                        occurred_at=now,
                        source=EventSource.NOW,
                    )
                )
                if result.record_id is not None:
                    await _show_feedback(
                        services,
                        bot,
                        auth_user,
                        _callback_chat_id(callback),
                        result.record_id,
                    )
                else:
                    await _show_dashboard(services, bot, auth_user, _callback_chat_id(callback))
            else:
                await services.tracking.log_wake(
                    LogEventCommand(
                        user_id=auth_user.id,
                        telegram_update_id=event_update.update_id,
                        occurred_at=now,
                        source=EventSource.NOW,
                    )
                )
                await _show_dashboard(
                    services,
                    bot,
                    auth_user,
                    _callback_chat_id(callback),
                    prefix="Отметил пробуждение. Хорошее начало — дальше просто наблюдаем.\n\n",
                )
        except WakeAlreadyExistsError:
            await services.screens.show(
                bot=bot,
                user=auth_user,
                chat_id=_callback_chat_id(callback),
                text="Сегодня пробуждение уже записано. Заменить его текущим временем?",
                reply_markup=confirm_event_keyboard("replacewake-now", now.to_unix_seconds()),
                screen_kind="record",
            )

    @router.callback_query(Authenticated(), F.data == "backfill")
    async def backfill(callback: CallbackQuery, auth_user: AuthenticatedUser, bot: Bot) -> None:
        await callback.answer()
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="Что добавить другим временем?",
            reply_markup=backfill_kind_keyboard(),
            screen_kind="record",
        )

    @router.callback_query(Authenticated(), F.data.startswith("bfkind:"))
    async def choose_backfill_kind(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        kind = (callback.data or "").split(":")[1]
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="За какой день?",
            reply_markup=backfill_date_keyboard(kind),
            screen_kind="record",
        )

    @router.callback_query(Authenticated(), F.data.startswith("bfdate:"))
    async def choose_backfill_date(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        await callback.answer()
        _, kind, choice = (callback.data or "").split(":")
        timezone = ZoneInfo(auth_user.timezone_name)
        today = services.clock.now().value.astimezone(timezone).date()
        if choice == "custom":
            await state.set_state(BackfillFlow.date)
            await state.set_data({"kind": kind})
            prompt = "Введи дату в формате ДД.ММ.ГГГГ."
        else:
            selected = today if choice == "today" else today - timedelta(days=1)
            await state.set_state(BackfillFlow.time)
            await state.set_data({"kind": kind, "date": selected.isoformat()})
            prompt = f"Дата {selected:%d.%m.%Y}. Теперь введи время ЧЧ:ММ."
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text=prompt,
            reply_markup=dashboard_keyboard(),
            screen_kind="record",
        )

    @router.message(Authenticated(), BackfillFlow.date, F.text)
    async def receive_backfill_date(
        message: Message,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        try:
            selected = datetime.strptime(message.text or "", "%d.%m.%Y").date()
        except ValueError:
            await message.answer("Не получилось прочитать дату. Нужен формат ДД.ММ.ГГГГ.")
            return
        data = await state.get_data()
        await state.set_state(BackfillFlow.time)
        await state.set_data({"kind": data["kind"], "date": selected.isoformat()})
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=message.chat.id,
            text=f"Дата {selected:%d.%m.%Y}. Теперь введи время ЧЧ:ММ.",
            reply_markup=dashboard_keyboard(),
            screen_kind="record",
        )

    @router.message(Authenticated(), BackfillFlow.time, F.text)
    async def receive_backfill_time(
        message: Message,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        try:
            selected_time = datetime.strptime(message.text or "", "%H:%M").time()
        except ValueError:
            await message.answer("Не получилось прочитать время. Нужен формат ЧЧ:ММ.")
            return
        data = await state.get_data()
        local_value = datetime.combine(date.fromisoformat(str(data["date"])), selected_time)
        await state.clear()
        await _confirm_local_event(
            services,
            bot,
            auth_user,
            message.chat.id,
            str(data["kind"]),
            local_value,
        )

    @router.callback_query(Authenticated(), F.data.startswith("bfok:"))
    async def save_backfill(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        event_update: Update,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        await callback.answer()
        await state.clear()
        _, kind, raw_timestamp = (callback.data or "").split(":")
        occurred_at = UtcInstant.from_unix_seconds(int(raw_timestamp))
        command = LogEventCommand(
            user_id=auth_user.id,
            telegram_update_id=event_update.update_id,
            occurred_at=occurred_at,
            source=(EventSource.NOW if kind == "replacewake-now" else EventSource.BACKFILL),
        )
        try:
            if kind in {"wake", "replacewake", "replacewake-now"}:
                await services.tracking.log_wake(
                    command,
                    replace_existing=kind in {"replacewake", "replacewake-now"},
                )
                await _show_dashboard(
                    services,
                    bot,
                    auth_user,
                    _callback_chat_id(callback),
                    prefix="Пробуждение сохранено.\n\n",
                )
            else:
                result = await services.tracking.log_session(command)
                if result.record_id is not None:
                    await _show_feedback(
                        services,
                        bot,
                        auth_user,
                        _callback_chat_id(callback),
                        result.record_id,
                    )
        except WakeAlreadyExistsError:
            await services.screens.show(
                bot=bot,
                user=auth_user,
                chat_id=_callback_chat_id(callback),
                text="На эту дату уже есть пробуждение. Заменить его выбранным временем?",
                reply_markup=confirm_event_keyboard("replacewake", occurred_at.to_unix_seconds()),
                screen_kind="record",
            )
        except EventOutsideTrackedPeriodError as error:
            await _show_error(services, bot, auth_user, _callback_chat_id(callback), str(error))

    @router.callback_query(Authenticated(), F.data.startswith("history:"))
    async def history(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        offset = int((callback.data or "").split(":")[1])
        await _show_history(services, bot, auth_user, _callback_chat_id(callback), offset=offset)

    @router.callback_query(Authenticated(), F.data == "history-date")
    async def history_date(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        await callback.answer()
        await state.set_state(HistoryFlow.date)
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="Введи дату истории в формате ДД.ММ.ГГГГ.",
            reply_markup=dashboard_keyboard(),
            screen_kind="history",
        )

    @router.message(Authenticated(), HistoryFlow.date, F.text)
    async def receive_history_date(
        message: Message,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        try:
            selected = datetime.strptime(message.text or "", "%d.%m.%Y").date()
        except ValueError:
            await message.answer("Не получилось прочитать дату. Нужен формат ДД.ММ.ГГГГ.")
            return
        await state.clear()
        period = local_day_period(selected, ZoneInfo(auth_user.timezone_name))
        await _show_history(
            services,
            bot,
            auth_user,
            message.chat.id,
            offset=0,
            start_at=period.start,
            end_at=period.end,
        )

    @router.callback_query(Authenticated(), F.data.startswith("record:"))
    async def record(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        _, raw_kind, raw_id = (callback.data or "").split(":")
        await _show_record(
            services,
            bot,
            auth_user,
            _callback_chat_id(callback),
            HistoryKind(raw_kind),
            int(raw_id),
        )

    @router.callback_query(Authenticated(), F.data.startswith("edit:"))
    async def edit_record(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        await callback.answer()
        _, kind, raw_id = (callback.data or "").split(":")
        item = await services.gateway.get_history_item(
            user_id=auth_user.id, kind=HistoryKind(kind), record_id=int(raw_id)
        )
        if item is None:
            await _show_error(
                services, bot, auth_user, _callback_chat_id(callback), "Запись не найдена."
            )
            return
        await state.set_state(EditFlow.datetime)
        await state.set_data({"kind": kind, "record_id": int(raw_id)})
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="Введи новое фактическое время: ДД.ММ.ГГГГ ЧЧ:ММ.",
            reply_markup=record_keyboard(kind, int(raw_id)),
            screen_kind="record",
        )

    @router.message(Authenticated(), EditFlow.datetime, F.text)
    async def receive_edit_datetime(
        message: Message,
        auth_user: AuthenticatedUser,
        event_update: Update,
        bot: Bot,
        state: FSMContext,
    ) -> None:
        try:
            local_value = datetime.strptime(message.text or "", "%d.%m.%Y %H:%M")
            occurred_at = resolve_local_datetime(local_value, ZoneInfo(auth_user.timezone_name))
        except ValueError as error:
            await message.answer(f"Не удалось использовать время: {error}")
            return
        data = await state.get_data()
        await state.clear()
        command = EditEventCommand(
            user_id=auth_user.id,
            telegram_update_id=event_update.update_id,
            record_id=int(data["record_id"]),
            occurred_at=occurred_at,
        )
        try:
            if data["kind"] == "session":
                await services.tracking.edit_session(command)
            else:
                await services.tracking.edit_wake(command)
            await _show_record(
                services,
                bot,
                auth_user,
                message.chat.id,
                HistoryKind(str(data["kind"])),
                int(data["record_id"]),
            )
        except TrackingError as error:
            await _show_error(services, bot, auth_user, message.chat.id, str(error))

    @router.callback_query(Authenticated(), F.data.startswith("delete:"))
    async def delete_prompt(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        _, kind, raw_id = (callback.data or "").split(":")
        item = await services.gateway.get_history_item(
            user_id=auth_user.id, kind=HistoryKind(kind), record_id=int(raw_id)
        )
        if item is None:
            await _show_error(
                services, bot, auth_user, _callback_chat_id(callback), "Запись не найдена."
            )
            return
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="Удалить эту запись? Расчёты после неё будут пересчитаны.",
            reply_markup=delete_confirm_keyboard(kind, int(raw_id)),
            screen_kind="record",
        )

    @router.callback_query(Authenticated(), F.data.startswith("delete-ok:"))
    async def delete_record(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        event_update: Update,
        bot: Bot,
    ) -> None:
        await callback.answer()
        _, kind, raw_id = (callback.data or "").split(":")
        command = DeleteEventCommand(
            user_id=auth_user.id,
            telegram_update_id=event_update.update_id,
            record_id=int(raw_id),
        )
        try:
            if kind == "session":
                await services.tracking.delete_session(command)
            else:
                await services.tracking.delete_wake(command)
            await _show_history(services, bot, auth_user, _callback_chat_id(callback), offset=0)
        except RecordNotFoundError:
            await _show_error(
                services, bot, auth_user, _callback_chat_id(callback), "Запись уже удалена."
            )

    @router.callback_query(Authenticated(), F.data.startswith("undo:"))
    async def undo_session(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        event_update: Update,
        bot: Bot,
    ) -> None:
        await callback.answer()
        record_id = int((callback.data or "").split(":")[1])
        try:
            await services.tracking.delete_session(
                DeleteEventCommand(
                    user_id=auth_user.id,
                    telegram_update_id=event_update.update_id,
                    record_id=record_id,
                )
            )
            await _show_dashboard(
                services,
                bot,
                auth_user,
                _callback_chat_id(callback),
                prefix="Запись отменена.\n\n",
            )
        except RecordNotFoundError:
            await _show_error(
                services, bot, auth_user, _callback_chat_id(callback), "Запись уже удалена."
            )

    @router.callback_query(Authenticated(), F.data == "settings")
    async def settings(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        await _show_settings(services, bot, auth_user, _callback_chat_id(callback))

    @router.callback_query(Authenticated(), F.data.startswith("interval-unit:"))
    async def interval_unit(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        unit = (callback.data or "").split(":")[1]
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="Выбери целое число. Часы: 1–24, дни: 1–7.",
            reply_markup=interval_values_keyboard(unit),
            screen_kind="settings",
        )

    @router.callback_query(Authenticated(), F.data.startswith("interval:"))
    async def set_interval(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        event_update: Update,
        bot: Bot,
    ) -> None:
        await callback.answer()
        _, unit, raw_count = (callback.data or "").split(":")
        count = int(raw_count)
        interval = TargetInterval.hours(count) if unit == "hour" else TargetInterval.days(count)
        await services.tracking.change_interval(
            ChangeIntervalCommand(
                user_id=auth_user.id,
                telegram_update_id=event_update.update_id,
                interval=interval,
            )
        )
        await _show_dashboard(
            services,
            bot,
            auth_user,
            _callback_chat_id(callback),
            prefix=f"Новый интервал: {format_interval(interval)}.\n\n",
        )

    @router.callback_query(Authenticated(), F.data == "notify-toggle")
    async def toggle_notifications(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        facts = await services.gateway.dashboard_facts(auth_user.id)
        if facts is None:
            return
        await services.gateway.set_milestone_notifications(
            auth_user.id, not facts.user.milestone_notifications_enabled
        )
        await _show_settings(services, bot, auth_user, _callback_chat_id(callback))

    @router.callback_query(Authenticated(), F.data == "reports")
    async def reports_placeholder(
        callback: CallbackQuery,
        auth_user: AuthenticatedUser,
        bot: Bot,
    ) -> None:
        await callback.answer()
        await services.screens.show(
            bot=bot,
            user=auth_user,
            chat_id=_callback_chat_id(callback),
            text="Отчёты появятся на следующем этапе. Уже сохранённые данные в них попадут.",
            reply_markup=dashboard_keyboard(),
            screen_kind="report",
        )

    return router


async def _redeem_access_code(
    services: BotServices,
    plaintext_code: str,
    *,
    telegram_user_id: int,
    telegram_private_chat_id: int,
) -> AuthenticatedUser | None:
    admin = await services.admin_bootstrap_service.redeem(
        plaintext_code=plaintext_code,
        telegram_user_id=telegram_user_id,
        telegram_private_chat_id=telegram_private_chat_id,
    )
    if admin is not None:
        return admin
    return await services.invite_service.redeem(
        plaintext_code=plaintext_code,
        telegram_user_id=telegram_user_id,
        telegram_private_chat_id=telegram_private_chat_id,
    )


async def _show_dashboard(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
    *,
    prefix: str = "",
) -> None:
    facts = await services.gateway.dashboard_facts(user.id)
    if facts is None:
        return
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=prefix + render_dashboard(facts, services.clock.now()),
        reply_markup=dashboard_keyboard(),
        screen_kind="dashboard",
    )


async def _show_feedback(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
    record_id: int,
) -> None:
    facts = await services.gateway.dashboard_facts(user.id)
    if facts is None:
        return
    timeline = build_timeline(list(facts.sessions), list(facts.intervals))
    assessment = next(item for item in timeline.sessions if item.session.id == record_id)
    template = choose_feedback_template(
        assessment.reaction,
        previous_template_key=facts.last_feedback_template_key,
        chooser=RandomTemplateIndexChooser(),
    )
    await services.gateway.set_feedback_template_key(user.id, template.key)
    timezone = ZoneInfo(user.timezone_name)
    text = (
        f"Сессия сохранена: {format_local(assessment.session.occurred_at, timezone)}\n\n"
        + render_session_feedback(template, assessment, timezone)
    )
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=text,
        reply_markup=feedback_keyboard(record_id),
        screen_kind="dashboard",
    )


async def _confirm_local_event(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
    kind: str,
    local_value: datetime,
) -> None:
    timezone = ZoneInfo(user.timezone_name)
    try:
        instant = resolve_local_datetime(local_value, timezone)
    except AmbiguousLocalTimeError as error:
        await services.screens.show(
            bot=bot,
            user=user,
            chat_id=chat_id,
            text="Это местное время встречается дважды из-за перевода часов. Выбери вариант.",
            reply_markup=ambiguous_time_keyboard(
                kind, error.first.to_unix_seconds(), error.second.to_unix_seconds()
            ),
            screen_kind="record",
        )
        return
    except NonexistentLocalTimeError:
        await _show_error(
            services,
            bot,
            user,
            chat_id,
            "Такого местного времени не было из-за перевода часов. Выбери другое.",
        )
        return
    label = "сессию" if kind == "session" else "пробуждение"
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=f"Сохранить {label}: {local_value:%d.%m.%Y %H:%M}?",
        reply_markup=confirm_event_keyboard(kind, instant.to_unix_seconds()),
        screen_kind="record",
    )


async def _show_history(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
    *,
    offset: int,
    start_at: UtcInstant | None = None,
    end_at: UtcInstant | None = None,
) -> None:
    items = await services.gateway.history(
        user_id=user.id,
        offset=offset,
        limit=20,
        start_at=start_at,
        end_at=end_at,
    )
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=render_history(items, ZoneInfo(user.timezone_name), offset),
        reply_markup=history_keyboard(items, offset),
        screen_kind="history",
    )


async def _show_record(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
    kind: HistoryKind,
    record_id: int,
) -> None:
    item = await services.gateway.get_history_item(user_id=user.id, kind=kind, record_id=record_id)
    if item is None:
        await _show_error(services, bot, user, chat_id, "Запись не найдена.")
        return
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=render_record(item, ZoneInfo(user.timezone_name)),
        reply_markup=record_keyboard(kind.value, record_id),
        screen_kind="record",
    )


async def _show_settings(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
) -> None:
    facts = await services.gateway.dashboard_facts(user.id)
    if facts is None:
        return
    timeline = build_timeline(list(facts.sessions), list(facts.intervals))
    current = format_interval(timeline.active_interval) if timeline.active_interval else "не задан"
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=(
            "Настройки\n\n"
            f"Часовой пояс: {user.timezone_name}\n"
            f"Текущий интервал: {current}\n"
            "Выбери единицу нового интервала."
        ),
        reply_markup=interval_units_keyboard(facts.user.milestone_notifications_enabled),
        screen_kind="settings",
    )


async def _show_error(
    services: BotServices,
    bot: Bot,
    user: AuthenticatedUser,
    chat_id: int,
    text: str,
) -> None:
    await services.screens.show(
        bot=bot,
        user=user,
        chat_id=chat_id,
        text=text,
        reply_markup=dashboard_keyboard(),
        screen_kind="dashboard",
    )


def _callback_chat_id(callback: CallbackQuery) -> int:
    if callback.message is None:
        raise RuntimeError("Callback has no message")
    return callback.message.chat.id
