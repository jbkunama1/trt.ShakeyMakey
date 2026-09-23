import os
import functools
import requests
import telebot

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_IDS_RAW = os.environ.get("TELEGRAM_ALLOWED_IDS", "").strip()
ALLOWED_IDS = set(int(x) for x in ALLOWED_IDS_RAW.split(",") if x.strip().lstrip("-").isdigit())
API_BASE = os.environ.get("TEAMSHAKE_INTERNAL_API", "http://127.0.0.1:5000/api")

if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN nicht gesetzt - Bot wird nicht gestartet.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Merkt sich pro Chat, welche Klasse gerade aktiv ist (Reset bei Container-Neustart)
active_class = {}


def is_allowed(user_id):
    if not ALLOWED_IDS:
        return False
    return user_id in ALLOWED_IDS


def deny(message):
    bot.reply_to(
        message,
        "🚫 Nicht autorisiert.\n"
        f"Deine Telegram-ID: <code>{message.from_user.id}</code>\n"
        "Bitte diese ID zur Umgebungsvariable TELEGRAM_ALLOWED_IDS hinzufügen "
        "(kommagetrennt) und den Container neu starten.",
    )


def require_auth(func):
    @functools.wraps(func)
    def wrapper(message):
        if not is_allowed(message.from_user.id):
            deny(message)
            return
        try:
            func(message)
        except Exception as exc:  # noqa: BLE001
            bot.reply_to(message, f"⚠️ Fehler: {exc}")

    return wrapper


def api_get(path):
    r = requests.get(f"{API_BASE}{path}", timeout=10)
    r.raise_for_status()
    return r.json()


def api_post(path, payload=None):
    r = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=10)
    if not r.ok:
        raise RuntimeError(r.json().get("error", "Unbekannter Fehler"))
    return r.json()


def api_put(path, payload=None):
    r = requests.put(f"{API_BASE}{path}", json=payload or {}, timeout=10)
    if not r.ok:
        raise RuntimeError(r.json().get("error", "Unbekannter Fehler"))
    return r.json()


def api_delete(path):
    r = requests.delete(f"{API_BASE}{path}", timeout=10)
    if not r.ok:
        raise RuntimeError(r.json().get("error", "Unbekannter Fehler"))
    return r.json() if r.text else {}


def get_active_class_id(chat_id):
    cid = active_class.get(chat_id)
    if cid:
        return cid
    classes = api_get("/classes")
    if classes:
        active_class[chat_id] = classes[0]["id"]
        return classes[0]["id"]
    return None


def class_label(cid):
    for c in api_get("/classes"):
        if c["id"] == cid:
            return c["name"]
    return "?"


def format_teams(teams):
    lines = []
    for idx, team in enumerate(teams, start=1):
        lines.append(f"<b>Team {idx}</b> ({len(team)}): " + ", ".join(team))
    return "\n".join(lines)


@bot.message_handler(commands=["id"])
def cmd_id(message):
    bot.reply_to(message, f"Deine Telegram-ID: <code>{message.from_user.id}</code>")


@bot.message_handler(commands=["start", "hilfe", "help"])
def cmd_help(message):
    text = (
        "🎲 <b>ShakeyMakey Bot</b>\n\n"
        "/klassen - Klassen auflisten\n"
        "/klasse &lt;Name&gt; - Klasse wechseln oder anlegen\n"
        "/namen - Namen der aktiven Klasse anzeigen\n"
        "/add &lt;Name1, Name2, ...&gt; - Namen hinzufügen\n"
        "/entfernen &lt;Name&gt; - einen Namen entfernen\n"
        "/leeren - alle Namen der aktiven Klasse löschen\n"
        "/shake &lt;Anzahl Teams&gt; - Teams zufällig auslosen\n"
        "/groesse &lt;Spieler pro Team&gt; - nach Gruppengröße auslosen\n"
        "/verlauf [n] - letzte Auslosungen anzeigen (Standard 5)\n"
        "/id - deine Telegram-ID anzeigen (für TELEGRAM_ALLOWED_IDS)\n"
    )
    bot.reply_to(message, text)
    if not is_allowed(message.from_user.id):
        deny(message)


