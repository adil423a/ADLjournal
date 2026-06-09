import os
import asyncio
import asyncpg

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ── STATES ─────────────────────────────────────────────
class AddTrade(StatesGroup):
    symbol = State()
    direction = State()
    entry = State()
    exit_ = State()
    size = State()
    notes = State()


# ── DB ────────────────────────────────────────────────
async def get_db():
    return await asyncpg.connect(
        DATABASE_URL,
        ssl="require" if "railway" in DATABASE_URL else False
    )


async def init_db():
    conn = await get_db()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT,
            symbol TEXT,
            direction TEXT,
            entry NUMERIC,
            exit_price NUMERIC,
            size NUMERIC,
            pnl NUMERIC,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await conn.close()


# ── HELPERS ───────────────────────────────────────────
def calc_pnl(direction, entry, exit_price, size):
    raw = (exit_price - entry) * size
    return -raw if direction.lower() == "short" else raw


def fmt(v):
    return f"+{v:.2f}" if v > 0 else f"{v:.2f}"


def pnl_emoji(v):
    return "🟢" if v >= 0 else "🔴"


# ── START ─────────────────────────────────────────────
@dp.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "👋 Привет!\n\n"
        "Команды:\n"
        "/new — новая сделка\n"
        "/stats — статистика\n"
        "/history — последние сделки\n"
        "/delete <id> — удалить сделку"
    )


# ── NEW TRADE FLOW ────────────────────────────────────
@dp.message(Command("new"))
async def new_trade(msg: Message, state: FSMContext):
    await state.set_state(AddTrade.symbol)
    await msg.answer("📌 Введи символ (BTC, EURUSD, AAPL):")


@dp.message(AddTrade.symbol)
async def symbol(msg: Message, state: FSMContext):
    await state.update_data(symbol=msg.text.upper())
    await state.set_state(AddTrade.direction)
    await msg.answer("📍 Направление (long / short):")


@dp.message(AddTrade.direction)
async def direction(msg: Message, state: FSMContext):
    await state.update_data(direction=msg.text.lower())
    await state.set_state(AddTrade.entry)
    await msg.answer("🔵 Цена входа:")


@dp.message(AddTrade.entry)
async def entry(msg: Message, state: FSMContext):
    await state.update_data(entry=float(msg.text))
    await state.set_state(AddTrade.exit_)
    await msg.answer("🔵 Цена выхода:")


@dp.message(AddTrade.exit_)
async def exit_price(msg: Message, state: FSMContext):
    await state.update_data(exit_price=float(msg.text))
    await state.set_state(AddTrade.size)
    await msg.answer("📦 Размер позиции:")


@dp.message(AddTrade.size)
async def size(msg: Message, state: FSMContext):
    await state.update_data(size=float(msg.text))
    await state.set_state(AddTrade.notes)
    await msg.answer("📝 Заметки (или '-' если нет):")


@dp.message(AddTrade.notes)
async def notes(msg: Message, state: FSMContext):
    data = await state.get_data()

    notes = msg.text if msg.text != "-" else ""
    pnl = calc_pnl(data["direction"], data["entry"], data["exit_price"], data["size"])

    conn = await get_db()
    await conn.execute(
        """
        INSERT INTO trades(user_id, symbol, direction, entry, exit_price, size, pnl, notes)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8)
        """,
        msg.from_user.id,
        data["symbol"],
        data["direction"],
        data["entry"],
        data["exit_price"],
        data["size"],
        pnl,
        notes
    )
    await conn.close()

    await state.clear()

    await msg.answer(
        f"✅ Сохранено\n\n"
        f"{data['symbol']} {pnl_emoji(pnl)} {fmt(pnl)}"
    )


# ── STATS ─────────────────────────────────────────────
@dp.message(Command("stats"))
async def stats(msg: Message):
    conn = await get_db()
    rows = await conn.fetch("SELECT pnl FROM trades WHERE user_id=$1", msg.from_user.id)
    await conn.close()

    if not rows:
        await msg.answer("Нет сделок")
        return

    pnls = [float(r["pnl"]) for r in rows]
    total = sum(pnls)

    winrate = len([p for p in pnls if p > 0]) / len(pnls) * 100

    await msg.answer(
        f"📊 Статистика\n\n"
        f"PnL: {fmt(total)}\n"
        f"Winrate: {winrate:.1f}%\n"
        f"Trades: {len(pnls)}"
    )


# ── HISTORY ───────────────────────────────────────────
@dp.message(Command("history"))
async def history(msg: Message):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT symbol, direction, pnl FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
        msg.from_user.id
    )
    await conn.close()

    if not rows:
        await msg.answer("История пуста")
        return

    text = "📋 Последние сделки:\n\n"
    for r in rows:
        text += f"{r['symbol']} {pnl_emoji(r['pnl'])} {fmt(float(r['pnl']))}\n"

    await msg.answer(text)


# ── DELETE ────────────────────────────────────────────
@dp.message(Command("delete"))
async def delete(msg: Message):
    parts = msg.text.split()

    if len(parts) < 2:
        await msg.answer("Используй: /delete ID")
        return

    trade_id = int(parts[1])

    conn = await get_db()
    await conn.execute(
        "DELETE FROM trades WHERE id=$1 AND user_id=$2",
        trade_id,
        msg.from_user.id
    )
    await conn.close()

    await msg.answer("🗑 Удалено")


# ── MAIN ──────────────────────────────────────────────
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
