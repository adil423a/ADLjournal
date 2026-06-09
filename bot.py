import os
import asyncio
import asyncpg
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

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
def pnl(direction, entry, exit_price, size):
    raw = (exit_price - entry) * size
    return -raw if direction.lower() == "short" else raw


def fmt(x):
    return f"+{x:.2f}" if x > 0 else f"{x:.2f}"


def equity_curve(pnls):
    eq = []
    total = 0
    for p in pnls:
        total += p
        eq.append(total)
    return eq


def profit_factor(pnls):
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    return wins / losses if losses else float("inf")


def expectancy(pnls):
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    winrate = len(wins) / len(pnls) if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    return winrate * avg_win - (1 - winrate) * avg_loss


def max_drawdown(eq):
    peak = -float("inf")
    dd = 0
    for x in eq:
        peak = max(peak, x)
        dd = min(dd, x - peak)
    return dd


def insights(rows):
    txt = []

    long = [float(r["pnl"]) for r in rows if r["direction"].lower() == "long"]
    short = [float(r["pnl"]) for r in rows if r["direction"].lower() == "short"]

    if long and sum(long) < sum(short):
        txt.append("SHORT стратегии работают лучше LONG")

    if len([r for r in rows if float(r["pnl"]) < 0]) / len(rows) > 0.6:
        txt.append("Ты часто пересиживаешь убытки")

    if len(rows) > 20:
        last10 = [float(r["pnl"]) for r in rows[:10]]
        prev10 = [float(r["pnl"]) for r in rows[10:20]]
        if sum(last10) < sum(prev10):
            txt.append("Последние сделки хуже предыдущих → возможно tilt")

    return txt


# ───────────────── START ─────────────────
@dp.message(CommandStart())
async def start(msg: Message):
    await msg.answer(
        "📊 Trading Journal Bot (Level 3)\n\n"
        "Команды:\n"
        "/new — добавить сделку\n"
        "/stats — аналитика\n"
        "/history — сделки\n"
        "/delete ID — удалить"
    )


# ───────────────── NEW TRADE ─────────────────
@dp.message(Command("new"))
async def new(msg: Message, state: FSMContext):
    await state.set_state(AddTrade.symbol)
    await msg.answer("Symbol:")


@dp.message(AddTrade.symbol)
async def s(msg, state):
    await state.update_data(symbol=msg.text.upper())
    await state.set_state(AddTrade.direction)
    await msg.answer("Direction (long/short):")


@dp.message(AddTrade.direction)
async def d(msg, state):
    await state.update_data(direction=msg.text.lower())
    await state.set_state(AddTrade.entry)
    await msg.answer("Entry price:")


@dp.message(AddTrade.entry)
async def e(msg, state):
    await state.update_data(entry=float(msg.text))
    await state.set_state(AddTrade.exit_)
    await msg.answer("Exit price:")


@dp.message(AddTrade.exit_)
async def x(msg, state):
    await state.update_data(exit_price=float(msg.text))
    await state.set_state(AddTrade.size)
    await msg.answer("Size:")


@dp.message(AddTrade.size)
async def size(msg, state):
    await state.update_data(size=float(msg.text))
    await state.set_state(AddTrade.tag)
    await msg.answer("Tag (scalp/swing/breakout):")


@dp.message(AddTrade.tag)
async def tag(msg, state):
    await state.update_data(tag=msg.text)
    await state.set_state(AddTrade.mood)
    await msg.answer("Mood (confident/revenge/tilt):")


@dp.message(AddTrade.mood)
async def mood(msg, state):
    data = await state.get_data()

    conn = await get_db()

    pnl_value = pnl(
        data["direction"],
        data["entry"],
        data["exit_price"],
        data["size"]
    )

    await conn.execute("""
        INSERT INTO trades(user_id, symbol, direction, entry, exit_price, size, pnl, tag, mood)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
    """, msg.from_user.id,
        data["symbol"], data["direction"],
        data["entry"], data["exit_price"], data["size"],
        pnl_value, data["tag"], msg.text
    )

    await conn.close()
    await state.clear()

    await msg.answer(f"Saved {data['symbol']} {fmt(pnl_value)}")


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
        await msg.answer("No trades")
        return

    pnls = [float(r["pnl"]) for r in rows]

    eq = equity_curve(pnls)

    # graph
    plt.figure()
    plt.plot(eq)
    plt.title("Equity Curve")
    plt.savefig("equity.png")
    plt.close()

    pf = profit_factor(pnls)
    ex = expectancy(pnls)
    dd = max_drawdown(eq)

    ins = insights(rows)

    text = (
        f"📊 DASHBOARD\n\n"
        f"PnL: {sum(pnls):.2f}\n"
        f"PF: {pf:.2f}\n"
        f"Expectancy: {ex:.2f}\n"
        f"Max DD: {dd:.2f}\n\n"
    )

    if ins:
        text += "🧠 Insights:\n" + "\n".join("- " + i for i in ins)

    await msg.answer(text)
    await msg.answer_photo(FSInputFile("equity.png"))


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
        text += f"{r['symbol']} | {r['direction']} | {r['pnl']:.2f} | {r['tag']} | {r['mood']}\n"

    await msg.answer(text)


# ───────────────── DELETE ─────────────────
@dp.message(Command("delete"))
async def delete(msg: Message):
    try:
        trade_id = int(msg.text.split()[1])
    except:
        await msg.answer("Use /delete ID")
        return

    conn = await get_db()
    await conn.execute(
        "DELETE FROM trades WHERE id=$1 AND user_id=$2",
        trade_id, msg.from_user.id
    )
    await conn.close()

    await msg.answer("Deleted")


# ───────────────── MAIN ─────────────────
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
