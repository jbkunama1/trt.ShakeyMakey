import os
import html
import time
import functools
from datetime import datetime
import requests
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_IDS_RAW = os.environ.get("TELEGRAM_ALLOWED_IDS", "").strip()
ALLOWED_IDS = set(int(x) for x in ALLOWED_IDS_RAW.split(",") if x.strip().lstrip("-").isdigit())
API_BASE = os.environ.get("TEAMSHAKE_INTERNAL_API", "http://127.0.0.1:5000/api")
# Startmeldung an alle erlaubten IDs (abschaltbar mit TELEGRAM_STARTUP_NOTIFY=0)
STARTUP_NOTIFY = os.environ.get("TELEGRAM_STARTUP_NOTIFY", "1").strip().lower() not in ("0", "false", "no", "off")

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

HELP_TEXT = (
    "🎲 <b>ShakeyMakey Bot</b>\n\n"
    "/menu - Hauptmenü mit Buttons\n"
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
    "• <b>trennen</b> - bildet reine Jungen- und reine Mädchenteams "
    "(die Zahl gilt pro Gruppe: 3 Teams bei Jungen und Mädchen = 6 Teams)\n"
)


def esc(value):
    """HTML-Escape fuer alle Nutzerdaten (Bot laeuft mit parse_mode=HTML)."""
    return html.escape(str(value), quote=False)


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
            bot.reply_to(message, f"⚠️ Fehler: {esc(exc)}")

    return wrapper


def _error_text(r):
    try:
        return r.json().get("error", "Unbekannter Fehler")
    except ValueError:
        return f"HTTP {r.status_code}"


def api_get(path):
    r = requests.get(f"{API_BASE}{path}", timeout=10)
    r.raise_for_status()
    return r.json()


def api_post(path, payload=None):
    r = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=10)
    if not r.ok:
        raise RuntimeError(_error_text(r))
    return r.json()


def api_put(path, payload=None):
    r = requests.put(f"{API_BASE}{path}", json=payload or {}, timeout=10)
    if not r.ok:
        raise RuntimeError(_error_text(r))
    return r.json()


def api_delete(path):
    r = requests.delete(f"{API_BASE}{path}", timeout=10)
    if not r.ok:
        raise RuntimeError(_error_text(r))
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
        lines.append(f"<b>{esc(title)}</b> ({len(team)}): " + ", ".join(esc(n) for n in team))
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


# ---------- Texte (von Befehlen UND Buttons genutzt) ----------

def main_text(chat_id):
    cid = get_active_class_id(chat_id)
    if cid is None:
        return "🎲 <b>ShakeyMakey</b>\nKeine Klasse vorhanden. Mit /klasse &lt;Name&gt; anlegen."
    name, count = "?", 0
    for c in api_get("/classes"):
        if c["id"] == cid:
            name, count = c["name"], c["student_count"]
            break
    return (
        "🎲 <b>ShakeyMakey</b>\n"
        f"Aktive Klasse: <b>{esc(name)}</b> ({count} Namen)\n"
        "Was möchtest du tun?"
    )


def names_text(chat_id):
    cid = get_active_class_id(chat_id)
    if cid is None:
        return "Keine Klasse vorhanden. Mit /klasse &lt;Name&gt; anlegen."
    students = api_get(f"/classes/{cid}/students")
    label = esc(class_label(cid))
    if not students:
        return f"Klasse '{label}': keine Namen."
    gender_symbol = {"m": "♂", "w": "♀", "d": "⚧"}
    lines = []
    for s in students:
        sym = gender_symbol.get(s.get("gender"), "")
        lines.append(f"{esc(s['name'])} (★{s.get('strength', 3)}{(' ' + sym) if sym else ''})")
    return f"👥 Klasse '{label}' ({len(lines)}):\n" + "\n".join(lines)


def history_text(chat_id, limit=5):
    cid = get_active_class_id(chat_id)
    if cid is None:
        return "Keine aktive Klasse."
    history_rows = api_get(f"/classes/{cid}/history")[:limit]
    if not history_rows:
        return "Kein Verlauf vorhanden."
    parts = []
    for entry in history_rows:
        bal_label = BALANCE_LABELS.get(entry.get("balance", "random"), entry.get("balance"))
        parts.append(
            f"#{entry['id']} ({esc(entry['created_at'])}, {esc(bal_label)}):\n"
            + format_teams(entry["teams"], entry.get("labels"))
        )
    return "\n\n".join(parts)


