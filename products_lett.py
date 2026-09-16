import asyncio
import io
import logging
import re

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command

import config
import sources
import storage
from allergens import ALLERGENS, check_allergens, extract_property
from vkusvill import get_client

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

# Пользователи, которым мы задали вопрос «напиши название товара».
awaiting_name: set[int] = set()

# Пользователи, которых мы попросили прислать состав текстом: user_id -> штрихкод.
awaiting_composition: dict[int, str] = {}

WELCOME = (
    "Привет! Я — твой помощник по составу продуктов. 🫶\n\n"
    "Помогу быстро проверить, есть ли в еде то, что нельзя твоему ребёнку. "
    "Пришлёшь штрихкод — я найду состав и скажу, безопасно или нет.\n\n"
    "Спокойно, без паники и мелкого шрифта на этикетках."
)

ALLERGENS_CHOOSE = (
    "Отметь, что нельзя ребёнку. Нажимай на кнопки — можно выбрать сколько "
    "угодно, повторное нажатие убирает.\n\n"
    "Когда закончишь — жми «Готово»."
)

ALLERGENS_NOT_SET = (
    "Сначала отметь, что ребёнку нельзя — иначе я не пойму, что искать в составе."
)

SEARCHING = "Секундочку, ищу состав…"

NOT_FOUND = (
    "Не нашла этот штрихкод. Бывает — база не всезнайка.\n\n"
    "Напиши, как называется товар, или пришли состав текстом — начиная со слова "
    "«состав:». Я разберусь."
)

BARCODE_TOO_SHORT = (
    "Эти цифры коротковаты для штрихкода. Проверь, не пропустила ли пару "
    "символов, и пришли ещё раз."
)

PHOTO_NOT_READ = (
    "Не разобрала фото. Сними, пожалуйста, ближе и чётче, чтобы штрихкод "
    "попал целиком. 📸"
)

QR_NOT_BARCODE = (
    "Это QR-код, а не штрихкод. Мне нужен тот, что из полосочек — обычно он "
    "на обороте или сбоку упаковки."
)

NAME_NOT_FOUND = (
    "Не нашла по названию. Попробуй пришли состав текстом, начиная со слова "
    "«состав:» — тогда проверю точно."
)

NOT_ABOUT_FOOD = (
    "Я умею проверять состав продуктов — на остальное, увы, не гожусь. "
    "Пришли штрихкод или название товара, и разберёмся с составом."
)

# Аллергены, которые можно отметить кнопками (берём из allergens.ALLERGENS,
# чтобы кнопки всегда совпадали с тем, что умеет распознавать проверка).
ALLERGEN_CHOICES = list(ALLERGENS.keys())


def allergens_keyboard(selected: list[str]) -> types.InlineKeyboardMarkup:
    """Клавиатура выбора аллергенов (2 кнопки в ряд) + «Готово»."""
    rows = []
    for i in range(0, len(ALLERGEN_CHOICES), 2):
        pair = ALLERGEN_CHOICES[i:i + 2]
        row = []
        for name in pair:
            mark = "✅" if name in selected else "⬜"
            row.append(types.InlineKeyboardButton(
                text=f"{mark} {name}", callback_data=f"allergen:{name}"
            ))
        rows.append(row)
    rows.append([types.InlineKeyboardButton(text="Готово", callback_data="allergens:done")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def choose_button() -> types.InlineKeyboardMarkup:
    """Одна кнопка «Выбрать аллергены» — минимум шума."""
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="Выбрать аллергены", callback_data="allergens:open")
    ]])


