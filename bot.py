
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
GRACE_MINUTES = 20

BOTS = {
    "bot01": os.getenv("BOT_01_TOKEN"),
    "bot02": os.getenv("BOT_02_TOKEN"),
    "bot03": os.getenv("BOT_03_TOKEN"),
    "bot04": os.getenv("BOT_04_TOKEN"),
    "bot05": os.getenv("BOT_05_TOKEN"),
}


def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


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


def process_updates(bot_id, token, subscribers, state):
    offset = state.get(bot_id, 0)

    updates = telegram_api(
        token,
        "getUpdates",
        {"offset": offset, "timeout": 0},
    )

    subscribers.setdefault(bot_id, [])

    for update in updates:
        state[bot_id] = update["update_id"] + 1

        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = message.get("chat", {}).get("id")

        # Rejestrujemy /start, ale nie wysyłamy powitania.
        if text == "/start" and chat_id is not None:
            if chat_id not in subscribers[bot_id]:
                subscribers[bot_id].append(chat_id)

    return subscribers, state


def send_due_posts(subscribers, sent):
    now = datetime.now(TIMEZONE)
    today = now.date()
    current_time = now.time()

    with CALENDAR_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        posts = list(csv.DictReader(file))

    for post in posts:
        bot_id = post.get("bot", "").strip().lower()
        post_id = post.get("id", "").strip()
        post_date = post.get("date", "").strip()
        token = BOTS.get(bot_id)

        if not token:
            print(f"Missing token for {bot_id}")
            continue

        # Не отправляем публикации за другие даты.
        if post_date != today.isoformat():
            continue

        if not post_id:
            print("Skipping a post without an ID")
            continue

        try:
            scheduled_time = datetime.strptime(
                post.get("time", "").strip(),
                "%H:%M",
            ).time()
        except ValueError:
            print(f"Invalid time for post {post_id}")
            continue

        scheduled_datetime = datetime.combine(
            today,
            scheduled_time,
            tzinfo=TIMEZONE,
        )

        # Ещё не наступило время публикации.
        if now < scheduled_datetime:
            continue

        # Не отправляем старые публикации после длительного простоя.
        if now - scheduled_datetime > timedelta(minutes=GRACE_MINUTES):
            print(f"Outside sending window: {bot_id}, {post_id}")
            continue

        subscribers.setdefault(bot_id, [])

        text = (post.get("text") or "").replace("\\n", "\n")
        url = (post.get("url") or "").strip()
        post_type = (post.get("type") or "text").strip().lower()

        for chat_id in list(subscribers[bot_id]):
            # Новый ключ учитывает конкретного подписчика.
            sent_key = (
                f"{today.isoformat()}:{bot_id}:{post_id}:{chat_id}"
            )

            # Поддерживаем старый формат ключей sent.json.
            legacy_key = f"{today.isoformat()}:{bot_id}:{post_id}"

            if sent_key in sent or legacy_key in sent:
                continue

            try:
                message_text = personalize_text(
                    token,
                    chat_id,
                    text,
                )

                if post_type == "image":
                    if not url:
                        raise ValueError(
                            f"Missing image URL for post {post_id}"
                        )

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
                    raise ValueError(
                        f"Unknown post type: {post_type}"
                    )

                # Сохраняем каждую успешную отправку сразу.
                sent.add(sent_key)
                save_json(SENT_FILE, sorted(sent))

                print(
                    f"Sent: {bot_id}, post {post_id}, "
                    f"chat {chat_id}"
                )

            except Exception as error:
                print(
                    f"Failed: {bot_id}, chat {chat_id}, "
                    f"post {post_id}: {error}"
                )

    return sent


def main():
    subscribers = load_json(SUBSCRIBERS_FILE, {})
    state = load_json(STATE_FILE, {})
    sent = set(load_json(SENT_FILE, []))

    for bot_id, token in BOTS.items():
        if not token:
            print(f"Missing token for {bot_id}")
            continue

        try:
            subscribers, state = process_updates(
                bot_id,
                token,
                subscribers,
                state,
            )
        except Exception as error:
            print(f"Update error for {bot_id}: {error}")

    save_json(SUBSCRIBERS_FILE, subscribers)
    save_json(STATE_FILE, state)

    try:
        send_due_posts(subscribers, sent)
    except Exception as error:
        print(f"Scheduler error: {error}")

    save_json(SENT_FILE, sorted(sent))


if __name__ == "__main__":
    main()
