# bot.py
import time
import sqlite3
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import LabeledPrice, ContentType
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import config

bot = Bot(token=config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

DB_PATH = 'channel.sql'
mychannel_id = -1002807601247
OWNER_ID = config.OWNER_ID  

# В памяти (необязательно, удобство)
pending_invoices = {}

# Состояния для регистрации и доната
class RegStates(StatesGroup):
    personal_name = State()
    community_name = State()

class DonateStates(StatesGroup):
    amount = State()
    note = State()

# Инициализация БД (минимальная таблица users, как у вас)
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            personal_name TEXT,
            community_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            note TEXT,
            payload TEXT,
            paid INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_user(user_id, personal_name, community_name):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO users (id, personal_name, community_name) VALUES (?, ?, ?)',
                (user_id, personal_name, community_name))
    conn.commit()
    conn.close()

def save_pending_donation(user_id, amount, note, payload):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO donations (user_id, amount, note, payload, paid) VALUES (?, ?, ?, ?, 0)',
                (user_id, amount, note, payload))
    conn.commit()
    conn.close()

def mark_donation_paid(payload):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('UPDATE donations SET paid = 1 WHERE payload = ?', (payload,))
    conn.commit()
    conn.close()

# Клавиатура
def main_menu_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton('Перейти в канал'), types.KeyboardButton('Пожертвовать'))
    markup.row(types.KeyboardButton('Удалить фото'), types.KeyboardButton('Изменить текст'))
    return markup

# Хэндлеры
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id FROM users WHERE id = ?', (message.from_user.id,))
    exists = cur.fetchone() is not None
    conn.close()

    if exists:
        await message.answer(f'С возвращением, {message.from_user.first_name}!', reply_markup=main_menu_markup())
    else:
        await message.answer('Здравствуйте! Как мне Вас называть? (например, ваше имя)')
        await RegStates.personal_name.set()

@dp.message_handler(state=RegStates.personal_name, content_types=ContentType.TEXT)
async def reg_personal_name(message: types.Message, state: FSMContext):
    await state.update_data(personal_name=message.text.strip())
    await message.answer('Отлично! А как вы хотите, чтобы вас называли в сообществе? (публичный ник)')
    await RegStates.community_name.set()

@dp.message_handler(state=RegStates.community_name, content_types=ContentType.TEXT)
async def reg_community_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    personal_name = data.get('personal_name')
    community_name = message.text.strip()
    save_user(message.from_user.id, personal_name, community_name)
    await state.finish()
    await message.answer(f'Готово! Теперь я буду звать вас «{personal_name}», а в сообществе вы — «{community_name}»!',
                         reply_markup=main_menu_markup())

# Простая обработка текстовых кнопок (как в telebot)
@dp.message_handler(content_types=ContentType.TEXT)
async def text_handler(message: types.Message):
    txt = message.text.strip().lower()
    if txt == 'пожертвовать':
        await message.answer('Сколько вы хотите пожертвовать каналу? Введите сумму в рублях (например: 150 или 99.50).')
        await DonateStates.amount.set()
        return
    if txt == 'перейти в канал':
        await message.answer('https://t.me/onehourone')
        return
    if txt == 'удалить фото':
        await message.answer('Функция удаления пока недоступна.')
        return
    if txt == 'изменить текст':
        await message.answer('Функция редактирования пока недоступна.')
        return
    if txt == 'привет':
        await message.answer('Здравствуйте!')
        return
    if txt == 'id':
        await message.answer(f'Ваш ID: {message.from_user.id}')
        return
    # иначе игнорируем или отвечаем по умолчанию

# Получаем сумму
@dp.message_handler(state=DonateStates.amount, content_types=ContentType.TEXT)
async def donate_amount_received(message: types.Message, state: FSMContext):
    text = message.text.replace(',', '.').strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer('Некорректная сумма. Введите положительное число, например: 100 или 50.5')
        return
    await state.update_data(amount=amount)
    await message.answer('Хотите оставить сообщение к донату? Напишите его (или отправьте "-" чтобы пропустить).')
    await DonateStates.note.set()

# Получаем примечание и отправляем инвойс
@dp.message_handler(state=DonateStates.note, content_types=ContentType.TEXT)
async def donate_note_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get('amount')
    note_text = message.text.strip()
    if note_text == '-':
        note_text = ''

    user = message.from_user
    payload = f"donation:{user.id}:{int(time.time())}"
    pending_invoices[payload] = {
        'user_id': user.id,
        'amount': amount,
        'note': note_text,
        'username': f"{user.full_name} (@{user.username})" if user.username else f"{user.full_name}"
    }
    save_pending_donation(user.id, amount, note_text, payload)

    title = 'Пожертвование'
    description = 'Возможность сказать команде канала "Спасибо!" рублём'
    #provider_token = config.PAYMENT_TOKEN
    currency = 'RUB'
    price_in_kopecks = int(round(amount * 100))
    prices = [LabeledPrice('Пожертвовать', price_in_kopecks)]

    try:
        await bot.send_invoice(
            message.chat.id,
            title=title,
            description=description,
            payload=payload,
            provider_token=config.PAYMENT_TOKEN,
            currency=currency,
            prices=prices,
            start_parameter='donation-start'
        )
    except Exception as e:
        await message.answer('Ошибка при создании счёта. Попробуйте позже.')
        print('send_invoice error:', e)
    await state.finish()

# Успешная оплата
@dp.message_handler(content_types=ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: types.Message):
    successful = message.successful_payment
    payload = getattr(successful, 'invoice_payload', None) or getattr(successful, 'payload', None)
    if not payload:
        await message.answer('Платёж принят. Спасибо!')
        return

    # помечаем в БД
    mark_donation_paid(payload)

    info = pending_invoices.get(payload)
    amount = info['amount'] if info else (successful.total_amount / 100.0 if hasattr(successful, 'total_amount') else 'неизвестно')
    note = info.get('note', '') if info else ''
    donor = info.get('username') if info else f'{message.from_user.full_name} (id:{message.from_user.id})'

    await message.answer('Спасибо за пожертвование! Ваша поддержка очень ценна ❤️')

    owner_msg = f'Новый донат!\nОт: {donor}\nСумма: {amount} руб.\nКомментарий: {note or "—"}\nPayload: {payload}'
    # отправляем владельцу (без проверки)
    await bot.send_message(OWNER_ID, owner_msg)

    if payload in pending_invoices:
        del pending_invoices[payload]

# Фотки и inline-кнопки
@dp.message_handler(content_types=ContentType.PHOTO)
async def photo_handler(message: types.Message):
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton('Перейти в канал', url='https://t.me/onehourone'))
    markup.row(types.InlineKeyboardButton('Удалить фото', callback_data='delete'),
               types.InlineKeyboardButton('Изменить текст', callback_data='edit'))
    await message.reply('Вы потрясающи!', reply_markup=markup)

@dp.callback_query_handler(lambda c: True)
async def process_callback(callback_query: types.CallbackQuery):
    if callback_query.data == 'delete':
        await bot.answer_callback_query(callback_query.id, 'Функция удаления пока недоступна.')
    elif callback_query.data == 'edit':
        await bot.answer_callback_query(callback_query.id, 'Функция редактирования пока недоступна.')
    else:
        await bot.answer_callback_query(callback_query.id, 'Неизвестная команда.')

if __name__ == '__main__':
    init_db()
    executor.start_polling(dp, skip_updates=True)