# Лёгкая беседа на тему еды (ответы по ключевым словам).
FOOD_FAQ = [
    (("глютен", "клейковин", "пшениц"), "Глютен — это белок пшеницы, ржи и "
     "ячменя. Он есть в хлебе, макаронах, печенье, многих кашах и соусах. "
     "Прячется там, где не ждёшь: в колбасе, соевом соусе, даже в конфетах. "
     "Пришли штрихкод — если в составе есть пшеница или глютен, я скажу."),
    (("лактоз", "молоко", "молочк", "казеин", "сывороточн"), "Молоко и лактоза — "
     "разные вещи. Лактоза — это сахар молока, его убирают в безлактозных "
     "продуктах. А аллергия на молоко — это про белок: казеин или сывороточный. "
     "Я ищу в составе именно молочные белки и их производные."),
    (("яйц", "меланж", "альбумин"), "Яйцо чаще всего прячется как «яичный белок», "
     "«меланж», «альбумин» — или в майонезе, выпечке, панировке. "
     "Пришли штрихкод — проверю всё, включая скрытые названия."),
    (("орех", "арахис", "миндал", "фундук"), "Арахис — это бобовое, а не орех, но "
     "аллергия на него встречается так же часто. В списке они у меня отдельно — "
     "отмечай оба, если нужно. Орехи часто бывают в шоколаде, выпечке и там, где "
     "«возможны следы» — это я тоже увижу."),
    (("соя", "соев", "лецитин"), "Соя — в составе как «соевый белок», «лецитин», "
     "«текстурат», «соевый соус». Часто встречается в колбасах, полуфабрикатах и "
     "сладостях. Я проверю все эти хитрые названия."),
    (("как пользоваться", "помощь", "что ты умеешь", "инструкция"), "Всё просто: "
     "выбери аллергены — пришли фото штрихкода или цифры — я найду состав и скажу "
     "вердикт: 🟢 можно, 🟠 возможны следы, 🔴 нельзя. Менять список — командой "
     "/myallergens."),
]


def food_reply(text: str) -> str:
    """Простые ответы на вопросы о еде; всё остальное — мягко перенаправляем."""
    t = text.lower()
    for keywords, answer in FOOD_FAQ:
        if any(k in t for k in keywords):
            return answer
    return NOT_ABOUT_FOOD


