import asyncio
import os
import sqlite3
from collections import OrderedDict
from datetime import date, datetime

import database
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from program import PROGRAMS, LEVELS
from database import (
    init_db, save_set, get_last,
    get_user_exercises, get_exercise_history,
    delete_set, update_set, update_set_date, move_set, merge_exercises,
    get_user_program, set_user_program,
    update_streak, get_streak,
    get_total_tonnage, count_sets_on_date,
)

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()


# ═══════════════════════════════════════════
# ОСОБЫЕ ПОЛЬЗОВАТЕЛИ И СКРЫТЫЕ ПРОГРАММЫ
# ═══════════════════════════════════════════

USER_HIDDEN_PROGRAMS = {
    "papirosov": ["papirosovv", "lactucoideae"],
    "veronika": ["veronikaszb"],
}


def can_access_program(prog_key, user):
    if prog_key not in USER_HIDDEN_PROGRAMS:
        return True
    username = (user.username or "").lower()
    allowed = [u.lower() for u in USER_HIDDEN_PROGRAMS[prog_key]]
    return username in allowed


def visible_programs_for_level(level_key, user):
    out = {}
    for k, v in PROGRAMS.items():
        if v.get("level") != level_key:
            continue
        if not can_access_program(k, user):
            continue
        out[k] = v
    return out


def get_user_hidden_programs(user):
    res = []
    for prog_key in USER_HIDDEN_PROGRAMS:
        if prog_key in PROGRAMS and can_access_program(prog_key, user):
            res.append(prog_key)
    return res


# ═══════════════════════════════════════════
# FSM
# ═══════════════════════════════════════════

class Workout(StatesGroup):
    entering_set = State()


class EditFlow(StatesGroup):
    entering_new = State()
    entering_date = State()


class AddRecord(StatesGroup):
    choosing_set_type = State()
    entering_date = State()
    entering_value = State()


class MergeFlow(StatesGroup):
    choosing_from = State()
    choosing_to = State()


# ═══════════════════════════════════════════
# РАБОТА С БАЗОЙ И УПРАЖНЕНИЯМИ
# ═══════════════════════════════════════════

def _get_db_path():
    for attr in ("DB_PATH", "DB_NAME", "DB_FILE", "DATABASE"):
        if hasattr(database, attr):
            val = getattr(database, attr)
            if isinstance(val, str) and val:
                return val
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        candidate = os.path.join(data_dir, "workouts.db")
        if os.path.exists(candidate):
            return candidate
    return "workouts.db"


def _program_exercises_ordered(prog_key):
    """Возвращает список упражнений текущей программы в порядке дней."""
    if not prog_key or prog_key not in PROGRAMS:
        return []
    result = []
    for day in PROGRAMS[prog_key]["days"]:
        for ex in day["exercises"]:
            name = ex.get("name")
            if name and name not in result:
                result.append(name)
    return result


def _unused_exercises(user_id):
    """Возвращает список упражнений из БД, которых нет в активной программе."""
    prog_key = get_user_program(user_id)
    active = set(_program_exercises_ordered(prog_key))
    db_names = set(get_user_exercises(user_id))
    return sorted(db_names - active)


def _all_exercises(user_id):
    """Сначала упражнения текущей программы по порядку, затем остальные из базы."""
    prog_key = get_user_program(user_id)
    ordered = _program_exercises_ordered(prog_key)
    seen = set(ordered)
    extras = []
    for n in get_user_exercises(user_id):
        if n not in seen:
            extras.append(n)
            seen.add(n)
    return ordered + sorted(extras)


def _find_day_for_exercise(prog_key, ex_name):
    if not prog_key or prog_key not in PROGRAMS:
        return ""
    for day in PROGRAMS[prog_key]["days"]:
        for ex in day["exercises"]:
            if ex.get("name") == ex_name:
                return day["name"]
    return ""


def _find_exercise_config(prog_key, ex_name):
    if not prog_key or prog_key not in PROGRAMS:
        return None
    for day in PROGRAMS[prog_key]["days"]:
        for ex in day["exercises"]:
            if ex.get("name") == ex_name:
                return ex
    return None