# ---------- Inline-Tastaturen ----------

def btn(text, data):
    return types.InlineKeyboardButton(text, callback_data=data)


def kb_main():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(btn("🎲 Shake", "menu:shake"), btn("👥 Namen", "menu:names"))
    kb.add(btn("📚 Klassen", "menu:classes"), btn("📜 Verlauf", "menu:history"))
    kb.add(btn("❓ Hilfe", "menu:help"))
    return kb


def kb_back():
    kb = types.InlineKeyboardMarkup()
    kb.add(btn("🏠 Menü", "menu:main"))
    return kb


def kb_classes(classes, active_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in classes:
        mark = "✅ " if c["id"] == active_id else ""
        label = f"{mark}{c['name'][:30]} ({c['student_count']})"
        kb.add(btn(label, f"cls:{c['id']}"))
    kb.add(btn("🏠 Menü", "menu:main"))
    return kb


def kb_balance():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        btn("🎯 Zufall", "sb:random"),
        btn("💪 Stärke", "sb:strength"),
        btn("⚥ Gemischt", "sb:gender_mixed"),
        btn("✂️ Getrennt", "sb:gender_separate"),
    )
    kb.add(btn("🏠 Menü", "menu:main"))
    return kb


def kb_numbers(balance):
    kb = types.InlineKeyboardMarkup(row_width=5)
    kb.add(*[btn(f"{n} Teams", f"sh:count:{balance}:{n}") for n in range(2, 7)])
    kb.add(*[btn(f"{n} je Team", f"sh:size:{balance}:{n}") for n in range(2, 7)])
    kb.add(btn("⬅️ Zurück", "menu:shake"), btn("🏠 Menü", "menu:main"))
    return kb


def kb_result(mode, balance, number):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(btn("🔁 Nochmal", f"sh:{mode}:{balance}:{number}"), btn("🏠 Menü", "menu:main"))
    return kb


def edit(call, text, kb=None):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except ApiTelegramException as exc:
        if "message is not modified" not in str(exc):
            raise


# ---------- Befehle ----------

@bot.message_handler(commands=["id"])
def cmd_id(message):
    bot.reply_to(message, f"Deine Telegram-ID: <code>{message.from_user.id}</code>")


@bot.message_handler(commands=["start", "hilfe", "help"])
def cmd_help(message):
    allowed = is_allowed(message.from_user.id)
    bot.reply_to(message, HELP_TEXT, reply_markup=kb_main() if allowed else None)
    if not allowed:
        deny(message)


@bot.message_handler(commands=["menu"])
@require_auth
def cmd_menu(message):
    bot.reply_to(message, main_text(message.chat.id), reply_markup=kb_main())


@bot.message_handler(commands=["klassen"])
@require_auth
def cmd_classes(message):
    classes = api_get("/classes")
    if not classes:
        bot.reply_to(message, "Keine Klassen vorhanden.")
        return
    lines = [f"• {esc(c['name'])} ({c['student_count']} Namen)" for c in classes]
    bot.reply_to(message, "📚 Klassen:\n" + "\n".join(lines))


@bot.message_handler(commands=["klasse"])
@require_auth
def cmd_set_class(message):
    name = message.text.partition(" ")[2].strip()
    if not name:
        bot.reply_to(message, "Nutzung: /klasse &lt;Name&gt;")
        return
    classes = api_get("/classes")
    match = next((c for c in classes if c["name"].lower() == name.lower()), None)
    if not match:
        match = api_post("/classes", {"name": name})
        bot.reply_to(message, f"✅ Neue Klasse '{esc(name)}' angelegt und aktiviert.")
    else:
        bot.reply_to(message, f"✅ Klasse '{esc(match['name'])}' aktiviert.")
    active_class[message.chat.id] = match["id"]


@bot.message_handler(commands=["namen"])
@require_auth
def cmd_names(message):
    bot.reply_to(message, names_text(message.chat.id))


