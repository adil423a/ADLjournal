import os
import asyncio
import asyncpg
import random
import calendar

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from collections import defaultdict
from datetime import datetime


TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ───────────────── MOTIVATION ─────────────────
MOTIVATION_MESSAGES = [
    "📊 Дисциплина делает деньги, не эмоции.",
    "🧠 Сначала система — потом прибыль.",
    "📉 Убытки — часть игры. Важно управление риском.",
    "🚀 Главное не заработать быстро, а остаться в игре.",
    "💡 Профессионалы думают в вероятностях, не в эмоциях.",
    "📍 Одна хорошая сделка лучше десяти случайных.",
    "⏱ Рынок вознаграждает терпение, не спешку.",
    "💰 Защищай капитал — это твой главный актив.",
]


# ───────────────── STATES ─────────────────
class AddTrade(StatesGroup):
    symbol = State()
    direction = State()
    entry = State()
    exit_ = State()
    size = State()
    notes = State()


class DeleteTrade(StatesGroup):
    choose = State()


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
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await conn.close()


# ───────────────── HELPERS ─────────────────
def calc_pnl(direction, entry, exit_price, size):
    raw = (exit_price - entry) * size
    return -raw if direction.lower() == "short" else raw


def fmt(v):
    return f"+{v:.2f}" if v > 0 else f"{v:.2f}"


def pnl_emoji(v):
    return "🟢" if v >= 0 else "🔴"


def mot():
    return random.choice(MOTIVATION_MESSAGES)


# ───────────────── START ─────────────────
@dp.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()

    name = msg.from_user.first_name or "трейдер"

    await msg.answer(
        f"👋 Привет, {name}!\n\n"
        "📊 Trading Journal готов к работе.по вопросам пиши @theadil_0\n\n"
        f"🔥💪🏻 {mot()}\n\n"
        "📌 Команды:\n"
        "/new — новая сделка\n"
        "/history — история\n"
        "/stats — статистика\n"
        "/delete — удалить сделку\n"
        "/calendar — совет"
        "/trade — совет"
        "/tip — совет"
    )


# ───────────────── ADD TRADE ─────────────────
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
    text = msg.text.lower().strip()

    if text not in ["long", "short"]:
        await msg.answer("❌ Введи только: long или short")
        return

    await state.update_data(direction=text)
    await state.set_state(AddTrade.entry)

    icon = "▲ LONG" if text == "long" else "▼ SHORT"
    await msg.answer(f"📍 Направление: {icon}\n\n🔵 Цена входа:")


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

    notes = "" if msg.text == "-" else msg.text
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
    f"💰 Сделка сохранена!\n\n"
    f"{data['symbol']} {pnl_emoji(pnl)} {fmt(pnl)}\n\n"
    f"💡 {mot()}\n\n"
    "📌 Что дальше?\n"
    "• /history — посмотреть сделки\n"
    "• /stats — статистика\n"
    "• /calendar — PnL по дням\n"
    "• /new — новая сделка"
)


# ───────────────── HISTORY ─────────────────
@dp.message(Command("history"))
async def history(msg: Message):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT id, symbol, pnl FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
        msg.from_user.id
    )
    await conn.close()

    if not rows:
        await msg.answer("История пуста")
        return

    text = "📋 Последние сделки:\n\n"
    for r in rows:
        text += f"ID: {r['id']} | {r['symbol']} {pnl_emoji(float(r['pnl']))} {fmt(float(r['pnl']))}\n"

    await msg.answer(text)
