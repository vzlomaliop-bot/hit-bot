import os
os.makedirs("/app/data", exist_ok=True)
os.chdir("/app/data")

import sqlite3
from datetime import datetime, date

# Максимальный перерыв между тренировками (в днях), при котором стрик не сгорает.
# 4 = до 3 полных дней отдыха между тренировками (например, Пт -> Пн/Вт).
MAX_STREAK_GAP_DAYS = 4


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


def save_set(user_id, program, day_name, exercise, set_num, weight, reps, rpe=0, date_str=None):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    when = date_str if date_str else datetime.now().isoformat()
    c.execute(
        "INSERT INTO sets (user_id, date, program, day_name, exercise, set_num, weight, reps, rpe) VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, when, program, day_name, exercise, set_num, weight, reps, rpe),
    )
    conn.commit()
    conn.close()


def get_last(user_id, program, exercise):
    """
    Возвращает все подходы с последней тренировки ДО сегодняшнего дня
    по данному упражнению в порядке от 1-го подхода к последнему.
    """
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    today_str = date.today().isoformat()
    short = exercise.split("|")[-1].strip() if "|" in exercise else exercise.strip()
    pattern_with_day = "%|" + short

    # 1. Находим последнюю дату (до сегодня), когда выполнялось это упражнение
    c.execute(
        """SELECT substr(date, 1, 10) as d
           FROM sets
           WHERE user_id=?
             AND (exercise = ? OR exercise LIKE ? OR exercise = ?)
             AND substr(date, 1, 10) < ?
           ORDER BY d DESC
           LIMIT 1""",
        (user_id, exercise, pattern_with_day, short, today_str),
    )
    row = c.fetchone()
    if not row or not row[0]:
        conn.close()
        return []

    last_date_prefix = row[0] + "%"

    # 2. Забираем все подходы за ту дату в хронологическом порядке
    c.execute(
        """SELECT weight, reps, set_num
           FROM sets
           WHERE user_id=?
             AND (exercise = ? OR exercise LIKE ? OR exercise = ?)
             AND date LIKE ?
           ORDER BY set_num ASC, id ASC""",
        (user_id, exercise, pattern_with_day, short, last_date_prefix),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_user_exercises(user_id):
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
    """Ищет записи и с '|' в ключе, и без него — на случай старых записей."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    pattern_with_day = "%|" + short_name
    c.execute("""SELECT id, date, weight, reps, set_num, exercise, day_name
                 FROM sets
                 WHERE user_id=?
                   AND (exercise LIKE ? OR exercise = ?)
                 ORDER BY id ASC""",
              (user_id, pattern_with_day, short_name))
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


def update_set_date(user_id, set_id, date_str):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("UPDATE sets SET date=? WHERE id=? AND user_id=?",
              (date_str, set_id, user_id))
    conn.commit()
    conn.close()


def move_set(user_id, set_id, new_exercise_short):
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
    if day_name:
        new_key = day_name + "|" + new_exercise_short
    else:
        new_key = new_exercise_short
    c.execute("UPDATE sets SET exercise=? WHERE id=? AND user_id=?", (new_key, set_id, user_id))
    conn.commit()
    conn.close()
    return True


def get_tonnage_by_exercise(user_id, day_name=None, date_str=None):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    target_date = (date_str or date.today().isoformat())[:10] + "%"
    if day_name:
        c.execute(
            """SELECT exercise, SUM(weight*reps), COUNT(*)
               FROM sets
               WHERE user_id=? AND day_name=? AND date LIKE ?
               GROUP BY exercise""",
            (user_id, day_name, target_date),
        )
    else:
        c.execute(
            """SELECT exercise, SUM(weight*reps), COUNT(*)
               FROM sets
               WHERE user_id=? AND date LIKE ?
               GROUP BY exercise""",
            (user_id, target_date),
        )
    rows = c.fetchall()
    conn.close()
    return rows


def get_total_tonnage(user_id, day_name=None, date_str=None):
    """Считает суммарный тоннаж всех подходов за указанную дату (по умолчанию — за сегодня)."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    target_date = (date_str or date.today().isoformat())[:10] + "%"
    if day_name:
        c.execute(
            "SELECT SUM(weight*reps) FROM sets WHERE user_id=? AND day_name=? AND date LIKE ?",
            (user_id, day_name, target_date),
        )
    else:
        c.execute(
            "SELECT SUM(weight*reps) FROM sets WHERE user_id=? AND date LIKE ?",
            (user_id, target_date),
        )
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return 0


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
    if row:
        return row[0]
    return None


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
        streak = streak or 0
        try:
            last_date = datetime.fromisoformat(last_date_str[:10]).date() if last_date_str else None
        except Exception:
            last_date = None

        if last_date is None:
            streak = 1
        else:
            delta = (today - last_date).days
            if delta == 0:
                streak = max(streak, 1)
            elif 1 <= delta <= MAX_STREAK_GAP_DAYS:
                streak += 1
            else:
                streak = 1

        c.execute(
            "UPDATE user_stats SET streak=?, last_workout_date=? WHERE user_id=?",
            (streak, today.isoformat(), user_id),
        )
    else:
        streak = 1
        c.execute(
            "INSERT INTO user_stats (user_id, streak, last_workout_date) VALUES (?,?,?)",
            (user_id, streak, today.isoformat()),
        )
    conn.commit()
    conn.close()
    return streak


def get_streak(user_id):
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    c.execute("SELECT streak, last_workout_date FROM user_stats WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0
    streak, last_date_str = row
    if not streak or not last_date_str:
        return 0
    try:
        last_date = datetime.fromisoformat(last_date_str[:10]).date()
        if (date.today() - last_date).days > MAX_STREAK_GAP_DAYS:
            return 0
    except Exception:
        return 0
    return streak


def count_sets_on_date(user_id, exercise_key, date_str):
    """Считает подходы за день. Работает и с ключом '|', и без него."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    pattern = date_str + "%"
    short = exercise_key.split("|")[-1] if "|" in exercise_key else exercise_key
    pattern_with_day = "%|" + short
    c.execute("""SELECT COUNT(*) FROM sets
                 WHERE user_id=?
                   AND (exercise = ? OR exercise LIKE ?)
                   AND date LIKE ?""",
              (user_id, exercise_key, pattern_with_day, pattern))
    n = c.fetchone()[0]
    conn.close()
    return n


def merge_exercises(user_id, from_name, to_name):
    """Переименовывает все записи с from_name в to_name, сохраняя day_name."""
    conn = sqlite3.connect("workouts.db")
    c = conn.cursor()
    pattern = "%|" + from_name
    c.execute("""SELECT id, exercise FROM sets
                 WHERE user_id=? AND (exercise LIKE ? OR exercise = ?)""",
              (user_id, pattern, from_name))
    rows = c.fetchall()
    for set_id, old_key in rows:
        if "|" in old_key:
            day_name = old_key.split("|", 1)[0]
            new_key = day_name + "|" + to_name
        else:
            new_key = to_name
        c.execute("UPDATE sets SET exercise=? WHERE id=?", (new_key, set_id))
    conn.commit()
    conn.close()
    return len(rows)
