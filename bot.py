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
# FIX 4: SSL через отдельную переменную окружения, не по строке
DB_SSL = os.environ.get("DB_SSL", "false").lower() == "true"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

pool: asyncpg.Pool = None


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
    screenshot = State()


class AddScreenshot(StatesGroup):
    trade_id = State()
    photo = State()


class DeleteTrade(StatesGroup):
    choose = State()


class ResetAccount(StatesGroup):
    confirm = State()


# ───────────────── DB INIT ─────────────────
async def init_db():
    async with pool.acquire() as conn:
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY,
                start_balance NUMERIC DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_screenshots (
                trade_id BIGINT PRIMARY KEY REFERENCES trades(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)


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


def parse_float(text: str):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


# ───────────────── START ─────────────────
@dp.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()

    name = msg.from_user.first_name or "трейдер"

    await msg.answer(
        f"👋 Привет, {name}!\n\n"
        "📊 Trading Journal готов к работе. По вопросам пиши @theadil_0\n\n"
        f"🔥💪🏻 {mot()}\n\n"
        "📌 Команды:\n"
        "/new — новая сделка\n"
        "/history — история\n"
        "/stats — статистика\n"
        "/delete — удалить сделку\n"
        "/calendar — календарь\n"
        "/trade — сделка\n"
        "/setdeposit — установить депозит\n"
        "/balance — текущий баланс\n"
        "/resetaccount — полный сброс аккаунта\n"
        "/tip — совет\n"
        "/screenshot — добавить скриншот к сделке"
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
    value = parse_float(msg.text)
    if value is None:
        await msg.answer("❌ Введи число, например: 42500.5")
        return
    await state.update_data(entry=value)
    await state.set_state(AddTrade.exit_)
    await msg.answer("🔵 Цена выхода:")


@dp.message(AddTrade.exit_)
async def exit_price(msg: Message, state: FSMContext):
    value = parse_float(msg.text)
    if value is None:
        await msg.answer("❌ Введи число, например: 43000.0")
        return
    await state.update_data(exit_price=value)
    await state.set_state(AddTrade.size)
    await msg.answer("📦 Размер позиции:")


@dp.message(AddTrade.size)
async def size(msg: Message, state: FSMContext):
    value = parse_float(msg.text)
    if value is None:
        await msg.answer("❌ Введи число, например: 0.5")
        return
    await state.update_data(size=value)
    await state.set_state(AddTrade.notes)
    await msg.answer("📝 Заметки (или '-' если нет):")


@dp.message(AddTrade.notes)
async def notes(msg: Message, state: FSMContext):
    data = await state.get_data()

    notes_text = "" if msg.text.strip() == "-" else msg.text
    pnl = calc_pnl(data["direction"], data["entry"], data["exit_price"], data["size"])

    async with pool.acquire() as conn:
        trade_id = await conn.fetchval(
            """
            INSERT INTO trades(user_id, symbol, direction, entry, exit_price, size, pnl, notes)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id
            """,
            msg.from_user.id,
            data["symbol"],
            data["direction"],
            data["entry"],
            data["exit_price"],
            data["size"],
            pnl,
            notes_text
        )

    await state.update_data(trade_id=trade_id, pnl=pnl)
    await state.set_state(AddTrade.screenshot)
    await msg.answer(
        f"💰 Сделка сохранена! {data['symbol']} {pnl_emoji(pnl)} {fmt(pnl)}\n\n"
        "📸 Прикрепи скриншот графика или '-' чтобы пропустить:"
    )


@dp.message(AddTrade.screenshot)
async def trade_screenshot(msg: Message, state: FSMContext):
    data = await state.get_data()
    trade_id = data["trade_id"]
    pnl = data["pnl"]

    if msg.photo:
        file_id = msg.photo[-1].file_id  # берём наибольшее разрешение
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trade_screenshots(trade_id, file_id)
                VALUES($1, $2)
                ON CONFLICT(trade_id) DO UPDATE SET file_id = EXCLUDED.file_id
                """,
                trade_id, file_id
            )
        screenshot_note = "📸 Скриншот сохранён."
    else:
        screenshot_note = "📷 Скриншот пропущен."

    await state.clear()
    await msg.answer(
        f"{screenshot_note}\n\n"
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
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, symbol, pnl FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
            msg.from_user.id
        )

    if not rows:
        await msg.answer("История пуста")
        return

    text = "📋 Последние сделки:\n\n"
    for r in rows:
        text += f"ID: {r['id']} | {r['symbol']} {pnl_emoji(float(r['pnl']))} {fmt(float(r['pnl']))}\n"

    await msg.answer(text)


# ───────────────── TRADE ─────────────────
@dp.message(Command("trade"))
async def trade_view(msg: Message):
    parts = msg.text.split()

    if len(parts) < 2:
        await msg.answer("Используй: /trade ID")
        return

    # FIX 2: чистый int() в try/except вместо двойного parse_float + is_integer
    try:
        trade_id = int(parts[1])
    except ValueError:
        await msg.answer("❌ ID должен быть числом\nПример: /trade 12")
        return

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM trades WHERE id=$1 AND user_id=$2",
            trade_id,
            msg.from_user.id
        )

    if not row:
        await msg.answer("Сделка не найдена")
        return

    text = (
        f"📊 Сделка #{row['id']}\n\n"
        f"📌 Символ: {row['symbol']}\n"
        f"📍 Направление: {row['direction']}\n"
        f"🔵 Вход: {float(row['entry']):.2f}\n"
        f"🔵 Выход: {float(row['exit_price']):.2f}\n"
        f"📦 Размер: {float(row['size']):.2f}\n"
        f"💰 PnL: {fmt(float(row['pnl']))}\n\n"
        f"📝 Заметки:\n{row['notes'] or '—'}"
    )

    async with pool.acquire() as conn:
        screenshot = await conn.fetchrow(
            "SELECT file_id FROM trade_screenshots WHERE trade_id=$1", trade_id
        )

    if screenshot:
        await msg.answer_photo(screenshot["file_id"], caption=text)
    else:
        await msg.answer(text)


# ───────────────── CALENDAR ─────────────────
@dp.message(Command("calendar"))
async def calendar_view(msg: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT pnl, created_at FROM trades WHERE user_id=$1",
            msg.from_user.id
        )

    if not rows:
        await msg.answer("Нет сделок")
        return

    # FIX 1: winrate по сделкам, а не по дням
    daily = defaultdict(float)
    all_pnls = []
    for r in rows:
        day = r["created_at"].date()
        daily[day] += float(r["pnl"])
        all_pnls.append(float(r["pnl"]))

    now = datetime.now()
    year, month = now.year, now.month
    month_name = calendar.month_name[month]

    total_pnl = sum(all_pnls)
    wins = len([p for p in all_pnls if p > 0])
    winrate = (wins / len(all_pnls) * 100) if all_pnls else 0
    best_day = max(daily.values()) if daily else 0
    worst_day = min(daily.values()) if daily else 0

    text = f"📅 PnL календарь — {month_name} {year}\n\n"

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

    text += "\n📊 Итоги месяца\n\n"
    text += f"💰 PnL: {fmt(total_pnl)}\n"
    text += f"📈 Winrate: {winrate:.1f}% (по сделкам)\n"
    text += f"📦 Всего сделок: {len(all_pnls)}\n\n"
    text += f"🏆 Лучший день: {fmt(best_day)}\n"
    text += f"💀 Худший день: {fmt(worst_day)}"

    await msg.answer(text)


# ───────────────── STATS ─────────────────
@dp.message(Command("stats"))
async def stats(msg: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT pnl FROM trades WHERE user_id=$1",
            msg.from_user.id
        )

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


# ───────────────── DELETE ─────────────────
@dp.message(Command("delete"))
async def delete_start(msg: Message, state: FSMContext):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, symbol, pnl FROM trades WHERE user_id=$1 ORDER BY created_at DESC LIMIT 10",
            msg.from_user.id
        )

    if not rows:
        await msg.answer("Сделок нет")
        return

    rows_data = [{"id": r["id"], "symbol": r["symbol"], "pnl": float(r["pnl"])} for r in rows]
    await state.update_data(rows=rows_data)
    await state.set_state(DeleteTrade.choose)

    text = "🗑 Введи номер сделки:\n\n"
    for i, r in enumerate(rows_data, 1):
        text += f"{i}. {r['symbol']} {fmt(r['pnl'])}\n"
    # FIX 3: явно указываем допустимый диапазон
    text += f"\nВведи число от 1 до {len(rows_data)}:"

    await msg.answer(text)


@dp.message(DeleteTrade.choose)
async def delete_choose(msg: Message, state: FSMContext):
    data = await state.get_data()
    rows = data["rows"]

    try:
        index = int(msg.text)
        if not (1 <= index <= len(rows)):
            raise ValueError
    except ValueError:
        await msg.answer(f"❌ Введи число от 1 до {len(rows)}")
        return

    trade_id = rows[index - 1]["id"]

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM trades WHERE id=$1 AND user_id=$2",
            trade_id,
            msg.from_user.id
        )

    await state.clear()
    await msg.answer("🗑 Удалено")


# ───────────────── DEPOSIT ─────────────────
@dp.message(Command("setdeposit"))
async def set_deposit(msg: Message):
    parts = msg.text.split()

    if len(parts) != 2:
        await msg.answer("Использование:\n/setdeposit 1000")
        return

    deposit = parse_float(parts[1])
    if deposit is None:
        await msg.answer("❌ Введите корректное число")
        return

    deposit = round(deposit, 2)

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO user_settings(user_id, start_balance)
            VALUES($1, $2)
            ON CONFLICT(user_id)
            DO UPDATE SET start_balance = EXCLUDED.start_balance
        """, msg.from_user.id, deposit)

    await msg.answer(f"💰 Начальный депозит установлен:\n{deposit:.2f}$")


# ───────────────── BALANCE ─────────────────
@dp.message(Command("balance"))
async def balance(msg: Message):
    user_id = msg.from_user.id
    today = datetime.now().date()
    month_start = datetime.now().replace(day=1).date()

    async with pool.acquire() as conn:
        setting = await conn.fetchrow(
            "SELECT start_balance FROM user_settings WHERE user_id=$1", user_id
        )
        all_rows = await conn.fetch(
            "SELECT pnl FROM trades WHERE user_id=$1", user_id
        )
        today_rows = await conn.fetch(
            "SELECT pnl FROM trades WHERE user_id=$1 AND created_at::date=$2",
            user_id, today
        )
        month_rows = await conn.fetch(
            "SELECT pnl FROM trades WHERE user_id=$1 AND created_at::date>=$2",
            user_id, month_start
        )

    start_balance = float(setting["start_balance"]) if setting else 0

    def total(rows):
        return sum(float(r["pnl"]) for r in rows)

    all_pnl = total(all_rows)
    today_pnl = total(today_rows)
    month_pnl = total(month_rows)
    current_balance = start_balance + all_pnl

    balance_before_today = start_balance + (all_pnl - today_pnl)
    balance_before_month = start_balance + (all_pnl - month_pnl)

    def pct(pnl, base):
        return (pnl / base) * 100 if base > 0 else 0

    text = (
        f"💰 Balance Dashboard\n\n"
        f"📅 Сегодня: {today_pnl:+.2f}$ ({pct(today_pnl, balance_before_today):+.2f}%)\n"
        f"📆 Месяц: {month_pnl:+.2f}$ ({pct(month_pnl, balance_before_month):+.2f}%)\n"
        f"♾ Всё время: {all_pnl:+.2f}$ ({pct(all_pnl, start_balance):+.2f}%)\n\n"
        f"💵 Депозит: {start_balance:.2f}$\n"
        f"🏦 Баланс: {current_balance:.2f}$"
    )
    await msg.answer(text)


# ───────────────── RESET ACCOUNT ─────────────────
@dp.message(Command("resetaccount"))
async def reset_account(msg: Message, state: FSMContext):
    await state.set_state(ResetAccount.confirm)
    await msg.answer(
        "⚠️ ВНИМАНИЕ!\n\n"
        "Это удалит:\n"
        "• все сделки\n"
        "• депозит\n"
        "• всю статистику\n\n"
        "Напиши ДА для подтверждения или НЕТ для отмены:"
    )


@dp.message(ResetAccount.confirm)
async def confirm_reset(msg: Message, state: FSMContext):
    answer = msg.text.strip().upper()
 
    if answer == "ДА":
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM trades WHERE user_id=$1", msg.from_user.id
            )
            await conn.execute(
                "DELETE FROM user_settings WHERE user_id=$1", msg.from_user.id
            )
            # Сбрасываем счётчик ID если в таблице больше нет сделок
            remaining = await conn.fetchval("SELECT COUNT(*) FROM trades")
            if remaining == 0:
                await conn.execute("ALTER SEQUENCE trades_id_seq RESTART WITH 1")
        await msg.answer(
            "🗑 Аккаунт полностью сброшен.\n\n"
            "Начни заново:\n/setdeposit 1000"
        )
    else:
        await msg.answer("✅ Отменено")
 
    await state.clear()
 

