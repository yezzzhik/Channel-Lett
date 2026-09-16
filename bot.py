import asyncio
import io
from datetime import datetime, date

import config
import db
import keyboards as kb
from knowledge import KNOWLEDGE, HELP_TEXTS

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, BufferedInputFile

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class QuickLog(StatesGroup):
    date = State()
    intensity = State()
    trigger = State()


class Analyze(StatesGroup):
    physical = State()
    cognitive = State()
    thoughts_of_danger = State()
    catastrophe = State()
    distortions = State()
    coping = State()
    avoidance = State()
    base_fear = State()
    overest_prob = State()
    overest_sev = State()
    helplessness = State()
    desired_coping = State()
    worry_productive = State()


class Goals(StatesGroup):
    behavioral = State()
    cognitive = State()
    emotional = State()


ANALYZE_STEPS = [
    ("physical", "Опишите **физические симптомы** и их интенсивность (0-100).\nПример: сердцебиение — 80, дрожь — 40.", Analyze.cognitive),
    ("cognitive", "Опишите **когнитивные симптомы**: что происходило в сознании?", Analyze.thoughts_of_danger),
    ("thoughts_of_danger", "Какие **мысли об опасности** возникли?", Analyze.catastrophe),
    ("catastrophe", "Какая **воображаемая катастрофа** — худший вариант развития?", "distortions"),
    ("coping", "Какие **стратегии совладания** вы использовали?", Analyze.avoidance),
    ("avoidance", "Каких ситуаций/людей/действий вы **избегаете**?", Analyze.base_fear),
    ("base_fear", "Какой **базовый страх** стоит за этой тревогой?", Analyze.overest_prob),
    ("overest_prob", "Насколько вы **переоцениваете вероятность** негативного исхода?", Analyze.overest_sev),
    ("overest_sev", "Насколько вы **переоцениваете серьёзность** последствий?", Analyze.helplessness),
    ("helplessness", "Какие у вас **мысли о беспомощности**?", Analyze.desired_coping),
    ("desired_coping", "Какой была бы ваша **желаемая реакция совладания**?", "worry_productive"),
]
STEP_INDEX = {name: i for i, (name, _, _) in enumerate(ANALYZE_STEPS)}


async def _goto_analyze_step(message: Message, state: FSMContext, step_name: str):
    if step_name == "distortions":
        await state.set_state(Analyze.distortions)
        await state.update_data(distortions_selected=[])
        await message.answer(
            "Отметьте **ошибки мышления**, которые узнаёте (можно несколько):",
            reply_markup=kb.distortions_kb([]),
        )
        return
    if step_name == "worry_productive":
        await state.set_state(Analyze.worry_productive)
        await message.answer(
            "Было ли это беспокойство **продуктивным**? (помогло найти решение?)",
            reply_markup=kb.yes_no_kb("worry"),
        )
        return
    idx = STEP_INDEX[step_name]
    _, text, _ = ANALYZE_STEPS[idx]
    await state.set_state(getattr(Analyze, step_name))
    await message.answer(text, reply_markup=kb.skip_kb(step_name))