@bot.message_handler(commands=["add"])
@require_auth
def cmd_add(message):
    raw = message.text.partition(" ")[2].strip()
    if not raw:
        bot.reply_to(message, "Nutzung: /add Name1, Name2, Name3")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Erst eine Klasse anlegen: /klasse &lt;Name&gt;")
        return
    names = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]
    api_post(f"/classes/{cid}/students", {"names": names})
    bot.reply_to(message, f"✅ {len(names)} Name(n) hinzugefügt.")


@bot.message_handler(commands=["entfernen"])
@require_auth
def cmd_remove(message):
    name = message.text.partition(" ")[2].strip()
    if not name:
        bot.reply_to(message, "Nutzung: /entfernen &lt;Name&gt;")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    students = api_get(f"/classes/{cid}/students")
    match = next((s for s in students if s["name"].lower() == name.lower()), None)
    if not match:
        bot.reply_to(message, f"'{esc(name)}' nicht gefunden.")
        return
    api_delete(f"/students/{match['id']}")
    bot.reply_to(message, f"🗑️ '{esc(name)}' entfernt.")


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
        bot.reply_to(message, "Nutzung: /staerke &lt;Name&gt; &lt;1-5&gt;")
        return
    name, value = parts[0].strip(), int(parts[1])
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    students = api_get(f"/classes/{cid}/students")
    match = next((s for s in students if s["name"].lower() == name.lower()), None)
    if not match:
        bot.reply_to(message, f"'{esc(name)}' nicht gefunden.")
        return
    api_put(f"/students/{match['id']}", {"strength": value})
    bot.reply_to(message, f"✅ Stärke von '{esc(name)}' auf {max(1, min(5, value))} gesetzt.")


@bot.message_handler(commands=["geschlecht"])
@require_auth
def cmd_gender(message):
    parts = message.text.partition(" ")[2].strip().rsplit(" ", 1)
    if len(parts) != 2:
        bot.reply_to(message, "Nutzung: /geschlecht &lt;Name&gt; &lt;m|w|d&gt;")
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
        bot.reply_to(message, f"'{esc(name)}' nicht gefunden.")
        return
    api_put(f"/students/{match['id']}", {"gender": gender})
    bot.reply_to(message, f"✅ Geschlecht von '{esc(name)}' gesetzt.")


@bot.message_handler(commands=["shake"])
@require_auth
def cmd_shake(message):
    raw = message.text.partition(" ")[2].strip()
    number, balance = parse_shake_args(raw)
    if number is None:
        bot.reply_to(message, "Nutzung: /shake &lt;Anzahl Teams&gt; [zufall|staerke|geschlecht|trennen]")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    result = api_post(f"/classes/{cid}/shake", {"mode": "count", "param": number, "balance": balance})
    header = f"🎲 <i>{BALANCE_LABELS.get(balance, balance)}</i>\n"
    bot.reply_to(message, header + format_teams(result["teams"], result.get("labels")), reply_markup=kb_result("count", balance, number))


@bot.message_handler(commands=["groesse"])
@require_auth
def cmd_shake_size(message):
    raw = message.text.partition(" ")[2].strip()
    number, balance = parse_shake_args(raw)
    if number is None:
        bot.reply_to(message, "Nutzung: /groesse &lt;Spieler pro Team&gt; [zufall|staerke|geschlecht|trennen]")
        return
    cid = get_active_class_id(message.chat.id)
    if cid is None:
        bot.reply_to(message, "Keine aktive Klasse.")
        return
    result = api_post(f"/classes/{cid}/shake", {"mode": "size", "param": number, "balance": balance})
    header = f"🎲 <i>{BALANCE_LABELS.get(balance, balance)}</i>\n"
    bot.reply_to(message, header + format_teams(result["teams"], result.get("labels")), reply_markup=kb_result("size", balance, number))


@bot.message_handler(commands=["verlauf"])
@require_auth
def cmd_history(message):
    arg = message.text.partition(" ")[2].strip()
    limit = int(arg) if arg.isdigit() else 5
    limit = max(1, min(limit, 20))
    bot.reply_to(message, history_text(message.chat.id, limit))


# ---------- Inline-Button-Callbacks ----------

