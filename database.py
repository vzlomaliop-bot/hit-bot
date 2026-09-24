import os
os.makedirs("/app/data", exist_ok=True)
os.chdir("/app/data")

import sqlite3
from datetime import datetime, date


def init_db():
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        date TEXT,
        program TEXT,
        day_name TEXT,
        exercise TEXT,
        set_num INTEGER,
        weight REAL,
        reps INTEGER,
        rpe INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_program (
        user_id INTEGER PRIMARY KEY,
        program TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_stats (
        user_id INTEGER PRIMARY KEY,
        streak INTEGER DEFAULT 0,
        last_workout_date TEXT
    )""")
    conn.commit()
    conn.close()


def save_set(user_id, program, day_name, exercise, set_num, weight, reps, rpe=0):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO sets (user_id, date, program, day_name, exercise, set_num, weight, reps, rpe) VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, datetime.now().isoformat(), program, day_name, exercise, set_num, weight, reps, rpe),
    )
    conn.commit()
    conn.close()


def get_last(user_id, program, exercise):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute(
        "SELECT weight, reps, set_num FROM sets WHERE user_id=? AND program=? AND exercise=? ORDER BY id DESC LIMIT 6",
        (user_id, program, exercise),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_user_exercises(user_id):
    """Возвращает уникальные короткие имена упражнений (без дня)."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("SELECT DISTINCT exercise FROM sets WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    names = set()
    for r in rows:
        if not r[0]:
            continue
        short = r[0].split("|")[-1].strip()
        if short and short.lower() not in ("none", "null", "nan", "0"):
            names.add(short)
    return sorted(names)


def get_exercise_history(user_id, short_name):
    """Возвращает всю историю по короткому имени, агрегируя все дни."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    pattern = f"%|{short_name}"
    c.execute("""SELECT id, date, weight, reps, set_num, exercise, day_name
                 FROM sets WHERE user_id=? AND exercise LIKE ?
                 ORDER BY id ASC""", (user_id, pattern))
    rows = c.fetchall()
    conn.close()
    return rows


def delete_set(user_id, set_id):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("DELETE FROM sets WHERE id=? AND user_id=?", (set_id, user_id))
    conn.commit()
    conn.close()


def update_set(user_id, set_id, weight, reps):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("UPDATE sets SET weight=?, reps=? WHERE id=? AND user_id=?",
              (weight, reps, set_id, user_id))
    conn.commit()
    conn.close()


def move_set(user_id, set_id, new_exercise_short):
    """Переносит запись в другое упражнение. Сохраняет day_name."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("SELECT exercise FROM sets WHERE id=? AND user_id=?", (set_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    old_key = row[0] or ""
    if "|" in old_key:
        day_name = old_key.split("|", 1)[0]
    else:
        day_name = ""
    new_key = f"{day_name}|{new_exercise_short}" if day_name else new_exercise_short
    c.execute("UPDATE sets SET exercise=? WHERE id=? AND user_id=?", (new_key, set_id, user_id))
    conn.commit()
    conn.close()
    return True


def get_tonnage_by_exercise(user_id, day_name=None):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    if day_name:
        c.execute("SELECT exercise, SUM(weight*reps), COUNT(*) FROM sets WHERE user_id=? AND day_name=? AND set_num=1 GROUP BY exercise", (user_id, day_name))
    else:
        c.execute("SELECT exercise, SUM(weight*reps), COUNT(*) FROM sets WHERE user_id=? AND set_num=1 GROUP BY exercise", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_total_tonnage(user_id, day_name=None):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    if day_name:
        c.execute("SELECT SUM(weight*reps) FROM sets WHERE user_id=? AND day_name=? AND set_num=1", (user_id, day_name))
    else:
        c.execute("SELECT SUM(weight*reps) FROM sets WHERE user_id=? AND set_num=1", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else 0


def check_plateau(user_id, exercise, weeks=3):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("""
        SELECT date, MAX(weight) as max_w FROM sets
        WHERE user_id=? AND exercise=? AND set_num=1
        GROUP BY date(date)
        ORDER BY date DESC LIMIT ?
    """, (user_id, exercise, weeks))
    rows = c.fetchall()
    conn.close()
    if len(rows) < 2:
        return False
    weights = [r[1] for r in rows]
    return max(weights) - min(weights) < 0.1


def get_user_program(user_id):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("SELECT program FROM user_program WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def set_user_program(user_id, program):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_program (user_id, program) VALUES (?,?)", (user_id, program))
    conn.commit()
    conn.close()


def update_streak(user_id):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("SELECT streak, last_workout_date FROM user_stats WHERE user_id=?", (user_id,))
    row = c.fetchone()
    today = date.today()
    if row:
        streak, last_date_str = row
        try:
            last_date = datetime.fromisoformat(last_date_str).date() if last_date_str else today
        except Exception:
            last_date = today
        delta = (today - last_date).days
        if delta == 1:
            streak += 1
        elif delta > 1:
            streak = 1
        c.execute("UPDATE user_stats SET streak=?, last_workout_date=? WHERE user_id=?",
                  (streak, today.isoformat(), user_id))
    else:
        streak = 1
        c.execute("INSERT INTO user_stats (user_id, streak, last_workout_date) VALUES (?,?,?)",
                  (user_id, streak, today.isoformat()))
    conn.commit()
    conn.close()
    return streak


def get_streak(user_id):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("SELECT streak FROM user_stats WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0
