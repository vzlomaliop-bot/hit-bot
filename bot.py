import asyncio
import os
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
    get_user_exercises, get_exercise_history,
    delete_set, update_set, move_set,
    get_user_program, set_user_program,
    update_streak, get_streak,
    get_total_tonnage,
)

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()


class Workout(StatesGroup):
    entering_set = State()


class EditFlow(StatesGroup):
    entering_new = State()
    choosing_exercise = State()


# ===== КЛАВИАТУРЫ =====

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


# ===== START =====

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


# ===== ТРЕНИРОВКА =====

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
    idx = int(call.data.split("_")[1])
    p = PROGRAMS[data["program"]]
    day = p["days"][data["day"]]
    ex = day["exercises"][data["ex"]]
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


# ===== ПРОГРЕСС =====

@dp.callback_query(F.data == "progress")
async def progress_cb(call: CallbackQuery):
    names = get_user_exercises(call.from_user.id)
    if not names:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")]
        ])
        await call.message.edit_text(
            "📊 Пока нет записанных упражнений.\n\nНачни тренировку → данные появятся здесь.",
            reply_markup=kb,
        )
        return
    rows = [[InlineKeyboardButton(text=name, callback_data=f"ex_{i}")]
            for i, name in enumerate(names)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="change_prog")])
    await call.message.edit_text(
        f"📊 <b>Твой прогресс</b>\n\nВыбери упражнение ({len(names)} шт.):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("ex_"))
async def show_exercise_history(call: CallbackQuery):
    idx = int(call.data.split("_", 1)[1])
    names = get_user_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Не найдено", show_alert=True)
        return
    name = names[idx]
    rows = get_exercise_history(call.from_user.id, name)
    if not rows:
        await call.answer("Нет данных", show_alert=True)
        return
    text, kb = _render_history(name, rows, idx)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


def _render_history(name, rows, idx):
    by_date = OrderedDict()
    for set_id, date, w, r, sn, ex, day_name in rows:
        d = date[:10]
        if d not in by_date:
            by_date[d] = {"sets": [], "day": day_name or ""}
        by_date[d]["sets"].append((sn, w, r))

    max_w = max(r[2] for r in rows)
    last = rows[-1]
    lines = [
        f"📈 <b>{name}</b>", "",
        f"🔹 Рекорд: <b>{max_w} кг</b>",
        f"🔹 Последний: {last[2]} кг × {last[3]}",
        f"🔹 Тренировок: {len(by_date)}", "",
        "<b>История:</b>",
    ]
    for d, ddata in by_date.items():
        sets_str = " • ".join(f"{w}×{r}" for _, w, r in ddata["sets"])
        lbl = f" <i>({ddata['day']})</i>" if ddata["day"] else ""
        lines.append(f"<code>{d}</code>{lbl}: {sets_str}")
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3400] + "\n...<i>(обрезано)</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать записи", callback_data=f"editlist_{idx}")],
        [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")],
    ])
    return text, kb


# ===== СПИСОК ЗАПИСЕЙ ДЛЯ РЕДАКТИРОВАНИЯ =====

@dp.callback_query(F.data.startswith("editlist_"))
async def edit_list(call: CallbackQuery):
    idx = int(call.data.split("_")[1])
    names = get_user_exercises(call.from_user.id)
    if idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[idx]
    rows = get_exercise_history(call.from_user.id, name)
    if not rows:
        await call.answer("Нет записей")
        return

    # Показываем последние 20 записей в виде кнопок
    display = rows[-20:]
    buttons = []
    for set_id, date, w, r, sn, ex, day_name in reversed(display):
        d = date[:10]
        label = f"{d} • {w}кг×{r}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"rec_{set_id}_{idx}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ex_{idx}")])
    await call.message.edit_text(
        f"✏️ <b>{name}</b>\n\nВыбери запись для изменения "
        f"(показаны последние {len(display)} из {len(rows)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


# ===== ДЕЙСТВИЯ С КОНКРЕТНОЙ ЗАПИСЬЮ =====

@dp.callback_query(F.data.startswith("rec_"))
async def show_record_actions(call: CallbackQuery):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = get_user_exercises(call.from_user.id)
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
    _, date, w, r, sn, ex, day_name = target
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить вес/повторы", callback_data=f"editrec_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="🔀 Перенести в другое упражнение", callback_data=f"moverec_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delrec_{set_id}_{ex_idx}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"editlist_{ex_idx}")],
    ])
    await call.message.edit_text(
        f"📝 <b>{name}</b>\n\n"
        f"📅 {date[:10]}\n"
        f"🏋️ {w} кг × {r} повторений\n"
        f"Подход №{sn}\n\n"
        f"Что делаем?",
        reply_markup=kb, parse_mode="HTML",
    )


