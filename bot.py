import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://")

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
    return await asyncpg.connect(DATABASE_URL, ssl='require' if 'railway' in DATABASE_URL else False)

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

# ── Keyboards ─────────────────────────────────────────────────────────────────
def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новая сделка", callback_data="new_trade")],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="📋 История",    callback_data="history"),
        ],
    ])

def direction_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▲ LONG",  callback_data="dir_Long"),
        InlineKeyboardButton(text="▼ SHORT", callback_data="dir_Short"),
    ]])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    ]])

def confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Сохранить", callback_data="confirm_trade"),
        InlineKeyboardButton(text="❌ Отмена",    callback_data="cancel"),
    ]])

# ── Helpers ───────────────────────────────────────────────────────────────────
def calc_pnl(direction, entry, exit_price, size):
    raw = (exit_price - entry) * size
    return -raw if direction == "Short" else raw

def fmt(v, sign=True):
    return ("+" if sign and v > 0 else "") + f"{v:.2f}"

def pnl_emoji(v):
    return "🟢" if v >= 0 else "🔴"

def build_stats_text(pnls):
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    winrate = len(wins) / len(pnls) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    rr = avg_win / avg_loss if avg_loss > 0 else 0
    bar = "🟩" * round(winrate / 100 * 12) + "🟥" * (12 - round(winrate / 100 * 12))
    return (
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
        f"🏆 Лучшая: <b>+{max(pnls):.2f}</b>\n"
        f"💀 Худшая: <b>{min(pnls):.2f}</b>"
    )

def build_history_text(rows):
    lines = ["📋 <b>Последние 10 сделок:</b>\n"]
    for r in rows:
        pnl = float(r["pnl"])
        dir_icon = "▲" if r["direction"] == "Long" else "▼"
        date_str = r["date"].strftime("%d.%m") if r["date"] else ""
        lines.append(f"{pnl_emoji(pnl)} <b>{r['symbol']}</b> {dir_icon} {fmt(pnl)}  <i>{date_str}</i>")
    return "\n".join(lines)

# ── /start & /menu ────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        f"Привет, {msg.from_user.first_name}! 👋\n\n"
        "<b>Торговый журнал</b> — записывай сделки и следи за PnL.\n\n"
        "📌 <b>Команды:</b>\n"
        "/new — добавить новую сделку\n"
        "/stats — статистика и PnL\n"
        "/history — последние 10 сделок\n"
        "/delete — удалить сделку\n"
        "/menu — главное меню",
        parse_mode="HTML",
        reply_markup=main_kb()
    )

@dp.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Главное меню:", reply_markup=main_kb())

# ── /new ──────────────────────────────────────────────────────────────────────
@dp.message(Command("new"))
@dp.callback_query(F.data == "new_trade")
async def new_trade(event, state: FSMContext):
    await state.set_state(AddTrade.symbol)
    text = "📝 <b>Новая сделка</b>\n\nШаг 1/5 — Введи инструмент:\n<i>Например: BTC, EURUSD, AAPL</i>"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=cancel_kb())
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=cancel_kb())

# ── /stats ────────────────────────────────────────────────────────────────────
@dp.message(Command("stats"))
@dp.callback_query(F.data == "stats")
async def show_stats(event, **kwargs):
    user_id = event.from_user.id
    conn = await get_db()
    rows = await conn.fetch("SELECT pnl FROM trades WHERE user_id=$1", user_id)
    await conn.close()
    is_cb = isinstance(event, CallbackQuery)
    if not rows:
        text = "Статистики пока нет — добавь первую сделку!"
        if is_cb: await event.message.edit_text(text, reply_markup=main_kb())
        else: await event.answer(text, reply_markup=main_kb())
        return
    text = build_stats_text([float(r["pnl"]) for r in rows])
    if is_cb: await event.message.edit_text(text, parse_mode="HTML", reply_markup=main_kb())
    else: await event.answer(text, parse_mode="HTML", reply_markup=main_kb())

# ── /history ──────────────────────────────────────────────────────────────────
@dp.message(Command("history"))
@dp.callback_query(F.data == "history")
async def show_history(event, **kwargs):
    user_id = event.from_user.id
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT * FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10", user_id
    )
    await conn.close()
    is_cb = isinstance(event, CallbackQuery)
    if not rows:
        text = "История пуста — добавь первую сделку!"
        if is_cb: await event.message.edit_text(text, reply_markup=main_kb())
        else: await event.answer(text, reply_markup=main_kb())
        return
    text = build_history_text(rows)
    if is_cb: await event.message.edit_text(text, parse_mode="HTML", reply_markup=main_kb())
    else: await event.answer(text, parse_mode="HTML", reply_markup=main_kb())

