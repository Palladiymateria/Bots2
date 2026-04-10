import asyncio
import asyncpg
import os
import random
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, InputMediaPhoto, FSInputFile
)

load_dotenv()

TOKEN = os.getenv("API_TOKEN")

SUPER_ADMINS = [7918010548]

OPERATORS = {
    8475690397: "Оператор1",
    8311036902: "Оператор2",
    6629153171: "Создатель бота",
    8702536330: "Оператор4",
    6728218861: "Оператор3",
    7918010548: "Шеф",
}

ALL_STAFF = set(SUPER_ADMINS) | set(OPERATORS.keys())
SUPPORT_ADMIN_IDS = list(SUPER_ADMINS) + list(OPERATORS.keys())
GROUP_URL = "https://t.me/tether_tjs"
CHANNEL_ID = "@tether_tjs"

DB_CONFIG = {
    'user': 'bothost_db_8a35aa8ad5f0',
    'password': 't6onqZ2Sw157ZGm1S5pmmCuuStkTqw_Jiw4ZDS6TSTE',
    'database': 'bothost_db_8a35aa8ad5f0',
    'host': 'node1.pghost.ru',
    'port': 32840
}

bot = Bot(token=TOKEN)
dp = Dispatcher()


# ==================== STATES ====================

class SupportState(StatesGroup):
    waiting_for_question = State()
    waiting_for_answer = State()

class MsgBuyerState(StatesGroup):
    waiting_for_message = State()

class OrderProcess(StatesGroup):
    confirm_regulations = State()
    confirm_receipt = State()
    waiting_for_bank = State()
    waiting_for_method = State()
    waiting_for_action = State()
    waiting_for_data = State()

class AdminStates(StatesGroup):
    add_bank = State()
    add_bank_choose = State()
    add_method = State()
    add_val = State()
    set_chek1 = State()
    set_chek2 = State()
    waiting_for_photo_to_user = State()

class ConfirmState(StatesGroup):
    waiting_for_tjs_amount = State()

class WithdrawProcess(StatesGroup):
    waiting_for_amount = State()
    waiting_for_method = State()
    waiting_for_req = State()       # выбор реквизита из истории
    waiting_for_address = State()   # ввод адреса кошелька
    waiting_for_confirm = State()


# ==================== DB INIT ====================

