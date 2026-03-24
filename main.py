import asyncio
import asyncpg
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, InputMediaPhoto, FSInputFile
)

# --- КОНФИГУРАЦИЯ ---
import os
import asyncio
import asyncpg
import logging
from dotenv import load_dotenv  # Добавь этот импорт
from datetime import datetime, timedelta
# ... остальные импорты ...

# Загружаем переменные из файла .env
load_dotenv()

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("API_TOKEN")
ADMINS = [6629153171, 7918010548, 8307996710, 8369762827, 1596833660, 1430448792, 8177259186]
SUPPORT_ADMIN_ID = 7918010548
GROUP_URL = "https://t.me/tether_tjs"
CHANNEL_ID = "@tether_tjs"

# Конфигурация БД тоже через окружение (так безопаснее)
DB_CONFIG = {
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'database': os.getenv("DB_NAME"),
    'host': os.getenv("DB_HOST"),
    'port': int(os.getenv("DB_PORT", 5432))
}

# Далее идет твой основной код без изменений...
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ ---
class SupportState(StatesGroup):
    waiting_for_question = State()
    waiting_for_answer = State()

class OrderProcess(StatesGroup):
    confirm_regulations = State()
    confirm_receipt = State()
    waiting_for_bank = State()
    waiting_for_method = State()
    waiting_for_action = State()
    waiting_for_data = State()

class AdminStates(StatesGroup):
    add_bank = State()
    add_method = State()
    add_val = State()

class WithdrawProcess(StatesGroup):
    waiting_for_amount = State()
    waiting_for_address = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute('''CREATE TABLE IF NOT EXISTS requisites 
                          (id SERIAL PRIMARY KEY, bank TEXT, method TEXT, val TEXT, added_by BIGINT)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS usage_log 
                          (req_id INTEGER, used_at TIMESTAMP, user_id BIGINT)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, balance NUMERIC DEFAULT 0)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS orders 
                          (id SERIAL PRIMARY KEY, user_id BIGINT, amount NUMERIC, created_at TIMESTAMP)''')
    await conn.execute("INSERT INTO settings (key, value) VALUES ('rate', '0') ON CONFLICT DO NOTHING")
    await conn.close()

async def get_balance(user_id):
    conn = await asyncpg.connect(**DB_CONFIG)
    val = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1", user_id)
    if val is None:
        await conn.execute("INSERT INTO users (user_id) VALUES ($1)", user_id)
        val = 0
    await conn.close()
    return float(val)

async def update_balance(user_id, amount):
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("UPDATE users SET balance = balance + $2 WHERE user_id=$1", user_id, amount)
    await conn.close()

async def get_rate():
    conn = await asyncpg.connect(**DB_CONFIG)
    val = await conn.fetchval("SELECT value FROM settings WHERE key='rate'")
    await conn.close()
    return val or "0"

async def get_available_req(bank, method, user_id):
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("DELETE FROM usage_log WHERE used_at < $1", datetime.now() - timedelta(hours=24))
    query = '''
        SELECT r.id, r.val, r.added_by FROM requisites r
        LEFT JOIN usage_log l ON r.id = l.req_id
        WHERE r.bank = $1 AND r.method = $2
        GROUP BY r.id, r.val, r.added_by
        HAVING COUNT(l.req_id) < 2 LIMIT 1
    '''
    row = await conn.fetchrow(query, bank, method)
    if row:
        await conn.execute("INSERT INTO usage_log (req_id, used_at, user_id) VALUES ($1, $2, $3)", row['id'], datetime.now(), user_id)
    await conn.close()
    return row

