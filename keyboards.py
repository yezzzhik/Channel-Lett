from datetime import date, timedelta
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
)
import db

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚨 Мне тревожно!")],
        [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="📈 Статистика")],
        [KeyboardButton(text="📚 Справочник"), KeyboardButton(text="🎯 Мои цели")],
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


def quick_date_kb():
    today = date.today()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data=f"qd_{today}"),
         InlineKeyboardButton(text="Вчера", callback_data=f"qd_{today - timedelta(days=1)}")],
        [InlineKeyboardButton(text="🗓 Другая дата", callback_data="qd_custom")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_log")],
    ])


def intensity_kb():
    # тап вместо ввода числа — быстрее в момент тревоги
    rows = []
    row = []
    for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
        row.append(InlineKeyboardButton(text=str(v), callback_data=f"int_{v}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


QUICK_TRIGGER_TAGS = [
    "Работа/учёба", "Здоровье/тело", "Отношения", "Соц. ситуация",
    "Транспорт/толпа", "Финансы", "Мысли/воспоминания", "Не знаю",
]


def trigger_kb():
    rows = [[InlineKeyboardButton(text=t, callback_data=f"trig_{i}")]
            for i, t in enumerate(QUICK_TRIGGER_TAGS)]
    rows.append([InlineKeyboardButton(text="✏️ Написать свой", callback_data="trig_custom")])
    rows.append([InlineKeyboardButton(text="⏭ Пропустить", callback_data="trig_skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_kb(step: str, with_help=True):
    row = []
    if with_help:
        row.append(InlineKeyboardButton(text="📖 Подсказка", callback_data=f"help_{step}"))
    row.append(InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip_{step}"))
    return InlineKeyboardMarkup(inline_keyboard=[row, [InlineKeyboardButton(text="❌ Стоп", callback_data="cancel_log")]])


def yes_no_kb(prefix: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_yes"),
         InlineKeyboardButton(text="❌ Нет", callback_data=f"{prefix}_no")]
    ])


def distortions_kb(selected_ids: list):
    rows = []
    for did, name in db.list_distortions():
        mark = "☑️" if did in selected_ids else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"dist_{did}")])
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="dist_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_quick_save_kb(entry_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Разобрать сейчас (5 мин)", callback_data=f"analyze_{entry_id}")],
        [InlineKeyboardButton(text="Позже, из списка записей", callback_data="dismiss")],
    ])


def entries_list_kb(rows):
    kb = []
    for entry_id, date_, time_, intensity, analyzed in rows:
        mark = "✅" if analyzed else "▫️"
        kb.append([InlineKeyboardButton(
            text=f"{mark} {date_} {time_} · тревога {intensity}",
            callback_data=f"open_{entry_id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def entry_actions_kb(entry_id: int, analyzed: bool):
    label = "🔁 Дополнить разбор" if analyzed else "🔎 Разобрать"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"analyze_{entry_id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="back_to_list")],
    ])
