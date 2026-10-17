import sqlite3
import json
import random
import os
import time
import fcntl
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, send_from_directory

DB_PATH = os.environ.get("TEAMSHAKE_DB", "/data/teamshake.db")
MAX_DRAWS_PER_CLASS = int(os.environ.get("TEAMSHAKE_MAX_DRAWS", "50"))

# Zusatzoptionen: Regeln (getrennt/zusammen) und Abwechslung
RULE_WEIGHT = 1000          # Strafpunkte je verletzter Regel
CANDIDATES = 15             # Zufalls-Startvarianten, wenn Regeln/Abwechslung aktiv sind
RECENT_DRAWS_FOR_VARIETY = 5  # so viele letzte Auslosungen zaehlen bei "Abwechslung"
OPTIMIZE_TIME_LIMIT = 1.5   # Sekunden fuer das Nachbessern per Tausch

app = Flask(__name__, static_folder="static", static_url_path="")

GENDER_LABELS = {"m": "Jungen", "w": "Mädchen", "d": "Divers"}
PAIR_KINDS = ("apart", "together")


def now_iso():
    # Gleiches Format wie bisher (naive UTC-Zeit), ohne deprecated utcnow()
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def json_body():
    data = request.get_json(force=True, silent=True)
    return data if isinstance(data, dict) else {}


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        db = g._database = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db


