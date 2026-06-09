import os
import asyncio
import asyncpg
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ── States ────────────────────────────────────────────────────────────────────
class AddTrade(StatesGroup):
    symbol    = State()
    direction = State()
    entry     = State()
    exit_     = State()
    size      = State()
    notes     = State()

# ── DB ────────────────────────────────────────────────────────────────────────
async def get_db():
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT NOT NULL,
            symbol     TEXT NOT NULL,
            direction  TEXT NOT NULL,
            entry      NUMERIC NOT NULL,
            exit_price NUMERIC NOT NULL,
            size       NUMERIC NOT NULL,
            pnl        NUMERIC NOT NULL,
            notes      TEXT DEFAULT '',
            date       DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await conn.close()

# ── Keyboards ────────────────────────────────────────────────────────────────
def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новая сделка", callback_data="new_trade")],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="📋 История",    callback_data="history"),
        ],
    ])

def direction_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▲ LONG",  callback_data="dir_Long"),
            InlineKeyboardButton(text="▼ SHORT", callback_data="dir_Short"),
        ]
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сохранить", callback_data="confirm_trade"),
            InlineKeyboardButton(text="❌ Отмена",    callback_data="cancel"),
        ]
    ])

# ── Helpers ───────────────────────────────────────────────────────────────────
def calc_pnl(direction, entry, exit_price, size):
    raw = (exit_price - entry) * size
    return -raw if direction == "Short" else raw

def fmt(v, sign=True):
    s = "+" if sign and v > 0 else ""
    return f"{s}{v:.2f}"

def pnl_emoji(v):
    return "🟢" if v >= 0 else "🔴"

# ── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    name = msg.from_user.first_name
    await msg.answer(
        f"Привет, {name}! 👋\n\n"
        "Я твой <b>торговый журнал</b>.\n"
        "Записывай сделки, смотри статистику и следи за PnL.",
        parse_mode="HTML",
        reply_markup=main_kb()
    )

@dp.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Главное меню:", reply_markup=main_kb())

# ── CANCEL ────────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "cancel")
async def cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено.", reply_markup=main_kb())

# ── ADD TRADE FLOW ────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "new_trade")
async def new_trade(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddTrade.symbol)
    await cb.message.edit_text(
        "📝 <b>Новая сделка</b>\n\nШаг 1/5 — Введи инструмент:\n<i>Например: BTC, EURUSD, AAPL</i>",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )

@dp.message(AddTrade.symbol)
async def got_symbol(msg: Message, state: FSMContext):
    await state.update_data(symbol=msg.text.strip().upper())
    await state.set_state(AddTrade.direction)
    await msg.answer(
        "Шаг 2/5 — Направление сделки:",
        reply_markup=direction_kb()
    )

@dp.callback_query(F.data.startswith("dir_"))
async def got_direction(cb: CallbackQuery, state: FSMContext):
    direction = cb.data.split("_")[1]
    await state.update_data(direction=direction)
    await state.set_state(AddTrade.entry)
    await cb.message.edit_text(
        f"Направление: <b>{'▲ LONG' if direction=='Long' else '▼ SHORT'}</b>\n\n"
        "Шаг 3/5 — Цена <b>входа</b>:",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )

@dp.message(AddTrade.entry)
async def got_entry(msg: Message, state: FSMContext):
    try:
        entry = float(msg.text.replace(",", "."))
        if entry <= 0: raise ValueError
    except ValueError:
        await msg.answer("Введи корректное число, например: 42500.5")
        return
    await state.update_data(entry=entry)
    await state.set_state(AddTrade.exit_)
    await msg.answer("Шаг 4/5 — Цена <b>выхода</b>:", parse_mode="HTML", reply_markup=cancel_kb())

@dp.message(AddTrade.exit_)
async def got_exit(msg: Message, state: FSMContext):
    try:
        exit_price = float(msg.text.replace(",", "."))
        if exit_price <= 0: raise ValueError
    except ValueError:
        await msg.answer("Введи корректное число, например: 43200")
        return
    await state.update_data(exit_price=exit_price)
    await state.set_state(AddTrade.size)
    await msg.answer("Шаг 5/5 — <b>Объём</b> (лоты / количество):", parse_mode="HTML", reply_markup=cancel_kb())

@dp.message(AddTrade.size)
async def got_size(msg: Message, state: FSMContext):
    try:
        size = float(msg.text.replace(",", "."))
        if size <= 0: raise ValueError
    except ValueError:
        await msg.answer("Введи корректное число, например: 0.5")
        return
    await state.update_data(size=size)
    await state.set_state(AddTrade.notes)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_notes")],
        [InlineKeyboardButton(text="❌ Отмена",     callback_data="cancel")],
    ])
    await msg.answer("Заметки (стратегия, причина входа):", reply_markup=kb)

