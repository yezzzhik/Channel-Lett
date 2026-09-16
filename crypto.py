"""
Шифрование чувствительных текстовых полей.

Подход: один мастер-секрет в конфиге (config.MASTER_SECRET), из него для
каждого user_id через HKDF выводится персональный ключ Fernet. Так не нужно
хранить ключи в БД, но компрометация БД без мастер-секрета бесполезна для
атакующего, а компрометация одного пользователя не даёт ключей других.

Если параноидальнее — реальный master secret нужно держать не в коде, а в
переменной окружения (см. config.py: MASTER_SECRET = os.environ["..."]).
"""
import base64
import json
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import config


def _derive_key(user_id: int) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(user_id).encode(),
        info=b"anxiety-diary-v1",
    )
    raw = hkdf.derive(config.MASTER_SECRET.encode())
    return base64.urlsafe_b64encode(raw)


def encrypt_json(user_id: int, data: dict) -> bytes:
    """Сериализует dict в JSON и шифрует. Пустые/None значения не пишем,
    чтобы не раздувать blob и не хранить лишний открытый текст в структуре."""
    clean = {k: v for k, v in data.items() if v not in (None, "")}
    payload = json.dumps(clean, ensure_ascii=False).encode("utf-8")
    f = Fernet(_derive_key(user_id))
    return f.encrypt(payload)


def decrypt_json(user_id: int, token: bytes) -> dict:
    if not token:
        return {}
    f = Fernet(_derive_key(user_id))
    try:
        raw = f.decrypt(token if isinstance(token, bytes) else bytes(token))
    except InvalidToken:
        # ключ поменяли или данные повреждены — не роняем бота
        return {}
    return json.loads(raw.decode("utf-8"))
