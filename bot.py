import asyncio
import os
import random
from collections import OrderedDict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from program import PROGRAMS
from database import (
    init_db, save_set, get_last,
    get_user_exercises, get_exercise_full,
    get_user_program, set_user_program,
    update_streak, get_streak,
    get_total_tonnage,
)

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()


class Workout(StatesGroup):
    entering_set = State()


def programs_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p["title"], callback_data=f"prog_{k}")]
        for k, p in PROGRAMS.items()
    ])


def days_kb(program_key):
    p = PROGRAMS[program_key]
    rows = [[InlineKeyboardButton(text=d["name"], callback_data=f"day_{i}")]
            for i, d in enumerate(p["days"])]
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
        streak_text = f"\n🔥 Стрик: {streak}" if streak > 1 else ""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"prog_{saved}")],
            [InlineKeyboardButton(text="📊 Прогресс", callback_data="progress")],
            [InlineKeyboardButton(text="🔄 Сменить программу", callback_data="change_prog")],
        ])
        await msg.answer(
            f"Твоя программа: <b>{p['title']}</b>{streak_text}\n\n{p['note']}",
            reply_markup=kb, parse_mode="HTML",
        )
    else:
        await msg.answer("Выбери программу тренировок:", reply_markup=programs_kb())


@dp.callback_query(F.data == "change_prog")
async def change_prog(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Выбери программу:", reply_markup=programs_kb())


@dp.callback_query(F.data.startswith("prog_"))
async def choose_program(call: CallbackQuery, state: FSMContext):
    key = call.data.split("_", 1)[1]
    if key not in PROGRAMS:
        await call.answer("Программа не найдена")
        return
    set_user_program(call.from_user.id, key)
    p = PROGRAMS[key]
    await call.message.edit_text(
        f"<b>{p['title']}</b>\n\n{p['note']}\n\nВыбери день:",
        reply_markup=days_kb(key), parse_mode="HTML",
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать тренировку", callback_data=f"start_{day_idx}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prog_{saved}")],
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
    await state.update_data(program=saved, day=day_idx, ex=0, st=0, current_ex_name=None)
    await call.message.edit_text(f"<b>{day['name']}</b>\n\nНачинаем 👇", parse_mode="HTML")
    await state.set_state(Workout.entering_set)
    await send_current(call.message, state)


async def send_current(message, state):
    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    st = data["st"]
    ex_name = data.get("current_ex_name") or ex["name"]
    key = f"{day['name']}|{ex_name}"
    last = get_last(message.chat.id, data["program"], key)
    hint = ""
    if last:
        hint = "\n📌 Прошлый раз: " + ", ".join(f"{w}кг×{r}" for w, r, _ in last[:3])
    text = (
        f"<b>{ex_name}</b>\n"
        f"Подход {st + 1} из {ex['sets']}\n"
        f"🎯 {ex['reps']} повторов, RIR {ex['rir']}{hint}\n\n"
        f"Введи: <code>вес повторения</code>\n"
        f"Например: <code>80 8</code>"
    )
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
    await call.message.edit_text(
        f"Выбери замену для <b>{ex['name']}</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
    )


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
    await state.update_data(current_ex_name=subs[idx])
    await call.message.edit_text(f"✅ Заменено на <b>{subs[idx]}</b>", parse_mode="HTML")
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
        await msg.answer("❌ Формат: <code>80 8</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
    ex_name = data.get("current_ex_name") or ex["name"]
    ex_name_safe = (ex_name or "").strip() or "Без названия"
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
                [InlineKeyboardButton(text="📊 Прогресс", callback_data="progress")],
                [InlineKeyboardButton(text="🔁 К дням", callback_data=f"prog_{prog_key}")],
            ])
            await msg.answer(
                f"🎉 Тренировка завершена!\n\n🔥 Стрик: {streak}\n"
                f"🏋️ Тоннаж: {tonnage:,.0f} кг\n📊 Последнее: {weight}кг × {reps}",
                reply_markup=kb,
            )
            return
        await state.update_data(ex=ex_i, st=0, current_ex_name=None)
        await msg.answer(f"✅ {weight}кг × {reps}\n\n➡️ Следующее упражнение")
        await send_current(msg, state)
    else:
        await state.update_data(st=st)
        await msg.answer(f"✅ {weight}кг × {reps}")
        await send_current(msg, state)


@dp.callback_query(F.data == "progress")
async def progress_cb(call: CallbackQuery):
    exercises = get_user_exercises(call.from_user.id)
    cleaned = []
    seen = set()
    for i, ex in enumerate(exercises):
        if not ex:
            continue
        name = ex.split("|")[-1].strip()
        if not name or name.lower() in ("none", "null", "nan", "0"):
            continue
        if name in seen:
            continue
        seen.add(name)
        cleaned.append((i, ex, name))

    if not cleaned:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")]
        ])
        await call.message.edit_text(
            "📊 Пока нет записанных упражнений.\n\nНачни тренировку → данные появятся здесь.",
            reply_markup=kb,
        )
        return

    rows = [[InlineKeyboardButton(text=name, callback_data=f"ex_{idx}")] for idx, _, name in cleaned]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")])
    await call.message.edit_text(
        f"📊 <b>Твой прогресс</b>\n\nВыбери упражнение ({len(cleaned)} шт.):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("ex_"))
async def show_exercise_history(call: CallbackQuery):
    try:
        idx = int(call.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await call.answer("Ошибка")
        return
    exercises = get_user_exercises(call.from_user.id)
    if idx >= len(exercises):
        await call.answer("Не найдено", show_alert=True)
        return
    ex_key = exercises[idx]
    name = ex_key.split("|")[-1].strip()
    if not name or name.lower() in ("none", "null", "nan", "0"):
        await call.answer("Запись повреждена", show_alert=True)
        return
    rows = get_exercise_full(call.from_user.id, ex_key)
    if not rows:
        await call.answer("Нет данных", show_alert=True)
        return

    by_date = OrderedDict()
    for date, w, r, sn, day_name in rows:
        d = date[:10]
        if d not in by_date:
            by_date[d] = {"sets": [], "day": day_name or ""}
        by_date[d]["sets"].append((sn, w, r))

    max_w = max(r[1] for r in rows)
    last_w = rows[-1][1]
    last_r = rows[-1][2]
    lines = [
        f"📈 <b>{name}</b>", "",
        f"🔹 Рекорд: <b>{max_w} кг</b>",
        f"🔹 Последний: {last_w} кг × {last_r}",
        f"🔹 Тренировок: {len(by_date)}", "",
        "<b>История:</b>",
    ]
    for d, ddata in by_date.items():
        sets_str = " • ".join(f"{w}×{r}" for _, w, r in ddata["sets"])
        lbl = f" <i>({ddata['day']})</i>" if ddata["day"] else ""
        lines.append(f"<code>{d}</code>{lbl}: {sets_str}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n...<i>(обрезано)</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