# ===== ИЗМЕНИТЬ ВЕС/ПОВТОРЫ =====

@dp.callback_query(F.data.startswith("editrec_"))
async def edit_record(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = get_user_exercises(call.from_user.id)
    name = names[ex_idx]
    await state.update_data(edit_set_id=set_id, edit_ex_idx=ex_idx, edit_name=name)
    await state.set_state(EditFlow.entering_new)
    await call.message.edit_text(
        f"✏️ <b>Изменение записи</b>\n\n"
        f"<b>{name}</b>\n\n"
        f"Введи новые значения: <code>вес повторения</code>\n"
        f"Например: <code>85 8</code>",
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
    await msg.answer(f"✅ Записано: {weight} кг × {reps}")
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


# ===== ПЕРЕНЕСТИ В ДРУГОЕ УПРАЖНЕНИЕ =====

@dp.callback_query(F.data.startswith("moverec_"))
async def move_record(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = get_user_exercises(call.from_user.id)
    current = names[ex_idx]
    others = [n for n in names if n != current]

    await state.update_data(move_set_id=set_id, move_from_idx=ex_idx, move_from_name=current)

    rows = [[InlineKeyboardButton(text=n, callback_data=f"moveto_{n}_{ex_idx}")]
            for n in others]
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"rec_{set_id}_{ex_idx}")])
    await call.message.edit_text(
        f"🔀 <b>Перенести запись</b>\n\n"
        f"Из: <b>{current}</b>\n\n"
        f"Куда перенести?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("moveto_"))
async def apply_move(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    # format: moveto_<name>_<from_idx> — name может содержать "_", поэтому берём последний как idx
    from_idx = int(parts[-1])
    target_name = "_".join(parts[1:-1])

    data = await state.get_data()
    set_id = data.get("move_set_id")
    from_name = data.get("move_from_name")

    if not set_id:
        await call.answer("Ошибка")
        return

    move_set(call.from_user.id, set_id, target_name)
    await state.clear()

    await call.message.edit_text(
        f"✅ Запись перенесена:\n"
        f"Из <b>{from_name}</b> → в <b>{target_name}</b>",
        parse_mode="HTML",
    )
    # Показать историю нового упражнения
    names = get_user_exercises(call.from_user.id)
    try:
        new_idx = names.index(target_name)
    except ValueError:
        return
    rows = get_exercise_history(call.from_user.id, target_name)
    text, kb = _render_history(target_name, rows, new_idx)
    await call.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ===== УДАЛИТЬ =====

@dp.callback_query(F.data.startswith("delrec_"))
async def delete_record(call: CallbackQuery):
    parts = call.data.split("_")
    set_id = int(parts[1])
    ex_idx = int(parts[2])
    names = get_user_exercises(call.from_user.id)
    if ex_idx >= len(names):
        await call.answer("Ошибка")
        return
    name = names[ex_idx]
    delete_set(call.from_user.id, set_id)
    await call.answer("Удалено ✅")

    rows = get_exercise_history(call.from_user.id, name)
    if not rows:
        await call.message.edit_text(
            f"📈 <b>{name}</b>\n\nВсе записи удалены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К упражнениям", callback_data="progress")]
            ]), parse_mode="HTML",
        )
        return
    text, kb = _render_history(name, rows, ex_idx)
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ===== ЗАПУСК =====

async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
