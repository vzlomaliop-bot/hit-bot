import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from program import PROGRAMS
from database import (
    init_db, save_set, get_last,
    get_user_exercises, get_exercise_full,
    get_user_program, set_user_program,
    update_streak, get_streak,
    get_tonnage_by_exercise, get_total_tonnage,
    check_plateau, get_recent_workouts,
)

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()


class Workout(StatesGroup):
    entering_set = State()


def is_owner(user):
    return (user.username or "").lower() == "papirosovv"


def programs_kb(user=None):
    items = []
    for k, p in PROGRAMS.items():
        if k == "papirosov" and not (user and is_owner(user)):
            continue
        items.append([InlineKeyboardButton(text=p["title"], callback_data=f"prog_{k}")])
    return InlineKeyboardMarkup(inline_keyboard=items)


def days_kb(program_key):
    p = PROGRAMS[program_key]
    rows = [[InlineKeyboardButton(text=d["name"], callback_data=f"day_{i}")] for i, d in enumerate(p["days"])]
    rows.append([InlineKeyboardButton(text="📊 Прогресс", callback_data="progress")])
    rows.append([InlineKeyboardButton(text="⬅️ Сменить программу", callback_data="change_prog")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    await state.clear()
    saved = get_user_program(msg.from_user.id)
    if saved and saved in PROGRAMS:
        p = PROGRAMS[saved]
        streak = get_streak(msg.from_user.id)
        streak_text = f"\n🔥 Стрик: {streak} тренировок" if streak > 1 else ""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"prog_{saved}")],
            [InlineKeyboardButton(text="📊 Прогресс", callback_data="progress")],
            [InlineKeyboardButton(text="📈 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="🔄 Сменить программу", callback_data="change_prog")],
        ])
        await msg.answer(f"Твоя программа: <b>{p['title']}</b>{streak_text}\n\n{p['note']}", reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer("Выбери программу тренировок:", reply_markup=programs_kb(msg.from_user))


@dp.message(Command("progress"))
async def progress_cmd(msg: Message):
    await show_exercise_list(msg, msg.from_user.id)


@dp.message(Command("stats"))
async def stats_cmd(msg: Message):
    await show_stats(msg, msg.from_user.id)


@dp.callback_query(F.data == "stats")
async def stats_cb(call: CallbackQuery):
    await call.answer("Загружаю...")
    try:
        await show_stats(call.message, call.from_user.id, edit=True)
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print("STATS ERROR:\n", err)
        try:
            await call.message.answer(f"⚠️ Ошибка статистики: <code>{e}</code>", parse_mode="HTML")
        except:
            await call.message.answer(f"⚠️ Ошибка: {e}")


async def show_stats(message, user_id, edit=False):
    total = get_total_tonnage(user_id)
    by_ex = get_tonnage_by_exercise(user_id)
    streak = get_streak(user_id)
    recent = get_recent_workouts(user_id, 5)

    lines = [f"📈 <b>Твоя статистика</b>", ""]
    lines.append(f"🔥 Стрик: <b>{streak}</b> тренировок подряд")
    lines.append(f"🏋️ Общий тоннаж: <b>{total:,.0f}</b> кг")
    lines.append("")
    lines.append("<b>Тоннаж по упражнениям (только 1-е подходы):</b>")
    for ex, tonnage, count in by_ex[:10]:
        short = ex.split("|")[-1] if "|" in ex else ex
        lines.append(f"• {short}: <b>{tonnage:,.0f}</b> кг ({count} раз)")
    lines.append("")
    lines.append("<b>Последние тренировки:</b>")
    for date, day_name, prog in recent:
        lines.append(f"• {date}: {day_name}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n...<i>(обрезано)</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")]])
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "progress")
async def progress_cb(call: CallbackQuery):
    await call.answer("Загружаю...")
    try:
        await show_exercise_list(call.message, call.from_user.id, edit=True)
    except Exception as e:
        import traceback
        print("PROGRESS ERROR:\n", traceback.format_exc())
        await call.message.answer(f"⚠️ Ошибка прогресса: {e}")


