# Торговый журнал — Telegram Bot

## Как запустить на Railway (бесплатно)

### Шаг 1 — Создай бота в Telegram
1. Открой Telegram, найди @BotFather
2. Напиши `/newbot`
3. Придумай имя и username (например: `MyTradeJournalBot`)
4. Скопируй токен — выглядит как `7123456789:AAF...`

### Шаг 2 — Загрузи код на GitHub
1. Зайди на github.com → New repository → назови `trade-bot`
2. Загрузи все файлы из этой папки (bot.py, requirements.txt, Procfile)
   - Нажми "Add file" → "Upload files"

### Шаг 3 — Задеплой на Railway
1. Зайди на railway.app → Login with GitHub
2. Нажми "New Project" → "Deploy from GitHub repo"
3. Выбери репозиторий `trade-bot`
4. Railway автоматически задеплоит бота

### Шаг 4 — Добавь PostgreSQL
1. В проекте Railway нажми "New" → "Database" → "PostgreSQL"
2. Railway автоматически добавит переменную `DATABASE_URL`

### Шаг 5 — Добавь токен бота
1. В Railway открой твой сервис → вкладка "Variables"
2. Нажми "New Variable"
3. Имя: `BOT_TOKEN`, значение: токен от BotFather
4. Нажми "Add"

### Готово!
Бот запустится автоматически. Открой Telegram и напиши `/start`.

---

## Команды бота
- `/start` — главное меню
- `/menu` — вернуться в меню

## Функции
- ➕ Добавить сделку (symbol, long/short, вход, выход, объём, заметки)
- 📊 Статистика (PnL, винрейт, R/R, лучшая/худшая)
- 📋 История последних 10 сделок