@dp.callback_query(F.data == "skip_notes")
async def skip_notes(cb: CallbackQuery, state: FSMContext):
    await state.update_data(notes="")
    await show_confirm(cb.message, state)

@dp.message(AddTrade.notes)
async def got_notes(msg: Message, state: FSMContext):
    await state.update_data(notes=msg.text.strip())
    await show_confirm(msg, state)

async def show_confirm(msg, state: FSMContext):
    data = await state.get_data()
    pnl = calc_pnl(data["direction"], data["entry"], data["exit_price"], data["size"])
    emoji = pnl_emoji(pnl)
    dir_str = "▲ LONG" if data["direction"] == "Long" else "▼ SHORT"
    text = (
        f"<b>Проверь сделку:</b>\n\n"
        f"📌 Инструмент: <b>{data['symbol']}</b>\n"
        f"📍 Направление: <b>{dir_str}</b>\n"
        f"🔵 Вход: <b>{data['entry']}</b>\n"
        f"🔵 Выход: <b>{data['exit_price']}</b>\n"
        f"📦 Объём: <b>{data['size']}</b>\n"
        f"{emoji} PnL: <b>{fmt(pnl)}</b>\n"
    )
    if data.get("notes"):
        text += f"📝 Заметки: {data['notes']}\n"
    await state.update_data(pnl=pnl)
    if hasattr(msg, "edit_text"):
        await msg.edit_text(text, parse_mode="HTML", reply_markup=confirm_kb())
    else:
        await msg.answer(text, parse_mode="HTML", reply_markup=confirm_kb())

@dp.callback_query(F.data == "confirm_trade")
async def confirm_trade(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    conn = await get_db()
    await conn.execute(
        "INSERT INTO trades(user_id,symbol,direction,entry,exit_price,size,pnl,notes) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
        cb.from_user.id, data["symbol"], data["direction"],
        data["entry"], data["exit_price"], data["size"],
        data["pnl"], data.get("notes","")
    )
    await conn.close()
    await state.clear()
    pnl = data["pnl"]
    await cb.message.edit_text(
        f"{pnl_emoji(pnl)} Сделка сохранена!\n\n"
        f"<b>{data['symbol']}</b> · {fmt(pnl)} PnL",
        parse_mode="HTML",
        reply_markup=main_kb()
    )

# ── STATISTICS ────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "stats")
async def show_stats(cb: CallbackQuery):
    conn = await get_db()
    rows = await conn.fetch("SELECT pnl FROM trades WHERE user_id=$1", cb.from_user.id)
    await conn.close()

    if not rows:
        await cb.message.edit_text("Статистики пока нет — добавь первую сделку!", reply_markup=main_kb())
        return

    pnls = [float(r["pnl"]) for r in rows]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    winrate = len(wins) / len(pnls) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    best = max(pnls)
    worst = min(pnls)

    bar_len = 12
    win_blocks = round(winrate / 100 * bar_len)
    bar = "🟩" * win_blocks + "🟥" * (bar_len - win_blocks)

    text = (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"💰 Общий PnL: <b>{fmt(total)}</b>\n"
        f"📈 Винрейт: <b>{winrate:.1f}%</b>\n"
        f"{bar}\n\n"
        f"📦 Всего сделок: <b>{len(pnls)}</b>\n"
        f"✅ Прибыльных: <b>{len(wins)}</b>\n"
        f"❌ Убыточных: <b>{len(losses)}</b>\n\n"
        f"⚖️ Risk/Reward: <b>{rr:.2f}</b>\n"
        f"📈 Ср. выигрыш: <b>+{avg_win:.2f}</b>\n"
        f"📉 Ср. потеря: <b>-{avg_loss:.2f}</b>\n\n"
        f"🏆 Лучшая: <b>+{best:.2f}</b>\n"
        f"💀 Худшая: <b>{worst:.2f}</b>"
    )
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=main_kb())

# ── HISTORY ───────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "history")
async def show_history(cb: CallbackQuery):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT * FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
        cb.from_user.id
    )
    await conn.close()

    if not rows:
        await cb.message.edit_text("История пуста — добавь первую сделку!", reply_markup=main_kb())
        return

    lines = ["📋 <b>Последние 10 сделок:</b>\n"]
    for r in rows:
        pnl = float(r["pnl"])
        d = r["direction"]
        dir_icon = "▲" if d == "Long" else "▼"
        date_str = r["date"].strftime("%d.%m") if r["date"] else ""
        lines.append(
            f"{pnl_emoji(pnl)} <b>{r['symbol']}</b> {dir_icon} "
            f"{fmt(pnl)}  <i>{date_str}</i>"
        )

    await cb.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=main_kb())

# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
