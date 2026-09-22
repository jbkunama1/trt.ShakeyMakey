import sqlite3
import json
import random
import os
from datetime import datetime
from flask import Flask, request, jsonify, g, send_from_directory

DB_PATH = os.environ.get("TEAMSHAKE_DB", "/data/teamshake.db")
MAX_DRAWS_PER_CLASS = int(os.environ.get("TEAMSHAKE_MAX_DRAWS", "50"))

app = Flask(__name__, static_folder="static", static_url_path="")


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db


@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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

        CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
        CREATE INDEX IF NOT EXISTS idx_draws_class ON draws(class_id, id);
        """
    )
    conn.commit()

    cur = conn.execute("SELECT COUNT(*) FROM classes")
    if cur.fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO classes (name, created_at) VALUES (?, ?)",
            ("Meine Klasse", datetime.utcnow().isoformat()),
        )
        conn.commit()
    conn.close()


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
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name erforderlich"}), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO classes (name, created_at) VALUES (?, ?)",
            (name, datetime.utcnow().isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Klasse existiert bereits"}), 409
    row = db.execute("SELECT id, name FROM classes WHERE name = ?", (name,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/classes/<int:class_id>", methods=["PUT"])
def rename_class(class_id):
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name erforderlich"}), 400
    db = get_db()
    try:
        db.execute("UPDATE classes SET name = ? WHERE id = ?", (name, class_id))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Name bereits vergeben"}), 409
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
        "SELECT id, name FROM students WHERE class_id = ? ORDER BY id", (class_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/classes/<int:class_id>/students", methods=["POST"])
def add_students(class_id):
    data = request.get_json(force=True)
    names = data.get("names")
    if names is None:
        single = (data.get("name") or "").strip()
        names = [single] if single else []
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return jsonify({"error": "Kein gültiger Name"}), 400
    db = get_db()
    db.executemany(
        "INSERT INTO students (class_id, name) VALUES (?, ?)",
        [(class_id, n) for n in names],
    )
    db.commit()
    return jsonify({"added": len(names)}), 201


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


# ---------- Shake / Draws ----------

@app.route("/api/classes/<int:class_id>/shake", methods=["POST"])
def shake(class_id):
    data = request.get_json(force=True)
    mode = data.get("mode", "count")  # "count" or "size"
    param = int(data.get("param", 2))
    if param < 1:
        param = 1

    db = get_db()
    students = db.execute(
        "SELECT name FROM students WHERE class_id = ?", (class_id,)
    ).fetchall()
    names = [r["name"] for r in students]

    if len(names) < 2:
        return jsonify({"error": "Mindestens 2 Namen erforderlich"}), 400

    shuffled = names[:]
    random.shuffle(shuffled)

    if mode == "size":
        team_count = max(1, -(-len(shuffled) // param))  # ceil division
    else:
        team_count = min(param, len(shuffled))

    teams = [[] for _ in range(team_count)]
    for idx, name in enumerate(shuffled):
        teams[idx % team_count].append(name)

    created_at = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO draws (class_id, created_at, mode, param, teams_json) VALUES (?, ?, ?, ?, ?)",
        (class_id, created_at, mode, param, json.dumps(teams, ensure_ascii=False)),
    )
    enforce_draw_limit(db, class_id)
    db.commit()

    return jsonify({"teams": teams, "created_at": created_at})


@app.route("/api/classes/<int:class_id>/history", methods=["GET"])
def history(class_id):
    db = get_db()
    rows = db.execute(
        """
        SELECT id, created_at, mode, param, teams_json
        FROM draws
        WHERE class_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (class_id, MAX_DRAWS_PER_CLASS),
    ).fetchall()
    result = []
    for r in rows:
        result.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "mode": r["mode"],
                "param": r["param"],
                "teams": json.loads(r["teams_json"]),
            }
        )
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