@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def _add_column_if_missing(conn, table, column, coldef):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def _init_db_locked():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS draws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            param INTEGER NOT NULL,
            teams_json TEXT NOT NULL,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            a INTEGER NOT NULL,
            b INTEGER NOT NULL,
            kind TEXT NOT NULL,
            UNIQUE (a, b),
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
            FOREIGN KEY (a) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (b) REFERENCES students(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
        CREATE INDEX IF NOT EXISTS idx_draws_class ON draws(class_id, id);
        CREATE INDEX IF NOT EXISTS idx_pairs_class ON pairs(class_id);
        """
    )
    conn.commit()

    # Migration: optionale Felder fuer Staerke-/Geschlechterausgleich
    # (Sportmethodik: leistungsausgleichende & geschlechterbewusste Teams)
    _add_column_if_missing(conn, "students", "strength", "INTEGER DEFAULT 3")
    _add_column_if_missing(conn, "students", "gender", "TEXT")
    _add_column_if_missing(conn, "draws", "balance", "TEXT DEFAULT 'random'")
    conn.commit()

    cur = conn.execute("SELECT COUNT(*) FROM classes")
    if cur.fetchone()[0] == 0:
        conn.execute(
            "INSERT OR IGNORE INTO classes (name, created_at) VALUES (?, ?)",
            ("Meine Klasse", now_iso()),
        )
        conn.commit()
    conn.close()


def init_db():
    """Initialisierung mit Dateilock, damit mehrere Gunicorn-Worker nicht kollidieren."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _init_db_locked()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def enforce_draw_limit(db, class_id):
    """Round-robin: keep only the newest MAX_DRAWS_PER_CLASS draws per class."""
    cur = db.execute(
        "SELECT COUNT(*) AS c FROM draws WHERE class_id = ?", (class_id,)
    )
    count = cur.fetchone()["c"]
    overflow = count - MAX_DRAWS_PER_CLASS
    if overflow > 0:
        db.execute(
            """
            DELETE FROM draws
            WHERE id IN (
                SELECT id FROM draws
                WHERE class_id = ?
                ORDER BY id ASC
                LIMIT ?
            )
            """,
            (class_id, overflow),
        )


def compute_team_count(mode, param, total):
    if mode == "size":
        return max(1, -(-total // param))  # ceil division
    return min(param, total)


def snake_distribute(items, team_count):
    """Schlangenverteilung: gleicht Summen eines Merkmals (z.B. Staerke) zwischen Teams aus."""
    teams = [[] for _ in range(team_count)]
    idx = 0
    direction = 1
    for item in items:
        teams[idx].append(item)
        idx += direction
        if idx == team_count:
            idx = team_count - 1
            direction = -1
        elif idx < 0:
            idx = 0
            direction = 1
    return teams


def round_robin_distribute(items, team_count, start_offset=0):
    teams = [[] for _ in range(team_count)]
    for i, item in enumerate(items):
        teams[(i + start_offset) % team_count].append(item)
    return teams


def names_only(teams):
    return [[s["name"] for s in team] for team in teams]


# Die shake_*-Funktionen liefern Teams als Listen von Schueler-Dicts (mit id),
# damit Regeln/Abwechslung danach per Tausch optimiert werden koennen.

def shake_random(students, team_count):
    shuffled = students[:]
    random.shuffle(shuffled)
    return round_robin_distribute(shuffled, team_count), None


def shake_strength(students, team_count):
    pool = students[:]
    random.shuffle(pool)  # Gleichstand zufaellig auflösen
    pool.sort(key=lambda s: s["strength"] if s["strength"] is not None else 3, reverse=True)
    return snake_distribute(pool, team_count), None


def shake_gender_mixed(students, team_count):
    groups = {}
    for s in students:
        key = s["gender"] if s["gender"] in ("m", "w", "d") else "unbekannt"
        groups.setdefault(key, []).append(s)

    teams = [[] for _ in range(team_count)]
    offset = 0
    for key, members in groups.items():
        random.shuffle(members)
        sub_teams = round_robin_distribute(members, team_count, start_offset=offset)
        for i in range(team_count):
            teams[i].extend(sub_teams[i])
        offset += len(members) % team_count
    return teams, None


def shake_gender_separate(students, mode, param):
    """Bildet reine Gruppen je Geschlecht.

    Achtung: `param` gilt PRO Geschlechtergruppe (mode=count: Teams je Gruppe,
    mode=size: Spieler pro Team). Bei count=3 und zwei Gruppen entstehen 6 Teams.
    """
    groups = {}
    for s in students:
        key = s["gender"] if s["gender"] in ("m", "w", "d") else "unbekannt"
        groups.setdefault(key, []).append(s)

    all_teams = []
    labels = []
    for key in sorted(groups.keys()):
        members = groups[key][:]
        random.shuffle(members)
        tc = compute_team_count(mode, param, len(members))
        sub_teams = round_robin_distribute(members, tc)
        label = GENDER_LABELS.get(key, "Ohne Angabe")
        for i, team in enumerate(sub_teams, start=1):
            all_teams.append(list(team))
            labels.append(f"{label} {i}" if len(sub_teams) > 1 else label)
    return all_teams, labels


# ---------- Regeln / Abwechslung ----------

def load_pairs(db, class_id):
    rows = db.execute(
        "SELECT id, a, b, kind FROM pairs WHERE class_id = ?", (class_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def recent_cooccurrence(db, class_id, students, limit=RECENT_DRAWS_FOR_VARIETY):
    """Zaehlt, wie oft zwei Personen in den letzten Auslosungen im selben Team waren."""
    ids_by_name = {}
    for s in students:
        ids_by_name.setdefault(s["name"], []).append(s["id"])
    rows = db.execute(
        "SELECT teams_json FROM draws WHERE class_id = ? ORDER BY id DESC LIMIT ?",
        (class_id, limit),
    ).fetchall()
    co = {}
    for r in rows:
        payload = json.loads(r["teams_json"])
        teams = payload.get("teams", []) if isinstance(payload, dict) else payload
        for team in teams:
            ids = []
            for nm in team:
                lst = ids_by_name.get(nm, [])
                if len(lst) == 1:  # doppelte Namen sind nicht eindeutig -> ignorieren
                    ids.append(lst[0])
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    key = (min(ids[i], ids[j]), max(ids[i], ids[j]))
                    co[key] = co.get(key, 0) + 1
    return co


def team_penalty(teams, rules, co):
    where = {}
    for ti, team in enumerate(teams):
        for s in team:
            where[s["id"]] = ti
    pen = 0
    for p in rules:
        ta, tb = where.get(p["a"]), where.get(p["b"])
        if ta is None or tb is None:
            continue
        same = (ta == tb)
        if p["kind"] == "apart" and same:
            pen += RULE_WEIGHT
        elif p["kind"] == "together" and not same:
            pen += RULE_WEIGHT
    if co:
        for team in teams:
            ids = sorted(s["id"] for s in team)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pen += co.get((ids[i], ids[j]), 0)
    return pen


def swap_key_fn(balance):
    """Nur Personen mit gleichem Merkmal duerfen beim Nachbessern getauscht werden,
    damit die gewaehlte Methode (Staerke/Geschlecht) erhalten bleibt."""
    if balance == "strength":
        return lambda s: s["strength"] if s["strength"] is not None else 3
    if balance in ("gender_mixed", "gender_separate"):
        return lambda s: s["gender"] if s["gender"] in ("m", "w", "d") else "unbekannt"
    return lambda s: None


def optimize_teams(teams, rules, co, key_fn, time_limit=OPTIMIZE_TIME_LIMIT):
    best = team_penalty(teams, rules, co)
    deadline = time.monotonic() + time_limit
    improved = True
    while improved and best > 0 and time.monotonic() < deadline:
        improved = False
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                if time.monotonic() > deadline:
                    return teams
                for x in range(len(teams[i])):
                    for y in range(len(teams[j])):
                        a, b = teams[i][x], teams[j][y]
                        if key_fn(a) != key_fn(b):
                            continue
                        teams[i][x], teams[j][y] = b, a
                        p = team_penalty(teams, rules, co)
                        if p < best:
                            best = p
                            improved = True
                        else:
                            teams[i][x], teams[j][y] = a, b
    return teams


def find_violations(where, rules, id_names):
    """where: Schueler-id -> Team-Index. Liefert alle nicht erfuellten Regeln."""
    out = []
    for p in rules:
        ta, tb = where.get(p["a"]), where.get(p["b"])
        if ta is None or tb is None:
            continue
        same = (ta == tb)
        if (p["kind"] == "apart" and same) or (p["kind"] == "together" and not same):
            out.append({"kind": p["kind"], "a": id_names.get(p["a"], "?"), "b": id_names.get(p["b"], "?")})
    return out


# ---------- Classes ----------

@app.route("/api/classes", methods=["GET"])
def list_classes():
    db = get_db()
    rows = db.execute(
        """
        SELECT c.id, c.name, c.created_at,
               (SELECT COUNT(*) FROM students s WHERE s.class_id = c.id) AS student_count
        FROM classes c
        ORDER BY c.name COLLATE NOCASE
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/classes", methods=["POST"])
def create_class():
    data = json_body()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name erforderlich"}), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO classes (name, created_at) VALUES (?, ?)",
            (name, now_iso()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Klasse existiert bereits"}), 409
    row = db.execute("SELECT id, name FROM classes WHERE name = ?", (name,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/classes/<int:class_id>", methods=["PUT"])
def rename_class(class_id):
    data = json_body()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name erforderlich"}), 400
    db = get_db()
    try:
        cur = db.execute("UPDATE classes SET name = ? WHERE id = ?", (name, class_id))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Name bereits vergeben"}), 409
    if cur.rowcount == 0:
        return jsonify({"error": "Klasse nicht gefunden"}), 404
    return jsonify({"ok": True})


@app.route("/api/classes/<int:class_id>", methods=["DELETE"])
def delete_class(class_id):
    db = get_db()
    count = db.execute("SELECT COUNT(*) AS c FROM classes").fetchone()["c"]
    if count <= 1:
        return jsonify({"error": "Mindestens eine Klasse muss bestehen bleiben"}), 400
    db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------- Students ----------

@app.route("/api/classes/<int:class_id>/students", methods=["GET"])
def list_students(class_id):
    db = get_db()
    rows = db.execute(
        "SELECT id, name, strength, gender FROM students WHERE class_id = ? ORDER BY id",
        (class_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


def _normalize_gender(value):
    if value in ("m", "w", "d"):
        return value
    return None


def _normalize_strength(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, v))


@app.route("/api/classes/<int:class_id>/students", methods=["POST"])
def add_students(class_id):
    data = json_body()
    raw_names = data.get("names")
    if raw_names is not None and not isinstance(raw_names, list):
        return jsonify({"error": "names muss eine Liste sein"}), 400
    entries = []
    if raw_names is not None:
        for item in raw_names:
            if isinstance(item, dict):
                nm = str(item.get("name") or "").strip()
                if nm:
                    entries.append(
                        (nm, _normalize_strength(item.get("strength")), _normalize_gender(item.get("gender")))
                    )
            else:
                nm = str(item).strip()
                if nm:
                    entries.append((nm, 3, None))
    else:
        single = str(data.get("name") or "").strip()
        if single:
            entries.append((single, _normalize_strength(data.get("strength")), _normalize_gender(data.get("gender"))))

    if not entries:
        return jsonify({"error": "Kein gültiger Name"}), 400
    db = get_db()
    if not db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone():
        return jsonify({"error": "Klasse nicht gefunden"}), 404
    db.executemany(
        "INSERT INTO students (class_id, name, strength, gender) VALUES (?, ?, ?, ?)",
        [(class_id, n, s, gd) for n, s, gd in entries],
    )
    db.commit()
    return jsonify({"added": len(entries)}), 201


@app.route("/api/students/<int:student_id>", methods=["PUT"])
def update_student(student_id):
    data = json_body()
    db = get_db()
    fields = []
    values = []
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Name darf nicht leer sein"}), 400
        fields.append("name = ?")
        values.append(name)
    if "strength" in data:
        fields.append("strength = ?")
        values.append(_normalize_strength(data.get("strength")))
    if "gender" in data:
        fields.append("gender = ?")
        values.append(_normalize_gender(data.get("gender")))
    if not fields:
        return jsonify({"error": "Keine Aenderung angegeben"}), 400
    values.append(student_id)
    cur = db.execute(f"UPDATE students SET {', '.join(fields)} WHERE id = ?", values)
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Eintrag nicht gefunden"}), 404
    return jsonify({"ok": True})


@app.route("/api/students/<int:student_id>", methods=["DELETE"])
def delete_student(student_id):
    db = get_db()
    db.execute("DELETE FROM students WHERE id = ?", (student_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/classes/<int:class_id>/students", methods=["DELETE"])
def clear_students(class_id):
    db = get_db()
    db.execute("DELETE FROM students WHERE class_id = ?", (class_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------- Regeln (Paare: getrennt / zusammen) ----------

@app.route("/api/classes/<int:class_id>/pairs", methods=["GET"])
def list_pairs(class_id):
    db = get_db()
    rows = db.execute(
        """
        SELECT p.id, p.kind, p.a, p.b, sa.name AS a_name, sb.name AS b_name
        FROM pairs p
        JOIN students sa ON sa.id = p.a
        JOIN students sb ON sb.id = p.b
        WHERE p.class_id = ?
        ORDER BY p.id
        """,
        (class_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/classes/<int:class_id>/pairs", methods=["POST"])
def add_pair(class_id):
    data = json_body()
    try:
        a = int(data.get("a"))
        b = int(data.get("b"))
    except (TypeError, ValueError):
        return jsonify({"error": "Zwei Personen angeben (a, b)"}), 400
    kind = data.get("kind", "apart")
    if kind not in PAIR_KINDS:
        return jsonify({"error": "kind muss 'apart' oder 'together' sein"}), 400
    if a == b:
        return jsonify({"error": "Bitte zwei verschiedene Personen wählen"}), 400
    a, b = min(a, b), max(a, b)
    db = get_db()
    found = db.execute(
        "SELECT COUNT(*) AS c FROM students WHERE class_id = ? AND id IN (?, ?)",
        (class_id, a, b),
    ).fetchone()["c"]
    if found != 2:
        return jsonify({"error": "Personen nicht in dieser Klasse gefunden"}), 404
    try:
        cur = db.execute(
            "INSERT INTO pairs (class_id, a, b, kind) VALUES (?, ?, ?, ?)",
            (class_id, a, b, kind),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Für dieses Paar existiert bereits eine Regel"}), 409
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/pairs/<int:pair_id>", methods=["DELETE"])
def delete_pair(pair_id):
    db = get_db()
    db.execute("DELETE FROM pairs WHERE id = ?", (pair_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------- Shake / Draws ----------

@app.route("/api/classes/<int:class_id>/shake", methods=["POST"])
def shake(class_id):
    data = json_body()
    mode = data.get("mode", "count")  # "count" or "size"
    if mode not in ("count", "size"):
        return jsonify({"error": "mode muss 'count' oder 'size' sein"}), 400
    balance = data.get("balance", "random")  # random | strength | gender_mixed | gender_separate
    try:
        param = int(data.get("param", 2))
    except (TypeError, ValueError):
        return jsonify({"error": "param muss eine Zahl sein"}), 400
    if param < 1:
        param = 1
    use_rules = data.get("use_rules", True) is not False   # Regeln standardmaessig beachten
    variety = data.get("variety") is True                   # Abwechslung nur auf Wunsch

    db = get_db()
    rows = db.execute(
        "SELECT id, name, strength, gender FROM students WHERE class_id = ?", (class_id,)
    ).fetchall()
    students = [dict(r) for r in rows]

    if len(students) < 2:
        return jsonify({"error": "Mindestens 2 Namen erforderlich"}), 400

    if balance == "strength":
        team_count = compute_team_count(mode, param, len(students))
        maker = lambda: shake_strength(students, team_count)
    elif balance == "gender_mixed":
        if not any(s["gender"] for s in students):
            return jsonify({"error": "Kein Geschlecht hinterlegt. Bitte bei Namen ergänzen."}), 400
        team_count = compute_team_count(mode, param, len(students))
        maker = lambda: shake_gender_mixed(students, team_count)
    elif balance == "gender_separate":
        if not any(s["gender"] for s in students):
            return jsonify({"error": "Kein Geschlecht hinterlegt. Bitte bei Namen ergänzen."}), 400
        maker = lambda: shake_gender_separate(students, mode, param)
    else:
        balance = "random"
        team_count = compute_team_count(mode, param, len(students))
        maker = lambda: shake_random(students, team_count)

    rules = load_pairs(db, class_id) if use_rules else []
    co = recent_cooccurrence(db, class_id, students) if variety else {}

    tries = CANDIDATES if (rules or co) else 1
    candidates = [maker() for _ in range(tries)]
    teams, labels = min(candidates, key=lambda c: team_penalty(c[0], rules, co))
    if rules or co:
        teams = optimize_teams(teams, rules, co, swap_key_fn(balance))

    where = {s["id"]: ti for ti, team in enumerate(teams) for s in team}
    id_names = {s["id"]: s["name"] for s in students}
    violations = find_violations(where, rules, id_names)
    team_names = names_only(teams)

    options = {"variety": variety, "rules": bool(rules)}
    created_at = now_iso()
    payload = {"teams": team_names, "options": options}
    if labels:
        payload["labels"] = labels
    cur = db.execute(
        "INSERT INTO draws (class_id, created_at, mode, param, teams_json, balance) VALUES (?, ?, ?, ?, ?, ?)",
        (class_id, created_at, mode, param, json.dumps(payload, ensure_ascii=False), balance),
    )
    draw_id = cur.lastrowid
    enforce_draw_limit(db, class_id)
    db.commit()

    result = {
        "id": draw_id,
        "teams": team_names,
        "created_at": created_at,
        "balance": balance,
        "options": options,
        "violations": violations,
    }
    if labels:
        result["labels"] = labels
    return jsonify(result)


@app.route("/api/draws/<int:draw_id>/swap", methods=["POST"])
def swap_draw(draw_id):
    """Tauscht zwei Personen innerhalb einer gespeicherten Auslosung.
    Body: {"from": [team_index, position], "to": [team_index, position]}"""
    data = json_body()
    db = get_db()
    row = db.execute(
        "SELECT id, class_id, teams_json FROM draws WHERE id = ?", (draw_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Auslosung nicht gefunden"}), 404
    payload = json.loads(row["teams_json"])
    if not isinstance(payload, dict):
        payload = {"teams": payload}
    teams = payload.get("teams", [])
    try:
        ta, pa = [int(x) for x in data.get("from", [])]
        tb, pb = [int(x) for x in data.get("to", [])]
    except (TypeError, ValueError):
        return jsonify({"error": "from/to müssen [Team, Position] sein"}), 400
    if not (0 <= ta < len(teams) and 0 <= tb < len(teams)
            and 0 <= pa < len(teams[ta]) and 0 <= pb < len(teams[tb])):
        return jsonify({"error": "Ungültige Position"}), 400

    teams[ta][pa], teams[tb][pb] = teams[tb][pb], teams[ta][pa]
    payload["teams"] = teams
    db.execute(
        "UPDATE draws SET teams_json = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), draw_id),
    )
    db.commit()

    class_id = row["class_id"]
    students = db.execute(
        "SELECT id, name FROM students WHERE class_id = ?", (class_id,)
    ).fetchall()
    name_ids = {}
    id_names = {}
    for s in students:
        name_ids.setdefault(s["name"], []).append(s["id"])
        id_names[s["id"]] = s["name"]
    where = {}
    for ti, team in enumerate(teams):
        for nm in team:
            ids = name_ids.get(nm, [])
            if len(ids) == 1:
                where[ids[0]] = ti
    violations = find_violations(where, load_pairs(db, class_id), id_names)
    return jsonify({
        "ok": True,
        "teams": teams,
        "labels": payload.get("labels"),
        "violations": violations,
    })


@app.route("/api/classes/<int:class_id>/history", methods=["GET"])
def history(class_id):
    db = get_db()
    rows = db.execute(
        """
        SELECT id, created_at, mode, param, teams_json, balance
        FROM draws
        WHERE class_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (class_id, MAX_DRAWS_PER_CLASS),
    ).fetchall()
    result = []
    for r in rows:
        payload = json.loads(r["teams_json"])
        entry = {
            "id": r["id"],
            "created_at": r["created_at"],
            "mode": r["mode"],
            "param": r["param"],
            "balance": r["balance"] or "random",
            "teams": payload.get("teams", payload) if isinstance(payload, dict) else payload,
            "options": payload.get("options", {}) if isinstance(payload, dict) else {},
        }
        if isinstance(payload, dict) and payload.get("labels"):
            entry["labels"] = payload["labels"]
        result.append(entry)
    return jsonify(result)


@app.route("/api/classes/<int:class_id>/history", methods=["DELETE"])
def clear_history(class_id):
    db = get_db()
    db.execute("DELETE FROM draws WHERE class_id = ?", (class_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------- Frontend (fallback, normally served by nginx) ----------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