def handle_callback(call):
    parts = (call.data or "").split(":")
    action = parts[0]
    chat_id = call.message.chat.id

    if action == "menu":
        target = parts[1] if len(parts) > 1 else "main"
        if target == "classes":
            classes = api_get("/classes")
            if not classes:
                edit(call, "Keine Klassen vorhanden. Mit /klasse &lt;Name&gt; anlegen.", kb_back())
            else:
                edit(call, "📚 <b>Klasse wählen</b>", kb_classes(classes, get_active_class_id(chat_id)))
        elif target == "names":
            edit(call, names_text(chat_id), kb_back())
        elif target == "shake":
            edit(call, "🎲 <b>Aufteilung wählen</b>", kb_balance())
        elif target == "history":
            edit(call, history_text(chat_id, 3), kb_back())
        elif target == "help":
            edit(call, HELP_TEXT, kb_back())
        else:
            edit(call, main_text(chat_id), kb_main())

    elif action == "cls":
        active_class[chat_id] = int(parts[1])
        edit(call, main_text(chat_id), kb_main())

    elif action == "sb":
        balance = parts[1] if len(parts) > 1 else "random"
        if balance not in BALANCE_LABELS:
            balance = "random"
        edit(
            call,
            f"🎲 <i>{BALANCE_LABELS[balance]}</i>\nWie viele Teams bzw. welche Teamgröße?",
            kb_numbers(balance),
        )

    elif action == "sh":
        mode, balance, number = parts[1], parts[2], int(parts[3])
        if mode not in ("count", "size") or balance not in BALANCE_LABELS or not 1 <= number <= 50:
            raise ValueError("Ungültige Auswahl")
        cid = get_active_class_id(chat_id)
        if cid is None:
            edit(call, "Keine aktive Klasse.", kb_back())
            return
        result = api_post(f"/classes/{cid}/shake", {"mode": mode, "param": number, "balance": balance})
        what = f"{number} Teams" if mode == "count" else f"je {number} Spieler"
        header = f"🎲 <i>{BALANCE_LABELS[balance]} · {what}</i>\n"
        edit(call, header + format_teams(result["teams"], result.get("labels")), kb_result(mode, balance, number))


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    if not is_allowed(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 Nicht autorisiert", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    try:
        handle_callback(call)
    except Exception as exc:  # noqa: BLE001
        bot.send_message(call.message.chat.id, f"⚠️ Fehler: {esc(exc)}", reply_markup=kb_back())


# ---------- Start ----------

def wait_for_api(timeout=30):
    """gunicorn startet parallel zum Bot - kurz warten, bis die API antwortet."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            api_get("/classes")
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def startup_notify():
    if not STARTUP_NOTIFY or not ALLOWED_IDS:
        return
    api_ok = wait_for_api()
    now = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
    for uid in sorted(ALLOWED_IDS):
        try:
            if api_ok:
                body, kb = main_text(uid), kb_main()
            else:
                body, kb = "⚠️ API ist noch nicht erreichbar - bitte Logs prüfen.", None
            bot.send_message(uid, f"🟢 <b>ShakeyMakey Bot gestartet</b>\n🕒 {now}\n\n{body}", reply_markup=kb)
        except Exception as exc:  # noqa: BLE001
            print(f"Startmeldung an {uid} fehlgeschlagen: {exc}", flush=True)


def register_commands():
    try:
        bot.set_my_commands([
            types.BotCommand("menu", "Hauptmenü mit Buttons"),
            types.BotCommand("klassen", "Klassen auflisten"),
            types.BotCommand("namen", "Namen anzeigen"),
            types.BotCommand("shake", "Teams nach Anzahl auslosen"),
            types.BotCommand("groesse", "Teams nach Größe auslosen"),
            types.BotCommand("verlauf", "Letzte Auslosungen"),
            types.BotCommand("hilfe", "Hilfe"),
        ])
    except Exception as exc:  # noqa: BLE001
        print(f"set_my_commands fehlgeschlagen: {exc}", flush=True)


if __name__ == "__main__":
    print("ShakeyMakey Telegram Bot gestartet.", flush=True)
    register_commands()
    startup_notify()
    bot.infinity_polling()
