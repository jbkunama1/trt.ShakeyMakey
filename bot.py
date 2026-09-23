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

GENDER_MAP = {"m": "m", "junge": "m", "jungen": "m", "w": "w", "maedchen": "w", "mädchen": "w", "d": "d", "divers": "d"}
BALANCE_ALIASES = {
    "zufall": "random", "zufaellig": "random", "random": "random",
    "staerke": "strength", "stärke": "strength", "strength": "strength",
    "geschlecht": "gender_mixed", "mix": "gender_mixed", "gender_mixed": "gender_mixed",
    "trennen": "gender_separate", "getrennt": "gender_separate", "gender_separate": "gender_separate",
}
BALANCE_LABELS = {
    "random": "Zufällig",
    "strength": "Stärke ausgeglichen",
    "gender_mixed": "Geschlecht gemischt",
    "gender_separate": "Geschlecht getrennt",
}


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


def format_teams(teams, labels=None):
    lines = []
    for idx, team in enumerate(teams, start=1):
        title = labels[idx - 1] if labels and idx - 1 < len(labels) else f"Team {idx}"
        lines.append(f"<b>{title}</b> ({len(team)}): " + ", ".join(team))
    return "\n".join(lines)


def parse_shake_args(raw):
    """Erwartet z.B. '4' oder '4 staerke' oder '4 geschlecht'."""
    parts = raw.split()
    if not parts or not parts[0].isdigit():
        return None, None
    number = int(parts[0])
    balance = "random"
    if len(parts) > 1:
        key = parts[1].lower()
        balance = BALANCE_ALIASES.get(key, "random")
    return number, balance


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
        "/staerke &lt;Name&gt; &lt;1-5&gt; - Stärke setzen\n"
        "/geschlecht &lt;Name&gt; &lt;m|w|d&gt; - Geschlecht setzen\n"
        "/shake &lt;Anzahl&gt; [zufall|staerke|geschlecht|trennen] - Teams nach Anzahl auslosen\n"
        "/groesse &lt;ProTeam&gt; [zufall|staerke|geschlecht|trennen] - nach Gruppengröße auslosen\n"
        "/verlauf [n] - letzte Auslosungen anzeigen (Standard 5)\n"
        "/id - deine Telegram-ID anzeigen (für TELEGRAM_ALLOWED_IDS)\n\n"
        "Aufteilungs-Optionen (nach Sportmethodik, z.B. Achtergarde):\n"
        "• <b>zufall</b> - rein zufällig (Standard)\n"
        "• <b>staerke</b> - gleicht hinterlegte Spielstärke (1-5) zwischen Teams aus\n"
        "• <b>geschlecht</b> - verteilt Jungen/Mädchen gleichmäßig auf alle Teams\n"
        "• <b>trennen</b> - bildet reine Jungen- und reine Mädchenteams\n"
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
    gender_symbol = {"m": "♂", "w": "♀", "d": "⚧"}
    lines = []
    for s in students:
        sym = gender_symbol.get(s.get("gender"), "")
        lines.append(f"{s['name']} (★{s.get('strength', 3)}{(' ' + sym) if sym else ''})")
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


@bot.message_handler(commands=["staerke"])
@require_auth
def cmd_strength(message):
    parts = message.text.partition(" ")[2].strip().rsplit(" ", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Nutzung: /staerke <Name> <1-5>")
        return
    name, value = parts[0].strip(), int(parts[1])
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    students = api_get(f"/classes/{cid}/students")
    match = next((s for s in students if s["name"].lower() == name.lower()), None)
    if not match:
        bot.reply_to(message, f"'{name}' nicht gefunden.")
        return
    api_put(f"/students/{match['id']}", {"strength": value})
    bot.reply_to(message, f"✅ Stärke von '{name}' auf {max(1, min(5, value))} gesetzt.")


@bot.message_handler(commands=["geschlecht"])
@require_auth
def cmd_gender(message):
    parts = message.text.partition(" ")[2].strip().rsplit(" ", 1)
    if len(parts) != 2:
        bot.reply_to(message, "Nutzung: /geschlecht <Name> <m|w|d>")
        return
    name, value = parts[0].strip(), parts[1].strip().lower()
    gender = GENDER_MAP.get(value)
    if not gender:
        bot.reply_to(message, "Ungültiges Geschlecht. Erlaubt: m, w, d")
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
    api_put(f"/students/{match['id']}", {"gender": gender})
    bot.reply_to(message, f"✅ Geschlecht von '{name}' gesetzt.")


@bot.message_handler(commands=["shake"])
@require_auth
def cmd_shake(message):
    raw = message.text.partition(" ")[2].strip()
    number, balance = parse_shake_args(raw)
    if number is None:
        bot.reply_to(message, "Nutzung: /shake <Anzahl Teams> [zufall|staerke|geschlecht|trennen]")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    result = api_post(f"/classes/{cid}/shake", {"mode": "count", "param": number, "balance": balance})
    header = f"🎲 <i>{BALANCE_LABELS.get(balance, balance)}</i>\n"
    bot.reply_to(message, header + format_teams(result["teams"], result.get("labels")))


@bot.message_handler(commands=["groesse"])
@require_auth
def cmd_shake_size(message):
    raw = message.text.partition(" ")[2].strip()
    number, balance = parse_shake_args(raw)
    if number is None:
        bot.reply_to(message, "Nutzung: /groesse <Spieler pro Team> [zufall|staerke|geschlecht|trennen]")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    result = api_post(f"/classes/{cid}/shake", {"mode": "size", "param": number, "balance": balance})
    header = f"🎲 <i>{BALANCE_LABELS.get(balance, balance)}</i>\n"
    bot.reply_to(message, header + format_teams(result["teams"], result.get("labels")))


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
        bal_label = BALANCE_LABELS.get(entry.get("balance", "random"), entry.get("balance"))
        parts.append(
            f"#{entry['id']} ({entry['created_at']}, {bal_label}):\n"
            + format_teams(entry["teams"], entry.get("labels"))
        )
    bot.reply_to(message, "\n\n".join(parts))


if __name__ == "__main__":
    print("ShakeyMakey Telegram Bot gestartet.")
    bot.infinity_polling()