# ── /delete ───────────────────────────────────────────────────────────────────
@dp.message(Command("delete"))
async def cmd_delete(msg: Message):
    conn = await get_db()
    rows = await conn.fetch(
        "SELECT id, symbol, direction, pnl, date FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
        msg.from_user.id
    )
    await conn.close()
    if not rows:
        await msg.answer("Сделок нет.", reply_markup=main_kb())
        return
    lines = ["🗑 <b>Выбери сделку для удаления:</b>\n"]
    buttons = []
    for i, r in enumerate(rows, 1):
        pnl = float(r["pnl"])
        date_str = r["date"].strftime("%d.%m") if r["date"] else ""
        lines.append(f"{i}. {pnl_emoji(pnl)} <b>{r['symbol']}</b> {fmt(pnl)}  <i>{date_str}</i>")
        buttons.append([InlineKeyboardButton(
            text=f"❌ {i}. {r['symbol']} {fmt(pnl)}",
            callback_data=f"del_{r['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    await msg.answer("\n".join(lines), parse_mode="HTML",
                     reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("del_"))
async def do_delete(cb: CallbackQuery):
    trade_id = int(cb.data.split("_")[1])
    conn = await get_db()
    await conn.execute("DELETE FROM trades WHERE id=$1 AND user_id=$2", trade_id, cb.from_user.id)
    await conn.close()
    await cb.message.edit_text("✅ Сделка удалена.", reply_markup=main_kb())

# ── CANCEL ────────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "cancel")
async def cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено.", reply_markup=main_kb())

# ── ADD TRADE FLOW ────────────────────────────────────────────────────────────
@dp.message(AddTrade.symbol)
async def got_symbol(msg: Message, state: FSMContext):
    await state.update_data(symbol=msg.text.strip().upper())
    await state.set_state(AddTrade.direction)
    await msg.answer("Шаг 2/5 — Направление сделки:", reply_markup=direction_kb())

@dp.callback_query(F.data.startswith("dir_"))
async def got_direction(cb: CallbackQuery, state: FSMContext):
    direction = cb.data.split("_")[1]
    await state.update_data(direction=direction)
    await state.set_state(AddTrade.entry)
    await cb.message.edit_text(
        f"Направление: <b>{'▲ LONG' if direction=='Long' else '▼ SHORT'}</b>\n\nШаг 3/5 — Цена <b>входа</b>:",
        parse_mode="HTML", reply_markup=cancel_kb()
    )

@dp.message(AddTrade.entry)
async def got_entry(msg: Message, state: FSMContext):
    try:
        entry = float(msg.text.replace(",", "."))
        assert entry > 0
    except:
        await msg.answer("Введи корректное число, например: 42500.5")
        return
    await state.update_data(entry=entry)
    await state.set_state(AddTrade.exit_)
    await msg.answer("Шаг 4/5 — Цена <b>выхода</b>:", parse_mode="HTML", reply_markup=cancel_kb())

@dp.message(AddTrade.exit_)
async def got_exit(msg: Message, state: FSMContext):
    try:
        exit_price = float(msg.text.replace(",", "."))
        assert exit_price > 0
    except:
        await msg.answer("Введи корректное число, например: 43200")
        return
    await state.update_data(exit_price=exit_price)
    await state.set_state(AddTrade.size)
    await msg.answer("Шаг 5/5 — <b>Объём</b> (лоты / количество):", parse_mode="HTML", reply_markup=cancel_kb())

@dp.message(AddTrade.size)
async def got_size(msg: Message, state: FSMContext):
    try:
        size = float(msg.text.replace(",", "."))
        assert size > 0
    except:
        await msg.answer("Введи корректное число, например: 0.5")
        return
    await state.update_data(size=size)
    await state.set_state(AddTrade.notes)
    await msg.answer("Заметки (стратегия, причина входа):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_notes")],
        [InlineKeyboardButton(text="❌ Отмена",     callback_data="cancel")],
    ]))

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
    dir_str = "▲ LONG" if data["direction"] == "Long" else "▼ SHORT"
    text = (
        f"<b>Проверь сделку:</b>\n\n"
        f"📌 Инструмент: <b>{data['symbol']}</b>\n"
        f"📍 Направление: <b>{dir_str}</b>\n"
        f"🔵 Вход: <b>{data['entry']}</b>\n"
        f"🔵 Выход: <b>{data['exit_price']}</b>\n"
        f"📦 Объём: <b>{data['size']}</b>\n"
        f"{pnl_emoji(pnl)} PnL: <b>{fmt(pnl)}</b>\n"
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
        data["pnl"], data.get("notes", "")
    )
    await conn.close()
    await state.clear()
    await cb.message.edit_text(
        f"{pnl_emoji(data['pnl'])} Сделка сохранена!\n\n<b>{data['symbol']}</b> · {fmt(data['pnl'])} PnL",
        parse_mode="HTML", reply_markup=main_kb()
    )

# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