# --- ПРОВЕРКА ПОДПИСКИ ---
async def check_sub(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status != 'left'
    except:
        return True # Если бот не админ в канале, пропускаем

# --- МЕНЮ ---
def main_menu():
    kb = [
        [KeyboardButton(text="✅Новая сделка")],
        [KeyboardButton(text="💰Мой баланс"), KeyboardButton(text="💸 Вывод")],
        [KeyboardButton(text="👥Наша группа"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔄 Перезапуск"), KeyboardButton(text="Техподдержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
@dp.message(F.text.contains("Перезапуск"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await get_balance(message.from_user.id)
    rate = await get_rate()
    welcome_text = (
        f"👋 **Добро пожаловать в Nazar Bot!**\n"
        f"Официальный сервис обмена **TJS ⇄ USDT**\n\n"
        f"💵 Текущий курс: `1 USDT = {rate} TJS`\n\n"
        "👇 **Выберите действие в меню ниже:**"
    )
    await message.answer(welcome_text, reply_markup=main_menu(), parse_mode="Markdown")

@dp.message(F.text == "💰Мой баланс")
async def show_balance(message: types.Message):
    bal = await get_balance(message.from_user.id)
    await message.answer(f"💳 **ВАШ БАЛАНС:** `{bal} USDT`", parse_mode="Markdown")

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    uid = message.from_user.id
    conn = await asyncpg.connect(**DB_CONFIG)

    # Получаем баланс
    bal = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1", uid)
    # Получаем статистику сделок
    stats = await conn.fetchrow("SELECT COUNT(*) as count, SUM(amount) as total FROM orders WHERE user_id=$1", uid)
    await conn.close()

    text = (
        "📊 **Ваша личная статистика**\n\n"
        f"🆔 Ваш ID: `{uid}`\n"
        f"💰 Текущий баланс: `{float(bal or 0)} USDT`\n"
        "────────────────────\n"
        f"🔄 Всего сделок: `{stats['count'] or 0}`\n"
        f"📥 Куплено всего: `{float(stats['total'] or 0)} USDT`"
    )

    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "Техподдержка")
async def support_start(message: types.Message, state: FSMContext):
    await state.set_state(SupportState.waiting_for_question)
    await message.answer("🛠 **Напишите ваш вопрос одним сообщением.** Оператор ответит вам здесь.")

@dp.message(SupportState.waiting_for_question)
async def forward_to_admins(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_{message.from_user.id}")]])
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, f"🆘 **НОВЫЙ ТИКЕТ**\nОт: @{message.from_user.username}\nID: `{message.from_user.id}`\n\n{message.text}", reply_markup=kb, parse_mode="Markdown")
        except: pass
    await message.answer("✅ Запрос отправлен поддержке.")
    await state.clear()

@dp.callback_query(F.data.startswith("reply_"))
async def admin_reply_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != SUPPORT_ADMIN_ID:
        return await callback.answer("🚫 Нет прав доступа.", show_alert=True)
    user_id = callback.data.split("_")[1]
    await state.update_data(reply_to=user_id)
    await state.set_state(SupportState.waiting_for_answer)
    await callback.message.answer(f"✍️ **Ответ для {user_id}:**")
    await callback.answer()

@dp.message(SupportState.waiting_for_answer)
async def admin_send_answer(message: types.Message, state: FSMContext):
    if message.from_user.id != SUPPORT_ADMIN_ID: return
    data = await state.get_data()
    user_id = data.get("reply_to")
    try:
        await bot.send_message(user_id, f"🛠 **ОТВЕТ ПОДДЕРЖКИ:**\n\n{message.text}", parse_mode="Markdown")
        await message.answer("✅ Отправлено пользователю.")
    except: await message.answer("❌ Ошибка доставки.")
    await state.clear()

# --- СДЕЛКА ---
@dp.message(F.text == "✅Новая сделка")
async def start_deal(message: types.Message, state: FSMContext):
    if not await check_sub(message.from_user.id):
        return await message.answer(f"❌ Для продолжения подпишитесь на наш канал: {GROUP_URL}")

    await state.set_state(OrderProcess.confirm_regulations)
    text = (
        "📜 **РЕГЛАМЕНТ И ПРАВИЛА СЕРВИСА**\n\n"
        "🔹 Время обмена: **5 - 30 минут**.\n"
        "🔹 Курс: фиксация в момент зачисления средств.\n"
        "🔹 Лимиты: минимум **14,000 RUB**.\n\n"
        "⚠️ Нажимая кнопку, вы подтверждаете согласие с [правилами](https://telegra.ph/Reglament-servisa-obmena-03-24)."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Согласен", callback_data="confirm")]])
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(OrderProcess.confirm_regulations, F.data == "confirm")
async def process_regs(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderProcess.confirm_receipt)
    receipt_text = (
        "📸 **ТРЕБОВАНИЯ К ПОДТВЕРЖДЕНИЮ ПЛАТЕЖА**\n\n"
        "1️⃣ **Валюта и сумма**\n"
        "• В чеке должна быть видна сумма в **TJS**.\n"
        "• Для SMS-переводов — скриншот сообщения.\n\n"
        "2️⃣ **Качество изображения**\n"
        "• Чек четкий и не обрезанный.\n\n"
        "⚠️ **ВАЖНО:** Чеки без суммы в TJS затрудняют работу."
    )
    await callback.message.delete()
    try:
        media = [InputMediaPhoto(media=FSInputFile("chek.jpg")),
                 InputMediaPhoto(media=FSInputFile("chek2.jpg"), caption=receipt_text, parse_mode="Markdown")]
        await callback.message.answer_media_group(media=media)
    except: await callback.message.answer(receipt_text, parse_mode="Markdown")

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆗 Понял, выбрать банк", callback_data="confirm_receipt")]])
    await callback.message.answer("Вы изучили требования к чеку?", reply_markup=kb)

@dp.callback_query(OrderProcess.confirm_receipt, F.data == "confirm_receipt")
async def choose_bank(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderProcess.waiting_for_bank)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Душанбе Сити", callback_data="bank_Душанбе Сити"), InlineKeyboardButton(text="Эсхата", callback_data="bank_Эсхата")],
        [InlineKeyboardButton(text="Корти Милли", callback_data="bank_Корти Милли"), InlineKeyboardButton(text="Васл", callback_data="bank_Васл")]
    ])
    await callback.message.answer("🏦 **Выберите банк для оплаты:**", reply_markup=kb)

@dp.callback_query(OrderProcess.waiting_for_bank, F.data.startswith("bank_"))
async def choose_meth(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(chosen_bank=callback.data.replace("bank_", ""))
    await state.set_state(OrderProcess.waiting_for_method)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Номер телефона", callback_data="meth_НТ")],
        [InlineKeyboardButton(text="💳 Номер карты", callback_data="meth_НК")]
    ])
    await callback.message.edit_text("Выберите способ оплаты:", reply_markup=kb)

@dp.callback_query(OrderProcess.waiting_for_method, F.data.startswith("meth_"))
async def give_reks(callback: types.CallbackQuery, state: FSMContext):
    meth = callback.data.replace("meth_", "")
    data = await state.get_data()
    req = await get_available_req(data['chosen_bank'], meth, callback.from_user.id)
    if not req: return await callback.message.answer("❌ Свободных реквизитов нет. Напишите в поддержку.")

    await state.update_data(owner_id=req['added_by'], req_val=req['val'])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Оплатил", callback_data="action_pay")]])
    await callback.message.edit_text(f"💳 **РЕКВИЗИТЫ**\n\nБанк: {data['chosen_bank']}\nРеквизит: `{req['val']}`\n\nНажмите для копирования.",
                                     reply_markup=kb, parse_mode="Markdown")
    await state.set_state(OrderProcess.waiting_for_action)

@dp.callback_query(OrderProcess.waiting_for_action, F.data == "action_pay")
async def ask_photo(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderProcess.waiting_for_data)
    await callback.message.edit_text("📸 **Пришлите скриншот чека:**")

@dp.message(OrderProcess.waiting_for_data, F.photo)
async def handle_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔎 Детали сделки", callback_data=f"manage_{message.from_user.id}")]])
    try:
        caption = f"📩 **НОВЫЙ ЧЕК**\nЮзер: {message.from_user.id}\nРеквизит: `{data['req_val']}`"
        await bot.send_photo(data['owner_id'], message.photo[-1].file_id, caption=caption, reply_markup=kb)
        await message.answer("✅ Чек получен. Ожидайте проверку (5-30 минут).")
    except: await message.answer("✅ Чек на проверке у оператора.")
    await state.clear()

@dp.callback_query(F.data.startswith("manage_"))
async def manage_deal(callback: types.CallbackQuery):
    uid = callback.data.split("_")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ПОДТВЕРДИТЬ", callback_data=f"conf_{uid}")]])
    await callback.message.edit_reply_markup(reply_markup=kb)

@dp.callback_query(F.data.startswith("conf_"))
async def conf_pay(callback: types.CallbackQuery):
    uid = int(callback.data.split("_")[1])
    try:
        await bot.send_message(uid, "💎 **УСПЕШНО!**\nВаша оплата подтверждена. Баланс пополнен.")
        await callback.message.answer(f"✅ Для начисления: `/add {uid} сумма`")
    except: pass

# --- АДМИНКА ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id in ADMINS:
        rate = await get_rate()
        text = (
            f"🛠 **АДМИН-ПАНЕЛЬ**\n\n"
            f"Курс: `{rate} TJS`\n\n"
            f"📍 `/set число` — сменить курс\n"
            f"📍 `/add ID СУММА` — начислить баланс\n"
            f"📍 `/list` — список всех карт"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить реквизиты", callback_data="adm_add")]])
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if message.from_user.id in ADMINS:
        conn = await asyncpg.connect(**DB_CONFIG)
        rows = await conn.fetch("SELECT * FROM requisites")
        await conn.close()
        if not rows: return await message.answer("Список карт пуст.")
        res = "📋 **ТЕКУЩИЕ КАРТЫ:**\n\n"
        for r in rows:
            res += f"• {r['bank']} ({r['method']}): `{r['val']}`\n"
        await message.answer(res, parse_mode="Markdown")

@dp.message(Command("add"))
async def cmd_add_balance(message: types.Message, command: CommandObject):
    if message.from_user.id in ADMINS and command.args:
        try:
            uid, amt = command.args.split()
            await update_balance(int(uid), float(amt))
            conn = await asyncpg.connect(**DB_CONFIG)
            await conn.execute("INSERT INTO orders (user_id, amount, created_at) VALUES ($1, $2, $3)", int(uid), float(amt), datetime.now())
            await conn.close()
            await message.answer(f"✅ Начислено {amt} USDT юзеру {uid}")
            try: await bot.send_message(int(uid), f"💰 Ваш баланс пополнен на {amt} USDT!")
            except: pass
        except: await message.answer("Ошибка! Формат: `/add 1234567 100`")

@dp.message(Command("set"))
async def cmd_set_rate(message: types.Message, command: CommandObject):
    if message.from_user.id in ADMINS and command.args:
        conn = await asyncpg.connect(**DB_CONFIG)
        await conn.execute("UPDATE settings SET value=$1 WHERE key='rate'", command.args)
        await conn.close()
        await message.answer(f"✅ Новый курс: {command.args} TJS")

@dp.callback_query(F.data == "adm_add")
async def adm_add(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.add_bank)
    await callback.message.answer("Введите название банка:")

@dp.message(AdminStates.add_bank)
async def adm_bank(message: types.Message, state: FSMContext):
    await state.update_data(bank=message.text)
    await state.set_state(AdminStates.add_method)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Тел", callback_data="setmeth_НТ"),
                                                InlineKeyboardButton(text="💳 Карта", callback_data="setmeth_НК")]])
    await message.answer("Выберите тип:", reply_markup=kb)

@dp.callback_query(AdminStates.add_method, F.data.startswith("setmeth_"))
async def adm_meth(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(method=callback.data.replace("setmeth_", ""))
    await state.set_state(AdminStates.add_val)
    await callback.message.answer("Введите номер:")

@dp.message(AdminStates.add_val)
async def adm_val(message: types.Message, state: FSMContext):
    d = await state.get_data()
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("INSERT INTO requisites (bank, method, val, added_by) VALUES ($1, $2, $3, $4)", d['bank'],
                       d['method'], message.text, message.from_user.id)
    await conn.close()
    await message.answer("✅ Реквизит добавлен.")
    await state.clear()

# --- ВЫВОД ---
@dp.message(F.text == "💸 Вывод")
async def withdraw_start(message: types.Message, state: FSMContext):
    bal = await get_balance(message.from_user.id)
    if bal <= 0: return await message.answer("⚠️ Недостаточно средств для вывода.")
    await state.set_state(WithdrawProcess.waiting_for_amount)
    await message.answer(f"💰 Ваш баланс: {bal} USDT\nВведите сумму:")

@dp.message(WithdrawProcess.waiting_for_amount)
async def wd_amt(message: types.Message, state: FSMContext):
    await state.update_data(wa=message.text)
    await state.set_state(WithdrawProcess.waiting_for_address)
    await message.answer("🌐 Введите адрес кошелька TRC20:")

@dp.message(WithdrawProcess.waiting_for_address)
async def wd_fin(message: types.Message, state: FSMContext):
    d = await state.get_data()
    try:
        await update_balance(message.from_user.id, -float(d['wa']))
        for a in ADMINS:
            try: await bot.send_message(a, f"💸 **ЗАЯВКА НА ВЫВОД**\nЮзер: {message.from_user.id}\nСумма: {d['wa']}\nАдрес: `{message.text}`")
            except: pass
        await message.answer("✅ Заявка на вывод принята!")
    except: await message.answer("❌ Ошибка при создании заявки.")
    await state.clear()

# --- ЗАПУСК ---
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