async def _finish_analysis(message: Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    entry_id = data["entry_id"]
    details = {k: v for k, v in data.items() if k not in ("entry_id", "distortions_selected")}
    db.update_entry_details(user_id, entry_id, details, mark_analyzed=True)
    if data.get("distortions_selected"):
        db.set_entry_distortions(entry_id, data["distortions_selected"])
    await message.answer(
        "✅ Разбор сохранён. Возвращаться к нему и дополнять можно в любой момент из «📋 Мои записи».",
        reply_markup=kb.MAIN_MENU,
    )
    await state.clear()


# ==================== СТАРТ ====================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.ensure_user(message.from_user.id)
    await message.answer(
        "👋 Дневник тревоги (КПТ, по Кларку и Беку).\n\n"
        "🚨 «Мне тревожно!» — фиксация занимает 15–30 секунд, специально коротко: "
        "в моменте тревоги не нужно ничего анализировать, только зафиксировать.\n"
        "Разбор (мысли, искажения, катастрофизация) вы делаете потом, в спокойном "
        "состоянии — из списка записей.\n\n"
        "Данные хранятся зашифрованными.",
        reply_markup=kb.MAIN_MENU,
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb.MAIN_MENU)


@dp.callback_query(F.data == "cancel_log")
async def cb_cancel_log(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Запись отменена.")
    await callback.message.answer("Главное меню:", reply_markup=kb.MAIN_MENU)


# ==================== БЫСТРАЯ ЗАПИСЬ ====================

@dp.message(F.text == "🚨 Мне тревожно!")
async def start_quick_log(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(QuickLog.date)
    await message.answer("Когда это было?", reply_markup=kb.quick_date_kb())


@dp.callback_query(StateFilter(QuickLog.date), F.data.startswith("qd_"))
async def quick_log_date(callback: CallbackQuery, state: FSMContext):
    val = callback.data[3:]
    if val == "custom":
        await callback.message.edit_text("Напишите дату в формате ДД.ММ.ГГГГ:")
        return
    await state.update_data(date=val, time=datetime.now().strftime("%H:%M"))
    await state.set_state(QuickLog.intensity)
    await callback.message.edit_text(f"📅 {val}")
    await callback.message.answer("Уровень тревоги (0–100):", reply_markup=kb.intensity_kb())


@dp.message(StateFilter(QuickLog.date))
async def quick_log_date_custom(message: Message, state: FSMContext):
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Не понял дату. Формат: ДД.ММ.ГГГГ")
        return
    await state.update_data(date=d.strftime("%Y-%m-%d"), time=datetime.now().strftime("%H:%M"))
    await state.set_state(QuickLog.intensity)
    await message.answer("Уровень тревоги (0–100):", reply_markup=kb.intensity_kb())


@dp.callback_query(StateFilter(QuickLog.intensity), F.data.startswith("int_"))
async def quick_log_intensity(callback: CallbackQuery, state: FSMContext):
    val = int(callback.data.split("_")[1])
    await state.update_data(intensity=val)
    await state.set_state(QuickLog.trigger)
    await callback.message.edit_text(f"Тревога: {val}/100")
    await callback.message.answer("Что послужило триггером?", reply_markup=kb.trigger_kb())


@dp.message(StateFilter(QuickLog.intensity))
async def quick_log_intensity_text(message: Message, state: FSMContext):
    if not message.text.isdigit() or not (0 <= int(message.text) <= 100):
        await message.answer("Число от 0 до 100, или воспользуйтесь кнопками выше.")
        return
    await state.update_data(intensity=int(message.text))
    await state.set_state(QuickLog.trigger)
    await message.answer("Что послужило триггером?", reply_markup=kb.trigger_kb())


async def _save_quick_entry(message_or_callback, state: FSMContext, user_id: int, trigger_text: str):
    data = await state.get_data()
    entry_id = db.create_entry(
        user_id, data["date"], data["time"], data["intensity"],
        details={"trigger": trigger_text} if trigger_text else None,
    )
    text = (
        f"✅ Записано.\n📅 {data['date']} {data['time']}  ·  Тревога: {data['intensity']}"
        + (f"\n💭 {trigger_text}" if trigger_text else "")
    )
    target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
    await target.answer(text, reply_markup=kb.after_quick_save_kb(entry_id))
    await state.clear()


@dp.callback_query(StateFilter(QuickLog.trigger), F.data.startswith("trig_"))
async def quick_log_trigger_tag(callback: CallbackQuery, state: FSMContext):
    val = callback.data[5:]
    if val == "custom":
        await callback.message.edit_text("Опишите триггер в одном сообщении:")
        return
    if val == "skip":
        await _save_quick_entry(callback, state, callback.from_user.id, "")
        return
    tag = kb.QUICK_TRIGGER_TAGS[int(val)]
    await _save_quick_entry(callback, state, callback.from_user.id, tag)


@dp.message(StateFilter(QuickLog.trigger))
async def quick_log_trigger_text(message: Message, state: FSMContext):
    await _save_quick_entry(message, state, message.from_user.id, message.text)


@dp.callback_query(F.data == "dismiss")
async def dismiss(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)


# ==================== РАЗБОР (углублённый) ====================

@dp.callback_query(F.data.startswith("analyze_"))
async def start_analysis(callback: CallbackQuery, state: FSMContext):
    entry_id = int(callback.data.split("_")[1])
    await state.clear()
    await state.update_data(entry_id=entry_id)
    await callback.message.answer(
        "Разбор эпизода — не обязателен весь сразу, каждый шаг можно пропустить "
        "и вернуться позже.",
    )
    await _goto_analyze_step(callback.message, state, "physical")


@dp.message(StateFilter(Analyze.physical))
async def a_physical(message: Message, state: FSMContext):
    await state.update_data(physical_symptoms=message.text)
    await _goto_analyze_step(message, state, "cognitive")

@dp.callback_query(StateFilter(Analyze.physical), F.data == "skip_physical")
async def a_physical_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "cognitive")


@dp.message(StateFilter(Analyze.cognitive))
async def a_cognitive(message: Message, state: FSMContext):
    await state.update_data(cognitive_symptoms=message.text)
    await _goto_analyze_step(message, state, "thoughts_of_danger")

@dp.callback_query(StateFilter(Analyze.cognitive), F.data == "skip_cognitive")
async def a_cognitive_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "thoughts_of_danger")


@dp.message(StateFilter(Analyze.thoughts_of_danger))
async def a_thoughts(message: Message, state: FSMContext):
    await state.update_data(thoughts_of_danger=message.text)
    await _goto_analyze_step(message, state, "catastrophe")

@dp.callback_query(StateFilter(Analyze.thoughts_of_danger), F.data == "skip_thoughts_of_danger")
async def a_thoughts_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "catastrophe")