@bot.message_handler(commands=["klassen"])
@require_auth
def cmd_classes(message):
    classes = api_get("/classes")
    if not classes:
        bot.reply_to(message, "Keine Klassen vorhanden.")
        return
    lines = [f"• {c['name']} ({c['student_count']} Namen)" for c in classes]
    bot.reply_to(message, "📚 Klassen:\n" + "\n".join(lines))


@bot.message_handler(commands=["klasse"])
@require_auth
def cmd_set_class(message):
    name = message.text.partition(" ")[2].strip()
    if not name:
        bot.reply_to(message, "Nutzung: /klasse <Name>")
        return
    classes = api_get("/classes")
    match = next((c for c in classes if c["name"].lower() == name.lower()), None)
    if not match:
        match = api_post("/classes", {"name": name})
        bot.reply_to(message, f"✅ Neue Klasse '{name}' angelegt und aktiviert.")
    else:
        bot.reply_to(message, f"✅ Klasse '{match['name']}' aktiviert.")
    active_class[message.chat.id] = match["id"]


@bot.message_handler(commands=["namen"])
@require_auth
def cmd_names(message):
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine Klasse vorhanden. Mit /klasse <Name> anlegen.")
        return
    students = api_get(f"/classes/{cid}/students")
    label = class_label(cid)
    if not students:
        bot.reply_to(message, f"Klasse '{label}': keine Namen.")
        return
    lines = [s["name"] for s in students]
    bot.reply_to(message, f"👥 Klasse '{label}' ({len(lines)}):\n" + "\n".join(lines))


@bot.message_handler(commands=["add"])
@require_auth
def cmd_add(message):
    raw = message.text.partition(" ")[2].strip()
    if not raw:
        bot.reply_to(message, "Nutzung: /add Name1, Name2, Name3")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Erst eine Klasse anlegen: /klasse <Name>")
        return
    names = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]
    api_post(f"/classes/{cid}/students", {"names": names})
    bot.reply_to(message, f"✅ {len(names)} Name(n) hinzugefügt.")


@bot.message_handler(commands=["entfernen"])
@require_auth
def cmd_remove(message):
    name = message.text.partition(" ")[2].strip()
    if not name:
        bot.reply_to(message, "Nutzung: /entfernen <Name>")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    students = api_get(f"/classes/{cid}/students")
    match = next((s for s in students if s["name"].lower() == name.lower()), None)
    if not match:
        bot.reply_to(message, f"'{name}' nicht gefunden.")
        return
    api_delete(f"/students/{match['id']}")
    bot.reply_to(message, f"🗑️ '{name}' entfernt.")


@bot.message_handler(commands=["leeren"])
@require_auth
def cmd_clear(message):
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    api_delete(f"/classes/{cid}/students")
    bot.reply_to(message, "🗑️ Alle Namen dieser Klasse gelöscht.")


@bot.message_handler(commands=["shake"])
@require_auth
def cmd_shake(message):
    arg = message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        bot.reply_to(message, "Nutzung: /shake <Anzahl Teams>")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    result = api_post(f"/classes/{cid}/shake", {"mode": "count", "param": int(arg)})
    bot.reply_to(message, "🎲 " + format_teams(result["teams"]))


@bot.message_handler(commands=["groesse"])
@require_auth
def cmd_shake_size(message):
    arg = message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        bot.reply_to(message, "Nutzung: /groesse <Spieler pro Team>")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    result = api_post(f"/classes/{cid}/shake", {"mode": "size", "param": int(arg)})
    bot.reply_to(message, "🎲 " + format_teams(result["teams"]))


@bot.message_handler(commands=["verlauf"])
@require_auth
def cmd_history(message):
    arg = message.text.partition(" ")[2].strip()
    limit = int(arg) if arg.isdigit() else 5
    limit = max(1, min(limit, 20))
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    history_rows = api_get(f"/classes/{cid}/history")[:limit]
    if not history_rows:
        bot.reply_to(message, "Kein Verlauf vorhanden.")
        return
    parts = []
    for entry in history_rows:
        parts.append(f"#{entry['id']} ({entry['created_at']}):\n" + format_teams(entry["teams"]))
    bot.reply_to(message, "\n\n".join(parts))


if __name__ == "__main__":
    print("ShakeyMakey Telegram Bot gestartet.")
    bot.infinity_polling()