async def init_db():
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute('''CREATE TABLE IF NOT EXISTS requisites 
                          (id SERIAL PRIMARY KEY, bank TEXT, method TEXT, val TEXT, added_by BIGINT)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS usage_log 
                          (id SERIAL PRIMARY KEY, req_id INTEGER, used_at TIMESTAMP, user_id BIGINT, confirmed BOOLEAN DEFAULT FALSE)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS blocked_reqs
                          (user_id BIGINT, req_val TEXT, blocked_at TIMESTAMP)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id BIGINT PRIMARY KEY, balance NUMERIC DEFAULT 0)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS orders 
                          (id SERIAL PRIMARY KEY, user_id BIGINT, amount NUMERIC, created_at TIMESTAMP)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS deals (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        username TEXT,
        full_name TEXT,
        req_val TEXT,
        bank TEXT,
        photo_id TEXT,
        status TEXT DEFAULT 'pending',
        amount NUMERIC DEFAULT 0,
        method TEXT DEFAULT '',
        address TEXT DEFAULT '',
        operator_id BIGINT DEFAULT 0,
        created_at TIMESTAMP
    )''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS operators (
        user_id BIGINT PRIMARY KEY,
        name TEXT,
        is_online BOOLEAN DEFAULT FALSE,
        last_deal_at TIMESTAMP DEFAULT '2000-01-01'
    )''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS purchase_history (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        req_val TEXT,
        bank TEXT,
        method TEXT,
        operator_id BIGINT,
        deal_id INTEGER,
        created_at TIMESTAMP
    )''')

    for op_id, op_name in OPERATORS.items():
        await conn.execute('''
            INSERT INTO operators (user_id, name, is_online, last_deal_at)
            VALUES ($1, $2, FALSE, '2000-01-01')
            ON CONFLICT (user_id) DO UPDATE SET name = $2
        ''', op_id, op_name)

    await conn.execute("INSERT INTO settings (key, value) VALUES ('rate', '0') ON CONFLICT DO NOTHING")
    await conn.execute("INSERT INTO settings (key, value) VALUES ('chek1', '') ON CONFLICT DO NOTHING")
    await conn.execute("INSERT INTO settings (key, value) VALUES ('chek2', '') ON CONFLICT DO NOTHING")

    await conn.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='username') THEN
                ALTER TABLE users ADD COLUMN username TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='full_name') THEN
                ALTER TABLE users ADD COLUMN full_name TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='usage_log' AND column_name='confirmed') THEN
                ALTER TABLE usage_log ADD COLUMN confirmed BOOLEAN DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='deals' AND column_name='operator_id') THEN
                ALTER TABLE deals ADD COLUMN operator_id BIGINT DEFAULT 0;
            END IF;
        END$$;
    """)
    await conn.execute("UPDATE usage_log SET confirmed = TRUE WHERE confirmed IS NULL")
    await conn.close()


# ==================== HELPERS ====================

def buyer_reply_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить оператору", callback_data="buyer_reply")]
    ])


async def get_balance(user_id, username=None, full_name=None):
    conn = await asyncpg.connect(**DB_CONFIG)
    val = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1", user_id)
    if val is None:
        await conn.execute(
            "INSERT INTO users (user_id, username, full_name) VALUES ($1, $2, $3)",
            user_id, username, full_name
        )
        val = 0
    else:
        if username is not None or full_name is not None:
            await conn.execute(
                "UPDATE users SET username=$2, full_name=$3 WHERE user_id=$1",
                user_id, username, full_name
            )
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


async def check_sub(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status != 'left'
    except:
        return True


# ==================== OPERATOR SELECTION ====================
# ИСПРАВЛЕНО: ищем любого онлайн оператора у которого есть свободный реквизит.
# Реквизиты принадлежат операторам через added_by. Если у онлайн-оператора нет
# реквизитов нужного банка/метода — он пропускается. Если ни один не подходит —
# берём любого онлайн-оператора (шеф всегда может обработать).

async def get_random_online_operator_for_bank(bank: str, method: str) -> int | None:
    conn = await asyncpg.connect(**DB_CONFIG)

    # 1. Онлайн-оператор со свободными реквизитами
    rows = await conn.fetch('''
        SELECT DISTINCT o.user_id FROM operators o
        WHERE o.is_online = TRUE
          AND EXISTS (
              SELECT 1 FROM requisites r
              LEFT JOIN usage_log l ON r.id = l.req_id
                  AND l.used_at >= NOW() - INTERVAL '24 hours'
                  AND l.confirmed = TRUE
              WHERE r.added_by = o.user_id
                AND LOWER(TRIM(r.bank)) = LOWER(TRIM($1))
                AND LOWER(TRIM(r.method)) = LOWER(TRIM($2))
              GROUP BY r.id
              HAVING COUNT(l.req_id) < 2
          )
    ''', bank, method)

    if rows:
        await conn.close()
        return random.choice([r['user_id'] for r in rows])

    # 2. Любой онлайн-оператор у кого есть реквизиты этого банка (даже если лимит)
    rows2 = await conn.fetch('''
        SELECT DISTINCT o.user_id FROM operators o
        WHERE o.is_online = TRUE
          AND EXISTS (
              SELECT 1 FROM requisites r
              WHERE r.added_by = o.user_id
                AND LOWER(TRIM(r.bank)) = LOWER(TRIM($1))
                AND LOWER(TRIM(r.method)) = LOWER(TRIM($2))
          )
    ''', bank, method)

    if rows2:
        await conn.close()
        return random.choice([r['user_id'] for r in rows2])

    # 3. Реквизит с added_by=0 или оффлайн-оператора — берём любого онлайн супер-админа
    #    (он увидит заявку и разберётся)
    has_req = await conn.fetchval('''
        SELECT COUNT(*) FROM requisites
        WHERE LOWER(TRIM(bank)) = LOWER(TRIM($1))
          AND LOWER(TRIM(method)) = LOWER(TRIM($2))
    ''', bank, method)

    await conn.close()

    if has_req:
        # Возвращаем первого онлайн супер-админа, или первого супер-админа вообще
        conn2 = await asyncpg.connect(**DB_CONFIG)
        op = await conn2.fetchval(
            "SELECT user_id FROM operators WHERE user_id = ANY($1::bigint[]) AND is_online = TRUE LIMIT 1",
            list(SUPER_ADMINS)
        )
        if not op:
            op = await conn2.fetchval(
                "SELECT user_id FROM operators WHERE user_id = ANY($1::bigint[]) LIMIT 1",
                list(SUPER_ADMINS)
            )
        await conn2.close()
        return op

    return None


async def get_any_operator_with_req(bank: str, method: str) -> int | None:
    """Возвращает любого оператора (онлайн или нет) у кого есть реквизит"""
    conn = await asyncpg.connect(**DB_CONFIG)
    rows = await conn.fetch('''
        SELECT DISTINCT added_by FROM requisites
        WHERE LOWER(TRIM(bank)) = LOWER(TRIM($1))
          AND LOWER(TRIM(method)) = LOWER(TRIM($2))
    ''', bank, method)
    await conn.close()
    if rows:
        return random.choice([r['added_by'] for r in rows])
    return None


async def mark_operator_deal(operator_id: int):
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute(
        "UPDATE operators SET last_deal_at = $1 WHERE user_id = $2",
        datetime.now(), operator_id
    )
    await conn.close()


# ==================== REQUISITES ====================

async def get_available_req(bank, method, user_id, operator_id, exclude_val=None):
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("DELETE FROM blocked_reqs WHERE blocked_at < NOW() - INTERVAL '24 hours'")
    blocked = await conn.fetch("SELECT req_val FROM blocked_reqs WHERE user_id=$1", user_id)
    blocked_vals = [r['req_val'] for r in blocked]
    if exclude_val and exclude_val not in blocked_vals:
        blocked_vals.append(exclude_val)

    exclude_condition = ""
    if blocked_vals:
        placeholders = ', '.join(f'${i+3}' for i in range(len(blocked_vals)))
        exclude_condition = f"AND r.val NOT IN ({placeholders})"

    # Сначала ищем реквизит конкретного оператора
    params = [bank, method, operator_id] + blocked_vals
    query = f'''
        SELECT r.id, r.val, r.added_by FROM requisites r
        LEFT JOIN usage_log l ON r.id = l.req_id
            AND l.used_at >= NOW() - INTERVAL '24 hours' AND l.confirmed = TRUE
        WHERE LOWER(TRIM(r.bank)) = LOWER(TRIM($1))
          AND LOWER(TRIM(r.method)) = LOWER(TRIM($2))
          AND r.added_by = $3
        {exclude_condition}
        GROUP BY r.id, r.val, r.added_by
        HAVING COUNT(l.req_id) < 2 LIMIT 1
    '''
    row = await conn.fetchrow(query, *params)

    # Если не нашли у оператора — ищем среди всех реквизитов этого банка/метода
    if not row:
        params2 = [bank, method] + blocked_vals
        exc2 = ""
        if blocked_vals:
            placeholders2 = ', '.join(f'${i+3}' for i in range(len(blocked_vals)))
            exc2 = f"AND r.val NOT IN ({placeholders2})"
        query2 = f'''
            SELECT r.id, r.val, r.added_by FROM requisites r
            LEFT JOIN usage_log l ON r.id = l.req_id
                AND l.used_at >= NOW() - INTERVAL '24 hours' AND l.confirmed = TRUE
            WHERE LOWER(TRIM(r.bank)) = LOWER(TRIM($1))
              AND LOWER(TRIM(r.method)) = LOWER(TRIM($2))
            {exc2}
            GROUP BY r.id, r.val, r.added_by
            HAVING COUNT(l.req_id) < 2 LIMIT 1
        '''
        row = await conn.fetchrow(query2, *params2)

    if row:
        await conn.execute(
            "INSERT INTO usage_log (req_id, used_at, user_id, confirmed) VALUES ($1, $2, $3, FALSE)",
            row['id'], datetime.now(), user_id
        )
    await conn.close()
    return row


async def confirm_req_usage(req_val, user_id):
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute('''
        UPDATE usage_log SET confirmed = TRUE WHERE id = (
            SELECT l.id FROM usage_log l JOIN requisites r ON r.id = l.req_id
            WHERE r.val = $1 AND l.user_id = $2 AND l.confirmed = FALSE
            ORDER BY l.used_at DESC LIMIT 1)
    ''', req_val, user_id)
    await conn.close()


async def block_req_for_user(req_val, user_id):
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute('''
        DELETE FROM usage_log WHERE id = (
            SELECT l.id FROM usage_log l JOIN requisites r ON r.id = l.req_id
            WHERE r.val = $1 AND l.user_id = $2 AND l.confirmed = FALSE
            ORDER BY l.used_at DESC LIMIT 1)
    ''', req_val, user_id)
    await conn.execute(
        "INSERT INTO blocked_reqs (user_id, req_val, blocked_at) VALUES ($1, $2, $3)",
        user_id, req_val, datetime.now()
    )
    await conn.close()


# ==================== PURCHASE HISTORY ====================

async def save_purchase_history(user_id, req_val, bank, method, operator_id, deal_id):
    conn = await asyncpg.connect(**DB_CONFIG)
    exists = await conn.fetchval(
        "SELECT id FROM purchase_history WHERE user_id=$1 AND req_val=$2",
        user_id, req_val
    )
    if not exists:
        await conn.execute('''
            INSERT INTO purchase_history (user_id, req_val, bank, method, operator_id, deal_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        ''', user_id, req_val, bank, method, operator_id, deal_id, datetime.now())
    await conn.close()


async def get_purchase_history(user_id):
    conn = await asyncpg.connect(**DB_CONFIG)
    rows = await conn.fetch('''
        SELECT ph.req_val, ph.bank, ph.method, ph.operator_id, ph.created_at,
               COUNT(d.id) as deal_count,
               MAX(d.amount) as last_amount,
               MAX(d.created_at) as last_deal
        FROM purchase_history ph
        LEFT JOIN deals d ON d.user_id=$1 AND d.req_val=ph.req_val AND d.status='confirmed'
        WHERE ph.user_id=$1
        GROUP BY ph.req_val, ph.bank, ph.method, ph.operator_id, ph.created_at
        ORDER BY ph.created_at DESC
        LIMIT 10
    ''', user_id)
    await conn.close()
    return rows


async def get_operator_by_req_val(req_val: str) -> int | None:
    conn = await asyncpg.connect(**DB_CONFIG)
    row = await conn.fetchrow(
        "SELECT added_by FROM requisites WHERE val=$1 LIMIT 1",
        req_val
    )
    await conn.close()
    return row['added_by'] if row else None


# ==================== KEYBOARDS ====================

def main_menu():
    kb = [
        [KeyboardButton(text="✅Новая сделка")],
        [KeyboardButton(text="💰Мой баланс"), KeyboardButton(text="💸 Вывод")],
        [KeyboardButton(text="👥Наша группа"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📜 История"), KeyboardButton(text="🔄 Перезапуск"), KeyboardButton(text="Техподдержка")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def operator_menu(is_online: bool):
    status = "🟢 Вы ОНЛАЙН" if is_online else "🔴 Вы ОФФЛАЙН"
    toggle_text = "🔴 Выйти из работы (/offline)" if is_online else "🟢 Начать работу (/online)"
    kb = [
        [KeyboardButton(text="🔄 Перезапуск"), KeyboardButton(text=toggle_text)],
        [KeyboardButton(text="📋 Мои реквизиты"), KeyboardButton(text="📊 Моя статистика")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True), status


# ==================== /START ====================

@dp.message(Command("start"))
@dp.message(F.text.contains("Перезапуск"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id

    if uid in OPERATORS:
        conn = await asyncpg.connect(**DB_CONFIG)
        op = await conn.fetchrow("SELECT is_online FROM operators WHERE user_id=$1", uid)
        await conn.close()
        is_online = op['is_online'] if op else False
        kb, status = operator_menu(is_online)
        name = OPERATORS[uid]

        extra = ""
        if uid in SUPER_ADMINS:
            rate = await get_rate()
            extra = (
                f"\n\n👑 *Команды Шефа:*\n"
                f"• `/set число` — установить курс\n"
                f"• `/operators` — статус операторов\n"
                f"• `/users` — пользователи\n"
                f"• `/add ID СУММА` — пополнить баланс\n"
                f"• `/clearstats` — очистить обороты\n"
                f"• `/setchek` — фото примера чека\n\n"
                f"💵 Курс: `1 USDT = {rate} TJS`"
            )

        await message.answer(
            f"👋 *Кабинет оператора — {name}*\n\n"
            f"Статус: {status}\n\n"
            f"Команды:\n"
            f"• `/online` — начать принимать заявки\n"
            f"• `/offline` — остановить приём заявок\n"
            f"• `/myreqs` — мои реквизиты\n"
            f"• `/addreq` — добавить реквизит\n"
            f"• `/delreq ID` — удалить реквизит\n"
            f"• `/mystats` — моя статистика\n"
            f"• `/check` — все реквизиты"
            f"{extra}",
            reply_markup=kb, parse_mode="Markdown"
        )
        return

    await get_balance(
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name
    )
    rate = await get_rate()
    await message.answer(
        f"👋 *Добро пожаловать в Nazar Bot!*\n"
        f"Официальный сервис обмена *TJS ⇄ USDT*\n\n"
        f"💵 Текущий курс: `1 USDT = {rate} TJS`\n\n"
        "👇 *Выберите действие в меню ниже:*",
        reply_markup=main_menu(), parse_mode="Markdown"
    )


# ==================== OPERATOR COMMANDS ====================

@dp.message(Command("online"))
@dp.message(F.text == "🟢 Начать работу (/online)")
async def cmd_online(message: types.Message):
    uid = message.from_user.id
    if uid not in OPERATORS:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("UPDATE operators SET is_online=TRUE WHERE user_id=$1", uid)
    await conn.close()
    name = OPERATORS[uid]
    kb, _ = operator_menu(True)
    await message.answer(
        f"✅ *{name}*, вы теперь *ОНЛАЙН*!\n\nБот будет выдавать ваши реквизиты покупателям.",
        reply_markup=kb, parse_mode="Markdown"
    )
    for sa in SUPER_ADMINS:
        try:
            await bot.send_message(sa, f"🟢 Оператор *{name}* вышел онлайн.", parse_mode="Markdown")
        except:
            pass


@dp.message(Command("offline"))
@dp.message(F.text == "🔴 Выйти из работы (/offline)")
async def cmd_offline(message: types.Message):
    uid = message.from_user.id
    if uid not in OPERATORS:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("UPDATE operators SET is_online=FALSE WHERE user_id=$1", uid)
    await conn.close()
    name = OPERATORS[uid]
    kb, _ = operator_menu(False)
    await message.answer(
        f"🔴 *{name}*, вы теперь *ОФФЛАЙН*.\n\nВаши реквизиты больше не выдаются.",
        reply_markup=kb, parse_mode="Markdown"
    )
    for sa in SUPER_ADMINS:
        try:
            await bot.send_message(sa, f"🔴 Оператор *{name}* ушёл оффлайн.", parse_mode="Markdown")
        except:
            pass


@dp.message(Command("myreqs"))
@dp.message(F.text == "📋 Мои реквизиты")
async def cmd_myreqs(message: types.Message):
    uid = message.from_user.id
    if uid not in OPERATORS:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    rows = await conn.fetch("SELECT id, bank, method, val FROM requisites WHERE added_by=$1 ORDER BY id", uid)
    await conn.close()
    if not rows:
        return await message.answer("У вас нет реквизитов. Добавьте через `/addreq`.", parse_mode="Markdown")
    text = f"📋 *Ваши реквизиты ({OPERATORS[uid]}):*\n\n"
    for r in rows:
        text += f"• [ID: `{r['id']}`] {r['bank']} ({r['method']}): `{r['val']}`\n"
    text += "\n🗑 Удалить: `/delreq ID`"
    await message.answer(text, parse_mode="Markdown")


# ==================== ДОБАВЛЕНИЕ РЕКВИЗИТА ====================

BANK_LIST = ["Душанбе сити", "Эсхата", "Корти милли", "Васл"]

@dp.message(Command("addreq"))
async def cmd_addreq(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in OPERATORS:
        return
    await state.set_state(AdminStates.add_bank_choose)
    buttons = [[InlineKeyboardButton(text=b, callback_data=f"addbank_{b}")] for b in BANK_LIST]
    buttons.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="addbank_manual")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🏦 *Выберите банк:*", reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(AdminStates.add_bank_choose, F.data.startswith("addbank_"))
async def addreq_bank_chosen(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.replace("addbank_", "")
    if val == "manual":
        await state.set_state(AdminStates.add_bank)
        await callback.message.edit_text("✏️ Введите название банка вручную:")
        await callback.answer()
        return
    await state.update_data(bank=val)
    await state.set_state(AdminStates.add_method)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📱 Номер телефона", callback_data="setmeth_НТ"),
        InlineKeyboardButton(text="💳 Номер карты", callback_data="setmeth_НК")
    ]])
    await callback.message.edit_text(
        f"🏦 Банк: *{val}*\n\nВыберите тип реквизита:",
        reply_markup=kb, parse_mode="Markdown"
    )
    await callback.answer()


@dp.message(AdminStates.add_bank)
async def adm_bank(message: types.Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF:
        return
    await state.update_data(bank=message.text.strip())
    await state.set_state(AdminStates.add_method)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📱 Номер телефона", callback_data="setmeth_НТ"),
        InlineKeyboardButton(text="💳 Номер карты", callback_data="setmeth_НК")
    ]])
    await message.answer(
        f"🏦 Банк: *{message.text.strip()}*\n\nВыберите тип реквизита:",
        reply_markup=kb, parse_mode="Markdown"
    )


@dp.callback_query(AdminStates.add_method, F.data.startswith("setmeth_"))
async def adm_meth(callback: types.CallbackQuery, state: FSMContext):
    meth = callback.data.replace("setmeth_", "")
    meth_label = "📱 Номер телефона" if meth == "НТ" else "💳 Номер карты"
    data = await state.get_data()
    await state.update_data(method=meth)
    await state.set_state(AdminStates.add_val)
    await callback.message.edit_text(
        f"🏦 Банк: *{data['bank']}*\n"
        f"💳 Тип: *{meth_label}*\n\n"
        f"Введите {'номер телефона' if meth == 'НТ' else 'номер карты'}:",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.message(AdminStates.add_val)
async def adm_val(message: types.Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF:
        return
    d = await state.get_data()
    val = message.text.strip()
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute(
        "INSERT INTO requisites (bank, method, val, added_by) VALUES ($1, $2, $3, $4)",
        d['bank'], d['method'], val, message.from_user.id
    )
    await conn.close()
    meth_label = "📱 Телефон" if d['method'] == 'НТ' else "💳 Карта"
    await message.answer(
        f"✅ *Реквизит добавлен!*\n\n"
        f"🏦 Банк: `{d['bank']}`\n"
        f"💳 Тип: {meth_label}\n"
        f"🔢 Номер: `{val}`",
        parse_mode="Markdown"
    )
    await state.clear()


@dp.message(Command("delreq"))
async def cmd_delreq(message: types.Message, command: CommandObject):
    uid = message.from_user.id
    if uid not in OPERATORS:
        return
    if not command.args:
        return await message.answer("Укажи ID: `/delreq 5`", parse_mode="Markdown")
    try:
        req_id = int(command.args.strip())
        conn = await asyncpg.connect(**DB_CONFIG)
        row = await conn.fetchrow("SELECT * FROM requisites WHERE id=$1 AND added_by=$2", req_id, uid)
        if not row:
            await conn.close()
            return await message.answer(f"❌ Реквизит `{req_id}` не найден или не ваш.", parse_mode="Markdown")
        await conn.execute("DELETE FROM requisites WHERE id=$1", req_id)
        await conn.execute("DELETE FROM usage_log WHERE req_id=$1", req_id)
        await conn.close()
        await message.answer(f"✅ Удалён: `{row['bank']}` / `{row['val']}`", parse_mode="Markdown")
    except ValueError:
        await message.answer("❌ Формат: `/delreq 5`", parse_mode="Markdown")


@dp.message(Command("mystats"))
@dp.message(F.text == "📊 Моя статистика")
async def cmd_mystats(message: types.Message):
    uid = message.from_user.id
    if uid not in OPERATORS:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    total = await conn.fetchval("SELECT COUNT(*) FROM deals WHERE operator_id=$1 AND status='confirmed'", uid) or 0
    today = await conn.fetchval("SELECT COUNT(*) FROM deals WHERE operator_id=$1 AND status='confirmed' AND created_at >= CURRENT_DATE", uid) or 0
    month = await conn.fetchval("SELECT COUNT(*) FROM deals WHERE operator_id=$1 AND status='confirmed' AND created_at >= date_trunc('month', CURRENT_DATE)", uid) or 0
    vol_total = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE operator_id=$1 AND status='confirmed'", uid) or 0
    vol_today = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE operator_id=$1 AND status='confirmed' AND created_at >= CURRENT_DATE", uid) or 0
    vol_month = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM deals WHERE operator_id=$1 AND status='confirmed' AND created_at >= date_trunc('month', CURRENT_DATE)", uid) or 0
    is_online = await conn.fetchval("SELECT is_online FROM operators WHERE user_id=$1", uid)
    status_str = "🟢 Онлайн" if is_online else "🔴 Оффлайн"
    name = OPERATORS[uid]
    text = (
        f"📊 *Статистика оператора {name}*\n\n"
        f"Статус: {status_str}\n\n"
        f"📅 *За сегодня:*\n   • Сделок: `{today}`\n   • Оборот: `{float(vol_today):.2f} USDT`\n\n"
        f"🗓 *За этот месяц:*\n   • Сделок: `{month}`\n   • Оборот: `{float(vol_month):.2f} USDT`\n\n"
        f"🔄 *За всё время:*\n   • Сделок: `{total}`\n   • Оборот: `{float(vol_total):.2f} USDT`"
    )
    if uid == 7918010548:
        text += "\n\n👑 *ОБЩАЯ СТАТИСТИКА ВСЕХ ОПЕРАТОРОВ:*"
        ops = await conn.fetch("""
            SELECT o.user_id, o.name, o.is_online,
                   (SELECT COUNT(*) FROM deals WHERE operator_id=o.user_id AND status='confirmed') as t_cnt,
                   (SELECT COALESCE(SUM(amount),0) FROM deals WHERE operator_id=o.user_id AND status='confirmed') as t_vol
            FROM operators o ORDER BY o.name
        """)
        for o in ops:
            op_st = "🟢" if o['is_online'] else "🔴"
            text += f"\n{op_st} *{o['name']}*: `{o['t_cnt']}` сд. | `{float(o['t_vol']):.2f} USDT`"
    await conn.close()
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if message.from_user.id not in ALL_STAFF:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    rows = await conn.fetch("""
        SELECT r.id, r.bank, r.method, r.val, r.added_by,
               o.name as op_name, o.is_online,
               COUNT(l.id) as uses
        FROM requisites r
        LEFT JOIN operators o ON o.user_id = r.added_by
        LEFT JOIN usage_log l ON l.req_id = r.id
            AND l.used_at >= NOW() - INTERVAL '24 hours'
            AND l.confirmed = TRUE
        GROUP BY r.id, r.bank, r.method, r.val, r.added_by, o.name, o.is_online
        ORDER BY r.added_by, r.id
    """)
    await conn.close()
    if not rows:
        return await message.answer("❌ В базе нет ни одного реквизита.")
    by_op = {}
    for r in rows:
        op_key = r['added_by']
        if op_key not in by_op:
            by_op[op_key] = []
        by_op[op_key].append(r)
    text = "🔍 *Все реквизиты в БД:*\n\n"
    for op_id, reqs in by_op.items():
        first = reqs[0]
        op_name = first['op_name'] or str(op_id)
        op_status = "🟢" if first['is_online'] else "🔴"
        text += f"{op_status} *{op_name}:*\n"
        for r in reqs:
            limit_tag = "⚠️ лимит" if r['uses'] >= 2 else f"{r['uses']}/2"
            text += f"  `{r['id']}` | {r['bank']} ({r['method']}) | `{r['val']}` | {limit_tag}\n"
        text += "\n"
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i+4000], parse_mode="Markdown")
    else:
        await message.answer(text, parse_mode="Markdown")


@dp.message(Command("operators"))
async def cmd_operators(message: types.Message):
    if message.from_user.id not in SUPER_ADMINS:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    rows = await conn.fetch("SELECT user_id, name, is_online, last_deal_at FROM operators ORDER BY name")
    await conn.close()
    text = "👥 *Статус операторов:*\n\n"
    for r in rows:
        status = "🟢 Онлайн" if r['is_online'] else "🔴 Оффлайн"
        last = r['last_deal_at'].strftime("%d.%m %H:%M") if r['last_deal_at'] else "—"
        text += f"• *{r['name']}* (`{r['user_id']}`)\n  {status} | Последняя сделка: {last}\n\n"
    await message.answer(text, parse_mode="Markdown")


# ==================== MAIN MENU ====================

@dp.message(F.text == "👥Наша группа")
async def our_group(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Перейти в группу", url=GROUP_URL)]
    ])
    await message.answer("👥 *Наша группа*\n\nПрисоединяйтесь к нашему сообществу!",
                         reply_markup=kb, parse_mode="Markdown")


@dp.message(F.text == "💰Мой баланс")
async def show_balance(message: types.Message):
    if message.from_user.id in ALL_STAFF:
        return
    bal = await get_balance(message.from_user.id,
                            username=message.from_user.username,
                            full_name=message.from_user.full_name)
    await message.answer(f"💳 *ВАШ БАЛАНС:* `{bal} USDT`", parse_mode="Markdown")


@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if message.from_user.id in ALL_STAFF:
        return
    uid = message.from_user.id
    conn = await asyncpg.connect(**DB_CONFIG)
    bal = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1", uid)
    personal_all = await conn.fetchrow("SELECT COUNT(*) as count, SUM(amount) as total FROM orders WHERE user_id=$1", uid)
    personal_today = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE user_id=$1 AND created_at >= CURRENT_DATE", uid)
    await conn.close()
    text = (
        "📊 *Ваша личная статистика*\n\n"
        f"🆔 Ваш ID: `{uid}`\n"
        f"💰 Баланс: `{float(bal or 0)} USDT`\n"
        "────────────────────\n"
        f"📅 Сделок за сегодня: `{personal_today or 0}`\n"
        f"🔄 Всего сделок: `{personal_all['count'] or 0}`\n"
        f"📥 Куплено всего: `{float(personal_all['total'] or 0)} USDT`"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.text == "📜 История")
@dp.message(Command("history"))
async def show_history(message: types.Message):
    if message.from_user.id in ALL_STAFF:
        return
    uid = message.from_user.id
    rows = await get_purchase_history(uid)
    if not rows:
        return await message.answer(
            "📜 *История покупок*\n\nУ вас пока нет завершённых сделок.",
            parse_mode="Markdown"
        )
    text = "📜 *История ваших реквизитов:*\n\n"
    for i, r in enumerate(rows, 1):
        last_date = r['last_deal'].strftime("%d.%m.%Y") if r['last_deal'] else "—"
        method_label = "📱 Телефон" if r['method'] == 'НТ' else "💳 Карта"
        text += (
            f"*{i}. {r['bank']}* ({method_label})\n"
            f"   Реквизит: `{r['req_val']}`\n"
            f"   Сделок: `{r['deal_count'] or 0}` | Последняя: `{last_date}`\n\n"
        )
    text += "💡 _Реквизиты из истории используются при выводе средств._"
    await message.answer(text, parse_mode="Markdown")


# ==================== SUPPORT ====================

@dp.message(F.text == "Техподдержка")
async def support_start(message: types.Message, state: FSMContext):
    if message.from_user.id in ALL_STAFF:
        return
    await state.set_state(SupportState.waiting_for_question)
    await message.answer("🛠 *Напишите ваш вопрос одним сообщением.* Оператор ответит вам здесь.")


@dp.message(SupportState.waiting_for_question)
async def forward_to_admins(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_{message.from_user.id}")]
    ])
    for admin_id in SUPPORT_ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆘 *НОВЫЙ ТИКЕТ*\nОт: @{message.from_user.username}\nID: `{message.from_user.id}`\n\n{message.text}",
                reply_markup=kb, parse_mode="Markdown"
            )
        except:
            pass
    await message.answer("✅ Запрос отправлен поддержке.")
    await state.clear()


@dp.callback_query(F.data.startswith("reply_"))
async def admin_reply_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    user_id = callback.data.split("_")[1]
    await state.clear()
    await state.update_data(reply_to=user_id)
    await state.set_state(SupportState.waiting_for_answer)
    await callback.message.answer(f"✍️ Введите ответ для пользователя `{user_id}`:", parse_mode="Markdown")
    await callback.answer("Введите ответ ниже")


@dp.message(SupportState.waiting_for_answer)
async def admin_send_answer(message: types.Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF:
        return
    data = await state.get_data()
    user_id = data.get("reply_to")
    if not user_id:
        await state.clear()
        return
    kb_admin = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_{user_id}")]
    ])
    try:
        await bot.send_message(int(user_id), f"🛠 *ОТВЕТ ПОДДЕРЖКИ:*\n\n{message.text}",
                               parse_mode="Markdown", reply_markup=buyer_reply_kb())
        await message.answer(f"✅ Ответ отправлен пользователю `{user_id}`.",
                             reply_markup=kb_admin, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка доставки: {e}")
    await state.clear()


@dp.callback_query(F.data == "buyer_reply")
async def buyer_reply_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(SupportState.waiting_for_question)
    await callback.message.answer("✍️ *Напишите ваш ответ* — он уйдёт оператору:", parse_mode="Markdown")
    await callback.answer()


# ==================== NEW DEAL ====================

@dp.message(F.text == "✅Новая сделка")
async def start_deal(message: types.Message, state: FSMContext):
    if message.from_user.id in ALL_STAFF:
        return
    if not await check_sub(message.from_user.id):
        return await message.answer(f"❌ Для продолжения подпишитесь на наш канал: {GROUP_URL}")
    await state.set_state(OrderProcess.confirm_regulations)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Согласен", callback_data="confirm")]])
    await message.answer(
        "📜 *РЕГЛАМЕНТ И ПРАВИЛА СЕРВИСА*\n\n"
        "🔹 Время обмена: *5 - 30 минут*.\n"
        "🔹 Курс: фиксация в момент зачисления средств.\n"
        "🔹 Лимиты: минимум *13,000 RUB*.\n\n"
        "⚠️ Нажимая кнопку, вы подтверждаете согласие с [правилами](https://telegra.ph/Reglament-servisa-obmena-03-24).",
        reply_markup=kb, parse_mode="Markdown"
    )


@dp.callback_query(OrderProcess.confirm_regulations, F.data == "confirm")
async def process_regs(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderProcess.confirm_receipt)
    receipt_text = (
        "📸 *ТРЕБОВАНИЯ К ПОДТВЕРЖДЕНИЮ ПЛАТЕЖА*\n\n"
        "1️⃣ *Валюта и сумма*\n• В чеке должна быть видна сумма в *TJS*.\n\n"
        "2️⃣ *Качество изображения*\n• Чек должен быть чётким и не обрезанным.\n\n"
        "⚠️ *ВАЖНО:* Чеки без суммы в TJS затрудняют обработку."
    )
    await callback.message.delete()
    photos_sent = False
    chek1_path = next((e for e in ['chek.jpg', 'chek.png'] if os.path.exists(e)), None)
    chek2_path = next((e for e in ['chek2.jpg', 'chek2.png'] if os.path.exists(e)), None)
    if chek1_path and chek2_path:
        try:
            await callback.message.answer_media_group(media=[
                InputMediaPhoto(media=FSInputFile(chek1_path)),
                InputMediaPhoto(media=FSInputFile(chek2_path), caption=receipt_text, parse_mode="Markdown")
            ])
            photos_sent = True
        except:
            pass
    if not photos_sent:
        conn = await asyncpg.connect(**DB_CONFIG)
        chek1 = await conn.fetchval("SELECT value FROM settings WHERE key='chek1'")
        chek2 = await conn.fetchval("SELECT value FROM settings WHERE key='chek2'")
        await conn.close()
        if chek1 and chek2:
            try:
                await callback.message.answer_media_group(media=[
                    InputMediaPhoto(media=chek1),
                    InputMediaPhoto(media=chek2, caption=receipt_text, parse_mode="Markdown")
                ])
                photos_sent = True
            except:
                pass
        elif chek1:
            try:
                await callback.message.answer_photo(chek1, caption=receipt_text, parse_mode="Markdown")
                photos_sent = True
            except:
                pass
    if not photos_sent:
        await callback.message.answer(receipt_text, parse_mode="Markdown")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆗 Понял, выбрать банк", callback_data="confirm_receipt")]])
    await callback.message.answer("Вы изучили требования к чеку?", reply_markup=kb)


@dp.callback_query(OrderProcess.confirm_receipt, F.data == "confirm_receipt")
async def choose_bank(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderProcess.waiting_for_bank)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Душанбе сити", callback_data="bank_Душанбе сити"),
         InlineKeyboardButton(text="Эсхата", callback_data="bank_Эсхата")],
        [InlineKeyboardButton(text="Корти милли", callback_data="bank_Корти милли"),
         InlineKeyboardButton(text="Васл", callback_data="bank_Васл")]
    ])
    await callback.message.answer("🏦 *Выберите банк для оплаты:*", reply_markup=kb)


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
    bank = data['chosen_bank']

    operator_id = await get_random_online_operator_for_bank(bank, meth)

    if not operator_id:
        # Нет онлайн-операторов с реквизитами для этого банка/метода
        conn = await asyncpg.connect(**DB_CONFIG)
        any_req = await conn.fetchval(
            "SELECT COUNT(*) FROM requisites WHERE LOWER(TRIM(bank))=LOWER(TRIM($1)) AND LOWER(TRIM(method))=LOWER(TRIM($2))",
            bank, meth
        )
        await conn.close()
        if any_req:
            return await callback.message.answer(
                f"⏳ Все операторы для *{bank}* сейчас оффлайн. Попробуйте позже.",
                parse_mode="Markdown"
            )
        else:
            return await callback.message.answer(
                f"❌ Реквизитов для *{bank}* нет. Выберите другой банк.",
                parse_mode="Markdown"
            )

    req = await get_available_req(bank, meth, callback.from_user.id, operator_id)

    if not req:
        return await callback.message.answer(
            "⏳ Реквизиты временно исчерпаны. Попробуйте позже или выберите другой банк."
        )

    await state.update_data(operator_id=operator_id, req_val=req['val'], chosen_method=meth)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оплачено", callback_data="action_pay")],
        [InlineKeyboardButton(text="🔄 Другой реквизит", callback_data="action_other_req")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="action_cancel")]
    ])
    await callback.message.edit_text(
        f"💳 *РЕКВИЗИТЫ*\n\nБанк: {bank}\nРеквизит: `{req['val']}`\n\n"
        f"✅ Оплатили — нажмите «Оплачено»\n🔄 Не подходит — «Другой реквизит»\n❌ Передумали — «Отмена»",
        reply_markup=kb, parse_mode="Markdown"
    )
    await state.set_state(OrderProcess.waiting_for_action)


@dp.callback_query(OrderProcess.waiting_for_action, F.data == "action_other_req")
async def give_other_req(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    old_val = data.get('req_val')
    bank = data.get('chosen_bank')
    meth = data.get('chosen_method')
    operator_id = data.get('operator_id')
    if old_val:
        await block_req_for_user(old_val, callback.from_user.id)
    req = await get_available_req(bank, meth, callback.from_user.id, operator_id)
    if not req:
        return await callback.answer("❌ Других свободных реквизитов нет.", show_alert=True)
    await state.update_data(req_val=req['val'])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оплачено", callback_data="action_pay")],
        [InlineKeyboardButton(text="🔄 Другой реквизит", callback_data="action_other_req")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="action_cancel")]
    ])
    await callback.message.edit_text(
        f"💳 *РЕКВИЗИТЫ*\n\nБанк: {bank}\nРеквизит: `{req['val']}`\n\n"
        f"✅ Оплатили — нажмите «Оплачено»\n🔄 Не подходит — «Другой реквизит»\n❌ Передумали — «Отмена»",
        reply_markup=kb, parse_mode="Markdown"
    )
    await callback.answer()


@dp.callback_query(OrderProcess.waiting_for_action, F.data == "action_cancel")
async def cancel_deal(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ *Сделка отменена.*")
    await callback.answer()


@dp.callback_query(OrderProcess.waiting_for_action, F.data == "action_pay")
async def ask_photo(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderProcess.waiting_for_data)
    await callback.message.edit_text("📸 *Пришлите скриншот чека:*")


@dp.message(OrderProcess.waiting_for_data, F.photo)
async def handle_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    req_val = data.get('req_val')
    operator_id = data.get('operator_id')
    bank = data.get('chosen_bank', '')
    meth = data.get('chosen_method', '')
    user = message.from_user
    await confirm_req_usage(req_val, user.id)
    name = user.full_name or ""
    username = f"@{user.username}" if user.username else "нет username"
    photo_id = message.photo[-1].file_id

    conn = await asyncpg.connect(**DB_CONFIG)
    deal_id = await conn.fetchval("""
        INSERT INTO deals (user_id, username, full_name, req_val, bank, photo_id, status, operator_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7, $8)
        RETURNING id
    """, user.id, user.username or "", user.full_name or "",
        req_val, bank, photo_id, operator_id, datetime.now())
    await conn.close()

    await save_purchase_history(user.id, req_val, bank, meth, operator_id, deal_id)
    await mark_operator_deal(operator_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ ПОДТВЕРДИТЬ", callback_data=f"conf_d_{deal_id}"),
         InlineKeyboardButton(text="❌ ОТКЛОНИТЬ", callback_data=f"reject_d_{deal_id}")],
        [InlineKeyboardButton(text="✉️ Написать", callback_data=f"msg_{user.id}"),
         InlineKeyboardButton(text="📸 Отправить чек", callback_data=f"sendphoto_{user.id}")]
    ])

    try:
        await bot.send_photo(
            operator_id, photo_id,
            caption=(
                f"📩 *НОВЫЙ ЧЕК*\n"
                f"👤 Покупатель: {name} ({username})\n"
                f"🆔 ID: `{user.id}`\n"
                f"💳 Реквизит: `{req_val}`\n"
                f"🏦 Банк: {bank}"
            ),
            reply_markup=kb, parse_mode="Markdown"
        )
    except:
        pass

    for sa in SUPER_ADMINS:
        if sa == operator_id:
            continue
        op_name = OPERATORS.get(operator_id, str(operator_id))
        try:
            await bot.send_photo(
                sa, photo_id,
                caption=(
                    f"📩 *ЧЕК (инфо)*\n"
                    f"👤 {name} ({username})\n"
                    f"🆔 ID: `{user.id}`\n"
                    f"💳 Реквизит: `{req_val}`\n"
                    f"👷 Оператор: *{op_name}*"
                ),
                reply_markup=kb, parse_mode="Markdown"
            )
        except:
            pass

    await message.answer(
        f"✅ Чек получен. Ожидайте проверку (5-30 минут).\n\n"
        f"💳 Реквизит `{req_val}` сохранён в вашей истории.",
        parse_mode="Markdown"
    )
    await state.clear()


# ==================== CONFIRM / REJECT ====================

@dp.callback_query(F.data.startswith("conf_d_"))
async def conf_pay(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    deal_id = int(callback.data.split("_")[2])
    conn = await asyncpg.connect(**DB_CONFIG)
    deal = await conn.fetchrow("SELECT user_id, status FROM deals WHERE id=$1", deal_id)
    await conn.close()
    if not deal or deal['status'] != 'pending':
        return await callback.answer("⚠️ Эта заявка уже обработана.", show_alert=True)
    uid = deal['user_id']
    rate = await get_rate()
    await state.clear()
    await state.update_data(confirm_uid=uid, confirm_deal_id=deal_id)
    await state.set_state(ConfirmState.waiting_for_tjs_amount)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass
    await callback.message.answer(
        f"💵 Текущий курс: `1 USDT = {rate} TJS`\n\nВведите сумму в *TJS* от покупателя `{uid}`:",
        parse_mode="Markdown"
    )
    await callback.answer()


@dp.message(ConfirmState.waiting_for_tjs_amount)
async def process_tjs_amount(message: types.Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF:
        return
    data = await state.get_data()
    uid = data.get("confirm_uid")
    deal_id = data.get("confirm_deal_id")
    if not uid:
        await state.clear()
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    current_status = await conn.fetchval("SELECT status FROM deals WHERE id=$1", deal_id)
    if current_status != 'pending':
        await conn.close()
        await state.clear()
        return await message.answer("⚠️ Сделка уже обработана другим администратором.")
    raw = message.text.strip().replace(",", ".").replace(" ", "")
    try:
        tjs_amount = float(raw)
    except:
        await conn.close()
        return await message.answer(f"❌ Не могу распознать число: `{message.text}`", parse_mode="Markdown")
    rate_str = await get_rate()
    try:
        rate = float(rate_str.replace(",", ".").strip())
    except:
        await conn.close()
        await message.answer("❌ Курс не установлен.")
        await state.clear()
        return
    if rate <= 0:
        await conn.close()
        await message.answer("❌ Курс равен 0. Установите курс через `/set число`", parse_mode="Markdown")
        await state.clear()
        return
    usdt_amount = round(tjs_amount / rate, 2)
    await update_balance(uid, usdt_amount)
    await conn.execute("INSERT INTO orders (user_id, amount, created_at) VALUES ($1, $2, $3)", uid, usdt_amount, datetime.now())
    await conn.execute("UPDATE deals SET status='confirmed', amount=$1 WHERE id=$2", usdt_amount, deal_id)
    await conn.close()
    try:
        await bot.send_message(uid,
            f"💎 *ОПЛАТА ПОДТВЕРЖДЕНА!*\n\n📥 Получено: `{tjs_amount} TJS`\n💱 Курс: `1 USDT = {rate} TJS`\n💰 Начислено: `{usdt_amount} USDT`\n\nСпасибо за обмен! 🙏",
            parse_mode="Markdown", reply_markup=buyer_reply_kb())
    except Exception as e:
        await message.answer(f"⚠️ Не удалось уведомить покупателя: {e}")
    op_name = OPERATORS.get(message.from_user.id, "Супер-Админ")
    await message.answer(
        f"✅ Готово! [{op_name}]\nЮзер: `{uid}`\nTJS: `{tjs_amount}`\nUSDT: `{usdt_amount}`",
        parse_mode="Markdown"
    )
    await state.clear()


@dp.callback_query(F.data.startswith("reject_d_"))
async def reject_pay(callback: types.CallbackQuery):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    deal_id = int(callback.data.split("_")[2])
    conn = await asyncpg.connect(**DB_CONFIG)
    deal = await conn.fetchrow("SELECT user_id, status FROM deals WHERE id=$1", deal_id)
    if not deal or deal['status'] != 'pending':
        await conn.close()
        return await callback.answer("⚠️ Эта заявка уже обработана.", show_alert=True)
    uid = deal['user_id']
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass
    await conn.execute("UPDATE deals SET status='rejected' WHERE id=$1", deal_id)
    await conn.close()
    try:
        await bot.send_message(uid,
            "❌ *Оплата отклонена.*\n\nВаш чек не прошёл проверку.\nЕсли считаете это ошибкой — напишите в поддержку.",
            parse_mode="Markdown", reply_markup=buyer_reply_kb())
        await callback.message.answer(f"❌ Сделка для юзера {uid} отклонена.")
    except:
        await callback.message.answer("❌ Не удалось уведомить покупателя.")
    await callback.answer()


# ==================== WRITE TO BUYER ====================

@dp.callback_query(F.data.startswith("msg_"))
async def msg_buyer_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    uid = callback.data[4:]
    await state.clear()
    await state.update_data(msg_to=uid)
    await state.set_state(MsgBuyerState.waiting_for_message)
    await callback.message.answer(f"✍️ Введите сообщение для покупателя `{uid}`:", parse_mode="Markdown")
    await callback.answer("Введите сообщение ниже")


@dp.message(MsgBuyerState.waiting_for_message)
async def msg_buyer_send(message: types.Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF:
        return
    data = await state.get_data()
    user_id = data.get("msg_to")
    kb_admin = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"msg_{user_id}")]
    ])
    try:
        await bot.send_message(int(user_id),
            f"🛠 *СООБЩЕНИЕ ОТ ОПЕРАТОРА:*\n\n{message.text}",
            parse_mode="Markdown", reply_markup=buyer_reply_kb())
        await message.answer(f"✅ Сообщение отправлено покупателю `{user_id}`.",
                             reply_markup=kb_admin, parse_mode="Markdown")
    except:
        await message.answer(f"❌ Не удалось отправить юзеру `{user_id}`.", parse_mode="Markdown")
    await state.clear()


# ==================== SEND PHOTO TO USER ====================

@dp.callback_query(F.data.startswith("sendphoto_"))
async def admin_sendphoto_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    uid = callback.data.split("_")[1]
    await state.clear()
    await state.update_data(photo_to=uid)
    await state.set_state(AdminStates.waiting_for_photo_to_user)
    await callback.message.answer(f"📸 Отправьте фото (чек) для пользователя `{uid}`:", parse_mode="Markdown")
    await callback.answer()


@dp.message(AdminStates.waiting_for_photo_to_user, F.photo)
async def admin_sendphoto_done(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = int(data.get("photo_to"))
    try:
        await bot.send_photo(uid, message.photo[-1].file_id,
                             caption="✅ *Операция выполнена.*\n\nВаш чек во вложении.",
                             parse_mode="Markdown", reply_markup=buyer_reply_kb())
        await message.answer(f"✅ Чек успешно отправлен пользователю `{uid}`.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить фото: {e}")
    await state.clear()


# ==================== WITHDRAW ====================
# ИСПРАВЛЕНО: два отдельных шага — выбор реквизита из истории (для оператора)
# и ввод адреса кошелька (USDT/Bybit). Пользователь сначала видит реквизиты
# из истории (кому платить оператору при выводе), потом вводит адрес кошелька.

@dp.message(F.text == "💸 Вывод")
async def withdraw_start(message: types.Message, state: FSMContext):
    if message.from_user.id in ALL_STAFF:
        return
    bal = await get_balance(message.from_user.id,
                            username=message.from_user.username,
                            full_name=message.from_user.full_name)
    if bal <= 0:
        return await message.answer("⚠️ Недостаточно средств для вывода.")
    await state.set_state(WithdrawProcess.waiting_for_amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="wd_cancel")]])
    await message.answer(f"💰 Ваш баланс: `{bal} USDT`\n\nВведите сумму для вывода:",
                         reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data == "wd_cancel")
async def wd_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Вывод отменён.")
    await callback.answer()


@dp.message(WithdrawProcess.waiting_for_amount)
async def wd_amt(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        bal = await get_balance(message.from_user.id)
        if amount <= 0:
            return await message.answer("❌ Сумма должна быть больше 0.")
        if amount > bal:
            return await message.answer(f"❌ Недостаточно средств. Баланс: `{bal} USDT`", parse_mode="Markdown")
    except:
        return await message.answer("❌ Введите корректную сумму.", parse_mode="Markdown")
    await state.update_data(wa=message.text.strip())
    await state.set_state(WithdrawProcess.waiting_for_method)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 USDT TRC20", callback_data="wdmeth_trc20")],
        [InlineKeyboardButton(text="🟡 Bybit UID", callback_data="wdmeth_bybit")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="wd_cancel")]
    ])
    await message.answer("Выберите способ вывода:", reply_markup=kb)


@dp.callback_query(WithdrawProcess.waiting_for_method, F.data.startswith("wdmeth_"))
async def wd_choose_method(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data.replace("wdmeth_", "")
    method_label = "🌐 USDT TRC20" if method == "trc20" else "🟡 Bybit UID"
    address_label = "адрес USDT TRC20" if method == "trc20" else "Bybit UID"
    await state.update_data(wd_method=method, wd_method_label=method_label)

    uid = callback.from_user.id
    history = await get_purchase_history(uid)

    # Шаг 1: если есть история — показываем реквизиты для выбора оператора
    if history:
        buttons = []
        for r in history:
            method_emoji = "📱" if r['method'] == 'НТ' else "💳"
            btn_text = f"{method_emoji} {r['bank']}: {r['req_val']}"
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"wd_req_{r['req_val']}")])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="wd_cancel")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(
            f"💳 *Шаг 1 из 2: Выберите реквизит*\n\n"
            f"Укажите реквизит, которым вы пополняли — оператор этого реквизита обработает вывод:",
            reply_markup=kb, parse_mode="Markdown"
        )
        await state.set_state(WithdrawProcess.waiting_for_req)
    else:
        # Нет истории — сразу переходим к вводу адреса кошелька
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="wd_cancel")]])
        await callback.message.edit_text(
            f"📬 *Введите {address_label}* для получения средств:",
            reply_markup=kb, parse_mode="Markdown"
        )
        await state.set_state(WithdrawProcess.waiting_for_address)
    await callback.answer()


@dp.callback_query(WithdrawProcess.waiting_for_req, F.data.startswith("wd_req_"))
async def wd_select_req(callback: types.CallbackQuery, state: FSMContext):
    req_val = callback.data[len("wd_req_"):]
    operator_id = await get_operator_by_req_val(req_val)
    await state.update_data(wd_req_val=req_val, wd_operator_id=operator_id)

    data = await state.get_data()
    method = data.get('wd_method', 'trc20')
    method_label = data.get('wd_method_label', '🌐 USDT TRC20')
    address_label = "адрес USDT TRC20" if method == "trc20" else "Bybit UID"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="wd_cancel")]])
    await callback.message.edit_text(
        f"✅ Реквизит выбран: `{req_val}`\n\n"
        f"📬 *Шаг 2 из 2: Введите {address_label}* для получения крипты:",
        reply_markup=kb, parse_mode="Markdown"
    )
    await state.set_state(WithdrawProcess.waiting_for_address)
    await callback.answer()


@dp.message(WithdrawProcess.waiting_for_address)
async def wd_address(message: types.Message, state: FSMContext):
    address = message.text.strip()
    await state.update_data(wd_address=address)
    data = await state.get_data()

    amount = data.get('wa')
    if not amount:
        await message.answer("⚠️ Сессия устарела. Начните вывод заново.")
        await state.clear()
        return

    method = data.get('wd_method', 'trc20')
    method_label = data.get('wd_method_label', '🌐 USDT TRC20')
    address_label = "Адрес TRC20" if method == "trc20" else "Bybit UID"
    req_val = data.get('wd_req_val')
    operator_id = data.get('wd_operator_id')
    op_name = OPERATORS.get(operator_id, "Оператор") if operator_id else "—"

    summary = (
        f"📋 *Проверьте данные:*\n\n"
        f"💰 Сумма: `{amount} USDT`\n"
        f"📤 Способ: {method_label}\n"
        f"📬 {address_label}: `{address}`\n"
    )
    if req_val:
        summary += f"💳 Реквизит пополнения: `{req_val}`\n"
        summary += f"👷 Оператор: *{op_name}*"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="wd_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="wd_cancel")]
    ])
    await message.answer(summary, reply_markup=kb, parse_mode="Markdown")
    await state.set_state(WithdrawProcess.waiting_for_confirm)


@dp.callback_query(F.data == "wd_confirm")
async def wd_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user = callback.from_user
    name = user.full_name or ""
    username = f"@{user.username}" if user.username else "нет username"
    amount = data.get('wa')
    address_val = data.get('wd_address')
    if not amount or not address_val:
        await callback.answer("⚠️ Сессия устарела. Начните вывод заново.", show_alert=True)
        await state.clear()
        return
    address = address_val
    method = data.get('wd_method', 'trc20')
    method_label = data.get('wd_method_label', '🌐 USDT TRC20')
    address_label = "Адрес TRC20" if method == "trc20" else "Bybit UID"
    req_val = data.get('wd_req_val', '')

    target_operator = data.get('wd_operator_id')

    # Если оператор не выбран — ищем по последней подтверждённой сделке
    if not target_operator:
        conn_t = await asyncpg.connect(**DB_CONFIG)
        target_operator = await conn_t.fetchval("""
            SELECT operator_id FROM deals
            WHERE user_id=$1 AND status='confirmed' AND operator_id != 0
            ORDER BY created_at DESC LIMIT 1
        """, user.id)
        await conn_t.close()

    await update_balance(user.id, -float(amount))

    conn = await asyncpg.connect(**DB_CONFIG)
    deal_id = await conn.fetchval("""
        INSERT INTO deals (user_id, username, full_name, req_val, bank, photo_id, status, amount, method, address, operator_id, created_at)
        VALUES ($1, $2, $3, $4, '', '', 'withdraw', $5, $6, $7, $8, $9)
        RETURNING id
    """, user.id, user.username or "", user.full_name or "",
        req_val, float(amount), method_label, address, target_operator or 0, datetime.now())
    await conn.close()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Крипта отправлена", callback_data=f"wd_sent_d_{deal_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd_reject_d_{deal_id}")],
        [InlineKeyboardButton(text="✉️ Написать", callback_data=f"msg_{user.id}"),
         InlineKeyboardButton(text="📸 Отправить чек", callback_data=f"sendphoto_{user.id}")]
    ])

    op_name = OPERATORS.get(target_operator, "Оператор") if target_operator else "Неизвестно"
    recipients = set(SUPER_ADMINS)
    if target_operator:
        recipients.add(target_operator)

    req_line = f"💳 Реквизит пополнения: `{req_val}`\n" if req_val else ""

    for a in recipients:
        try:
            await bot.send_message(a,
                f"💸 *ЗАЯВКА НА ВЫВОД*\n\n"
                f"👤 {name} ({username})\n"
                f"🆔 ID: `{user.id}`\n"
                f"💰 Сумма: `{amount} USDT`\n"
                f"📤 {method_label}\n"
                f"📬 {address_label}: `{address}`\n"
                f"{req_line}"
                f"👷 Оператор: *{op_name}*",
                reply_markup=kb, parse_mode="Markdown")
        except:
            pass

    await callback.answer("✅ Заявка принята!")
    await state.clear()
    try:
        await callback.message.edit_text(
            f"✅ *Заявка принята!*\n\n"
            f"💰 Сумма: `{amount} USDT`\n"
            f"📤 {method_label}\n"
            f"📬 {address_label}: `{address}`\n"
            f"👷 Оператор: *{op_name}*\n\n"
            f"Ожидайте 5-30 минут.",
            parse_mode="Markdown"
        )
    except:
        await callback.message.answer(
            f"✅ *Заявка принята!*\n\n"
            f"💰 Сумма: `{amount} USDT`\n"
            f"📤 {method_label}\n"
            f"📬 {address_label}: `{address}`\n"
            f"👷 Оператор: *{op_name}*\n\n"
            f"Ожидайте 5-30 минут.",
            parse_mode="Markdown"
        )


# ==================== WITHDRAW CONFIRM ====================

@dp.callback_query(F.data.startswith("wd_sent_d_"))
async def wd_sent_init(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    deal_id = int(callback.data.split("_")[3])
    conn = await asyncpg.connect(**DB_CONFIG)
    deal = await conn.fetchrow("SELECT user_id, status, amount FROM deals WHERE id=$1", deal_id)
    if not deal or deal['status'] != 'withdraw':
        await conn.close()
        return await callback.answer("⚠️ Этот вывод уже обработан.", show_alert=True)

    uid = deal['user_id']
    amount = float(deal['amount'] or 0)
    await conn.execute("UPDATE deals SET status='withdraw_done' WHERE id=$1", deal_id)
    await conn.close()

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass

    try:
        await bot.send_message(
            uid,
            f"✅ *КРИПТА ОТПРАВЛЕНА!*\n\n"
            f"💰 Сумма: `{amount} USDT`\n\n"
            f"Ваш вывод обработан. Средства отправлены на указанный адрес.",
            parse_mode="Markdown",
            reply_markup=buyer_reply_kb()
        )
    except Exception as e:
        await callback.message.answer(f"⚠️ Не удалось уведомить юзера `{uid}`: {e}")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Отправить фото-чек юзеру", callback_data=f"sendphoto_{uid}")]
    ])
    await callback.message.answer(
        f"✅ Вывод *#{deal_id}* подтверждён!\n"
        f"👤 Юзер `{uid}` уведомлён.\n\n"
        f"Хотите отправить скриншот транзакции?",
        reply_markup=kb, parse_mode="Markdown"
    )
    await callback.answer("✅ Готово!")


@dp.callback_query(F.data.startswith("wd_reject_d_"))
async def wd_reject(callback: types.CallbackQuery):
    if callback.from_user.id not in ALL_STAFF:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    deal_id = int(callback.data.split("_")[3])
    conn = await asyncpg.connect(**DB_CONFIG)
    deal = await conn.fetchrow("SELECT user_id, amount, status FROM deals WHERE id=$1", deal_id)
    if not deal or deal['status'] != 'withdraw':
        await conn.close()
        return await callback.answer("⚠️ Этот вывод уже обработан или отклонён.", show_alert=True)
    uid = deal['user_id']
    amount = float(deal['amount'] or 0)
    await update_balance(uid, amount)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass
    await conn.execute("UPDATE deals SET status='withdraw_rejected' WHERE id=$1", deal_id)
    await conn.close()
    try:
        await bot.send_message(uid,
            "❌ *Вывод отклонён.*\n\nСредства возвращены на баланс.",
            parse_mode="Markdown", reply_markup=buyer_reply_kb())
        await callback.message.answer(f"❌ Вывод для {uid} отклонён, баланс возвращён.")
    except:
        await callback.message.answer("❌ Не удалось уведомить юзера.")
    await callback.answer()


# ==================== SUPER ADMIN COMMANDS ====================

@dp.message(Command("set"))
async def cmd_set_rate(message: types.Message, command: CommandObject):
    if message.from_user.id not in SUPER_ADMINS:
        return
    if not command.args:
        return
    rate_val = command.args.strip().replace(",", ".")
    try:
        float(rate_val)
    except:
        return await message.answer("❌ Пример: `/set 10.15`", parse_mode="Markdown")
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("UPDATE settings SET value=$1 WHERE key='rate'", rate_val)
    await conn.close()
    await message.answer(f"✅ Новый курс: {rate_val} TJS")


@dp.message(Command("add"))
async def cmd_add_balance(message: types.Message, command: CommandObject):
    if message.from_user.id not in SUPER_ADMINS or not command.args:
        return
    try:
        uid, amt = command.args.split()
        await update_balance(int(uid), float(amt))
        conn = await asyncpg.connect(**DB_CONFIG)
        await conn.execute("INSERT INTO orders (user_id, amount, created_at) VALUES ($1, $2, $3)", int(uid), float(amt), datetime.now())
        await conn.close()
        await message.answer(f"✅ Начислено {amt} USDT юзеру {uid}")
        try:
            await bot.send_message(int(uid), f"💰 Ваш баланс пополнен на {amt} USDT!")
        except:
            pass
    except:
        await message.answer("Ошибка! Формат: `/add 1234567 100`")


@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id not in SUPER_ADMINS:
        return
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        rows = await conn.fetch("SELECT user_id, username, full_name, balance FROM users ORDER BY balance DESC")
        total = await conn.fetchval("SELECT SUM(balance) FROM users") or 0
        all_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        await conn.close()
    except Exception as e:
        return await message.answer(f"❌ Ошибка БД: {e}")
    if not rows:
        return await message.answer("📭 Нет пользователей.")
    text = "👥 *БАЛАНСЫ ПОЛЬЗОВАТЕЛЕЙ*\n\n"
    for idx, r in enumerate(rows, 1):
        uname = f"@{r['username']}" if r['username'] else "—"
        fname = r['full_name'] or "—"
        text += f"{idx}. `{r['user_id']}` | {uname} | {fname}\n   💰 `{float(r['balance'])} USDT`\n\n"
    text += f"────────────────────\n👤 Всего: `{all_count}`\n💵 Итого: `{float(total):.2f} USDT`"
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i+4000], parse_mode="Markdown")
    else:
        await message.answer(text, parse_mode="Markdown")


@dp.message(Command("clearstats"))
async def cmd_clearstats(message: types.Message):
    if message.from_user.id not in SUPER_ADMINS:
        return
    conn = await asyncpg.connect(**DB_CONFIG)
    total = await conn.fetchval("SELECT COUNT(*) FROM orders")
    turnover = await conn.fetchval("SELECT SUM(amount) FROM orders") or 0
    await conn.close()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, очистить", callback_data="clearstats_confirm"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="clearstats_cancel")]
    ])
    await message.answer(
        f"⚠️ *ОЧИСТКА СТАТИСТИКИ*\n\n• Сделок: `{total}`\n• Оборот: `{float(turnover):.2f} USDT`\n\nБалансы не изменятся. Уверены?",
        reply_markup=kb, parse_mode="Markdown"
    )


@dp.callback_query(F.data == "clearstats_confirm")
async def clearstats_do(callback: types.CallbackQuery):
    if callback.from_user.id not in SUPER_ADMINS:
        return await callback.answer("🚫 Нет прав.", show_alert=True)
    conn = await asyncpg.connect(**DB_CONFIG)
    deleted = await conn.fetchval("SELECT COUNT(*) FROM orders")
    await conn.execute("DELETE FROM orders")
    await conn.execute("DELETE FROM deals")
    await conn.close()
    await callback.message.edit_text(f"✅ Очищено. Удалено записей: `{deleted}`", parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data == "clearstats_cancel")
async def clearstats_cancel(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()


@dp.message(Command("setchek"))
async def cmd_setchek(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPER_ADMINS:
        return
    await state.set_state(AdminStates.set_chek1)
    await message.answer("📸 Отправьте *первое* фото примера чека:", parse_mode="Markdown")


@dp.message(AdminStates.set_chek1, F.photo)
async def save_chek1(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPER_ADMINS:
        return
    file_id = message.photo[-1].file_id
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("UPDATE settings SET value=$1 WHERE key='chek1'", file_id)
    await conn.close()
    await state.set_state(AdminStates.set_chek2)
    await message.answer("✅ Первое фото сохранено!\n\n📸 Отправьте *второе* фото:", parse_mode="Markdown")


@dp.message(AdminStates.set_chek2, F.photo)
async def save_chek2(message: types.Message, state: FSMContext):
    if message.from_user.id not in SUPER_ADMINS:
        return
    file_id = message.photo[-1].file_id
    conn = await asyncpg.connect(**DB_CONFIG)
    await conn.execute("UPDATE settings SET value=$1 WHERE key='chek2'", file_id)
    await conn.close()
    await state.clear()
    await message.answer("✅ Оба фото сохранены!")


# ==================== FORWARD MESSAGES ====================

@dp.message(F.photo)
async def forward_user_photo_to_admin(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid in ALL_STAFF:
        return
    if await state.get_state() is not None:
        return
    name = message.from_user.full_name or ""
    username = f"@{message.from_user.username}" if message.from_user.username else "нет username"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"msg_{uid}")]
    ])
    for admin_id in list(SUPER_ADMINS) + list(OPERATORS.keys()):
        try:
            await bot.send_photo(admin_id, message.photo[-1].file_id,
                                 caption=f"🖼 *ФОТО ОТ ПОКУПАТЕЛЯ*\n👤 {name} ({username})\n🆔 ID: `{uid}`",
                                 parse_mode="Markdown", reply_markup=kb)
        except:
            pass


@dp.message(F.text & ~F.text.startswith("/"))
async def forward_user_message_to_admin(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid in ALL_STAFF:
        return
    if await state.get_state() is not None:
        return
    menu_buttons = {
        "✅Новая сделка", "💰Мой баланс", "💸 Вывод", "👥Наша группа",
        "📊 Статистика", "🔄 Перезапуск", "Техподдержка", "📜 История"
    }
    if message.text in menu_buttons:
        return
    name = message.from_user.full_name or ""
    username = f"@{message.from_user.username}" if message.from_user.username else "нет username"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"msg_{uid}")]
    ])
    for admin_id in list(SUPER_ADMINS) + list(OPERATORS.keys()):
        try:
            await bot.send_message(admin_id,
                                   f"💬 *СООБЩЕНИЕ ОТ ПОКУПАТЕЛЯ*\n👤 {name} ({username})\n🆔 ID: `{uid}`\n\n{message.text}",
                                   parse_mode="Markdown", reply_markup=kb)
        except:
            pass


# ==================== RUN ====================

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