@dp.message(StateFilter(Analyze.catastrophe))
async def a_catastrophe(message: Message, state: FSMContext):
    await state.update_data(imagined_catastrophe=message.text)
    await _goto_analyze_step(message, state, "distortions")

@dp.callback_query(StateFilter(Analyze.catastrophe), F.data == "skip_catastrophe")
async def a_catastrophe_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "distortions")


@dp.callback_query(StateFilter(Analyze.distortions), F.data.startswith("dist_"))
async def a_distortion_toggle(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("distortions_selected", [])
    did = int(callback.data.split("_")[1])
    if did in selected:
        selected.remove(did)
    else:
        selected.append(did)
    await state.update_data(distortions_selected=selected)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=kb.distortions_kb(selected))

@dp.callback_query(StateFilter(Analyze.distortions), F.data == "dist_done")
async def a_distortions_done(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "coping")


@dp.message(StateFilter(Analyze.coping))
async def a_coping(message: Message, state: FSMContext):
    await state.update_data(coping_strategies=message.text)
    await _goto_analyze_step(message, state, "avoidance")

@dp.callback_query(StateFilter(Analyze.coping), F.data == "skip_coping")
async def a_coping_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "avoidance")


@dp.message(StateFilter(Analyze.avoidance))
async def a_avoidance(message: Message, state: FSMContext):
    await state.update_data(avoidance=message.text)
    await _goto_analyze_step(message, state, "base_fear")

@dp.callback_query(StateFilter(Analyze.avoidance), F.data == "skip_avoidance")
async def a_avoidance_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "base_fear")


@dp.message(StateFilter(Analyze.base_fear))
async def a_base_fear(message: Message, state: FSMContext):
    await state.update_data(base_fear_record=message.text)
    await _goto_analyze_step(message, state, "overest_prob")

@dp.callback_query(StateFilter(Analyze.base_fear), F.data == "skip_base_fear")
async def a_base_fear_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "overest_prob")


@dp.message(StateFilter(Analyze.overest_prob))
async def a_overest_prob(message: Message, state: FSMContext):
    await state.update_data(overestimation_prob=message.text)
    await _goto_analyze_step(message, state, "overest_sev")

@dp.callback_query(StateFilter(Analyze.overest_prob), F.data == "skip_overest_prob")
async def a_overest_prob_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "overest_sev")


@dp.message(StateFilter(Analyze.overest_sev))
async def a_overest_sev(message: Message, state: FSMContext):
    await state.update_data(overestimation_sev=message.text)
    await _goto_analyze_step(message, state, "helplessness")

@dp.callback_query(StateFilter(Analyze.overest_sev), F.data == "skip_overest_sev")
async def a_overest_sev_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "helplessness")


@dp.message(StateFilter(Analyze.helplessness))
async def a_helplessness(message: Message, state: FSMContext):
    await state.update_data(helplessness_thoughts=message.text)
    await _goto_analyze_step(message, state, "desired_coping")

@dp.callback_query(StateFilter(Analyze.helplessness), F.data == "skip_helplessness")
async def a_helplessness_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "desired_coping")


@dp.message(StateFilter(Analyze.desired_coping))
async def a_desired_coping(message: Message, state: FSMContext):
    await state.update_data(desired_coping=message.text)
    await _goto_analyze_step(message, state, "worry_productive")

