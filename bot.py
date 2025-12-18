\
import os
import json
import random
import asyncio
from dataclasses import dataclass
from typing import List, Dict, Any

import aiosqlite
from dotenv import load_dotenv
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Создай .env или добавь env BOT_TOKEN=...")

BASE_URL = os.getenv("BASE_URL", "").strip()
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg-webhook").strip() or "/tg-webhook"
if not BASE_URL:
    raise RuntimeError("BASE_URL не задан. Добавь env BASE_URL=https://<домен-хостинга>")

WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", "8080"))

DB_PATH = os.path.join("data", "bot.db")
QUESTIONS_PATH = os.path.join("content", "questions.json")
THEORY_PATH = os.path.join("content", "theory.json")


@dataclass
class Question:
    text: str
    options: List[str]
    correct_index: int
    topic: str = "general"
    level: int = 1


def load_questions() -> List[Question]:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out: List[Question] = []
    for item in raw:
        out.append(
            Question(
                text=item["text"],
                options=item["options"],
                correct_index=int(item["correct_index"]),
                topic=item.get("topic", "general"),
                level=int(item.get("level", 1)),
            )
        )
    return out


def load_theory() -> Dict[str, str]:
    with open(THEORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


QUESTIONS = load_questions()
THEORY = load_theory()


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS results (
            user_id INTEGER PRIMARY KEY,
            best_score INTEGER NOT NULL DEFAULT 0,
            last_score INTEGER NOT NULL DEFAULT 0,
            total_quizzes INTEGER NOT NULL DEFAULT 0
        )
        """
        )
        await db.commit()


async def get_result(user_id: int) -> Dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT best_score, last_score, total_quizzes FROM results WHERE user_id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO results(user_id, best_score, last_score, total_quizzes) VALUES(?,?,?,?)",
                (user_id, 0, 0, 0),
            )
            await db.commit()
            return {"best_score": 0, "last_score": 0, "total_quizzes": 0}
        return {"best_score": row[0], "last_score": row[1], "total_quizzes": row[2]}


async def save_result(user_id: int, last_score: int, total: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT best_score, total_quizzes FROM results WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        best = row[0] if row else 0
        quizzes = row[1] if row else 0

        best = max(best, last_score)
        quizzes += 1

        await db.execute(
            """
        INSERT INTO results(user_id, best_score, last_score, total_quizzes)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            best_score=excluded.best_score,
            last_score=excluded.last_score,
            total_quizzes=excluded.total_quizzes
        """,
            (user_id, best, last_score, quizzes),
        )
        await db.commit()


def main_menu_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📚 Теория")
    kb.button(text="🧠 Квиз")
    kb.button(text="🏆 Результаты")
    kb.button(text="ℹ️ Помощь")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)


def theory_kb():
    b = InlineKeyboardBuilder()
    b.button(text="История", callback_data="th:hist")
    b.button(text="Цифровые системы", callback_data="th:systems")
    b.button(text="Нейросети", callback_data="th:ai")
    b.button(text="Плюсы/перспективы", callback_data="th:future")
    b.adjust(2, 2)
    return b.as_markup()


def quiz_mode_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🎲 Случайный (10 вопросов)", callback_data="mode:rand10")
    b.button(text="📘 Лёгкий (10)", callback_data="mode:easy10")
    b.button(text="📗 Средний (10)", callback_data="mode:mid10")
    b.button(text="📕 Сложный (10)", callback_data="mode:hard10")
    b.adjust(1)
    return b.as_markup()


def question_kb(options: List[str]):
    b = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        b.button(text=opt, callback_data=f"ans:{i}")
    b.adjust(1)
    return b.as_markup()


quiz_state: Dict[int, Dict[str, Any]] = {}


def pick_questions(mode: str) -> List[int]:
    indices = list(range(len(QUESTIONS)))
    if mode == "rand10":
        random.shuffle(indices)
        return indices[: min(10, len(indices))]
    if mode == "easy10":
        pool = [i for i, q in enumerate(QUESTIONS) if q.level <= 1]
    elif mode == "mid10":
        pool = [i for i, q in enumerate(QUESTIONS) if q.level == 2]
    elif mode == "hard10":
        pool = [i for i, q in enumerate(QUESTIONS) if q.level >= 3]
    else:
        pool = indices
    if not pool:
        pool = indices
    random.shuffle(pool)
    return pool[: min(10, len(pool))]


async def send_question(bot: Bot, chat_id: int, user_id: int):
    st = quiz_state.get(user_id)
    if not st:
        return

    order = st["order"]
    idx = st["idx"]
    total = len(order)

    if idx >= total:
        score = st["score"]
        await save_result(user_id, score, total)
        await bot.send_message(
            chat_id,
            f"✅ Квиз завершён!\\nТвой результат: {score}/{total}",
            reply_markup=main_menu_kb(),
        )
        quiz_state.pop(user_id, None)
        return

    q = QUESTIONS[order[idx]]

    perm = list(range(len(q.options)))
    random.shuffle(perm)
    shuffled_options = [q.options[i] for i in perm]
    correct_shuffled_index = perm.index(q.correct_index)

    st["correct_idx"] = correct_shuffled_index
    st["shown_options"] = shuffled_options

    await bot.send_message(
        chat_id,
        f"Вопрос {idx+1}/{total}:\\n{q.text}",
        reply_markup=question_kb(shuffled_options),
    )


def register_handlers(dp: Dispatcher):
    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            "Привет! Это бот-справочник + квиз по теме медицинских IT.\\nВыбери режим 👇",
            reply_markup=main_menu_kb(),
        )

    @dp.message(F.text == "ℹ️ Помощь")
    async def help_(message: Message):
        await message.answer(
            "📚 Теория — короткие карточки по темам.\\n"
            "🧠 Квиз — тест из 10 вопросов (есть режимы сложности).\\n"
            "🏆 Результаты — лучший и последний результат.\\n\\n"
            "Команда: /start"
        )

    @dp.message(F.text == "📚 Теория")
    async def theory(message: Message):
        await message.answer("Выбери раздел:", reply_markup=theory_kb())

    @dp.callback_query(F.data.startswith("th:"))
    async def theory_section(call: CallbackQuery):
        key = call.data.split(":", 1)[1]
        text = THEORY.get(key, "Раздел не найден")
        await call.message.answer(text)
        await call.answer()

    @dp.message(F.text == "🧠 Квиз")
    async def quiz(message: Message):
        await message.answer("Выбери режим квиза:", reply_markup=quiz_mode_kb())

    @dp.callback_query(F.data.startswith("mode:"))
    async def quiz_mode(call: CallbackQuery):
        mode = call.data.split(":", 1)[1]
        order = pick_questions(mode)
        quiz_state[call.from_user.id] = {"order": order, "idx": 0, "score": 0}
        await call.message.answer("Начинаем!")
        await call.answer()
        await send_question(call.bot, call.message.chat.id, call.from_user.id)

    @dp.callback_query(F.data.startswith("ans:"))
    async def answer(call: CallbackQuery):
        user_id = call.from_user.id
        st = quiz_state.get(user_id)
        if not st:
            await call.answer("Квиз не запущен. Нажми «🧠 Квиз».", show_alert=True)
            return

        chosen = int(call.data.split(":", 1)[1])
        correct_idx = st.get("correct_idx")
        shown_options = st.get("shown_options")

        if correct_idx is None or shown_options is None:
            await call.answer("Ошибка состояния. Запусти квиз заново.", show_alert=True)
            quiz_state.pop(user_id, None)
            return

        if chosen == correct_idx:
            st["score"] += 1
            await call.message.answer("✅ Верно!")
        else:
            await call.message.answer(f"❌ Неверно. Правильный ответ: {shown_options[correct_idx]}")

        st["idx"] += 1
        await call.answer()
        await send_question(call.bot, call.message.chat.id, user_id)

    @dp.message(F.text == "🏆 Результаты")
    async def results(message: Message):
        r = await get_result(message.from_user.id)
        await message.answer(
            f"🏆 Лучший результат: {r['best_score']}/10\\n"
            f"🕘 Последний результат: {r['last_score']}/10\\n"
            f"📊 Пройдено квизов: {r['total_quizzes']}",
            reply_markup=main_menu_kb(),
        )


async def on_startup(bot: Bot):
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)


async def on_shutdown(bot: Bot):
    await bot.delete_webhook(drop_pending_updates=False)


async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    register_handlers(dp)

    dp.startup.register(lambda _: on_startup(bot))
    dp.shutdown.register(lambda _: on_shutdown(bot))

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
