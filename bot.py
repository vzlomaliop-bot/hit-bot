import asyncio
import os
from collections import OrderedDict
from datetime import date, datetime

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
# ОСОБЫЕ ПОЛЬЗОВАТЕЛИ
# ═══════════════════════════════════════════

OWNER_USERNAME = "papirosovv"
HIDDEN_PROGRAMS = ["papirosov"]


def is_owner(user):
    return (user.username or "").lower() == OWNER_USERNAME


def visible_programs_for_level(level_key, user):
    out = {}
    for k, v in PROGRAMS.items():
        if v.get("level") != level_key:
            continue
        if k in HIDDEN_PROGRAMS and not is_owner(user):
            continue
        out[k] = v
    return out


def has_hidden_program(user):
    if not is_owner(user):
        return False
    for k in HIDDEN_PROGRAMS:
        if k in PROGRAMS:
            return True
    return False


# ═══════════════════════════════════════════
# FSM
# ═══════════════════════════════════════════

class Workout(StatesGroup):
    entering_set = State()


class EditFlow(StatesGroup):
    entering_new = State()
    entering_date = State()


class AddRecord(StatesGroup):
    entering_date = State()
    entering_value = State()


class MergeFlow(StatesGroup):
    choosing_from = State()
    choosing_to = State()


# ═══════════════════════════════════════════
# ХЕЛПЕРЫ
# ═══════════════════════════════════════════

def _all_exercises(user_id):
    names = set()
    prog_key = get_user_program(user_id)
    if prog_key and prog_key in PROGRAMS:
        for day in PROGRAMS[prog_key]["days"]:
            for ex in day["exercises"]:
                if ex.get("name"):
                    names.add(ex["name"])
    for n in get_user_exercises(user_id):
        names.add(n)
    return sorted(names)