# ───────────────── trade ─────────────────
@dp.message(Command("trade"))
async def trade_view(msg: Message):
    try:
        parts = msg.text.split()

        if len(parts) < 2:
            await msg.answer("Используй: /trade ID")
            return

        trade_id = int(parts[1])

    except ValueError:
        await msg.answer("❌ ID должен быть числом\nПример: /trade 12")
        return

    conn = await get_db()
    row = await conn.fetchrow(
        "SELECT * FROM trades WHERE id=$1 AND user_id=$2",
        trade_id,
        msg.from_user.id
    )
    await conn.close()

    if not row:
        await msg.answer("Сделка не найдена")
        return

    text = (
        f"📊 Сделка #{row['id']}\n\n"
        f"📌 Символ: {row['symbol']}\n"
        f"📍 Направление: {row['direction']}\n"
        f"🔵 Вход: {row['entry']}\n"
        f"🔵 Выход: {row['exit_price']}\n"
        f"📦 Размер: {row['size']}\n"
        f"💰 PnL: {fmt(float(row['pnl']))}\n\n"
        f"📝 Заметки:\n{row['notes'] or '—'}"
    )

    await msg.answer(text)
# ───────────────── Calendar ─────────────────


@dp.message(Command("calendar"))
async def calendar_view(msg: Message):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT pnl, created_at FROM trades WHERE user_id=$1",
        msg.from_user.id
    )
    await conn.close()

    if not rows:
        await msg.answer("Нет сделок")
        return

    daily = defaultdict(float)

    for r in rows:
        day = r["created_at"].date()
        daily[day] += float(r["pnl"])

    now = datetime.now()
    year, month = now.year, now.month

    month_name = calendar.month_name[month]

    # ───── ANALYTICS ─────
    pnls = list(daily.values())

    total_pnl = sum(pnls)
    wins = len([p for p in pnls if p > 0])
    losses = len([p for p in pnls if p < 0])
    winrate = (wins / len(pnls) * 100) if pnls else 0

    best_day = max(pnls) if pnls else 0
    worst_day = min(pnls) if pnls else 0

    text = f"📅 PnL календарь — {month_name} {year}\n\n"

    # ───── CALENDAR GRID ─────
    cal = calendar.monthcalendar(year, month)

    for week in cal:
        line = ""

        for day in week:
            if day == 0:
                line += "    "
                continue

            date = datetime(year, month, day).date()
            pnl = daily.get(date, 0)

            if pnl > 0:
                icon = "🟩"
            elif pnl < 0:
                icon = "🟥"
            else:
                icon = "⬜️"

            line += f"{day:02d}{icon} "

        text += line + "\n"

    # ───── STATS ─────
    text += "\n📊 Итоги месяца\n\n"
    text += f"💰 PnL: {fmt(total_pnl)}\n"
    text += f"📈 Winrate: {winrate:.1f}%\n"
    text += f"📦 Дней с трейдингом: {len(pnls)}\n\n"
    text += f"🏆 Лучший день: {fmt(best_day)}\n"
    text += f"💀 Худший день: {fmt(worst_day)}"

    await msg.answer(text)
    
    
# ───────────────── STATS ─────────────────
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
        f"Trades: {len(pnls)}\n\n"
        f"💡 {mot()}"
    )


# ───────────────── DELETE FLOW ─────────────────
@dp.message(Command("delete"))
async def delete_start(msg: Message, state: FSMContext):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT id, symbol, pnl FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
        msg.from_user.id
    )
    await conn.close()

    if not rows:
        await msg.answer("Сделок нет")
        return

    await state.update_data(rows=rows)
    await state.set_state(DeleteTrade.choose)

    text = "🗑 Введи номер сделки:\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {r['symbol']} {fmt(float(r['pnl']))}\n"

    await msg.answer(text)


@dp.message(DeleteTrade.choose)
async def delete_choose(msg: Message, state: FSMContext):
    data = await state.get_data()
    rows = data["rows"]

    try:
        index = int(msg.text)
        assert 1 <= index <= len(rows)
    except:
        await msg.answer("Введи корректный номер")
        return

    trade_id = rows[index - 1]["id"]

    conn = await get_db()
    await conn.execute(
        "DELETE FROM trades WHERE id=$1 AND user_id=$2",
        trade_id,
        msg.from_user.id
    )
    await conn.close()

    await state.clear()

    await msg.answer("🗑 Удалено")


# ───────────────── TIP ─────────────────
@dp.message(Command("tip"))
async def tip(msg: Message):
    await msg.answer(f"💡 {mot()}")


# ───────────────── MAIN ─────────────────
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