async def show_exercise_list(message, user_id, edit=False):
    exercises = get_user_exercises(user_id)
    if not exercises:
        text = "📊 Пока нет записанных упражнений.\n\nНачни тренировку → данные появятся здесь."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")]])
        if edit:
            await message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        return

    rows = []
    for i, ex in enumerate(exercises):
        short = ex.split("|")[-1] if "|" in ex else ex
        rows.append([InlineKeyboardButton(text=short, callback_data=f"ex_{i}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    text = f"📊 <b>Твой прогресс</b>\n\nВыбери упражнение ({len(exercises)} шт.):"
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("ex_"))
async def show_exercise_history(call: CallbackQuery):
    idx = int(call.data.split("_")[1])
    exercises = get_user_exercises(call.from_user.id)
    if idx >= len(exercises):
        await call.answer("Не найдено")
        return
    ex_key = exercises[idx]
    short = ex_key.split("|")[-1] if "|" in ex_key else ex_key
    rows = get_exercise_full(call.from_user.id, ex_key)

    if not rows:
        await call.answer("Нет данных")
        return

    from collections import OrderedDict
    by_date = OrderedDict()
    for date, w, r, sn, day_name in rows:
        d = date[:10]
        if d not in by_date:
            by_date[d] = {"sets": [], "day": day_name}
        by_date[d]["sets"].append((sn, w, r))

    max_w = max(r[1] for r in rows)
    last_w = rows[-1][1]
    last_r = rows[-1][2]
    total_sessions = len(by_date)

    lines = [
        f"📈 <b>{short}</b>",
        f"",
        f"🔹 Рекорд: <b>{max_w} кг</b>",
        f"🔹 Последний: {last_w} кг × {last_r}",
        f"🔹 Тренировок: {total_sessions}",
        f"",
        f"<b>История:</b>",
    ]
    for d, data in by_date.items():
        sets_str = " • ".join(f"{w}×{r}" for _, w, r in data["sets"])
        lines.append(f"<code>{d}</code> ({data['day']}): {sets_str}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n...<i>(обрезано)</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "change_prog")
async def change_prog(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Выбери программу:", reply_markup=programs_kb(call.from_user))


@dp.callback_query(F.data.startswith("prog_"))
async def choose_program(call: CallbackQuery, state: FSMContext):
    key = call.data.split("_", 1)[1]
    if key not in PROGRAMS:
        await call.answer("Программа не найдена")
        return
    if key == "papirosov" and not is_owner(call.from_user):
        await call.answer("⛔ Эта программа недоступна", show_alert=True)
        return
    set_user_program(call.from_user.id, key)
    p = PROGRAMS[key]
    await call.message.edit_text(
        f"<b>{p['title']}</b>\n\n{p['note']}\n\nВыбери день:",
        reply_markup=days_kb(key),
        parse_mode="HTML",
    )


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

    lines = [f"<b>{day['name']}</b>", ""]
    for ex in day["exercises"]:
        lines.append(f"• {ex['name']} — {ex['sets']}×{ex['reps']}, RIR {ex['rir']}")
    text = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать тренировку", callback_data=f"start_{day_idx}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prog_{saved}")],
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


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
    await call.message.edit_text(f"<b>{day['name']}</b>\n\nНачинаем 👇", parse_mode="HTML")
    await state.set_state(Workout.entering_set)
    await send_current(call.message, state)


async def send_current(message, state):
    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    st = data["st"]
    key = f"{day['name']}|{ex['name']}"
    last = get_last(message.chat.id, data["program"], key)
    hint = ""
    if last:
        hint = "\n📌 Прошлый раз: " + ", ".join(f"{w}кг×{r}" for w, r, _ in last[:3])
        # Автопрогрессия
        if len(last) >= 1:
            last_w, last_r, _ = last[0]
            rep_range = ex["reps"].split("-")
            if len(rep_range) == 2:
                low, high = int(rep_range[0]), int(rep_range[1])
                if last_r >= high:
                    hint += f"\n🚀 Попробуй +2.5 кг (цель {low} повторений)"
                elif last_r < low:
                    hint += f"\n⚠️ Оставь вес, добейся {low} повторений"
    text = (
        f"<b>{ex['name']}</b>\n"
        f"Подход {st + 1} из {ex['sets']}\n"
        f"🎯 {ex['reps']} повторов, RIR {ex['rir']}{hint}\n\n"
        f"Введи: <code>вес повторения</code>\n"
        f"Например: <code>80 8</code>"
    )
    # Кнопка замены упражнения
    subs = ex.get("substitutes", [])
    if subs:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Заменить упражнение", callback_data=f"sub_{data['ex']}")],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data.startswith("sub_"))
async def substitute_exercise(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    subs = ex.get("substitutes", [])
    if not subs:
        await call.answer("Нет замен")
        return
    rows = [[InlineKeyboardButton(text=s, callback_data=f"subsel_{i}")] for i, s in enumerate(subs)]
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="subcancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(f"Выбери замену для <b>{ex['name']}</b>:", reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("subsel_"))
async def select_substitute(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    idx = int(call.data.split("_")[1])
    subs = ex.get("substitutes", [])
    if idx >= len(subs):
        await call.answer("Ошибка")
        return
    new_name = subs[idx]
    # Заменяем имя упражнения в текущем состоянии
    ex_copy = dict(ex)
    ex_copy["name"] = new_name
    day_copy = dict(day)
    day_copy["exercises"] = list(day["exercises"])
    day_copy["exercises"][data["ex"]] = ex_copy
    new_prog = dict(p)
    new_prog["days"] = list(p["days"])
    new_prog["days"][data["day"]] = day_copy
    # Сохраняем в state только новое имя
    await state.update_data(current_ex_name=new_name)
    await call.message.edit_text(f"✅ Заменено на <b>{new_name}</b>", parse_mode="HTML")
    await send_current(call.message, state)


@dp.callback_query(F.data == "subcancel")
async def cancel_sub(call: CallbackQuery, state: FSMContext):
    await send_current(call.message, state)


@dp.message(Workout.entering_set)
async def log_set(msg: Message, state: FSMContext):
    try:
        parts = msg.text.replace(",", ".").split()
        weight = float(parts[0])
        reps = int(parts[1])
    except (ValueError, IndexError):
        await msg.answer("❌ Формат: <code>80 8</code> (вес и повторы)", parse_mode="HTML")
        return

    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    ex_name = data.get("current_ex_name", ex["name"])
    key = f"{day['name']}|{ex_name}"
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
                [InlineKeyboardButton(text="📊 Прогресс", callback_data="progress")],
                [InlineKeyboardButton(text="📈 Статистика", callback_data="stats")],
                [InlineKeyboardButton(text="🔁 К дням", callback_data=f"prog_{prog_key}")],
            ])
            await msg.answer(
                f"🎉 Тренировка завершена!\n\n"
                f"🔥 Стрик: {streak}\n"
                f"🏋️ Тоннаж за тренировку: {tonnage:,.0f} кг\n"
                f"📊 Последнее: {weight}кг × {reps}",
                reply_markup=kb
            )
            return
        await state.update_data(ex=ex_i, st=0, current_ex_name=None)
        await msg.answer(f"✅ {weight}кг × {reps}\n\n➡️ Следующее упражнение")
        await send_current(msg, state)
    else:
        await state.update_data(st=st)
        await msg.answer(f"✅ {weight}кг × {reps}")
        await send_current(msg, state)


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
