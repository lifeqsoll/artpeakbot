import logging
import sys
import asyncio
from pathlib import Path

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)

# Чтобы импорты `artpeakbot.*` работали и при запуске:
# - `python -m artpeakbot.main` (обычно уже работает)
# - `python C:\\projects\\artpeakbot\\main.py`
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)  # C:\\projects
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


async def _run_bot():
    # В некоторых Windows-консолях стандартный вывод имеет cp1252, а в `bot_logic.py`
    # есть `print()` с эмодзи. Тогда импорт падает с UnicodeEncodeError.
    # Настраиваем stdout на UTF-8 перед импортом "тяжёлых" модулей.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Импорты делаем внутри `main()`, чтобы вышеописанная настройка успела примениться.
    from artpeakbot.bot_logic import (
        BOT_TOKEN,
        init_db,
        realtime_updater,
        send_notification_reminder,
        show_reactions_handler,
        next_reaction_handler,
        finish_reactions_handler,
        menu_from_reactions_handler,
        send_to_support_handler,
        approve_manual_handler,
        reject_manual_handler,
    )

    from artpeakbot.bot_handlers import (
        start,
        deleted_arts_command,
        appeals_command,
        button_handler,
        handle_message,
    )

    init_db()

    # Увеличиваем таймауты HTTP-клиента Telegram API, чтобы бот не падал
    # при кратковременных проблемах сети/провайдера.
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    application = Application.builder().token(BOT_TOKEN).request(request).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("deleted_arts", deleted_arts_command))
    application.add_handler(CommandHandler("appeals", appeals_command))

    # Кнопки
    application.add_handler(CallbackQueryHandler(button_handler))

    # Сообщения
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Реакции (navigation)
    application.add_handler(CallbackQueryHandler(show_reactions_handler, pattern='^show_reactions$'))
    application.add_handler(CallbackQueryHandler(next_reaction_handler, pattern='^next_reaction$'))
    application.add_handler(CallbackQueryHandler(finish_reactions_handler, pattern='^finish_reactions$'))
    application.add_handler(CallbackQueryHandler(menu_from_reactions_handler, pattern='^menu_from_reactions$'))

    # Ручная модерация
    application.add_handler(CallbackQueryHandler(send_to_support_handler, pattern='^send_to_support_'))
    application.add_handler(CallbackQueryHandler(approve_manual_handler, pattern='^approve_manual_'))
    application.add_handler(CallbackQueryHandler(reject_manual_handler, pattern='^reject_manual_'))

    # Планировщик для обновлений в реальном времени
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(realtime_updater, interval=3600, first=10)
        job_queue.run_repeating(send_notification_reminder, interval=43200, first=60)
        logging.info("Планировщики задач запущены")

    logging.info("Бот запускается...")

    # Явный жизненный цикл. На некоторых связках Python 3.13 + PTB 22.x
    # `run_polling()` может стартовать до инициализации ExtBot.
    retries = 5
    for attempt in range(1, retries + 1):
        try:
            await application.initialize()
            break
        except (TimedOut, NetworkError) as e:
            if attempt == retries:
                raise
            wait_seconds = min(5 * attempt, 20)
            logging.warning(
                "Сетевой таймаут при initialize (%s/%s): %s. Повтор через %s сек.",
                attempt,
                retries,
                e,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)

    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    try:
        # В PTB 22.x у `Updater` нет `idle()`. Держим процесс живым,
        # пока пользователь не остановит его (Ctrl+C).
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def main():
    asyncio.run(_run_bot())


if __name__ == '__main__':
    main()

