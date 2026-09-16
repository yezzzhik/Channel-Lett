import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.tl.types import Message
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

# --- Настройки логгирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Загрузка конфига ---
with open("config.json", encoding='utf-8') as f:
    config = json.load(f)

client = TelegramClient(config["SESSION"], config["API_ID"], config["API_HASH"])
PROCESSED_FILE = Path("processed_ids.json")

# Дата начала фильтрации: 1 января 2023 года
START_DATE = datetime(2023, 1, 1, tzinfo=timezone.utc)

# --- Паузы (секунды) ---
PAUSE_BETWEEN_WORKOUTS = 15.0   # между разными комплексами
PAUSE_BATCH = 40.0             # дополнительная пауза каждые N комплексов
BATCH_SIZE = 5                # после скольких комплексов делать длинную паузу

# --- Максимальный разрыв по времени внутри одного комплекса ---
MAX_GAP_MINUTES = 15

# --- Лимит для тестового запуска. Поставь None чтобы обработать всё ---
WORKOUT_LIMIT = None


def load_processed() -> set:
    if PROCESSED_FILE.exists():
        try:
            return set(json.loads(PROCESSED_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_processed(s: set):
    PROCESSED_FILE.write_text(json.dumps(list(s)))


def is_any_media(msg: Message) -> bool:
    if not msg:
        return False
    if msg.photo or msg.video:
        return True
    if msg.document:
        mt = getattr(msg.document, "mime_type", "") or ""
        return mt.startswith(("image/", "video/"))
    return False


def is_new_workout_trigger(text: str) -> bool:
    t = text.lower()
    return "станция" in t


async def get_chat_entity(link: str):
    try:
        return await client.get_entity(link)
    except Exception:
        if "+" in link or "/joinchat/" in link:
            hash_part = link.split('/')[-1].replace('+', '')
            try:
                await client(ImportChatInviteRequest(hash_part))
                return await client.get_entity(link)
            except UserAlreadyParticipantError:
                return await client.get_entity(link)
        raise ValueError(f"Ошибка доступа к ссылке: {link}")


async def forward_sequence(start_msg: Message, all_messages: list, start_idx: int, target_entity) -> bool:
    processed = load_processed()
    key = f"{start_msg.chat_id}:{start_msg.id}"
    if key in processed:
        logger.info(f"⏭ Уже обработано: {key}")
        return False

    author_id = start_msg.sender_id
    potential_context = all_messages[start_idx + 1: start_idx + 26]

    last_media_rel_idx = -1
    for i, m in enumerate(potential_context):
        m_text = (m.raw_text or "").lower()

        # Стоп 1: начало нового комплекса
        if is_new_workout_trigger(m_text):
            break

        # Стоп 2: слишком большой разрыв по времени
        prev_msg = all_messages[start_idx + i]
        if m.date and prev_msg.date:
            gap = (m.date - prev_msg.date).total_seconds() / 60
            if gap > MAX_GAP_MINUTES:
                logger.info(f"⏱ Разрыв {gap:.1f} мин — останавливаю сбор медиа")
                break

        # Стоп 3: текст от другого автора
        if m.sender_id != author_id and not is_any_media(m):
            break

        if is_any_media(m) and m.sender_id == author_id:
            last_media_rel_idx = i

    to_forward = [start_msg]
    if last_media_rel_idx != -1:
        for m in potential_context[: last_media_rel_idx + 1]:
            if m.sender_id == author_id:
                to_forward.append(m)

    unique_to_send = sorted(
        {m.id: m for m in to_forward}.values(), key=lambda x: x.id
    )

    try:
        date_str = start_msg.date.strftime("%d.%m.%Y")
        header = f"📅 **Тренировка от {date_str}**\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
        await client.send_message(target_entity, header)
        await asyncio.sleep(1.0)

        ids_to_forward = [m.id for m in unique_to_send]
        await client.forward_messages(target_entity, ids_to_forward, start_msg.chat_id)

        processed.add(key)
        save_processed(processed)
        logger.info(f"✅ Переслана тренировка за {date_str} ({len(unique_to_send)} сообщ.)")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка пересылки: {e}")
        return False


async def main():
    await client.start()
    logger.info("Бот запущен. Настраиваю чаты...")

    source_chat = await get_chat_entity(config["INVITE_LINK"])
    target_chat = await get_chat_entity(config["TARGET_CHAT_LINK"])

    logger.info(f"Загружаю историю {source_chat.title} (это может занять время)...")

    all_msgs = []
    async for m in client.iter_messages(source_chat, reverse=True):
        if m.date < START_DATE:
            continue
        all_msgs.append(m)

    logger.info(f"Загружено {len(all_msgs)} сообщений с {START_DATE.date()}.")

    if WORKOUT_LIMIT:
        logger.info(f"⚠️  Тестовый режим: не более {WORKOUT_LIMIT} комплексов. "
                    f"Поставь WORKOUT_LIMIT = None для полного запуска.")

    forwarded_count = 0
    for idx, msg in enumerate(all_msgs):
        if WORKOUT_LIMIT and forwarded_count >= WORKOUT_LIMIT:
            logger.info(f"🏁 Достигнут лимит {WORKOUT_LIMIT} комплексов. Останавливаюсь.")
            break

        text = msg.raw_text or ""
        if is_new_workout_trigger(text):
            was_forwarded = await forward_sequence(msg, all_msgs, idx, target_chat)
            if was_forwarded:
                forwarded_count += 1
                if forwarded_count % BATCH_SIZE == 0:
                    logger.info(f"⏸ Пауза {PAUSE_BATCH} сек после {forwarded_count} комплексов...")
                    await asyncio.sleep(PAUSE_BATCH)
                else:
                    await asyncio.sleep(PAUSE_BETWEEN_WORKOUTS)

    logger.info(f"Готово. Переслано комплексов: {forwarded_count}.")


if __name__ == "__main__":
    asyncio.run(main())