def _update_set_number_in_db(user_id, set_id, new_set_num):
    """Меняет номер подхода (1 = Топ-сет, 2 = Бэк-офф) в таблице sets."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("PRAGMA table_info(sets)")
    cols = [info[1] for info in c.fetchall()]
    set_col = None
    for candidate in ("set_num", "set_number", "set_idx", "st"):
        if candidate in cols:
            set_col = candidate
            break
    if set_col:
        c.execute(
            f"UPDATE sets SET {set_col}=? WHERE id=? AND user_id=?",
            (new_set_num, set_id, user_id),
        )
        conn.commit()
    conn.close()


def _delete_exercise_records(user_id, ex_name):
    """Удаляет все записи конкретного упражнения у пользователя."""
    rows = get_exercise_history(user_id, ex_name)
    count = 0
    for r in rows:
        delete_set(user_id, r[0])
        count += 1
    db_path = _get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            "DELETE FROM sets WHERE user_id=? AND (exercise=? OR exercise LIKE ?)",
            (user_id, ex_name, f"%|{ex_name}"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return count


def _delete_all_user_records(user_id):
    """Полностью очищает историю подходов пользователя."""
    db_path = _get_db_path()
    deleted = 0
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM sets WHERE user_id=?", (user_id,))
        deleted = c.fetchone()[0]
        c.execute("DELETE FROM sets WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
    except Exception:
        for name in get_user_exercises(user_id):
            deleted += _delete_exercise_records(user_id, name)
    return deleted


def _set_type_label(sn, idx_in_day=0, has_details=True, total_sets=2):
    """Возвращает красивую плашку Топ-сет / Бэк-офф."""
    if not has_details and total_sets > 2:
        return f"Сет #{sn or (idx_in_day + 1)}"
    effective = sn if sn in (1, 2) else (1 if idx_in_day == 0 else 2)
    if effective == 1 or total_sets == 1:
        return "🔥 Топ-сет"
    return "⚡️ Бэк-офф"


def _kb_back(cb, text="⬅️ Назад"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=cb)]
    ])


def levels_kb(user):
    buttons = []
    for key, info in LEVELS.items():
        buttons.append([InlineKeyboardButton(
            text=info["title"],
            callback_data=f"level_{key}",
        )])
    for prog_key in get_user_hidden_programs(user):
        btn_text = "🔥 HIT (для тебя)" if prog_key == "papirosov" else PROGRAMS[prog_key]["title"]
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"prog_{prog_key}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def programs_of_level_kb(level_key, user):
    progs = visible_programs_for_level(level_key, user)
    buttons = []
    for k, v in progs.items():
        buttons.append([InlineKeyboardButton(
            text=v["title"],
            callback_data=f"prog_{k}",
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ К стилям", callback_data="change_prog")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def days_kb(program_key):
    p = PROGRAMS[program_key]
    rows = [[InlineKeyboardButton(text=d["name"], callback_data=f"day_{i}")]
            for i, d in enumerate(p["days"])]
    rows.append([InlineKeyboardButton(text="📊 Прогресс", callback_data="progress")])
    rows.append([InlineKeyboardButton(text="⬅️ К программам", callback_data="change_prog")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_date(text):
    text = text.strip().lower()
    if text in ("сегодня", "today", "с"):
        return date.today().isoformat()
    if text in ("вчера", "вч", "yesterday"):
        from datetime import timedelta
        return (date.today() - timedelta(days=1)).isoformat()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            return d.isoformat()
        except ValueError:
            pass
    for fmt in ("%d.%m", "%d/%m", "%d-%m"):
        try:
            d = datetime.strptime(text, fmt).date()
            return d.replace(year=date.today().year).isoformat()
        except ValueError:
            pass
    return None


def _format_last_hint(last):
    if not last:
        return "🕐 <i>Первый раз — начни с комфортного веса</i>"
    parts = []
    for w, r, _ in last[:3]:
        parts.append(f"<code>{w}×{r}</code>")
    return "📌 Прошлый раз: " + " · ".join(parts)


def _format_specific_set_hint(user_id, ex_name, set_idx, short_label):
    """Ищет в истории именно конкретный подход (1-й = Топ-сет, 2-й = Бэк-офф)."""
    rows = get_exercise_history(user_id, ex_name)
    if not rows:
        return f"🕐 <i>Прошлый подход ({short_label}): записей ещё нет — выбери рабочий вес</i>"

    target_sn = set_idx + 1
    matched = []
    for row in rows:
        try:
            _, d, w, r, sn, _, _ = row[:7]
            if sn == target_sn:
                matched.append((d, w, r))
        except (ValueError, TypeError):
            continue

    if not matched:
        by_date = OrderedDict()
        for row in rows:
            try:
                _, d, w, r, sn, _, _ = row[:7]
                dkey = (d or "")[:10]
                by_date.setdefault(dkey, []).append((dkey, w, r))
            except (ValueError, TypeError):
                continue
        for dkey, dsets in by_date.items():
            if len(dsets) > set_idx:
                matched.append(dsets[set_idx])

    if not matched:
        return f"🕐 <i>Прошлый подход ({short_label}): ещё не выполнялся</i>"

    last_d, last_w, last_r = matched[-1]
    if len(matched) >= 2:
        prev_w, prev_r = matched[-2][1], matched[-2][2]
        return (
            f"📌 Прошлый подход (<b>{short_label}</b>): <code>{last_w} кг × {last_r}</code> "
            f"<i>(ранее: {prev_w}×{prev_r})</i>"
        )
    return f"📌 Прошлый подход (<b>{short_label}</b>): <code>{last_w} кг × {last_r}</code>"


# ═══════════════════════════════════════════
# КОМАНДЫ (СТАРТ, DEBUG, FIXNONE, EXPORT)
# ═══════════════════════════════════════════

@dp.message(Command("start", ignore_case=True))
async def start(msg: Message, state: FSMContext):
    await state.clear()
    name = msg.from_user.first_name or "друг"
    saved = get_user_program(msg.from_user.id)

    if saved and saved in PROGRAMS and can_access_program(saved, msg.from_user):
        p = PROGRAMS[saved]
        streak = get_streak(msg.from_user.id)
        header = f"💪 <b>Привет, {name}!</b>\n\n"
        card = f"┌─────────────────────\n│ 🎯 <b>{p['title']}</b>\n"
        if streak > 1:
            card += f"│ 🔥 Стрик: <b>{streak}</b> тренировок\n"
        card += f"└─────────────────────"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Продолжить тренировку", callback_data=f"prog_{saved}")],
            [InlineKeyboardButton(text="📊 Мой прогресс", callback_data="progress")],
            [InlineKeyboardButton(text="🔄 Сменить программу", callback_data="change_prog")],
        ])
        await msg.answer(header + card, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(
            f"👋 <b>Привет, {name}!</b>\n\n"
            f"Это бот для отслеживания тренировок.\n\n"
            f"<b>Выбери стиль тренировок:</b>",
            reply_markup=levels_kb(msg.from_user),
            parse_mode="HTML",
        )


@dp.message(Command("debug", ignore_case=True))
async def debug_cmd(msg: Message):
    conn = sqlite3.connect(_get_db_path())
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sets WHERE user_id=?", (msg.from_user.id,))
    total = c.fetchone()[0]
    c.execute("SELECT DISTINCT exercise FROM sets WHERE user_id=?", (msg.from_user.id,))
    rows = c.fetchall()
    conn.close()
    text = f"📊 <b>Debug</b>\n\nВсего записей: <b>{total}</b>\n"
    text += f"Активная: {get_user_program(msg.from_user.id)}\n\n"
    text += f"<b>Уникальных exercise ({len(rows)}):</b>\n"
    for r in rows[:40]:
        text += f"• <code>{r[0]}</code>\n"
    await msg.answer(text[:3900], parse_mode="HTML")


@dp.message(Command("fixnone", ignore_case=True))
async def fixnone_cmd(msg: Message):
    conn = sqlite3.connect(_get_db_path())
    c = conn.cursor()
    c.execute("""SELECT id, date, weight, reps, exercise, day_name
                 FROM sets
                 WHERE user_id=?
                   AND (exercise LIKE '%|None' OR exercise LIKE '%|none')
                 ORDER BY id ASC""", (msg.from_user.id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await msg.answer("✅ Записей с None нет — всё в порядке.")
        return

    text = f"🔧 <b>Найдено {len(rows)} записей с «None»</b>\n\n"
    for r in rows[:20]:
        set_id, d, w, rp, ex, day = r
        text += f"<code>#{set_id}</code> {d[:10]} — {w}кг × {rp} (день: {day})\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔧 Исправить по одной", callback_data="fixnone_start")],
    ])
    await msg.answer(text[:3900], reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "fixnone_start")
async def fixnone_start(call: CallbackQuery, state: FSMContext):
    conn = sqlite3.connect(_get_db_path())
    c = conn.cursor()
    c.execute("""SELECT id, date, weight, reps, exercise, day_name
                 FROM sets
                 WHERE user_id=?
                   AND (exercise LIKE '%|None' OR exercise LIKE '%|none')
                 ORDER BY id ASC""", (call.from_user.id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        await call.message.edit_text("✅ Все записи исправлены.")
        return
    set_id, d, w, rp, ex, day = rows[0]
    names = _all_exercises(call.from_user.id)
    await state.update_data(fix_set_id=set_id, fix_targets=names)
    buttons = [[InlineKeyboardButton(text=n, callback_data=f"fixto_{i}")]
               for i, n in enumerate(names)]
    await call.message.edit_text(
        f"🔧 <b>Исправление записи #{set_id}</b>\n\n"
        f"📅 {d[:10]}\n🏋️ {w} кг × {rp} повторов\nДень: {day}\n\n"
        f"К какому упражнению отнести эту запись?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("fixto_"))
async def fixnone_apply(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_", 1)[1])
    data = await state.get_data()
    set_id = data.get("fix_set_id")
    targets = data.get("fix_targets") or []
    if not set_id or idx >= len(targets):
        await call.answer("Ошибка, начни заново", show_alert=True)
        return
    target_name = targets[idx]
    move_set(call.from_user.id, set_id, target_name)
    await state.clear()
    await call.message.edit_text(
        f"✅ Запись #{set_id} перенесена в <b>{target_name}</b>\n\n"
        f"Проверь, остались ли ещё — /fixnone.",
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════
# ЭКСПОРТ ПОЛНОЙ ИСТОРИИ
# ═══════════════════════════════════════════

@dp.message(Command("export", ignore_case=True))
async def export_cmd(msg: Message):
    prog_key = get_user_program(msg.from_user.id)
    names = _all_exercises(msg.from_user.id)
    if not names:
        await msg.answer("В базе пока нет записей.")
        return

    messages = []
    current_chunk = "📊 <b>Полная история тренировок</b>\n"
    has_any = False

    for name in names:
        rows = get_exercise_history(msg.from_user.id, name)
        if not rows:
            continue
        has_any = True
        ex_cfg = _find_exercise_config(prog_key, name)
        has_details = bool(ex_cfg and ex_cfg.get("set_details"))
        total_sets = ex_cfg["sets"] if ex_cfg else 2

        by_date = OrderedDict()
        for row in rows:
            try:
                _, d, w, r, sn, _, _ = row[:7]
                dkey = str(d)[:10]
                by_date.setdefault(dkey, []).append((sn, w, r))
            except (ValueError, TypeError):
                continue

        block = f"\n🔹 <b>{name}</b>\n"
        for d, sets in by_date.items():
            if has_details or prog_key == "papirosov":
                parts = []
                for idx_s, (sn, w, r) in enumerate(sets):
                    lbl = "Топ" if (sn == 1 or (sn not in (1, 2) and idx_s == 0) or total_sets == 1) else "Бэк"
                    parts.append(f"{lbl}: {w}×{r}")
                sets_str = " · ".join(parts)
            else:
                sets_str = " · ".join(f"{w}×{r}" for _, w, r in sets)
            block += f"<code>{d}</code>: {sets_str}\n"

        if len(current_chunk) + len(block) > 3500:
            messages.append(current_chunk)
            current_chunk = block
        else:
            current_chunk += block

    if not has_any:
        await msg.answer("Нет подходящих записей для экспорта.")
        return

    if current_chunk:
        messages.append(current_chunk)

    for chunk in messages:
        await msg.answer(chunk, parse_mode="HTML")


# ═══════════════════════════════════════════
# ВЫБОР УРОВНЯ И ПРОГРАММЫ
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "change_prog")
async def change_prog(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "🏋️ <b>Выбери стиль тренировок</b>\n\n"
        "Каждый стиль — под свой опыт и объём нагрузки:",
        reply_markup=levels_kb(call.from_user),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("level_"))
async def choose_level(call: CallbackQuery, state: FSMContext):
    level_key = call.data.split("_", 1)[1]
    if level_key not in LEVELS:
        await call.answer("Уровень не найден")
        return
    info = LEVELS[level_key]
    progs = visible_programs_for_level(level_key, call.from_user)
    if not progs:
        await call.answer("В этом стиле пока нет программ", show_alert=True)
        return
    text = (
        f"{info['title']}\n\n"
        f"{info['note']}\n\n"
        f"<b>Выбери программу ({len(progs)}):</b>"
    )
    await call.message.edit_text(
        text,
        reply_markup=programs_of_level_kb(level_key, call.from_user),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("prog_"))
async def choose_program(call: CallbackQuery, state: FSMContext):
    key = call.data.split("_", 1)[1]
    if key not in PROGRAMS:
        await call.answer("Программа не найдена")
        return
    if not can_access_program(key, call.from_user):
        await call.answer("⛔ Программа недоступна", show_alert=True)
        return
    set_user_program(call.from_user.id, key)
    p = PROGRAMS[key]
    text = (
        f"┌─────────────────────\n"
        f"│ {p['title']}\n"
        f"└─────────────────────\n\n"
        f"📝 {p['note']}\n\n"
        f"🗓 <b>Выбери тренировочный день:</b>"
    )
    await call.message.edit_text(text, reply_markup=days_kb(key), parse_mode="HTML")


@dp.callback_query(F.data.startswith("day_"))
async def choose_day(call: CallbackQuery, state: FSMContext):
    saved = get_user_program(call.from_user.id)
    if not saved:
        await call.answer("Сначала выбери программу")
        return
    day_idx = int(call.data.split("_")[1])
    p = PROGRAMS[saved]
    if day_idx >= len(p["days"]):
        await call.answer("День не найден")
        return
    day = p["days"][day_idx]
    lines = [
        f"┌─────────────────────",
        f"│ 🗓 <b>{day['name']}</b>",
        f"└─────────────────────",
        "",
    ]
    for i, ex in enumerate(day["exercises"], 1):
        if ex.get("set_details"):
            sub_lines = []
            details = ex["set_details"]
            for s_idx, s_info in enumerate(details, 1):
                branch = "└" if s_idx == len(details) else "├"
                lbl = s_info.get("short_label", f"Сет {s_idx}")
                icon = "🔥" if s_idx == 1 else "⚡️"
                sh = f" • <i>{s_info['short_hint']}</i>" if s_info.get("short_hint") else ""
                sub_lines.append(f"    {branch} {icon} <b>{lbl}:</b> {s_info['reps']} повт.{sh}")
            lines.append(f"<b>{i}. {ex['name']}</b>\n" + "\n".join(sub_lines))
        else:
            hint_line = f"\n    💡 <i>{ex['hint']}</i>" if ex.get("hint") else ""
            lines.append(
                f"<b>{i}.</b> {ex['name']}\n"
                f"    └ {ex['sets']}×{ex['reps']} • RIR {ex['rir']}{hint_line}"
            )
    lines.append("")
    lines.append(f"⚡ <i>Всего упражнений: {len(day['exercises'])}</i>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 НАЧАТЬ ТРЕНИРОВКУ", callback_data=f"start_{day_idx}")],
        [InlineKeyboardButton(text="⬅️ К дням", callback_data=f"prog_{saved}")],
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("start_"))
async def start_workout(call: CallbackQuery, state: FSMContext):
    saved = get_user_program(call.from_user.id)
    day_idx = int(call.data.split("_")[1])
    if not saved:
        await call.answer("Ошибка")
        return
    p = PROGRAMS[saved]
    day = p["days"][day_idx]
    await state.update_data(program=saved, day=day_idx, ex=0, st=0)
    await call.message.edit_text(
        f"┌─────────────────────\n"
        f"│ 🏋️ <b>{day['name']}</b>\n"
        f"│ Тренировка началась\n"
        f"└─────────────────────\n\n"
        f"<i>Следуй инструкциям бота и вводи результат после каждого подхода.</i>",
        parse_mode="HTML",
    )
    await state.set_state(Workout.entering_set)
    await send_current(call.message, state)


async def send_current(message, state):
    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    st = data["st"]
    ex_name = ex["name"]
    total_ex = len(day["exercises"])
    ex_num = data["ex"] + 1

    set_details = ex.get("set_details")
    if set_details and st < len(set_details):
        s_info = set_details[st]
        set_title = s_info.get("title", f"🔹 Подход {st + 1}/{ex['sets']}")
        short_label = s_info.get("short_label", f"Сет {st + 1}")
        reps_str = s_info.get("reps", ex["reps"])
        instruction = s_info.get("instruction", "")
        last_line = _format_specific_set_hint(message.chat.id, ex_name, st, short_label)

        instr_block = f"📋 <b>Задача:</b> {instruction}\n\n" if instruction else ""
        text = (
            f"┌─────────────────────\n"
            f"│ 🎯 <b>{ex_name}</b>\n"
            f"│ Упражнение {ex_num} из {total_ex}\n"
            f"└─────────────────────\n\n"
            f"<b>{set_title}</b>\n"
            f"🎯 Повторы: <b>{reps_str}</b> • RIR {ex['rir']}\n"
            f"{instr_block}"
            f"{last_line}\n\n"
            f"✍️ Введи <b>вес и повторы</b> через пробел:\n"
            f"<code>80 8</code>"
        )
    else:
        key = f"{day['name']}|{ex_name}"
        last = get_last(message.chat.id, data["program"], key)
        progress = f"{st + 1}/{ex['sets']}"
        hint_block = f"💡 <i>{ex['hint']}</i>\n" if ex.get("hint") else ""
        text = (
            f"┌─────────────────────\n"
            f"│ 🎯 <b>{ex_name}</b>\n"
            f"│ Упражнение {ex_num} из {total_ex}\n"
            f"└─────────────────────\n\n"
            f"🔹 <b>Подход {progress}</b>\n"
            f"🎯 {ex['reps']} повторов • RIR {ex['rir']}\n"
            f"{hint_block}"
            f"{_format_last_hint(last)}\n\n"
            f"✍️ Введи <b>вес и повторы</b> через пробел:\n"
            f"<code>80 8</code>"
        )
    await message.answer(text, parse_mode="HTML")


@dp.message(Workout.entering_set)
async def log_set(msg: Message, state: FSMContext):
    try:
        parts = msg.text.replace(",", ".").split()
        weight = float(parts[0])
        reps = int(parts[1])
    except (ValueError, IndexError):
        await msg.answer(
            "❌ <b>Не понял формат</b>\n\n"
            "Введи вес и повторы через пробел:\n"
            "<code>80 8</code>",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    current_st = data["st"]
    ex_name_safe = (ex["name"] or "").strip() or "Без названия"
    key = f"{day['name']}|{ex_name_safe}"
    save_set(msg.from_user.id, data["program"], day["name"], key, current_st + 1, weight, reps)

    set_details = ex.get("set_details")
    if set_details and current_st < len(set_details):
        logged_label = set_details[current_st].get("short_label", f"Подход {current_st + 1}")
    else:
        logged_label = f"Подход {current_st + 1}"

    st = current_st + 1
    if st >= ex["sets"]:
        ex_i = data["ex"] + 1
        st = 0
        if ex_i >= len(day["exercises"]):
            prog_key = data["program"]
            streak = update_streak(msg.from_user.id)
            tonnage = get_total_tonnage(msg.from_user.id, day["name"])
            await state.clear()
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Мой прогресс", callback_data="progress")],
                [InlineKeyboardButton(text="🔁 К дням", callback_data=f"prog_{prog_key}")],
            ])
            await msg.answer(
                f"╔═══════════════════════╗\n"
                f"║   🎉 ТРЕНИРОВКА ГОТОВА   ║\n"
                f"╚═══════════════════════╝\n\n"
                f"✅ Подходов: <b>{sum(e['sets'] for e in day['exercises'])}</b>\n"
                f"🔥 Стрик: <b>{streak}</b>\n"
                f"🏋️ Тоннаж: <b>{tonnage:,.0f} кг</b>\n\n"
                f"<i>Отличная работа! Восстанавливайся и возвращайся 💪</i>",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        await state.update_data(ex=ex_i, st=0)
        next_ex = day["exercises"][ex_i]
        await msg.answer(
            f"✅ <b>{logged_label} записан: {weight} кг × {reps}</b>\n\n"
            f"➡️ Следующее упражнение: <b>{next_ex['name']}</b>",
            parse_mode="HTML",
        )
        await send_current(msg, state)
    else:
        await state.update_data(st=st)
        next_label = (
            set_details[st].get("short_label", f"Подход {st + 1}")
            if set_details and st < len(set_details)
            else f"Подход {st + 1}"
        )
        await msg.answer(
            f"✅ <b>{logged_label} записан: {weight} кг × {reps}</b>\n"
            f"🔽 Далее — <b>{next_label}</b>:",
            parse_mode="HTML",
        )
        await send_current(msg, state)


# ═══════════════════════════════════════════
# ПРОГРЕСС И ОЧИСТКА СТАРЫХ ДАННЫХ
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "progress")
async def progress_cb(call: CallbackQuery):
    user_id = call.from_user.id
    prog_key = get_user_program(user_id)
    active_set = set(_program_exercises_ordered(prog_key))
    names = _all_exercises(user_id)
    unused = _unused_exercises(user_id)

    if not names:
        await call.message.edit_text(
            "📊 <b>Прогресс пуст</b>\n\n"
            "<i>Начни тренировку — здесь появится история по каждому упражнению.</i>",
            reply_markup=_kb_back("change_prog", "⬅️ К стилям"),
            parse_mode="HTML",
        )
        return

    rows = []
    for i, name in enumerate(names):
        cnt = len(get_exercise_history(user_id, name))
        is_old = active_set and (name not in active_set)
        prefix = "📦 " if is_old else ""
        cnt_str = f" ({cnt})" if cnt > 0 else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{name}{cnt_str}", callback_data=f"ex_{i}")])

    rows.append([
        InlineKeyboardButton(text="🔗 Объединить", callback_data="merge_start"),
        InlineKeyboardButton(
            text=f"🧹 Очистка ({len(unused)} старых)" if unused else "🧹 Очистка истории",
            callback_data="cleanup_menu",
        ),
    ])
    back_cb = f"prog_{prog_key}" if prog_key else "change_prog"
    rows.append([InlineKeyboardButton(text="⬅️ Назад к программе", callback_data=back_cb)])

    unused_note = (
        f"\n📦 <i>Найдено {len(unused)} упр. со старой программы — нажми «🧹 Очистка», чтобы убрать их.</i>"
        if unused else ""
    )
    await call.message.edit_text(
        f"📊 <b>Твой прогресс</b>\n\n"
        f"Выбери упражнение для просмотра и редактирования истории:"
        f"{unused_note}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "cleanup_menu")
async def cleanup_menu(call: CallbackQuery):
    user_id = call.from_user.id
    unused = _unused_exercises(user_id)
    buttons = []
    if unused:
        buttons.append([InlineKeyboardButton(
            text=f"🗑 Удалить старые упражнения ({len(unused)} шт.)",
            callback_data="clean_unused_confirm",
        )])
    buttons.append([InlineKeyboardButton(
        text="🔥 Полный сброс всей истории",
        callback_data="clean_all_confirm",
    )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад в прогресс", callback_data="progress")])

    unused_list = "\n".join(f"• <code>{u}</code>" for u in unused[:25]) if unused else "<i>Нет лишних упражнений</i>"
    await call.message.edit_text(
        f"🧹 <b>Наведение порядка в истории</b>\n\n"
        f"<b>Незадействованные упражнения со старой программы ({len(unused)}):</b>\n"
        f"{unused_list}\n\n"
        f"Что нужно очистить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "clean_unused_confirm")
async def clean_unused_exec(call: CallbackQuery):
    user_id = call.from_user.id
    unused = _unused_exercises(user_id)
    removed_sets = 0
    for name in unused:
        removed_sets += _delete_exercise_records(user_id, name)
    await call.answer("Старые упражнения удалены ✅")
    await call.message.edit_text(
        f"✅ <b>Готово!</b>\n\n"
        f"Удалено неиспользуемых упражнений: <b>{len(unused)}</b>\n"
        f"Очищено старых подходов: <b>{removed_sets}</b>\n\n"
        f"Теперь в списке остались только актуальные упражнения твоей программы.",
        reply_markup=_kb_back("progress", "📊 К прогрессу"),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "clean_all_confirm")
async def clean_all_confirm(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Да, удалить ВСЮ историю", callback_data="clean_all_execute")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="cleanup_menu")],
    ])
    await call.message.edit_text(
        "⚠️ <b>Полный сброс истории</b>\n\n"
        "Ты уверен? Это удалит вообще все записанные веса и подходы по всем упражнениям, чтобы начать с нуля.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "clean_all_execute")
async def clean_all_execute(call: CallbackQuery):
    deleted = _delete_all_user_records(call.from_user.id)
    await call.answer("История полностью очищена ✅")
    await call.message.edit_text(
        f"✅ <b>Вся старая история удалена ({deleted} записей).</b>\n\n"
        f"База чистая и готова к новым тренировкам!",
        reply_markup=_kb_back("progress", "📊 К прогрессу"),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("ex_"))
async def show_exercise_history(call: CallbackQuery):
    idx = int(call.data.split("_", 1)[1])
    names = _all_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Не найдено", show_alert=True)
        return
    name = names[idx]
    rows = get_exercise_history(call.from_user.id, name)
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить запись", callback_data=f"addrec_{idx}")],
            [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")],
        ])
        await call.message.edit_text(
            f"📈 <b>{name}</b>\n\n"
            f"🕐 <i>Пока нет записей по этому упражнению.</i>\n\n"
            f"Выполни его на тренировке или добавь вручную.",
            reply_markup=kb, parse_mode="HTML",
        )
        return
    text, kb = _render_history(name, rows, idx, user_id=call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


def _render_history(name, rows, idx, user_id=None):
    by_date = OrderedDict()
    for row in rows:
        try:
            set_id, d, w, r, sn, ex, day_name = row[:7]
        except (ValueError, TypeError):
            continue
        dkey = (d or "")[:10]
        if dkey not in by_date:
            by_date[dkey] = {"sets": [], "day": day_name or ""}
        by_date[dkey]["sets"].append((sn, w, r))
    if not by_date:
        return (
            f"📈 <b>{name}</b>\n\n<i>Нет корректных записей.</i>",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить запись", callback_data=f"addrec_{idx}")],
                [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")],
            ]),
        )

    prog_key = get_user_program(user_id) if user_id else None
    ex_cfg = _find_exercise_config(prog_key, name)
    has_set_details = bool(ex_cfg and ex_cfg.get("set_details")) or (prog_key == "papirosov")
    total_sets = ex_cfg["sets"] if ex_cfg else 2

    weights = [r[2] for r in rows if isinstance(r[2], (int, float))]
    max_w = max(weights) if weights else 0
    last = rows[-1]

    lines = [
        f"📈 <b>{name}</b>",
        "",
        f"┌─────────────────────",
        f"│ 🏆 Рекорд: <b>{max_w} кг</b>",
    ]

    if has_set_details:
        top_list = []
        back_list = []
        for d, ddata in by_date.items():
            for i_s, (sn, w, r) in enumerate(ddata["sets"]):
                eff = sn if sn in (1, 2) else (1 if i_s == 0 else 2)
                if eff == 1 or total_sets == 1:
                    top_list.append((w, r))
                else:
                    back_list.append((w, r))
        if top_list:
            lines.append(f"│ 🔥 Посл. Топ-сет: <b>{top_list[-1][0]} кг × {top_list[-1][1]}</b>")
        if back_list and total_sets >= 2:
            lines.append(f"│ ⚡️ Посл. Бэк-офф: <b>{back_list[-1][0]} кг × {back_list[-1][1]}</b>")
    else:
        lines.append(f"│ ⏱ Последний: <b>{last[2]} кг × {last[3]}</b>")

    lines.extend([
        f"│ 🗓 Тренировок: <b>{len(by_date)}</b>",
        f"└─────────────────────",
        "",
        "<b>📅 История по дням:</b>",
    ])

    for d, ddata in by_date.items():
        lbl = f" <i>({ddata['day']})</i>" if ddata["day"] else ""
        lines.append(f"<code>{d}</code>{lbl}")
        if has_set_details:
            total_in_day = len(ddata["sets"])
            for i_s, (sn, w, r) in enumerate(ddata["sets"]):
                branch = "└" if i_s == total_in_day - 1 else "├"
                tag = _set_type_label(sn, i_s, has_set_details, total_sets)
                lines.append(f"   {branch} {tag}: <b>{w} кг × {r}</b>")
        else:
            sets_str = " · ".join(f"{w}×{r}" for _, w, r in ddata["sets"])
            lines.append(f"   └ {sets_str}")

    text = "\n".join(lines)
    if len(text) > 3400:
        text = text[:3300] + "\n...<i>(обрезано)</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data=f"addrec_{idx}"),
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"editlist_{idx}"),
        ],
        [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")],
    ])
    return text, kb


# ═══════════════════════════════════════════
# ОБЪЕДИНЕНИЕ УПРАЖНЕНИЙ
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "merge_start")
async def merge_start(call: CallbackQuery, state: FSMContext):
    names = _all_exercises(call.from_user.id)
    if len(names) < 2:
        await call.answer("Нужно минимум 2 упражнения", show_alert=True)
        return
    rows = []
    for i, n in enumerate(names):
        cnt = len(get_exercise_history(call.from_user.id, n))
        rows.append([InlineKeyboardButton(text=f"{n} ({cnt})", callback_data=f"mergefrom_{i}")])
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="progress")])
    await state.update_data(merge_names=names)
    await call.message.edit_text(
        "🔗 <b>Объединение упражнений</b>\n\n"
        "Все записи одного упражнения переносятся в другое.\n\n"
        "<b>Шаг 1.</b> Какое упражнение переносим?\n\n"
        "<i>Выбери то, которое нужно удалить (в скобках — сколько записей):</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await state.set_state(MergeFlow.choosing_from)


@dp.callback_query(MergeFlow.choosing_from, F.data.startswith("mergefrom_"))
async def merge_choose_from(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_")[1])
    data = await state.get_data()
    names = data.get("merge_names") or []
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    from_name = names[idx]
    await state.update_data(merge_from=from_name)

    others = [(i, n) for i, n in enumerate(names) if n != from_name]
    rows = [[InlineKeyboardButton(text=n, callback_data=f"mergeto_{i}")]
            for i, n in others]
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="progress")])
    await state.update_data(merge_others=others)
    await call.message.edit_text(
        f"🔗 <b>Объединение</b>\n\n"
        f"Переносим: <b>{from_name}</b>\n\n"
        f"<b>Шаг 2.</b> В какое упражнение перенести?\n\n"
        f"<i>Выбери итоговое (в него пойдут все записи):</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
    await state.set_state(MergeFlow.choosing_to)


@dp.callback_query(MergeFlow.choosing_to, F.data.startswith("mergeto_"))
async def merge_choose_to(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_")[1])
    data = await state.get_data()
    others = data.get("merge_others") or []
    from_name = data.get("merge_from")
    if idx >= len(others):
        await call.answer("Ошибка")
        return
    _, to_name = others[idx]

    before_from = len(get_exercise_history(call.from_user.id, from_name))
    before_to = len(get_exercise_history(call.from_user.id, to_name))

    moved = merge_exercises(call.from_user.id, from_name, to_name)
    await state.clear()

    after_to = len(get_exercise_history(call.from_user.id, to_name))

    await call.message.edit_text(
        f"✅ <b>Объединено</b>\n\n"
        f"Из: <b>{from_name}</b> ({before_from} записей)\n"
        f"В: <b>{to_name}</b>\n\n"
        f"Было у цели: {before_to}\n"
        f"Стало у цели: {after_to}\n"
        f"Перенесено: {moved}",
        parse_mode="HTML",
    )
    names = _all_exercises(call.from_user.id)
    try:
        new_idx = names.index(to_name)
    except ValueError:
        return
    rows = get_exercise_history(call.from_user.id, to_name)
    if not rows:
        return
    text, kb = _render_history(to_name, rows, new_idx, user_id=call.from_user.id)
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════
# УМНОЕ ДОБАВЛЕНИЕ ЗАПИСИ (ТОП-СЕТ / БЭК-ОФФ)
# ═══════════════════════════════════════════

@dp.callback_query(F.data.startswith("addrec_"))
async def add_record_start(call: CallbackQuery, state: FSMContext):
    suffix = call.data.split("_", 1)[1]
    if suffix in ("today", "yesterday"):
        return
    idx = int(suffix)
    names = _all_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[idx]
    prog_key = get_user_program(call.from_user.id)
    ex_cfg = _find_exercise_config(prog_key, name)
    total_sets = ex_cfg["sets"] if ex_cfg else 2
    has_details = bool(ex_cfg and ex_cfg.get("set_details")) or (prog_key == "papirosov")

    await state.update_data(add_idx=idx, add_name=name, add_set_type=1)

    if has_details and total_sets >= 2:
        await state.set_state(AddRecord.choosing_set_type)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Топ-сет (1-й подход)", callback_data="addtype_1")],
            [InlineKeyboardButton(text="⚡️ Бэк-офф (2-й подход)", callback_data="addtype_2")],
            [InlineKeyboardButton(text="🔥+⚡️ Сразу оба подхода", callback_data="addtype_both")],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"ex_{idx}")],
        ])
        await call.message.edit_text(
            f"➕ <b>Добавить запись</b>\n\n"
            f"🎯 <b>{name}</b>\n\n"
            f"Какой подход добавляем?",
            reply_markup=kb,
            parse_mode="HTML",
        )
    else:
        await _ask_add_date(call.message, state, idx, name, "🔥 Топ-сет")


@dp.callback_query(AddRecord.choosing_set_type, F.data.startswith("addtype_"))
async def add_record_choose_type(call: CallbackQuery, state: FSMContext):
    mode = call.data.split("_", 1)[1]
    data = await state.get_data()
    idx = data.get("add_idx", 0)
    name = data.get("add_name", "")
    if mode == "1":
        await state.update_data(add_set_type=1)
        label = "🔥 Топ-сет"
    elif mode == "2":
        await state.update_data(add_set_type=2)
        label = "⚡️ Бэк-офф"
    else:
        await state.update_data(add_set_type="both")
        label = "🔥 Топ-сет + ⚡️ Бэк-офф"
    await _ask_add_date(call.message, state, idx, name, label)


async def _ask_add_date(message, state: FSMContext, idx: int, name: str, type_label: str):
    await state.update_data(add_type_label=type_label)
    await state.set_state(AddRecord.entering_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="adddate_today"),
            InlineKeyboardButton(text="📅 Вчера", callback_data="adddate_yesterday"),
        ],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"ex_{idx}")],
    ])
    await message.edit_text(
        f"➕ <b>Добавить: {type_label}</b>\n"
        f"🎯 <b>{name}</b>\n\n"
        f"<b>За какую дату?</b>\n"
        f"Нажми кнопку или введи дату (например <code>30.10</code>):",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data.in_({"adddate_today", "adddate_yesterday"}))
async def add_rec_quick_date(call: CallbackQuery, state: FSMContext):
    from datetime import timedelta
    data = await state.get_data()
    name = data.get("add_name")
    if name is None:
        await call.answer("Ошибка")
        return
    if call.data == "adddate_today":
        iso = date.today().isoformat()
    else:
        iso = (date.today() - timedelta(days=1)).isoformat()
    await state.update_data(add_date=iso)
    await state.set_state(AddRecord.entering_value)
    await _prompt_add_value(call.message, data, iso, edit=True)


@dp.message(AddRecord.entering_date)
async def add_record_date(msg: Message, state: FSMContext):
    iso = _parse_date(msg.text)
    if not iso:
        await msg.answer(
            "❌ Не понял дату.\n\n"
            "Попробуй: <code>30.10</code>, <code>30.10.2026</code>",
            parse_mode="HTML",
        )
        return
    await state.update_data(add_date=iso)
    data = await state.get_data()
    await state.set_state(AddRecord.entering_value)
    await _prompt_add_value(msg, data, iso, edit=False)


async def _prompt_add_value(message, data, iso, edit=False):
    name = data.get("add_name")
    type_label = data.get("add_type_label", "🔥 Топ-сет")
    set_type = data.get("add_set_type", 1)
    if set_type == "both":
        hint = (
            "Введи <b>Топ-сет и Бэк-офф</b> через слэш или с новой строки:\n"
            "<code>80 8 / 65 12</code>"
        )
    else:
        hint = "Введи <b>вес и повторы</b> через пробел:\n<code>80 8</code>"
    text = (
        f"➕ <b>{name}</b>\n"
        f"🏷 {type_label} • 📅 {iso}\n\n"
        f"{hint}"
    )
    if edit:
        await message.edit_text(text, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


@dp.message(AddRecord.entering_value)
async def add_record_apply(msg: Message, state: FSMContext):
    data = await state.get_data()
    name = data.get("add_name")
    iso = data.get("add_date") or date.today().isoformat()
    set_type = data.get("add_set_type", 1)

    if name is None:
        await msg.answer("Что-то потерялось, открой 📊 Прогресс")
        await state.clear()
        return

    prog_key = get_user_program(msg.from_user.id)
    day_name = _find_day_for_exercise(prog_key, name)
    key = f"{day_name}|{name}" if day_name else name

    if set_type == "both":
        raw = msg.text.replace(",", ".").replace("\n", "/")
        chunks = [c.strip() for c in raw.split("/") if c.strip()]
        if len(chunks) < 2 and len(raw.split()) == 4:
            tokens = raw.split()
            chunks = [f"{tokens[0]} {tokens[1]}", f"{tokens[2]} {tokens[3]}"]
        try:
            p1 = chunks[0].split()
            w1, r1 = float(p1[0]), int(p1[1])
            p2 = chunks[1].split()
            w2, r2 = float(p2[0]), int(p2[1])
        except (ValueError, IndexError):
            await msg.answer(
                "❌ Введи два подхода через слэш:\n<code>80 8 / 65 12</code>",
                parse_mode="HTML",
            )
            return
        save_set(msg.from_user.id, prog_key or "", day_name, key, 1, w1, r1, date_str=iso)
        save_set(msg.from_user.id, prog_key or "", day_name, key, 2, w2, r2, date_str=iso)
        summary = f"🔥 Топ-сет: <b>{w1} кг × {r1}</b>\n⚡️ Бэк-офф: <b>{w2} кг × {r2}</b>"
    else:
        try:
            parts = msg.text.replace(",", ".").split()
            weight = float(parts[0])
            reps = int(parts[1])
        except (ValueError, IndexError):
            await msg.answer("❌ Формат: <code>80 8</code>", parse_mode="HTML")
            return
        set_num = int(set_type) if set_type in (1, 2) else (count_sets_on_date(msg.from_user.id, key, iso) + 1)
        save_set(msg.from_user.id, prog_key or "", day_name, key, set_num, weight, reps, date_str=iso)
        lbl = "🔥 Топ-сет" if set_num == 1 else "⚡️ Бэк-офф"
        summary = f"{lbl}: <b>{weight} кг × {reps}</b>"

    await state.clear()
    await msg.answer(
        f"✅ <b>Запись добавлена</b>\n\n"
        f"🎯 {name}\n"
        f"📅 {iso}\n"
        f"{summary}",
        parse_mode="HTML",
    )
    names = _all_exercises(msg.from_user.id)
    try:
        new_idx = names.index(name)
    except ValueError:
        return
    rows = get_exercise_history(msg.from_user.id, name)
    if not rows:
        return
    text, kb = _render_history(name, rows, new_idx, user_id=msg.from_user.id)
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════
# УМНОЕ РЕДАКТИРОВАНИЕ
# ═══════════════════════════════════════════

@dp.callback_query(F.data.startswith("editlist_"))
async def edit_list(call: CallbackQuery):
    idx = int(call.data.split("_")[1])
    names = _all_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[idx]
    rows = get_exercise_history(call.from_user.id, name)
    if not rows:
        await call.answer("Нет записей", show_alert=True)
        return

    prog_key = get_user_program(call.from_user.id)
    ex_cfg = _find_exercise_config(prog_key, name)
    total_sets = ex_cfg["sets"] if ex_cfg else 2
    has_details = bool(ex_cfg and ex_cfg.get("set_details")) or (prog_key == "papirosov")

    display = rows[-20:]
    buttons = []
    for set_id, d, w, r, sn, ex, day_name in reversed(display):
        dkey = d[:10]
        tag = _set_type_label(sn, 0, has_details, total_sets)
        label = f"{dkey} • {tag}: {w}кг × {r}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"rec_{set_id}_{idx}")])

    buttons.append([InlineKeyboardButton(text="🗑 Очистить всё упражнение", callback_data=f"wipeex_{idx}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_{idx}")])
    await call.message.edit_text(
        f"✏️ <b>Редактирование: {name}</b>\n\n"
        f"Выбери подход для изменения\n"
        f"<i>(показаны последние {len(display)} из {len(rows)})</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("wipeex_"))
async def wipe_exercise_confirm(call: CallbackQuery):
    idx = int(call.data.split("_")[1])
    names = _all_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[idx]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Да, удалить все записи", callback_data=f"wipeexok_{idx}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"editlist_{idx}")],
    ])
    await call.message.edit_text(
        f"🗑 <b>Удалить всю историю упражнения?</b>\n\n"
        f"🎯 <b>{name}</b>\n"
        f"Все записанные подходы в этом упражнении будут удалены.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("wipeexok_"))
async def wipe_exercise_exec(call: CallbackQuery):
    idx = int(call.data.split("_")[1])
    names = _all_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[idx]
    cnt = _delete_exercise_records(call.from_user.id, name)
    await call.answer(f"Удалено записей: {cnt} ✅")
    await progress_cb(call)


@dp.callback_query(F.data.startswith("rec_"))
async def show_record_actions(call: CallbackQuery):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    if ex_idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[ex_idx]
    rows = get_exercise_history(call.from_user.id, name)
    target = None
    for r in rows:
        if r[0] == set_id:
            target = r
            break
    if not target:
        await call.answer("Запись не найдена")
        return
    _, d, w, r, sn, ex, day_name = target
    prog_key = get_user_program(call.from_user.id)
    ex_cfg = _find_exercise_config(prog_key, name)
    total_sets = ex_cfg["sets"] if ex_cfg else 2
    tag = _set_type_label(sn, 0, True, total_sets)
    toggle_text = "🔄 Сделать Бэк-оффом" if sn == 1 else "🔄 Сделать Топ-сетом"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Вес / повторы", callback_data=f"editrec_{set_id}_{ex_idx}"),
            InlineKeyboardButton(text="📅 Дату", callback_data=f"editdate_{set_id}_{ex_idx}"),
        ],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"toggleset_{set_id}_{ex_idx}")],
        [
            InlineKeyboardButton(text="🔀 Перенести", callback_data=f"moverec_{set_id}_{ex_idx}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delrec_{set_id}_{ex_idx}"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"editlist_{ex_idx}")],
    ])
    await call.message.edit_text(
        f"📝 <b>{name}</b>\n\n"
        f"┌─────────────────────\n"
        f"│ 🏷 Тип: <b>{tag}</b>\n"
        f"│ 📅 Дата: <b>{d[:10]}</b>\n"
        f"│ 🏋️ Результат: <b>{w} кг × {r}</b>\n"
        f"└─────────────────────\n\n"
        f"<b>Что меняем?</b>",
        reply_markup=kb, parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("toggleset_"))
async def toggle_set_type(call: CallbackQuery):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    if ex_idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[ex_idx]
    rows = get_exercise_history(call.from_user.id, name)
    target = next((r for r in rows if r[0] == set_id), None)
    if not target:
        await call.answer("Запись не найдена")
        return
    current_sn = target[4]
    new_sn = 2 if current_sn == 1 else 1
    _update_set_number_in_db(call.from_user.id, set_id, new_sn)
    new_lbl = "⚡️ Бэк-офф" if new_sn == 2 else "🔥 Топ-сет"
    await call.answer(f"Теперь это {new_lbl} ✅")
    await show_record_actions(call)


@dp.callback_query(F.data.startswith("editrec_"))
async def edit_record(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    name = names[ex_idx]
    rows = get_exercise_history(call.from_user.id, name)
    target = next((r for r in rows if r[0] == set_id), None)
    old_w = target[2] if target else 0
    old_r = target[3] if target else 0

    await state.update_data(
        edit_set_id=set_id,
        edit_ex_idx=ex_idx,
        edit_name=name,
        old_weight=old_w,
        old_reps=old_r,
    )
    await state.set_state(EditFlow.entering_new)
    kb = _kb_back(f"rec_{set_id}_{ex_idx}", "⬅️ Отмена")
    await call.message.edit_text(
        f"✏️ <b>Умное изменение</b>\n\n"
        f"🎯 <b>{name}</b>\n"
        f"Сейчас: <code>{old_w} кг × {old_r}</code>\n\n"
        f"• Введи <code>85 10</code> — чтобы изменить вес и повторы\n"
        f"• Введи <code>85</code> — чтобы изменить только вес (повторы останутся {old_r})\n"
        f"• Введи <code>+2.5</code> или <code>-5</code> — чтобы прибавить/убавить вес",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.message(EditFlow.entering_new)
async def apply_edit(msg: Message, state: FSMContext):
    data = await state.get_data()
    set_id = data["edit_set_id"]
    ex_idx = data["edit_ex_idx"]
    name = data["edit_name"]
    old_w = float(data.get("old_weight") or 0)
    old_r = int(data.get("old_reps") or 8)

    raw = msg.text.strip().replace(",", ".")
    parts = raw.split()
    try:
        if len(parts) == 1:
            token = parts[0]
            if token.startswith("+") or token.startswith("-"):
                weight = round(old_w + float(token), 2)
                reps = old_r
            else:
                weight = float(token)
                reps = old_r
        else:
            weight = float(parts[0])
            reps = int(parts[1])
    except (ValueError, IndexError):
        await msg.answer("❌ Примеры ввода: <code>85 8</code>, <code>85</code> или <code>+2.5</code>", parse_mode="HTML")
        return

    update_set(msg.from_user.id, set_id, weight, reps)
    await state.clear()
    rows = get_exercise_history(msg.from_user.id, name)
    text, kb = _render_history(name, rows, ex_idx, user_id=msg.from_user.id)
    await msg.answer(f"✅ <b>Обновлено:</b> {weight} кг × {reps}", parse_mode="HTML")
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("editdate_"))
async def edit_date(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    name = names[ex_idx]
    await state.update_data(editdate_set_id=set_id, editdate_ex_idx=ex_idx,
                            editdate_name=name)
    await state.set_state(EditFlow.entering_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня", callback_data=f"setdate_today_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="📅 Вчера", callback_data=f"setdate_yest_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"rec_{set_id}_{ex_idx}")],
    ])
    await call.message.edit_text(
        f"📅 <b>Изменить дату</b>\n\n"
        f"🎯 <b>{name}</b>\n\n"
        f"Выбери или введи вручную:\n"
        f"<code>30.10</code> · <code>30.10.2026</code>",
        reply_markup=kb, parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("setdate_"))
async def set_date_quick(call: CallbackQuery, state: FSMContext):
    from datetime import timedelta
    parts = call.data.split("_")
    mode = parts[1]
    set_id = int(parts[2])
    ex_idx = int(parts[3])
    if mode == "today":
        iso = date.today().isoformat()
    else:
        iso = (date.today() - timedelta(days=1)).isoformat()
    update_set_date(call.from_user.id, set_id, iso)
    await state.clear()
    names = _all_exercises(call.from_user.id)
    name = names[ex_idx]
    rows = get_exercise_history(call.from_user.id, name)
    text, kb = _render_history(name, rows, ex_idx, user_id=call.from_user.id)
    await call.message.edit_text(f"✅ Дата изменена на <b>{iso}</b>", parse_mode="HTML")
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(EditFlow.entering_date)
async def apply_date(msg: Message, state: FSMContext):
    iso = _parse_date(msg.text)
    if not iso:
        await msg.answer(
            "❌ Не понял дату. Попробуй: <code>30.10</code>, <code>30.10.2026</code>",
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    set_id = data.get("editdate_set_id")
    ex_idx = data.get("editdate_ex_idx")
    name = data.get("editdate_name")
    if not set_id:
        await msg.answer("Что-то потерялось, начни заново.")
        await state.clear()
        return
    update_set_date(msg.from_user.id, set_id, iso)
    await state.clear()
    rows = get_exercise_history(msg.from_user.id, name)
    text, kb = _render_history(name, rows, ex_idx, user_id=msg.from_user.id)
    await msg.answer(f"✅ Дата изменена на <b>{iso}</b>", parse_mode="HTML")
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("moverec_"))
async def move_record(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    if ex_idx >= len(names):
        await call.answer("Ошибка")
        return
    current = names[ex_idx]
    others = [n for n in names if n != current]
    await state.update_data(move_set_id=set_id, move_from_idx=ex_idx,
                            move_from_name=current, move_targets=others)
    rows = [[InlineKeyboardButton(text=n, callback_data=f"moveto_{i}")]
            for i, n in enumerate(others)]
    rows.append([InlineKeyboardButton(text="⬅️ Отмена",
                                       callback_data=f"rec_{set_id}_{ex_idx}")])
    await call.message.edit_text(
        f"🔀 <b>Перенести запись</b>\n\n"
        f"Из: <b>{current}</b>\n\n"
        f"Куда перенести?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("moveto_"))
async def apply_move(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_", 1)[1])
    data = await state.get_data()
    set_id = data.get("move_set_id")
    from_name = data.get("move_from_name")
    targets = data.get("move_targets") or []
    if not set_id or idx >= len(targets):
        await call.answer("Ошибка, попробуй заново", show_alert=True)
        return
    target_name = targets[idx]
    move_set(call.from_user.id, set_id, target_name)
    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Перенесено</b>\n\n"
        f"Из: {from_name}\n"
        f"В: <b>{target_name}</b>",
        parse_mode="HTML",
    )
    names = _all_exercises(call.from_user.id)
    try:
        new_idx = names.index(target_name)
    except ValueError:
        return
    rows = get_exercise_history(call.from_user.id, target_name)
    if not rows:
        return
    text, kb = _render_history(target_name, rows, new_idx, user_id=call.from_user.id)
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("delrec_"))
async def delete_record(call: CallbackQuery):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    if ex_idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[ex_idx]
    delete_set(call.from_user.id, set_id)
    await call.answer("Удалено ✅")
    rows = get_exercise_history(call.from_user.id, name)
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить запись", callback_data=f"addrec_{ex_idx}")],
            [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")],
        ])
        await call.message.edit_text(
            f"📈 <b>{name}</b>\n\nВсе записи удалены.",
            reply_markup=kb, parse_mode="HTML",
        )
        return
    text, kb = _render_history(name, rows, ex_idx, user_id=call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════
# FALLBACK (ДОЛЖЕН БЫТЬ В САМОМ КОНЦЕ!)
# ═══════════════════════════════════════════

@dp.message(F.text)
async def fallback(msg: Message, state: FSMContext):
    current = await state.get_state()
    if current:
        return
    await msg.answer(
        "🤔 <b>Не понял команду</b>\n\n"
        "Нажми /start чтобы открыть меню.",
        parse_mode="HTML",
    )


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
