
import csv
import html
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_DIR = Path(__file__).parent
CALENDAR_FILE = BASE_DIR / "calendar" / "schedule.csv"
SUBSCRIBERS_FILE = BASE_DIR / "subscribers" / "subscribers.json"
STATE_FILE = BASE_DIR / "subscribers" / "state.json"
SENT_FILE = BASE_DIR / "subscribers" / "sent.json"

TIMEZONE = ZoneInfo("Europe/Warsaw")

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
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def process_updates(bot_id, token, subscribers, state):
    offset = state.get(bot_id, 0)
    updates = telegram_api(
        token, "getUpdates", {"offset": offset, "timeout": 0}
    )

    subscribers.setdefault(bot_id, [])

    for update in updates:
        state[bot_id] = update["update_id"] + 1
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = message.get("chat", {}).get("id")

        # Rejestrujemy /start bez wysyłania powitania.
        if text == "/start" and chat_id is not None:
            if chat_id not in subscribers[bot_id]:
                subscribers[bot_id].append(chat_id)

    return subscribers, state


def personalize_text(token, chat_id, text):
    if "{username}" not in text:
        return text

    chat = telegram_api(token, "getChat", {"chat_id": chat_id})
    username = chat.get("username")

    if username:
        display_name = "@" + username
    else:
        display_name = chat.get("first_name") or "Cześć"

    return text.replace("{username}", html.escape(display_name))


def send_due_posts(subscribers, sent):
    now = datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")

    with CALENDAR_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        posts = list(csv.DictReader(f))

    for post in posts:
        bot_id = post["bot"].strip().lower()
        post_id = post["id"].strip()
        token = BOTS.get(bot_id)

        if not token:
            print(f"Missing token for {bot_id}")
            continue

        try:
            post_time = datetime.strptime(
                post["time"].strip(), "%H:%M"
            ).time()
        except ValueError:
            print(f"Invalid time for post {post_id}")
            continue

        if post_time > now.time():
            continue

        sent_key = f"{today}:{bot_id}:{post_id}"
        if sent_key in sent:
            continue

        text = (post.get("text") or "").replace("\\n", "\n")
        url = (post.get("url") or "").strip()
        post_type = (post.get("type") or "text").strip().lower()

        all_sent = True

        for chat_id in subscribers.get(bot_id, []):
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
                    telegram_api(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": message_text,
                        "parse_mode": "HTML",
                    })

                else:
                    print(f"Unknown post type: {post_type}")
                    all_sent = False

            except Exception as error:
                all_sent = False
                print(
                    f"Failed: {bot_id}, chat {chat_id}, "
                    f"post {post_id}: {error}"
                )

        if all_sent:
            sent.add(sent_key)

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
                bot_id, token, subscribers, state
            )
        except Exception as error:
            print(f"Update error for {bot_id}: {error}")

    try:
        sent = send_due_posts(subscribers, sent)
    except Exception as error:
        print(f"Scheduler error: {error}")

    save_json(SUBSCRIBERS_FILE, subscribers)
    save_json(STATE_FILE, state)
    save_json(SENT_FILE, sorted(sent))


if __name__ == "__main__":
    main()