@dp.callback_query(StateFilter(Analyze.desired_coping), F.data == "skip_desired_coping")
async def a_desired_coping_skip(callback: CallbackQuery, state: FSMContext):
    await _goto_analyze_step(callback.message, state, "worry_productive")


@dp.callback_query(StateFilter(Analyze.worry_productive), F.data.startswith("worry_"))
async def a_worry_productive(callback: CallbackQuery, state: FSMContext):
    await state.update_data(worry_productive=(callback.data == "worry_yes"))
    await _finish_analysis(callback.message, state, callback.from_user.id)


# generic help popup for any step
@dp.callback_query(F.data.startswith("help_"))
async def show_help(callback: CallbackQuery):
    topic = callback.data.split("_", 1)[1]
    await callback.answer(HELP_TEXTS.get(topic, "Подсказки нет."), show_alert=True)


# ==================== ЗАПИСИ ====================

@dp.message(F.text == "📋 Мои записи")
async def list_entries(message: Message):
    rows = db.get_entries(message.from_user.id, limit=15)
    if not rows:
        await message.answer("Записей пока нет.")
        return
    await message.answer(
        "▫️ — только зафиксировано, ✅ — разобрано. Нажмите, чтобы открыть:",
        reply_markup=kb.entries_list_kb(rows),
    )


@dp.callback_query(F.data == "back_to_list")
async def back_to_list(callback: CallbackQuery):
    rows = db.get_entries(callback.from_user.id, limit=15)
    await callback.message.edit_text(
        "▫️ — только зафиксировано, ✅ — разобрано. Нажмите, чтобы открыть:",
        reply_markup=kb.entries_list_kb(rows),
    )


@dp.callback_query(F.data.startswith("open_"))
async def open_entry(callback: CallbackQuery):
    entry_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    entry = db.get_entry(user_id, entry_id)
    if not entry:
        await callback.answer("Не найдено.")
        return
    _, d, t, intensity, analyzed = entry
    details = db.get_entry_details(user_id, entry_id)
    distortions = db.get_entry_distortions(entry_id)

    lines = [f"📅 {d} {t}  ·  Тревога: {intensity}"]
    labels = {
        "trigger": "💭 Триггер", "physical_symptoms": "🫀 Физические симптомы",
        "cognitive_symptoms": "🧠 Когнитивные симптомы", "thoughts_of_danger": "⚠️ Мысли об опасности",
        "imagined_catastrophe": "💥 Катастрофа", "coping_strategies": "🛠 Совладание",
        "avoidance": "🚫 Избегание", "base_fear_record": "😨 Базовый страх",
        "overestimation_prob": "📈 Переоценка вероятности", "overestimation_sev": "📈 Переоценка серьёзности",
        "helplessness_thoughts": "🤷 Беспомощность", "desired_coping": "🌟 Желаемое совладание",
    }
    for key, label in labels.items():
        if details.get(key):
            lines.append(f"{label}: {details[key]}")
    if distortions:
        lines.append(f"🔍 Искажения: {', '.join(distortions)}")
    if "worry_productive" in details:
        lines.append(f"🤔 Продуктивно: {'да' if details['worry_productive'] else 'нет'}")

    await callback.message.edit_text("\n".join(lines), reply_markup=kb.entry_actions_kb(entry_id, bool(analyzed)))


# ==================== СТАТИСТИКА / ЭКСПОРТ ====================

@dp.message(F.text == "📈 Статистика")
async def stats(message: Message):
    rows = db.get_entries(message.from_user.id, limit=1000)
    if not rows:
        await message.answer("Пока не на чем строить статистику.")
        return
    avg = sum(r[3] for r in rows) / len(rows)
    await message.answer(
        f"Всего записей: {len(rows)}\nСредняя интенсивность: {avg:.0f}\n\n"
        "Полная выгрузка: /export"
    )


@dp.message(Command("export"))
async def export_excel(message: Message):
    user_id = message.from_user.id
    data = db.get_all_entries_for_export(user_id)
    if not data:
        await message.answer("Нет записей для экспорта.")
        return
    try:
        import pandas as pd
    except ImportError:
        await message.answer("Для экспорта нужны pandas и openpyxl (pip install pandas openpyxl).")
        return
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    await message.answer_document(
        BufferedInputFile(buf.read(), filename=f"anxiety_{user_id}_{datetime.now():%Y%m%d_%H%M}.xlsx"),
        caption="📊 Расшифрованная выгрузка — храните файл так же аккуратно, как пароль.",
    )


# ==================== ЦЕЛИ ====================