# ───────────────── SCREENSHOT ─────────────────
@dp.message(Command("screenshot"))
async def screenshot_start(msg: Message, state: FSMContext):
    parts = msg.text.split()

    if len(parts) < 2:
        await msg.answer("Используй: /screenshot ID\nНапример: /screenshot 12")
        return

    try:
        trade_id = int(parts[1])
    except ValueError:
        await msg.answer("❌ ID должен быть числом\nПример: /screenshot 12")
        return

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, symbol, pnl FROM trades WHERE id=$1 AND user_id=$2",
            trade_id, msg.from_user.id
        )

    if not row:
        await msg.answer("Сделка не найдена")
        return

    await state.set_state(AddScreenshot.photo)
    await state.update_data(trade_id=trade_id)
    await msg.answer(
        f"📸 Сделка #{trade_id} — {row['symbol']} {fmt(float(row['pnl']))}\n\n"
        "Отправь скриншот графика:"
    )


@dp.message(AddScreenshot.photo)
async def screenshot_save(msg: Message, state: FSMContext):
    if not msg.photo:
        await msg.answer("❌ Нужно отправить фото. Попробуй ещё раз:")
        return

    data = await state.get_data()
    trade_id = data["trade_id"]
    file_id = msg.photo[-1].file_id

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO trade_screenshots(trade_id, file_id)
            VALUES($1, $2)
            ON CONFLICT(trade_id) DO UPDATE SET file_id = EXCLUDED.file_id
            """,
            trade_id, file_id
        )

    await state.clear()
    await msg.answer(
        f"📸 Скриншот сохранён для сделки #{trade_id}\n\n"
        f"Посмотреть: /trade {trade_id}"
    )


# ───────────────── TIP ─────────────────
@dp.message(Command("tip"))
async def tip(msg: Message):
    await msg.answer(f"💡 {mot()}")


# FIX 6: fallback для неизвестных сообщений вне FSM
@dp.message()
async def fallback(msg: Message, state: FSMContext):
    current_state = await state.get_state()
    # Если пользователь в FSM — не мешаем, хэндлеры сами разберутся
    if current_state is not None:
        return
    await msg.answer(
        "❓ Не понимаю команду.\n\n"
        "Используй /start чтобы увидеть список команд."
    )


# ───────────────── MAIN ─────────────────
async def main():
    global pool
    # FIX 4: SSL через переменную окружения DB_SSL=true
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl="require" if DB_SSL else False,
        min_size=1,
        max_size=5
    )
    await init_db()

    # FIX 7: передаём pool через workflow_data
    dp["pool"] = pool

    # FIX 8: закрываем пул при остановке
    try:
        await dp.start_polling(bot)
    finally:
        await pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