async def _safe_edit_text(callback: types.CallbackQuery, text: str,
                          reply_markup=None):
    """edit_text, который не падает на «message is not modified»."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        logging.warning("edit_text failed: %s", exc)


async def _safe_edit_reply_markup(callback: types.CallbackQuery, reply_markup):
    """edit_reply_markup, который не падает на «message is not modified»."""
    try:
        await callback.message.edit_reply_markup(reply_markup=reply_markup)
    except Exception as exc:
        logging.warning("edit_reply_markup failed: %s", exc)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u00a0", " ").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_composition(text: str | None) -> str | None:
    """Чистит состав и отбрасывает явный OCR-мусор (обрывки вместо состава)."""
    text = clean_text(text)
    if not text or len(text) < 2:
        return None
    # Длинный цифровой код (штрихкод/маркировка) внутри состава — признак OCR-мусора.
    if re.search(r"\d{6,}", text):
        return None
    tokens = re.findall(r"[А-ЯЁа-яёA-Za-z0-9]+", text)
    if not tokens:
        return None
    # Много одиночных букв — рассыпанный OCR, а не перечень ингредиентов.
    if len(tokens) >= 8:
        single = sum(1 for tok in tokens if len(tok) == 1)
        if single / len(tokens) > 0.3:
            return None
    return text


def truncate(text: str, limit: int = 500) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def normalize_barcode(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _allergens_phrase(user_allergens: list[str]) -> str:
    """Короткая фраза со списком аллергенов для зелёного вердикта."""
    names = [a for a in user_allergens if a]
    if not names:
        return "твоих аллергенов"
    if len(names) > 3:
        return "твоих аллергенов"
    return ", ".join(names)


def build_answer(name, composition, allergen_field, source, user_allergens,
                 unreliable: bool = False) -> str:
    """Собирает тёплый ответ по результатам проверки."""
    in_composition, in_traces = check_allergens(
        user_allergens, composition, allergen_field
    )
    label = name or "товар"

    parts = []
    if in_composition:
        found = ", ".join(in_composition)
        parts.append(f"🔴 В «{label}» есть {found}. Это нельзя.")
        parts.append(
            "Ты молодец, что проверила. Аллерген часто прячется в похожих "
            "товарах — держи ухо востро."
        )
    elif in_traces:
        traces = ", ".join(in_traces)
        parts.append(f"🟠 В «{label}» {traces} нет, но возможны следы.")
        parts.append(
            "Продукт могли делать рядом с аллергеном. Если реакция сильная — "
            "лучше не рисковать."
        )
    else:
        phrase = _allergens_phrase(user_allergens)
        parts.append(f"🟢 В «{label}» {phrase} не нашла. Похоже, можно.")

    if composition:
        parts.append(f"Состав: {truncate(composition)}")
    if in_traces and allergen_field:
        parts.append(f"Производитель предупреждает: {truncate(allergen_field, 400)}")

    if not in_composition and not in_traces:
        if unreliable:
            parts.append(
                "Правда, я нашла это через веб-поиск — сверься с этикеткой "
                "на всякий случай."
            )
        else:
            parts.append(
                "Если сомневаешься — глянь упаковку: в открытых базах состав "
                "иногда бывает неполным."
            )
        parts.append(
            "И если бот помогает — перешли его подруге, у которой ребёнок-аллергик. "
            "Ей будет спокойнее так же, как тебе."
        )
    elif unreliable:
        parts.append("Это из веб-поиска, а не из проверенной базы — сверься с этикеткой.")

    return "\n\n".join(parts)


async def process_barcode(message: types.Message, barcode: str, user_id: int):
    user_allergens = storage.get_allergens(user_id)
    if not user_allergens:
        await message.answer(ALLERGENS_NOT_SET, reply_markup=choose_button())
        return

    # 0. Локальный кэш — товары, которые мы уже знаем (или добавили вручную).
    cached = storage.get_product(barcode)
    if cached:
        await message.answer(
            build_answer(cached["name"], cached["composition"],
                         cached["allergen_field"], cached["source"], user_allergens)
        )
        return

    await message.answer(SEARCHING)

    name = None
    composition = None
    allergen_field = None
    source = None
    unreliable = False

    # 1. ВкусВилл — лучший источник: состав + отдельное поле аллергенов.
    product = None
    try:
        product = get_client().product_by_barcode(barcode)
    except Exception as exc:
        logging.warning("VkusVill barcode lookup failed: %s", exc)
        product = None

    if product:
        name = clean_text(product.get("name")) or "Неизвестный продукт"
        composition = clean_composition(
            extract_property(product.get("properties"), "Состав")
        )
        allergen_field = extract_property(
            product.get("properties"), "Аллергены по производителям"
        )
        source = "ВкусВилл"
    else:
        # 2. Open Food Facts — бесплатная открытая база, есть российские товары.
        off = await asyncio.to_thread(sources.openfoodfacts_product, barcode)
        if off:
            name = clean_text(off["name"])
            composition = clean_composition(off["composition"])
            source = "Open Food Facts"
        else:
            # 3. Веб-поиск по штрихкоду (обходной путь, результат ненадёжен).
            web = await asyncio.to_thread(sources.websearch_composition, barcode)
            if web:
                name = clean_text(web["name"])
                composition = clean_composition(web["composition"])
                source = "Веб-поиск"
                unreliable = True
            else:
                awaiting_name.add(user_id)
                awaiting_composition[user_id] = barcode
                await message.answer(NOT_FOUND)
                return

    if not composition and not allergen_field:
        awaiting_name.add(user_id)
        awaiting_composition[user_id] = barcode
        await message.answer(
            f"Для «{name or 'товара'}» не нашла данных о составе.\n\n"
            "Напиши название товара — поищу по нему, или пришли состав текстом, "
            "начиная со слова «состав:»."
        )
        return

    if source != "Веб-поиск":
        storage.save_product(barcode, name, composition, allergen_field, source)

    await message.answer(
        build_answer(name, composition, allergen_field, source, user_allergens,
                     unreliable=unreliable)
    )


async def process_name_search(message: types.Message, query: str, user_id: int):
    user_allergens = storage.get_allergens(user_id)
    if not user_allergens:
        await message.answer(ALLERGENS_NOT_SET, reply_markup=choose_button())
        return

    await message.answer(SEARCHING)

    # 1. ВкусВилл — поиск по названию, берём первый результат.
    try:
        items = get_client().search_products(query, limit=1)
    except Exception:
        items = []

    if items:
        item = items[0]
        product_id = item.get("id")
        product = None
        if product_id is not None:
            try:
                product = get_client().product_details(int(product_id))
            except Exception:
                product = None
        if product:
            name = clean_text(product.get("name")) or clean_text(item.get("name"))
            composition = clean_composition(
                extract_property(product.get("properties"), "Состав")
            )
            allergen_field = extract_property(
                product.get("properties"), "Аллергены по производителям"
            )
            if composition or allergen_field:
                await message.answer(
                    build_answer(name, composition, allergen_field, "ВкусВилл",
                                 user_allergens)
                )
                return

    # 2. Open Food Facts — поиск по названию.
    off_results = await asyncio.to_thread(sources.openfoodfacts_search, query, 1)
    if off_results:
        r = off_results[0]
        await message.answer(
            build_answer(clean_text(r["name"]), clean_composition(r["composition"]),
                         None, "Open Food Facts", user_allergens)
        )
        return

    # 3. Веб-поиск по названию (обходной путь, результат ненадёжен).
    web = await asyncio.to_thread(sources.websearch_composition_by_name, query)
    if web and web["composition"]:
        await message.answer(
            build_answer(clean_text(web["name"]), clean_composition(web["composition"]),
                         None, "Веб-поиск", user_allergens, unreliable=True)
        )
        return

    await message.answer(NAME_NOT_FOUND)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(WELCOME, reply_markup=choose_button())


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Всё просто:\n"
        "1. Выбери аллергены.\n"
        "2. Пришли фото штрихкода или цифры.\n"
        "3. Я скажу вердикт: 🟢 можно, 🟠 возможны следы, 🔴 нельзя.\n\n"
        "Менять список — командой /myallergens.",
        reply_markup=choose_button(),
    )


@dp.message(Command("myallergens"))
async def cmd_myallergens(message: types.Message):
    selected = storage.get_allergens(message.from_user.id)
    if selected:
        text = f"Сейчас проверяю, чтобы в составе не было: {', '.join(selected)}."
    else:
        text = "Список пуст. Отметь, что нельзя ребёнку."
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="Изменить список", callback_data="allergens:open")
    ]])
    await message.answer(text, reply_markup=kb)


@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    selected = storage.get_allergens(message.from_user.id)
    if not selected:
        await message.answer("Список и так пуст. Отметь, что нельзя ребёнку:",
                             reply_markup=choose_button())
        return
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="Да, очистить", callback_data="clear:yes"),
        types.InlineKeyboardButton(text="Нет, оставить", callback_data="clear:no"),
    ]])
    await message.answer(
        "Очистить список аллергенов? Тогда проверять станет нечего, пока "
        "снова не выберешь.",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "clear:yes")
async def cb_clear_yes(callback: types.CallbackQuery):
    storage.clear_allergens(callback.from_user.id)
    await _safe_edit_text(callback, "Очистила список. Когда захочешь — отметишь "
                                    "аллергены заново.", reply_markup=choose_button())
    await callback.answer()


@dp.callback_query(F.data == "clear:no")
async def cb_clear_no(callback: types.CallbackQuery):
    await _safe_edit_text(callback, "Оставила как было.")
    await callback.answer()


@dp.callback_query(F.data == "allergens:open")
async def cb_open_allergens(callback: types.CallbackQuery):
    selected = storage.get_allergens(callback.from_user.id)
    await _safe_edit_text(callback, ALLERGENS_CHOOSE,
                          reply_markup=allergens_keyboard(selected))
    await callback.answer()


@dp.callback_query(F.data.startswith("allergen:"))
async def cb_toggle_allergen(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    name = callback.data.split(":", 1)[1]
    selected = storage.get_allergens(user_id)
    if name in selected:
        selected.remove(name)
    else:
        selected.append(name)
    storage.set_allergens(user_id, selected)
    await _safe_edit_reply_markup(callback, reply_markup=allergens_keyboard(selected))
    await callback.answer(f"{name}: {'в списке' if name in selected else 'убрано'}")


@dp.callback_query(F.data == "allergens:done")
async def cb_done_allergens(callback: types.CallbackQuery):
    selected = storage.get_allergens(callback.from_user.id)
    if selected:
        text = (f"Запомнила. Проверяю, чтобы в составе не было: "
                f"{', '.join(selected)}.\n\n"
                "Если что-то не так — вернись к выбору, поменять легко.\n\n"
                "А теперь пришли штрихкод: сфотографируй его или просто набери "
                "цифры. 📸")
    else:
        text = "Список пуст. Пришли фото штрихкода или набери цифры."
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="Изменить список", callback_data="allergens:open")
    ]])
    await _safe_edit_text(callback, text, reply_markup=kb)
    await callback.answer()


@dp.message(F.text)
async def handle_text(message: types.Message):
    text = (message.text or "").strip()
    if text.startswith("/"):
        return

    user_id = message.from_user.id

    # Штрихкод (цифры, возможно с пробелами/дефисами) обрабатываем в первую
    # очередь — даже если бот ждёт название или состав.
    barcode = normalize_barcode(text)
    digits = sum(ch.isdigit() for ch in text)
    # Требуем ≥8 цифр И высокую плотность цифр, чтобы не ловить «2 яйца» как код.
    is_numeric = len(barcode) >= 8 and digits >= 8 and (digits / max(len(text), 1)) >= 0.8

    if is_numeric:
        awaiting_name.discard(user_id)
        awaiting_composition.pop(user_id, None)
        await process_barcode(message, barcode, user_id)
        return

    # Состав текстом («состав: …»), только если текст непустой.
    if user_id in awaiting_composition and re.match(r"(?i)^состав\s*[:：]\s*\S", text):
        saved_barcode = awaiting_composition.pop(user_id)
        awaiting_name.discard(user_id)
        comp = re.sub(r"(?i)^состав\s*[:：]\s*", "", text).strip()
        storage.save_product(saved_barcode, "Товар (добавлен вручную)", comp, None,
                             "Добавлено вручную")
        await message.answer(
            "Спасибо, запомнила. Теперь при повторном скане отвечу сразу.\n\n"
            + build_answer("Товар", comp, None, "Добавлено вручную",
                           storage.get_allergens(user_id))
        )
        return

    # Название товара.
    if user_id in awaiting_name:
        awaiting_name.discard(user_id)
        awaiting_composition.pop(user_id, None)
        await process_name_search(message, text, user_id)
        return

    # Слишком короткий штрихкод.
    if is_numeric and len(barcode) < 8:
        await message.answer(BARCODE_TOO_SHORT)
        return

    # Нет аллергенов — предложить выбор кнопками; иначе — лёгкая беседа о еде.
    if not storage.get_allergens(user_id):
        await message.answer(ALLERGENS_NOT_SET, reply_markup=choose_button())
        return
    await message.answer(food_reply(text))


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    if not storage.get_allergens(user_id):
        await message.answer(ALLERGENS_NOT_SET, reply_markup=choose_button())
        return

    await message.answer("📸 Смотрю фото…")

    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except Exception:
        await message.answer(
            "Не удалось загрузить модуль распознавания штрихкода. "
            "Просто пришли цифры с упаковки."
        )
        return

    try:
        photo_io = io.BytesIO()
        await bot.download(message.photo[-1], destination=photo_io)
        image = Image.open(photo_io)
        decoded = decode(image)
    except Exception as exc:
        logging.warning("photo decode error: %s", exc)
        await message.answer(PHOTO_NOT_READ)
        return

    if not decoded:
        await message.answer(PHOTO_NOT_READ)
        return

    # QR-коды (со ссылками) — это не штрихкод. Ищем именно 1D-штрихкод (EAN/UPC).
    barcode = None
    for sym in decoded:
        sym_type = getattr(sym, "type", "") or ""
        raw = (sym.data or b"").decode("utf-8", errors="replace").strip()
        if sym_type == "QRCODE":
            continue
        digits = normalize_barcode(raw)
        if 8 <= len(digits) <= 14:
            barcode = digits
            break

    if barcode is None:
        await message.answer(QR_NOT_BARCODE)
        return

    await process_barcode(message, barcode, user_id)


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN не задан. Выполни в терминале:\n"
            "  setx BOT_TOKEN \"твой_токен\"\n"
            "и перезапусти терминал."
        )
    logging.basicConfig(level=logging.INFO)
    storage.seed_known_products()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
