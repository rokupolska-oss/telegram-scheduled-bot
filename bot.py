
import csv
import html
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_DIR = Path(__file__).parent
CALENDAR_FILE = BASE_DIR / "calendar" / "schedule.csv"
SUBSCRIBERS_FILE = BASE_DIR / "subscribers" / "subscribers.json"
STATE_FILE = BASE_DIR / "subscribers" / "state.json"
SENT_FILE = BASE_DIR / "subscribers" / "sent.json"

TIMEZONE = ZoneInfo("Europe/Warsaw")

# Разрешаем отправку в течение 20 минут после планового времени.
# Пропущенные публикации не будут отправляться все сразу.
GRACE_MINUTES = 20

BOTS = {
    "bot01": os.getenv("BOT_01_TOKEN"),
    "bot02": os.getenv("BOT_02_TOKEN"),
    "bot03": os.getenv("BOT_03_TOKEN"),
    "bot04": os.getenv("BOT_04_TOKEN"),
    "bot05": os.getenv("BOT_05_TOKEN"),
}


def load_json(path, default):
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    os.replace(temp_path, path)


def telegram_api(token, method, payload=None):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload or {},
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    return result["result"]


def personalize_text(token, chat_id, text):
    if "{username}" not in text:
        return text

    chat = telegram_api(token, "getChat", {"chat_id": chat_id})
    username = chat.get("username")

    if username:
        display_name = "@" + username
    else:
        display_name = chat.get("first_name") or "Cześć"

    return text.replace(
        "{username}",
        html.escape(display_name),
    )


def send_due_posts(subscribers, sent):
    now = datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")

    with CALENDAR_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        posts = list(csv.DictReader(f))

    for post in posts:
        bot_id = post.get("bot", "").strip().lower()
        post_id = post.get("id", "").strip()
        post_date = post.get("date", "").strip()
        token = BOTS.get(bot_id)

        # Tylko dzisiejsze publikacje.
        if post_date != today:
            continue

        if not token:
            print(f"Missing token for {bot_id}")
            continue

        try:
            post_time = datetime.strptime(
                post.get("time", "").strip(),
                "%H:%M",
            ).time()
        except ValueError:
            print(f"Invalid time for post {post_id}")
            continue

        scheduled = datetime.combine(
            now.date(),
            post_time,
            tzinfo=TIMEZONE,
        )

        # Nie wysyляем заранее или слишком поздно.
        if now < scheduled:
            continue

        if now >= scheduled + timedelta(minutes=GRACE_MINUTES):
            print(f"Skipping expired post: {bot_id}, {post_id}")
            continue

        # Старый ключ нужен для совместимости с sent.json.
        legacy_key = f"{today}:{bot_id}:{post_id}"

        # Если старый код уже отметил пост отправленным,
        # не отправляем его повторно.
        if legacy_key in sent:
            print(f"Already sent (legacy): {bot_id}, {post_id}")
            continue

        text = (post.get("text") or "").replace("\\n", "\n")
        url = (post.get("url") or "").strip()
        post_type = (post.get("type") or "text").strip().lower()

        chat_ids = set(subscribers.get(bot_id, []))

        if not chat_ids:
            print(f"No subscribers for {bot_id}")
            continue

        for chat_id in sorted(chat_ids):
            # Учитываем отправку отдельно для каждого чата.
            chat_key = f"{today}:{bot_id}:{post_id}:{chat_id}"

            if chat_key in sent:
                print(f"Already sent: {bot_id}, {post_id}, chat {chat_id}")
                continue

            try:
                message_text = personalize_text(
                    token, chat_id, text
                )

                if post_type == "image":
                    payload = {
                        "chat_id": chat_id,
                        "photo": url,
                    }

                    if message_text:
                        payload["caption"] = message_text
                        payload["parse_mode"] = "HTML"

                    telegram_api(token, "sendPhoto", payload)

                elif post_type == "text":
                    telegram_api(
                        token,
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": message_text,
                            "parse_mode": "HTML",
                        },
                    )

                else:
                    print(f"Unknown post type: {post_type}")
                    continue

                # Сохраняем отметку сразу после успешного ответа Telegram.
                sent.add(chat_key)
                save_json(SENT_FILE, sorted(sent))

                print(f"Sent: {bot_id}, {post_id}, chat {chat_id}")

            except Exception as error:
                print(
                    f"Failed: {bot_id}, chat {chat_id}, "
                    f"post {post_id}: {error}"
                )


def main():
    subscribers = load_json(SUBSCRIBERS_FILE, {})
    sent = set(load_json(SENT_FILE, []))

    # Этот процесс не опрашивает getUpdates и не отправляет /start.
    # Список подписчиков берётся из subscribers.json.
    send_due_posts(subscribers, sent)

    # Сохраняем файлы, не связанные с отправкой, без изменения структуры.
    state = load_json(STATE_FILE, {})
    save_json(SUBSCRIBERS_FILE, subscribers)
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
