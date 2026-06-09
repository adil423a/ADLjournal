import os
import asyncio
import asyncpg

from PIL import Image, ImageDraw

from aiogram import Bot, Dispatcher
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ───────────────── CONFIG ─────────────────
TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ───────────────── STATES ─────────────────
class AddTrade(StatesGroup):
    symbol = State()
    direction = State()
    entry = State()
    exit_ = State()
    size = State()
    tag = State()
    mood = State()


# ───────────────── DB ─────────────────
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
            tag TEXT DEFAULT '',
            mood TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await conn.close()


# ───────────────── HELPERS ─────────────────
def calc_pnl(direction, entry, exit_price, size):
    raw = (exit_price - entry) * size
    return -raw if direction.lower() == "short" else raw


def fmt(x):
    return f"+{x:.2f}" if x > 0 else f"{x:.2f}"


# ───────────────── EQUITY CURVE (NO MATPLOTLIB) ─────────────────
def draw_equity_curve(pnls):
    width, height = 900, 400
    img = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(img)

    equity = []
    total = 0

    for p in pnls:
        total += p
        equity.append(total)

    if not equity:
        return None

    min_eq = min(equity)
    max_eq = max(equity)

    def scale(v):
        if max_eq == min_eq:
            return height // 2
        return height - int((v - min_eq) / (max_eq - min_eq) * (height - 40)) - 20

    step = width / max(len(equity) - 1, 1)

    points = []
    for i, v in enumerate(equity):
        x = int(i * step)
        y = scale(v)
        points.append((x, y))

    # grid
    for x in range(0, width, 100):
        draw.line([(x, 0), (x, height)], fill=(30, 30, 30))

    # zero line
    draw.line([(0, scale(0)), (width, scale(0))], fill=(80, 80, 80))

    # equity line
    for i in range(len(points) - 1):
        draw.line([points[i], points[i + 1]], fill=(0, 255, 120), width=3)

    path = "equity.png"
    img.save(path)
    return path


# ───────────────── START ─────────────────
@dp.message(CommandStart())
async def start(msg: Message):
    await msg.answer(
        "📊 Trading Journal Bot (Level 3)\n\n"
        "Команды:\n"
        "/new — добавить сделку\n"
        "/stats — аналитика\n"
        "/history — последние сделки\n"
        "/delete ID — удалить"
    )


# ───────────────── NEW TRADE FLOW ─────────────────
@dp.message(Command("new"))
async def new(msg: Message, state: FSMContext):
    await state.set_state(AddTrade.symbol)
    await msg.answer("📌 Symbol:")


@dp.message(AddTrade.symbol)
async def symbol(msg: Message, state: FSMContext):
    await state.update_data(symbol=msg.text.upper())
    await state.set_state(AddTrade.direction)
    await msg.answer("📍 Direction (long/short):")


@dp.message(AddTrade.direction)
async def direction(msg: Message, state: FSMContext):
    await state.update_data(direction=msg.text.lower())
    await state.set_state(AddTrade.entry)
    await msg.answer("🔵 Entry price:")


@dp.message(AddTrade.entry)
async def entry(msg: Message, state: FSMContext):
    await state.update_data(entry=float(msg.text))
    await state.set_state(AddTrade.exit_)
    await msg.answer("🔵 Exit price:")


@dp.message(AddTrade.exit_)
async def exit_price(msg: Message, state: FSMContext):
    await state.update_data(exit_price=float(msg.text))
    await state.set_state(AddTrade.size)
    await msg.answer("📦 Size:")


@dp.message(AddTrade.size)
async def size(msg: Message, state: FSMContext):
    await state.update_data(size=float(msg.text))
    await state.set_state(AddTrade.tag)
    await msg.answer("🏷 Tag (scalp/swing/breakout):")


@dp.message(AddTrade.tag)
async def tag(msg: Message, state: FSMContext):
    await state.update_data(tag=msg.text)
    await state.set_state(AddTrade.mood)
    await msg.answer("🧠 Mood (confident/revenge/tilt):")


@dp.message(AddTrade.mood)
async def mood(msg: Message, state: FSMContext):
    data = await state.get_data()

    pnl_value = calc_pnl(
        data["direction"],
        data["entry"],
        data["exit_price"],
        data["size"]
    )

    conn = await get_db()
    await conn.execute("""
        INSERT INTO trades(
            user_id, symbol, direction, entry,
            exit_price, size, pnl, tag, mood
        )
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
    """,
        msg.from_user.id,
        data["symbol"],
        data["direction"],
        data["entry"],
        data["exit_price"],
        data["size"],
        pnl_value,
        data["tag"],
        msg.text
    )
    await conn.close()
    await state.clear()

    await msg.answer(f"✅ Saved {data['symbol']} {fmt(pnl_value)}")


# ───────────────── STATS ─────────────────
@dp.message(Command("stats"))
async def stats(msg: Message):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT * FROM trades WHERE user_id=$1 ORDER BY created_at",
        msg.from_user.id
    )
    await conn.close()

    if not rows:
        await msg.answer("No trades yet")
        return

    pnls = [float(r["pnl"]) for r in rows]

    total = sum(pnls)
    wins = len([p for p in pnls if p > 0])
    losses = len([p for p in pnls if p < 0])
    winrate = wins / len(pnls) * 100 if pnls else 0

    # equity graph
    path = draw_equity_curve(pnls)

    text = (
        f"📊 DASHBOARD\n\n"
        f"PnL: {total:.2f}\n"
        f"Winrate: {winrate:.1f}%\n"
        f"Trades: {len(pnls)}\n"
    )

    await msg.answer(text)

    if path:
        await msg.answer_photo(FSInputFile(path))


# ───────────────── HISTORY ─────────────────
@dp.message(Command("history"))
async def history(msg: Message):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT * FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
        msg.from_user.id
    )
    await conn.close()

    text = "📋 Trades:\n\n"
    for r in rows:
        text += (
            f"{r['symbol']} | {r['direction']} | "
            f"{float(r['pnl']):.2f} | {r['tag']} | {r['mood']}\n"
        )

    await msg.answer(text)


# ───────────────── DELETE ─────────────────
@dp.message(Command("delete"))
async def delete(msg: Message):
    try:
        trade_id = int(msg.text.split()[1])
    except:
        await msg.answer("Usage: /delete ID")
        return

    conn = await get_db()
    await conn.execute(
        "DELETE FROM trades WHERE id=$1 AND user_id=$2",
        trade_id,
        msg.from_user.id
    )
    await conn.close()

    await msg.answer("🗑 Deleted")


# ───────────────── MAIN ─────────────────
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