def _find_day_for_exercise(prog_key, ex_name):
    if not prog_key or prog_key not in PROGRAMS:
        return ""
    for day in PROGRAMS[prog_key]["days"]:
        for ex in day["exercises"]:
            if ex.get("name") == ex_name:
                return day["name"]
    return ""


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
    if has_hidden_program(user):
        buttons.append([InlineKeyboardButton(
            text="🔥 HIT (для тебя)",
            callback_data="prog_papirosov",
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


# ═══════════════════════════════════════════
# СТАРТ
# ═══════════════════════════════════════════

@dp.message(Command("start", ignore_case=True))
async def start(msg: Message, state: FSMContext):
    await state.clear()
    name = msg.from_user.first_name or "друг"
    saved = get_user_program(msg.from_user.id)

    if saved and saved in PROGRAMS:
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
    import sqlite3
    conn = sqlite3.connect("workouts.db")
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
    import sqlite3
    conn = sqlite3.connect("workouts.db")
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
    import sqlite3
    conn = sqlite3.connect("workouts.db")
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
    if key in HIDDEN_PROGRAMS and not is_owner(call.from_user):
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
        lines.append(
            f"<b>{i}.</b> {ex['name']}\n"
            f"    └ {ex['sets']}×{ex['reps']} • RIR {ex['rir']}"
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
    key = f"{day['name']}|{ex_name}"
    last = get_last(message.chat.id, data["program"], key)
    progress = f"{st + 1}/{ex['sets']}"
    total_ex = len(day["exercises"])
    ex_num = data["ex"] + 1
    text = (
        f"┌─────────────────────\n"
        f"│ 🎯 <b>{ex_name}</b>\n"
        f"│ Упражнение {ex_num} из {total_ex}\n"
        f"└─────────────────────\n\n"
        f"🔹 <b>Подход {progress}</b>\n"
        f"🎯 {ex['reps']} повторов • RIR {ex['rir']}\n"
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
    ex_name_safe = (ex["name"] or "").strip() or "Без названия"
    key = f"{day['name']}|{ex_name_safe}"
    save_set(msg.from_user.id, data["program"], day["name"], key, data["st"] + 1, weight, reps)

    st = data["st"] + 1
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
            f"✅ <b>{weight} кг × {reps}</b>\n\n"
            f"➡️ Следующее: <b>{next_ex['name']}</b>",
            parse_mode="HTML",
        )
        await send_current(msg, state)
    else:
        await state.update_data(st=st)
        await msg.answer(f"✅ <b>{weight} кг × {reps}</b>", parse_mode="HTML")
        await send_current(msg, state)


# ═══════════════════════════════════════════
# ПРОГРЕСС
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "progress")
async def progress_cb(call: CallbackQuery):
    names = _all_exercises(call.from_user.id)
    if not names:
        await call.message.edit_text(
            "📊 <b>Прогресс пуст</b>\n\n"
            "<i>Начни тренировку — здесь появится история по каждому упражнению.</i>",
            reply_markup=_kb_back("change_prog", "⬅️ К стилям"),
            parse_mode="HTML",
        )
        return
    rows = [[InlineKeyboardButton(text=name, callback_data=f"ex_{i}")]
            for i, name in enumerate(names)]
    rows.append([InlineKeyboardButton(text="🔗 Объединить упражнения", callback_data="merge_start")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")])
    await call.message.edit_text(
        f"📊 <b>Твой прогресс</b>\n\n"
        f"Выбери упражнение для просмотра истории\n"
        f"<i>(всего: {len(names)})</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
    text, kb = _render_history(name, rows, idx)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


def _render_history(name, rows, idx):
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
    weights = [r[2] for r in rows if isinstance(r[2], (int, float))]
    max_w = max(weights) if weights else 0
    last = rows[-1]
    lines = [
        f"📈 <b>{name}</b>",
        "",
        f"┌─────────────────────",
        f"│ 🏆 Рекорд: <b>{max_w} кг</b>",
        f"│ ⏱ Последний: <b>{last[2]} кг × {last[3]}</b>",
        f"│ 🗓 Тренировок: <b>{len(by_date)}</b>",
        f"└─────────────────────",
        "",
        "<b>📅 История:</b>",
    ]
    for d, ddata in by_date.items():
        sets_str = " · ".join(f"{w}×{r}" for _, w, r in ddata["sets"])
        lbl = f" <i>({ddata['day']})</i>" if ddata["day"] else ""
        lines.append(f"<code>{d}</code>{lbl}\n   └ {sets_str}")
    text = "\n".join(lines)
    if len(text) > 3400:
        text = text[:3300] + "\n...<i>(обрезано)</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить запись", callback_data=f"addrec_{idx}")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"editlist_{idx}")],
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
        # Показываем количество записей
        cnt = len(get_exercise_history(call.from_user.id, n))
        rows.append([InlineKeyboardButton(text=f"{n} ({cnt})", callback_data=f"merge_from_{i}")])
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


@dp.callback_query(MergeFlow.choosing_from, F.data.startswith("merge_from_"))
async def merge_choose_from(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_", 1)[2])
    data = await state.get_data()
    names = data.get("merge_names") or []
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    from_name = names[idx]
    await state.update_data(merge_from=from_name)

    others = [(i, n) for i, n in enumerate(names) if n != from_name]
    rows = [[InlineKeyboardButton(text=n, callback_data=f"merge_to_{i}")]
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


@dp.callback_query(MergeFlow.choosing_to, F.data.startswith("merge_to_"))
async def merge_choose_to(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_", 1)[2])
    data = await state.get_data()
    others = data.get("merge_others") or []
    from_name = data.get("merge_from")
    if idx >= len(others):
        await call.answer("Ошибка")
        return
    _, to_name = others[idx]

    # Считаем записи до
    before_from = len(get_exercise_history(call.from_user.id, from_name))
    before_to = len(get_exercise_history(call.from_user.id, to_name))

    # Переносим
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
    # Показать результат
    names = _all_exercises(call.from_user.id)
    try:
        new_idx = names.index(to_name)
    except ValueError:
        return
    rows = get_exercise_history(call.from_user.id, to_name)
    if not rows:
        return
    text, kb = _render_history(to_name, rows, new_idx)
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════
# ДОБАВИТЬ ЗАПИСЬ
# ═══════════════════════════════════════════

@dp.callback_query(F.data.startswith("addrec_"))
async def add_record_start(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_", 1)[1])
    names = _all_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[idx]
    await state.update_data(add_idx=idx, add_name=name)
    await state.set_state(AddRecord.entering_date)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня", callback_data="addrec_today")],
        [InlineKeyboardButton(text="📅 Вчера", callback_data="addrec_yesterday")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"ex_{idx}")],
    ])
    await call.message.edit_text(
        f"➕ <b>Добавить запись</b>\n\n"
        f"🎯 <b>{name}</b>\n\n"
        f"<b>Шаг 1.</b> За какую дату?\n\n"
        f"Нажми кнопку или введи вручную:\n"
        f"<code>25.09</code> · <code>26.09.2026</code> · <code>2026-09-26</code>",
        reply_markup=kb, parse_mode="HTML",
    )


@dp.callback_query(F.data == "addrec_today")
async def add_rec_today(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    name = data.get("add_name")
    if name is None:
        await call.answer("Ошибка")
        return
    iso = date.today().isoformat()
    await state.update_data(add_date=iso)
    await state.set_state(AddRecord.entering_value)
    await call.message.edit_text(
        f"➕ <b>{name}</b>\n"
        f"📅 {iso}\n\n"
        f"<b>Шаг 2.</b> Введи вес и повторы:\n"
        f"<code>80 8</code>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "addrec_yesterday")
async def add_rec_yesterday(call: CallbackQuery, state: FSMContext):
    from datetime import timedelta
    data = await state.get_data()
    name = data.get("add_name")
    if name is None:
        await call.answer("Ошибка")
        return
    iso = (date.today() - timedelta(days=1)).isoformat()
    await state.update_data(add_date=iso)
    await state.set_state(AddRecord.entering_value)
    await call.message.edit_text(
        f"➕ <b>{name}</b>\n"
        f"📅 {iso}\n\n"
        f"<b>Шаг 2.</b> Введи вес и повторы:\n"
        f"<code>80 8</code>",
        parse_mode="HTML",
    )


@dp.message(AddRecord.entering_date)
async def add_record_date(msg: Message, state: FSMContext):
    iso = _parse_date(msg.text)
    if not iso:
        await msg.answer(
            "❌ Не понял дату.\n\n"
            "Попробуй: <code>25.09</code>, <code>26.09.2026</code>, <code>2026-09-26</code>",
            parse_mode="HTML",
        )
        return
    data = await state.get_data()
    name = data.get("add_name")
    await state.update_data(add_date=iso)
    await state.set_state(AddRecord.entering_value)
    await msg.answer(
        f"➕ <b>{name}</b>\n"
        f"📅 {iso}\n\n"
        f"<b>Шаг 2.</b> Введи вес и повторы:\n"
        f"<code>80 8</code>",
        parse_mode="HTML",
    )


@dp.message(AddRecord.entering_value)
async def add_record_apply(msg: Message, state: FSMContext):
    try:
        parts = msg.text.replace(",", ".").split()
        weight = float(parts[0])
        reps = int(parts[1])
    except (ValueError, IndexError):
        await msg.answer("❌ Формат: <code>80 8</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    name = data.get("add_name")
    iso = data.get("add_date") or date.today().isoformat()
    if name is None:
        await msg.answer("Что-то потерялось, начни заново: 📊 Прогресс")
        await state.clear()
        return
    prog_key = get_user_program(msg.from_user.id)
    day_name = _find_day_for_exercise(prog_key, name)
    key = f"{day_name}|{name}" if day_name else name
    set_num = count_sets_on_date(msg.from_user.id, key, iso) + 1
    save_set(msg.from_user.id, prog_key or "", day_name, key, set_num, weight, reps, date_str=iso)
    await state.clear()
    await msg.answer(
        f"✅ <b>Добавлено</b>\n\n"
        f"🎯 {name}\n"
        f"📅 {iso}\n"
        f"🏋️ {weight} кг × {reps}",
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
    text, kb = _render_history(name, rows, new_idx)
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════
# РЕДАКТИРОВАНИЕ
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
    display = rows[-20:]
    buttons = []
    for set_id, d, w, r, sn, ex, day_name in reversed(display):
        dkey = d[:10]
        label = f"{dkey} • {w}кг × {r}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"rec_{set_id}_{idx}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_{idx}")])
    await call.message.edit_text(
        f"✏️ <b>{name}</b>\n\n"
        f"Выбери запись для изменения\n"
        f"<i>(показаны последние {len(display)} из {len(rows)})</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Вес/повторы", callback_data=f"editrec_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="📅 Дату", callback_data=f"editdate_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="🔀 В др. упражнение", callback_data=f"moverec_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delrec_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"editlist_{ex_idx}")],
    ])
    await call.message.edit_text(
        f"📝 <b>{name}</b>\n\n"
        f"┌─────────────────────\n"
        f"│ 📅 {d[:10]}\n"
        f"│ 🏋️ {w} кг × {r} повторов\n"
        f"│ Подход №{sn}\n"
        f"└─────────────────────\n\n"
        f"<b>Что делаем?</b>",
        reply_markup=kb, parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("editrec_"))
async def edit_record(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = _all_exercises(call.from_user.id)
    name = names[ex_idx]
    await state.update_data(edit_set_id=set_id, edit_ex_idx=ex_idx, edit_name=name)
    await state.set_state(EditFlow.entering_new)
    await call.message.edit_text(
        f"✏️ <b>Изменение веса/повторов</b>\n\n"
        f"🎯 <b>{name}</b>\n\n"
        f"Введи новые значения: <code>85 8</code>",
        parse_mode="HTML",
    )


@dp.message(EditFlow.entering_new)
async def apply_edit(msg: Message, state: FSMContext):
    try:
        parts = msg.text.replace(",", ".").split()
        weight = float(parts[0])
        reps = int(parts[1])
    except (ValueError, IndexError):
        await msg.answer("❌ Формат: <code>85 8</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    set_id = data["edit_set_id"]
    ex_idx = data["edit_ex_idx"]
    name = data["edit_name"]
    update_set(msg.from_user.id, set_id, weight, reps)
    await state.clear()
    rows = get_exercise_history(msg.from_user.id, name)
    text, kb = _render_history(name, rows, ex_idx)
    await msg.answer(f"✅ <b>Записано:</b> {weight} кг × {reps}", parse_mode="HTML")
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
        f"<code>25.09</code> · <code>26.09.2026</code> · <code>2026-09-26</code>",
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
    text, kb = _render_history(name, rows, ex_idx)
    await call.message.edit_text(f"✅ Дата изменена на <b>{iso}</b>", parse_mode="HTML")
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(EditFlow.entering_date)
async def apply_date(msg: Message, state: FSMContext):
    iso = _parse_date(msg.text)
    if not iso:
        await msg.answer(
            "❌ Не понял дату. Попробуй: <code>25.09</code>, <code>26.09.2026</code>",
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
    text, kb = _render_history(name, rows, ex_idx)
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
    text, kb = _render_history(target_name, rows, new_idx)
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
    text, kb = _render_history(name, rows, ex_idx)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════
# FALLBACK
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