@dp.message(F.text == "🎯 Мои цели")
async def goals_menu(message: Message):
    g = db.get_goals(message.from_user.id)
    text = (
        "🎯 **Ваши цели**\n\n"
        f"Поведенческие: {g.get('behavioral') or '—'}\n"
        f"Когнитивные: {g.get('cognitive') or '—'}\n"
        f"Эмоциональные: {g.get('emotional') or '—'}\n\n"
        "/edit_goals — изменить"
    )
    await message.answer(text)


@dp.message(Command("edit_goals"))
async def edit_goals_start(message: Message, state: FSMContext):
    await state.set_state(Goals.behavioral)
    await message.answer("Поведенческие цели (какое поведение усилить/ослабить?):")

@dp.message(StateFilter(Goals.behavioral))
async def goals_behavioral(message: Message, state: FSMContext):
    await state.update_data(behavioral=message.text)
    await state.set_state(Goals.cognitive)
    await message.answer("Когнитивные цели (какие мысли/убеждения изменить?):")

@dp.message(StateFilter(Goals.cognitive))
async def goals_cognitive(message: Message, state: FSMContext):
    await state.update_data(cognitive=message.text)
    await state.set_state(Goals.emotional)
    await message.answer("Эмоциональные цели (какие чувства усилить/ослабить?):")

@dp.message(StateFilter(Goals.emotional))
async def goals_emotional(message: Message, state: FSMContext):
    data = await state.get_data()
    data["emotional"] = message.text
    db.update_goals(message.from_user.id, data)
    await state.clear()
    await message.answer("✅ Цели сохранены.", reply_markup=kb.MAIN_MENU)


# ==================== НАСТРОЙКИ ====================

@dp.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message):
    s = db.get_user_settings(message.from_user.id)
    status = "включены" if s.get("hints_enabled", True) else "выключены"
    await message.answer(f"Подсказки сейчас: {status}.\n/toggle_hints — переключить")

@dp.message(Command("toggle_hints"))
async def toggle_hints(message: Message):
    s = db.get_user_settings(message.from_user.id)
    s["hints_enabled"] = not s.get("hints_enabled", True)
    db.update_user_settings(message.from_user.id, s)
    await message.answer(f"Подсказки {'включены' if s['hints_enabled'] else 'выключены'}.")


# ==================== СПРАВОЧНИК ====================

@dp.message(F.text == "📚 Справочник")
async def reference_root(message: Message):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = [[InlineKeyboardButton(text="Что такое КПТ?", callback_data="ref_intro")]]
    for key, val in KNOWLEDGE.items():
        if key == "intro":
            continue
        rows.append([InlineKeyboardButton(text=val["title"], callback_data=f"ref_{key}")])
    await message.answer("Выберите раздел:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("ref_"))
async def reference_nav(callback: CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    key = callback.data[4:]
    if key == "intro":
        await callback.message.edit_text(KNOWLEDGE["intro"]["content"])
        return
    node = KNOWLEDGE.get(key)
    if not node:
        await callback.answer("Раздела пока нет — допишете после конспектирования.")
        return
    if "sections" in node:
        rows = [[InlineKeyboardButton(text=v["title"], callback_data=f"refsec_{key}_{k}")]
                for k, v in node["sections"].items()]
        await callback.message.edit_text(f"📚 {node['title']}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await callback.message.edit_text(f"**{node['title']}**\n\n{node['content']}")


@dp.callback_query(F.data.startswith("refsec_"))
async def reference_section(callback: CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    _, ch_key, sec_key = callback.data.split("_", 2)
    section = KNOWLEDGE[ch_key]["sections"][sec_key]
    text = f"**{section['title']}**\n\n{section['content']}"
    kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"ref_{ch_key}")]])
    await callback.message.edit_text(text, reply_markup=kb_back)


# ==================== ПОМОЩЬ ====================

@dp.message(F.text == "❓ Помощь")
async def help_cmd(message: Message):
    await message.answer(
        "🚨 Мне тревожно! — быстрая фиксация (15–30 сек)\n"
        "📋 Мои записи — список + разбор эпизода (когда есть время/силы)\n"
        "📈 Статистика — сводка, /export — выгрузка в Excel\n"
        "📚 Справочник — теория и рабочие листы книги\n"
        "🎯 Мои цели — /edit_goals для редактирования\n"
        "⚙️ Настройки — /toggle_hints\n\n"
        "/cancel — прервать любой опрос."
    )


async def main():
    db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
