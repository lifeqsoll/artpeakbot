import logging
import sqlite3
import re
import torch
import clip
from PIL import Image
from io import BytesIO
import telegram
import random

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.error import TimedOut, NetworkError, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import asyncio
from datetime import datetime, timedelta
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ========== НАСТРОЙКИ СИСТЕМЫ ==========
BOT_TOKEN = "token"
MAX_ARTS_PER_USER = 10
MAX_HASHTAGS_PER_ART = 5
SUPPORT_USERNAME = "supportUSERNAME"
SUPPORT_USER_IDS = ["support_id's"]
active_art_messages = {}

COMPLAINT_REASONS = [
    "🚫 Нарушение правил",
    "🔞 Неприемлемый контент", 
    "📢 Спам или реклама",
    "🎨 Кража авторских прав",
    "💬 Оскорбительное поведение",
    "❓ Другая причина"
]
async def safe_api_call(coro, fallback_message=None, max_retries=3):
    """
    Безопасный вызов API Telegram с обработкой ошибок подключения.
    Повторяет попытку при таймауте/ошибке сети.
    """
    for attempt in range(max_retries):
        try:
            return await coro
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Экспоненциальная задержка: 1, 2, 4 секунды
                logging.warning(f"Ошибка подключения (попытка {attempt + 1}/{max_retries}): {e}. Повторяем через {wait_time}с...")
                await asyncio.sleep(wait_time)
            else:
                logging.error(f"Не удалось выполнить API вызов после {max_retries} попыток: {e}")
                if fallback_message:
                    logging.error(f"Fallback: {fallback_message}")
                raise
        except Exception as e:
            logging.error(f"Ошибка при выполнении API вызова: {e}")
            raise

def get_persistent_menu():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔙 В меню")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def init_db():
    conn = sqlite3.connect('database.db', check_same_thread=False)
    cur = conn.cursor()

    try:
        cur.execute("PRAGMA table_info(reactions)")
        columns = [row[1] for row in cur.fetchall()]
        if 'timestamp' not in columns:
            logging.info("Обнаружена старая схема БД. Добавляем 'timestamp' в таблицу 'reactions'...")
            cur.execute("ALTER TABLE reactions ADD COLUMN timestamp DATETIME DEFAULT CURRENT_TIMESTAMP")
            conn.commit()
            logging.info("Таблица 'reactions' успешно обновлена.")
    except sqlite3.OperationalError:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            nickname TEXT,
            bio TEXT,
            profile_avatar_file_id TEXT,
            is_profile_public BOOLEAN DEFAULT TRUE
        )
    ''')
    
    try:
        cur.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cur.fetchall()]
        if 'nickname' not in columns:
            logging.info("Добавляем новые колонки в таблицу 'users'...")
            cur.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN bio TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN profile_avatar_file_id TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN is_profile_public BOOLEAN DEFAULT TRUE")
            conn.commit()
    except sqlite3.OperationalError:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS privacy_settings (
            user_id INTEGER PRIMARY KEY,
            hide_username BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS profile_followers (
            follower_id INTEGER,
            following_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (follower_id, following_id),
            FOREIGN KEY (follower_id) REFERENCES users (user_id),
            FOREIGN KEY (following_id) REFERENCES users (user_id)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS profile_violations (
            violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            violation_type TEXT,
            reason TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS arts (
            art_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            file_id TEXT,
            caption TEXT,
            likes INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users (user_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS hashtags (
            hashtag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            art_id INTEGER,
            hashtag TEXT,
            FOREIGN KEY (art_id) REFERENCES arts (art_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS all_hashtags (
            hashtag_text TEXT PRIMARY KEY,
            usage_count INTEGER DEFAULT 1
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS reactions (
            reaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            art_id INTEGER,
            type TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (art_id) REFERENCES arts (art_id),
            UNIQUE(user_id, art_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            art_id INTEGER,
            text TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (art_id) REFERENCES arts (art_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
            art_id INTEGER,
            reporter_id INTEGER,
            reason TEXT,
            comment TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (art_id) REFERENCES arts (art_id),
            FOREIGN KEY (reporter_id) REFERENCES users (user_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS viewed_reactions (
            view_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reaction_type TEXT,
            reaction_id INTEGER,
            art_id INTEGER,
            viewed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, reaction_type, reaction_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS pending_arts (
            pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_id TEXT,
            caption TEXT,
            hashtags TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS notification_messages (
            user_id INTEGER PRIMARY KEY,
            message_id INTEGER,
            chat_id INTEGER,
            last_count INTEGER DEFAULT 0,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS active_messages (
            message_id INTEGER,
            chat_id INTEGER,
            art_id INTEGER,
            user_id INTEGER,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (message_id, chat_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS deleted_arts (
            deleted_id INTEGER PRIMARY KEY AUTOINCREMENT,
            art_id INTEGER UNIQUE,
            owner_id INTEGER,
            file_id TEXT,
            caption TEXT,
            likes INTEGER DEFAULT 0,
            dislikes INTEGER DEFAULT 0,
            hashtags TEXT,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reason TEXT,
            restored_at DATETIME,
            FOREIGN KEY (owner_id) REFERENCES users (user_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_blocks (
            block_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            blocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reason TEXT,
            moderator_id INTEGER,
            appeal_status TEXT DEFAULT 'pending',
            appeal_reason TEXT,
            appeal_submitted_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (moderator_id) REFERENCES users (user_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS appeals (
            appeal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reason TEXT,
            submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            moderator_decision TEXT,
            decided_by INTEGER,
            decided_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (decided_by) REFERENCES users (user_id)
        )
    ''')

    conn.commit()
    conn.close()

# ========== СИСТЕМА ОБНОВЛЕНИЯ В РЕАЛЬНОМ ВРЕМЕНИ ==========

def add_active_message(message_id, chat_id, art_id, user_id):
    """Добавляет сообщение в список активных для обновления"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        INSERT OR REPLACE INTO active_messages (message_id, chat_id, art_id, user_id, last_updated)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (message_id, chat_id, art_id, user_id))
    
    conn.commit()
    conn.close()

def remove_active_message(message_id, chat_id):
    """Удаляет сообщение из списка активных"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('DELETE FROM active_messages WHERE message_id = ? AND chat_id = ?', 
                (message_id, chat_id))
    
    conn.commit()
    conn.close()

def get_active_messages_for_art(art_id):
    """Получает все активные сообщения для конкретного арта"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('SELECT message_id, chat_id, user_id FROM active_messages WHERE art_id = ?', 
                (art_id,))
    messages = cur.fetchall()
    conn.close()
    return messages

def cleanup_old_active_messages(hours=24):
    """Очищает старые записи об активных сообщениях"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cutoff_time = datetime.now() - timedelta(hours=hours)
    cur.execute('DELETE FROM active_messages WHERE last_updated < ?', 
                (cutoff_time,))
    
    deleted_count = cur.rowcount
    conn.commit()
    conn.close()
    
    if deleted_count > 0:
        logging.info(f"Очищено {deleted_count} устаревших активных сообщений")

async def update_art_message_realtime(context: ContextTypes.DEFAULT_TYPE, art_id: int):
    """Обновляет все активные сообщения с указанным артом"""
    try:
        art = get_art_by_id(art_id)
        if not art:
            return
        
        art_id, file_id, caption, likes, dislikes = art
        active_messages = get_active_messages_for_art(art_id)
        
        if not active_messages:
            return
        
        hashtags = get_art_hashtags(art_id)
        hashtags_text = " ".join(hashtags) if hashtags else ""
        
        text = f"Лайков: {likes} | Дизлайков: {dislikes}"
        if caption:
            text = f"{caption}\n\n{text}"
        if hashtags_text:
            text = f"{text}\n\n{hashtags_text}"
        
        for message_id, chat_id, user_id in active_messages:
            try:
                conn = sqlite3.connect('database.db')
                cur = conn.cursor()
                cur.execute('SELECT type FROM reactions WHERE user_id = ? AND art_id = ?', 
                           (user_id, art_id))
                existing_reaction = cur.fetchone()
                conn.close()
                
                if existing_reaction:
                    keyboard = [
                        [InlineKeyboardButton("💬 Комментарий", callback_data=f'comment_{art_id}')],
                        [InlineKeyboardButton("🚫 Пожаловаться", callback_data=f'complaint_{art_id}')],
                        [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
                    ]
                    
                    reaction_type = existing_reaction[0]
                    if reaction_type == 'like':
                        keyboard[0].insert(0, InlineKeyboardButton("❤️ Вы лайкнули", callback_data='already_reacted'))
                    else:
                        keyboard[0].insert(0, InlineKeyboardButton("👎 Вы дизлайкнули", callback_data='already_reacted'))
                else:
                    keyboard = [
                        [
                            InlineKeyboardButton("❤️ Лайк", callback_data=f'like_{art_id}'),
                            InlineKeyboardButton("👎 Дизлайк", callback_data=f'dislike_{art_id}')
                        ],
                        [InlineKeyboardButton("💬 Комментарий", callback_data=f'comment_{art_id}')],
                        [InlineKeyboardButton("🚫 Пожаловаться", callback_data=f'complaint_{art_id}')],
                        [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
                    ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    reply_markup=reply_markup
                )
                
                conn = sqlite3.connect('database.db')
                cur = conn.cursor()
                cur.execute('UPDATE active_messages SET last_updated = CURRENT_TIMESTAMP WHERE message_id = ? AND chat_id = ?',
                           (message_id, chat_id))
                conn.commit()
                conn.close()
                
            except telegram.error.BadRequest as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    logging.warning(f"Удаляем недействительное сообщение {message_id}: {e}")
                    remove_active_message(message_id, chat_id)
            except Exception as e:
                logging.error(f"Ошибка при обновлении сообщения {message_id}: {e}")
                remove_active_message(message_id, chat_id)
                
    except Exception as e:
        logging.error(f"Ошибка в update_art_message_realtime: {e}")

async def realtime_updater(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для обслуживания системы реального времени"""
    try:
        cleanup_old_active_messages(hours=24)
        cleanup_old_deleted_arts()
        
    except Exception as e:
        logging.error(f"Ошибка в realtime_updater: {e}")

def cleanup_old_deleted_arts():
    """Окончательно удаляет арты, которые были удалены более 1 дня назад"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        DELETE FROM deleted_arts 
        WHERE restored_at IS NULL 
        AND datetime(deleted_at, '+1 day') <= datetime('now')
    ''')
    
    deleted_count = cur.rowcount
    conn.commit()
    conn.close()
    
    if deleted_count > 0:
        logging.info(f"Окончательно удалено {deleted_count} старых удалённых артов")

# ========== СИСТЕМА УВЕДОМЛЕНИЙ О РЕАКЦИЯХ С ОБНОВЛЕНИЕМ В РЕАЛЬНОМ ВРЕМЕНИ ==========

def get_active_notification_messages(owner_id):
    """Получает все активные уведомления пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT message_id, chat_id, last_count FROM notification_messages WHERE user_id = ?', (owner_id,))
    result = cur.fetchall()
    conn.close()
    return result

async def create_or_update_reaction_notification(context: ContextTypes.DEFAULT_TYPE, owner_id: int):
    """Создает новое уведомление или обновляет существующее в реальном времени"""
    try:
        unviewed_count = get_unviewed_reactions_count(owner_id)
        
        if unviewed_count == 0:
            active_notifications = get_active_notification_messages(owner_id)
            for message_id, chat_id, _ in active_notifications:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except:
                    pass
            delete_all_notification_messages(owner_id)
            return
        
        message_text = f"🎉 Твой арт понравился {unviewed_count} человеку!" if unviewed_count == 1 else f"🎉 Твой арт понравился {unviewed_count} людям!"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Показать", callback_data='show_reactions')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        active_notifications = get_active_notification_messages(owner_id)
        
        if active_notifications:
            for message_id, chat_id, last_count in active_notifications:
                if unviewed_count != last_count:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=message_text,
                            reply_markup=reply_markup
                        )
                        save_notification_message(owner_id, message_id, chat_id, unviewed_count)
                    except Exception as e:
                        logging.error(f"Ошибка при обновлении уведомления: {e}")
                        delete_notification_message_by_id(owner_id, message_id)
                        await create_new_notification(context, owner_id, message_text, reply_markup)
        else:
            await create_new_notification(context, owner_id, message_text, reply_markup)
            
    except Exception as e:
        logging.error(f"Ошибка в create_or_update_reaction_notification: {e}")

def delete_all_notification_messages(user_id):
    """Удаляет все уведомления пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM notification_messages WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_notification_message(user_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT message_id, chat_id, last_count FROM notification_messages WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    conn.close()
    return result

def save_notification_message(user_id, message_id, chat_id, count):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO notification_messages (user_id, message_id, chat_id, last_count, last_update)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (user_id, message_id, chat_id, count))
    conn.commit()
    conn.close()

def delete_notification_message(user_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM notification_messages WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def delete_notification_message_by_id(user_id, message_id):
    """Удаляет конкретное уведомление по ID сообщения"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM notification_messages WHERE user_id = ? AND message_id = ?', (user_id, message_id))
    conn.commit()
    conn.close()

async def create_new_notification(context, owner_id, message_text, reply_markup):
    """Создает новое уведомление"""
    try:
        message = await context.bot.send_message(
            chat_id=owner_id,
            text=message_text,
            reply_markup=reply_markup
        )
        save_notification_message(owner_id, message.message_id, owner_id, get_unviewed_reactions_count(owner_id))
    except Exception as e:
        logging.error(f"Ошибка при создании уведомления: {e}")

def add_pending_art(user_id, file_id, caption, hashtags):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    hashtags_text = ",".join(hashtags) if hashtags else ""
    
    cur.execute(
        'INSERT INTO pending_arts (user_id, file_id, caption, hashtags) VALUES (?, ?, ?, ?)',
        (user_id, file_id, caption, hashtags_text)
    )
    pending_id = cur.lastrowid
    
    conn.commit()
    conn.close()
    return pending_id

def get_pending_art(pending_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('SELECT * FROM pending_arts WHERE pending_id = ?', (pending_id,))
    art = cur.fetchone()
    
    conn.close()
    return art

def delete_pending_art(pending_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('DELETE FROM pending_arts WHERE pending_id = ?', (pending_id,))
    
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def add_user(user_id, username):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute(
        'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
        (user_id, username)
    )
    cur.execute(
        'INSERT OR IGNORE INTO privacy_settings (user_id) VALUES (?)',
        (user_id,)
    )
    conn.commit()
    conn.close()

def get_privacy_settings(user_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT hide_username FROM privacy_settings WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    conn.close()
    
    if result:
        return {'hide_username': bool(result[0])}
    else:
        set_privacy_settings(user_id, hide_username=False)
        return {'hide_username': False}

def set_privacy_settings(user_id, hide_username=None):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    if hide_username is not None:
        cur.execute(
            'INSERT OR REPLACE INTO privacy_settings (user_id, hide_username) VALUES (?, ?)',
            (user_id, hide_username)
        )
    
    conn.commit()
    conn.close()

def get_display_name(user_id, for_moderator=False, profile_is_public=False):
    if for_moderator:
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        cur.execute('SELECT username FROM users WHERE user_id = ?', (user_id,))
        result = cur.fetchone()
        conn.close()
        
        if result and result[0]:
            return f"@{result[0]}"
        else:
            return "Пользователь"
    else:
        if profile_is_public:
            conn = sqlite3.connect('database.db')
            cur = conn.cursor()
            cur.execute('SELECT username FROM users WHERE user_id = ?', (user_id,))
            result = cur.fetchone()
            conn.close()
            
            if result and result[0]:
                return f"@{result[0]}"
            else:
                return "Пользователь"
        
        privacy_settings = get_privacy_settings(user_id)
        
        if privacy_settings['hide_username']:
            return "Аноним"
        else:
            conn = sqlite3.connect('database.db')
            cur = conn.cursor()
            cur.execute('SELECT username FROM users WHERE user_id = ?', (user_id,))
            result = cur.fetchone()
            conn.close()
            
            if result and result[0]:
                return f"@{result[0]}"
            else:
                return "Пользователь"

def get_user_art_count(user_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM arts WHERE owner_id = ?', (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

def add_hashtag_to_global(hashtag, cur):
    hashtag_lower = hashtag.lower()
    
    cur.execute(
        'INSERT OR IGNORE INTO all_hashtags (hashtag_text) VALUES (?)',
        (hashtag_lower,)
    )
    
    cur.execute(
        'UPDATE all_hashtags SET usage_count = usage_count + 1 WHERE hashtag_text = ?',
        (hashtag_lower,)
    )

def get_popular_hashtags(limit=20):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute(
        'SELECT hashtag_text, usage_count FROM all_hashtags ORDER BY usage_count DESC LIMIT ?',
        (limit,)
    )
    hashtags = cur.fetchall()
    conn.close()
    return hashtags

def search_hashtags(query, limit=10):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute(
        'SELECT hashtag_text, usage_count FROM all_hashtags WHERE hashtag_text LIKE ? ORDER BY usage_count DESC LIMIT ?',
        (f'%{query.lower()}%', limit)
    )
    hashtags = cur.fetchall()
    conn.close()
    return hashtags

def add_art(user_id, file_id, caption="", hashtags=None):
    if hashtags is None:
        hashtags = []
    
    art_count = get_user_art_count(user_id)
    if art_count >= MAX_ARTS_PER_USER:
        return None, f"❌ Лимит артов достигнут! Максимум {MAX_ARTS_PER_USER} артов на пользователя."
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    try:
        cur.execute(
            'INSERT INTO arts (owner_id, file_id, caption, timestamp) VALUES (?, ?, ?, CURRENT_TIMESTAMP)',
            (user_id, file_id, caption)
        )
        art_id = cur.lastrowid
        
        for hashtag in hashtags[:MAX_HASHTAGS_PER_ART]:
            cur.execute(
                'INSERT INTO hashtags (art_id, hashtag) VALUES (?, ?)',
                (art_id, hashtag)
            )
            add_hashtag_to_global(hashtag, cur)
        
        conn.commit()
        return art_id, "✅ Арт успешно добавлен!"
    
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка при добавлении арта: {str(e)}")
        return None, f"❌ Ошибка при добавлении арта: {str(e)}"
    
    finally:
        conn.close()

def get_art_hashtags(art_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT hashtag FROM hashtags WHERE art_id = ?', (art_id,))
    hashtags = [row[0] for row in cur.fetchall()]
    conn.close()
    return hashtags

def delete_art(user_id, art_number):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT art_id FROM arts 
        WHERE owner_id = ? 
        ORDER BY timestamp DESC
    ''', (user_id,))
    
    arts = cur.fetchall()
    
    if art_number < 1 or art_number > len(arts):
        conn.close()
        return False, "❌ Неверный номер арта!"
    
    art_id_to_delete = arts[art_number - 1][0]
    hashtags_to_delete = get_art_hashtags(art_id_to_delete)
    
    cur.execute('DELETE FROM reactions WHERE art_id = ?', (art_id_to_delete,))
    cur.execute('DELETE FROM comments WHERE art_id = ?', (art_id_to_delete,))
    cur.execute('DELETE FROM hashtags WHERE art_id = ?', (art_id_to_delete,))
    cur.execute('DELETE FROM complaints WHERE art_id = ?', (art_id_to_delete,))
    cur.execute('DELETE FROM viewed_reactions WHERE art_id = ?', (art_id_to_delete,))
    cur.execute('DELETE FROM active_messages WHERE art_id = ?', (art_id_to_delete,))
    
    cur.execute('DELETE FROM arts WHERE art_id = ? AND owner_id = ?', (art_id_to_delete, user_id))
    
    if cur.rowcount == 0:
        conn.close()
        return False, "❌ Ошибка при удалении арта!"
    
    for hashtag in hashtags_to_delete:
        cur.execute(
            'UPDATE all_hashtags SET usage_count = usage_count - 1 WHERE hashtag_text = ?',
            (hashtag.lower(),)
        )
        cur.execute('DELETE FROM all_hashtags WHERE usage_count <= 0')
    
    conn.commit()
    conn.close()
    return True, f"✅ Арт #{art_number} успешно удален!"

def delete_art_by_id(art_id, reason="User deletion"):
    """Мягкое удаление арта - помещает в deleted_arts вместо полного удаления"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('SELECT owner_id, file_id, caption, likes, dislikes FROM arts WHERE art_id = ?', (art_id,))
    art_info = cur.fetchone()
    
    if not art_info:
        conn.close()
        return False, "Арт не найден!"
    
    owner_id, file_id, caption, likes, dislikes = art_info
    hashtags_to_delete = get_art_hashtags(art_id)
    hashtags_text = ",".join(hashtags_to_delete) if hashtags_to_delete else ""
    
    try:
        cur.execute('''
            INSERT INTO deleted_arts (art_id, owner_id, file_id, caption, likes, dislikes, hashtags, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (art_id, owner_id, file_id, caption, likes, dislikes, hashtags_text, reason))
    except sqlite3.IntegrityError:
        cur.execute('''
            UPDATE deleted_arts SET deleted_at = CURRENT_TIMESTAMP, reason = ?
            WHERE art_id = ?
        ''', (reason, art_id))
    
    cur.execute('DELETE FROM reactions WHERE art_id = ?', (art_id,))
    cur.execute('DELETE FROM comments WHERE art_id = ?', (art_id,))
    cur.execute('DELETE FROM hashtags WHERE art_id = ?', (art_id,))
    cur.execute('DELETE FROM complaints WHERE art_id = ?', (art_id,))
    cur.execute('DELETE FROM viewed_reactions WHERE art_id = ?', (art_id,))
    cur.execute('DELETE FROM active_messages WHERE art_id = ?', (art_id,))
    cur.execute('DELETE FROM arts WHERE art_id = ?', (art_id,))
    
    if cur.rowcount == 0:
        conn.close()
        return False, "Ошибка при удалении арта!"
    
    for hashtag in hashtags_to_delete:
        cur.execute(
            'UPDATE all_hashtags SET usage_count = usage_count - 1 WHERE hashtag_text = ?',
            (hashtag.lower(),)
        )
        cur.execute('DELETE FROM all_hashtags WHERE usage_count <= 0')
    
    conn.commit()
    conn.close()
    return True, "Арт успешно удален!"

def get_user_block_status(user_id):
    """Получает информацию о блокировке пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT block_id, blocked_at, reason, appeal_status FROM user_blocks WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    conn.close()
    return result

def is_user_blocked(user_id):
    """Проверяет заблокирован ли пользователь"""
    return get_user_block_status(user_id) is not None

def block_user(user_id, reason, moderator_id):
    """Блокирует пользователя и скрывает все его арты"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    try:
        cur.execute('SELECT user_id FROM user_blocks WHERE user_id = ?', (user_id,))
        existing_block = cur.fetchone()
        
        if existing_block:
            cur.execute('''
                UPDATE user_blocks 
                SET reason = ?, moderator_id = ?, blocked_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (reason, moderator_id, user_id))
        else:
            cur.execute('''
                INSERT INTO user_blocks (user_id, reason, moderator_id)
                VALUES (?, ?, ?)
            ''', (user_id, reason, moderator_id))
        cur.execute('SELECT art_id FROM arts WHERE owner_id = ?', (user_id,))
        arts = cur.fetchall()
        
        for art in arts:
            art_id = art[0]
            cur.execute('SELECT file_id, caption, likes, dislikes FROM arts WHERE art_id = ?', (art_id,))
            art_info = cur.fetchone()
            if art_info:
                file_id, caption, likes, dislikes = art_info
                hashtags = get_art_hashtags(art_id)
                hashtags_text = ",".join(hashtags) if hashtags else ""
                
                cur.execute('''
                    INSERT OR IGNORE INTO deleted_arts (art_id, owner_id, file_id, caption, likes, dislikes, hashtags, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (art_id, user_id, file_id, caption, likes, dislikes, hashtags_text, "User blocked"))
        
        cur.execute('DELETE FROM reactions WHERE art_id IN (SELECT art_id FROM arts WHERE owner_id = ?)', (user_id,))
        cur.execute('DELETE FROM comments WHERE art_id IN (SELECT art_id FROM arts WHERE owner_id = ?)', (user_id,))
        cur.execute('DELETE FROM hashtags WHERE art_id IN (SELECT art_id FROM arts WHERE owner_id = ?)', (user_id,))
        cur.execute('DELETE FROM complaints WHERE art_id IN (SELECT art_id FROM arts WHERE owner_id = ?)', (user_id,))
        cur.execute('DELETE FROM viewed_reactions WHERE art_id IN (SELECT art_id FROM arts WHERE owner_id = ?)', (user_id,))
        cur.execute('DELETE FROM active_messages WHERE art_id IN (SELECT art_id FROM arts WHERE owner_id = ?)', (user_id,))
        cur.execute('DELETE FROM arts WHERE owner_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        return True, "Пользователь заблокирован!"
    except Exception as e:
        logging.error(f"Ошибка при блокировке пользователя: {e}")
        conn.close()
        return False, f"Ошибка: {e}"

def unblock_user(user_id):
    """Разблокирует пользователя и восстанавливает все его арты"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    try:
        cur.execute('''
            SELECT art_id, file_id, caption, likes, dislikes, hashtags 
            FROM deleted_arts 
            WHERE owner_id = ? AND reason = 'User blocked'
        ''', (user_id,))
        deleted_arts = cur.fetchall()
        
        for art_id, file_id, caption, likes, dislikes, hashtags_text in deleted_arts:
            cur.execute('''
                INSERT OR IGNORE INTO arts (art_id, owner_id, file_id, caption, likes, dislikes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (art_id, user_id, file_id, caption, likes, dislikes))
            if hashtags_text:
                for hashtag in hashtags_text.split(","):
                    cur.execute('INSERT INTO hashtags (art_id, hashtag) VALUES (?, ?)',
                               (art_id, hashtag))
                    cur.execute('INSERT OR IGNORE INTO all_hashtags (hashtag_text) VALUES (?)',
                               (hashtag.lower(),))
                    cur.execute(
                        'UPDATE all_hashtags SET usage_count = usage_count + 1 WHERE hashtag_text = ?',
                        (hashtag.lower(),)
                    )
            cur.execute('UPDATE deleted_arts SET restored_at = CURRENT_TIMESTAMP WHERE art_id = ?', (art_id,))
        cur.execute('DELETE FROM user_blocks WHERE user_id = ?', (user_id,))
        
        conn.commit()
        conn.close()
        return True, "Пользователь разблокирован и все арты восстановлены!"
    except Exception as e:
        logging.error(f"Ошибка при разблокировке пользователя: {e}")
        conn.close()
        return False, f"Ошибка: {e}"

def submit_appeal(user_id, reason):
    """Отправляет апелляцию от заблокированного пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    try:
        cur.execute('''
            INSERT INTO appeals (user_id, reason, status)
            VALUES (?, ?, 'pending')
        ''', (user_id, reason))
        
        conn.commit()
        conn.close()
        return True, "Ваша апелляция отправлена модераторам!"
    except Exception as e:
        logging.error(f"Ошибка при отправке апелляции: {e}")
        conn.close()
        return False, f"Ошибка: {e}"

def get_pending_appeals():
    """Получает список ожидающих апеляций"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT appeal_id, user_id, reason, submitted_at FROM appeals WHERE status = 'pending'
        ORDER BY submitted_at ASC
    ''')
    appeals = cur.fetchall()
    conn.close()
    return appeals

def get_deleted_arts(limit=10):
    """Получает удалённые арты последних N дней"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT deleted_id, art_id, owner_id, file_id, caption, deleted_at, reason
        FROM deleted_arts
        WHERE restored_at IS NULL AND datetime(deleted_at, '+1 day') > datetime('now')
        ORDER BY deleted_at DESC
        LIMIT ?
    ''', (limit,))
    deleted_arts = cur.fetchall()
    conn.close()
    return deleted_arts

def get_deleted_arts_by_user(username: str):
    """Получает удалённые арты конкретного пользователя по нику"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users WHERE nickname = ?', (username,))
    user_result = cur.fetchone()
    
    if not user_result:
        conn.close()
        return []
    
    user_id = user_result[0]
    cur.execute('''
        SELECT deleted_id, art_id, owner_id, file_id, caption, deleted_at, reason
        FROM deleted_arts
        WHERE owner_id = ? AND restored_at IS NULL AND datetime(deleted_at, '+1 day') > datetime('now')
        ORDER BY deleted_at DESC
    ''', (user_id,))
    
    deleted_arts = cur.fetchall()
    conn.close()
    return deleted_arts

def restore_deleted_art(art_id):
    """Восстанавливает удалённый арт"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT owner_id, file_id, caption, likes, dislikes, hashtags
        FROM deleted_arts WHERE art_id = ? AND restored_at IS NULL
    ''', (art_id,))
    result = cur.fetchone()
    
    if not result:
        conn.close()
        return False, "Арт не найден в удалённых!"
    
    owner_id, file_id, caption, likes, dislikes, hashtags_text = result
    
    try:
        cur.execute('''
            INSERT OR IGNORE INTO arts (art_id, owner_id, file_id, caption, likes, dislikes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (art_id, owner_id, file_id, caption, likes, dislikes))
        if hashtags_text:
            for hashtag in hashtags_text.split(","):
                cur.execute('INSERT INTO hashtags (art_id, hashtag) VALUES (?, ?)',
                           (art_id, hashtag))
                cur.execute('INSERT OR IGNORE INTO all_hashtags (hashtag_text) VALUES (?)',
                           (hashtag.lower(),))
                cur.execute(
                    'UPDATE all_hashtags SET usage_count = usage_count + 1 WHERE hashtag_text = ?',
                    (hashtag.lower(),)
                )
        cur.execute('UPDATE deleted_arts SET restored_at = CURRENT_TIMESTAMP WHERE art_id = ?', (art_id,))
        
        conn.commit()
        conn.close()
        return True, "Арт восстановлен!"
    except Exception as e:
        logging.error(f"Ошибка при восстановлении арта: {e}")
        conn.close()
        return False, f"Ошибка: {e}"
    
def add_complaint(art_id, reporter_id, reason, comment):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute(
        'INSERT INTO complaints (art_id, reporter_id, reason, comment) VALUES (?, ?, ?, ?)',
        (art_id, reporter_id, reason, comment)
    )
    
    conn.commit()
    conn.close()
    return True

def get_unseen_art(user_id, hashtag_filter=None):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT following_id FROM profile_followers WHERE follower_id = ?', (user_id,))
    following_users = [row[0] for row in cur.fetchall()]
    if hashtag_filter:
        if following_users:
            query = '''
                SELECT a.art_id, a.file_id, a.caption, a.likes, a.dislikes 
                FROM arts a
                JOIN hashtags h ON a.art_id = h.art_id
                WHERE a.art_id NOT IN (
                    SELECT art_id FROM reactions WHERE user_id = ?
                ) 
                AND a.owner_id != ?
                AND LOWER(h.hashtag) = LOWER(?)
                ORDER BY a.timestamp DESC
                LIMIT 1
            '''
            params = (user_id, user_id, hashtag_filter)
        else:
            query = '''
                SELECT a.art_id, a.file_id, a.caption, a.likes, a.dislikes 
                FROM arts a
                JOIN hashtags h ON a.art_id = h.art_id
                WHERE a.art_id NOT IN (
                    SELECT art_id FROM reactions WHERE user_id = ?
                ) 
                AND a.owner_id != ?
                AND LOWER(h.hashtag) = LOWER(?)
                ORDER BY a.timestamp DESC
                LIMIT 1
            '''
            params = (user_id, user_id, hashtag_filter)
    else:
        if following_users:
            query = '''
                SELECT art_id, file_id, caption, likes, dislikes 
                FROM arts 
                WHERE art_id NOT IN (
                    SELECT art_id FROM reactions WHERE user_id = ?
                ) 
                AND owner_id != ?
                ORDER BY timestamp DESC
                LIMIT 1
            '''
            params = (user_id, user_id)
        else:
            query = '''
                SELECT art_id, file_id, caption, likes, dislikes 
                FROM arts 
                WHERE art_id NOT IN (
                    SELECT art_id FROM reactions WHERE user_id = ?
                ) 
                AND owner_id != ?
                ORDER BY timestamp DESC
                LIMIT 1
            '''
            params = (user_id, user_id)
    
    cur.execute(query, params)
    art = cur.fetchone()
    if not art:
        logging.info(f"Все свежие арты просмотрены пользователем {user_id}. Ищем случайный арт.")
        if hashtag_filter:
            query = '''
                SELECT a.art_id, a.file_id, a.caption, a.likes, a.dislikes 
                FROM arts a
                JOIN hashtags h ON a.art_id = h.art_id
                WHERE a.art_id NOT IN (
                    SELECT art_id FROM reactions WHERE user_id = ?
                ) 
                AND a.owner_id != ?
                AND LOWER(h.hashtag) = LOWER(?)
                ORDER BY RANDOM()
                LIMIT 1
            '''
            params = (user_id, user_id, hashtag_filter)
        else:
            query = '''
                SELECT art_id, file_id, caption, likes, dislikes 
                FROM arts 
                WHERE art_id NOT IN (
                    SELECT art_id FROM reactions WHERE user_id = ?
                ) 
                AND owner_id != ?
                ORDER BY RANDOM()
                LIMIT 1
            '''
            params = (user_id, user_id)
        
        cur.execute(query, params)
        art = cur.fetchone()
    
    conn.close()
    return art

def has_new_arts_for_user(user_id):
    """Проверяет, есть ли арты, которые пользователь еще не оценил"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    query = '''
        SELECT COUNT(*) 
        FROM arts 
        WHERE art_id NOT IN (
            SELECT art_id FROM reactions WHERE user_id = ?
        ) 
        AND owner_id != ?
    '''
    
    cur.execute(query, (user_id, user_id))
    count = cur.fetchone()[0]
    conn.close()
    
    return count > 0

def get_art_owner(art_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT owner_id FROM arts WHERE art_id = ?', (art_id,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else None

def add_reaction(user_id, art_id, reaction_type):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute(
        'INSERT INTO reactions (user_id, art_id, type) VALUES (?, ?, ?)',
        (user_id, art_id, reaction_type)
    )
    
    if reaction_type == 'like':
        cur.execute('UPDATE arts SET likes = likes + 1 WHERE art_id = ?', (art_id,))
    else:
        cur.execute('UPDATE arts SET dislikes = dislikes + 1 WHERE art_id = ?', (art_id,))
    
    conn.commit()
    conn.close()

def add_comment(user_id, art_id, text):
    try:
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        
        cur.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        if not cur.fetchone():
            conn.close()
            return False, "Пользователь не найден"
        
        cur.execute('SELECT art_id FROM arts WHERE art_id = ?', (art_id,))
        if not cur.fetchone():
            conn.close()
            return False, "Арт не найден"
        
        if not text or not text.strip():
            conn.close()
            return False, "Комментарий не может быть пустым"
        
        cur.execute(
            'INSERT INTO comments (user_id, art_id, text) VALUES (?, ?, ?)',
            (user_id, art_id, text.strip())
        )
        conn.commit()
        conn.close()
        return True, "Комментарий успешно добавлен"
        
    except sqlite3.Error as e:
        logging.error(f"Ошибка при добавлении комментария: {e}")
        return False, f"Ошибка базы данных: {e}"

def get_art_by_id(art_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT art_id, file_id, caption, likes, dislikes 
        FROM arts 
        WHERE art_id = ?
    ''', (art_id,))
    art = cur.fetchone()
    conn.close()
    return art

def get_user_arts(user_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT 
            COUNT(*) as total_arts,
            SUM(likes) as total_likes,
            SUM(dislikes) as total_dislikes
        FROM arts 
        WHERE owner_id = ?
    ''', (user_id,))
    
    stats = cur.fetchone()
    
    cur.execute('''
        SELECT art_id, file_id, caption, likes, dislikes, timestamp
        FROM arts 
        WHERE owner_id = ?
        ORDER BY timestamp DESC
    ''', (user_id,))
    
    arts = cur.fetchall()
    conn.close()
    
    return stats, arts

def get_top_arts(limit=5, hashtag_filter=None):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    if hashtag_filter:
        query = '''
            SELECT a.art_id, a.file_id, a.caption, a.likes, a.dislikes, a.owner_id
            FROM arts a
            JOIN hashtags h ON a.art_id = h.art_id
            WHERE LOWER(h.hashtag) = LOWER(?)
            ORDER BY a.likes DESC, a.timestamp DESC
            LIMIT ?
        '''
        params = (hashtag_filter, limit)
    else:
        query = '''
            SELECT art_id, file_id, caption, likes, dislikes, owner_id
            FROM arts 
            ORDER BY likes DESC, timestamp DESC
            LIMIT ?
        '''
        params = (limit,)
    
    cur.execute(query, params)
    arts = cur.fetchall()
    conn.close()
    return arts

def get_top_arts_by_likes(limit=5, hashtag_filter=None):
    """Получает топ артов по количеству лайков"""
    return get_top_arts(limit, hashtag_filter)

def get_top_artists_by_followers(limit=5):
    """Получает топ художников по количеству подписчиков"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    query = '''
        SELECT u.user_id, u.username, u.nickname, COUNT(pf.follower_id) as followers_count,
               (SELECT COUNT(*) FROM arts WHERE owner_id = u.user_id) as art_count,
               (SELECT SUM(likes) FROM arts WHERE owner_id = u.user_id) as total_likes,
               u.bio, u.profile_avatar_file_id
        FROM users u
        LEFT JOIN profile_followers pf ON u.user_id = pf.following_id
        WHERE u.user_id IN (SELECT owner_id FROM arts)
        GROUP BY u.user_id
        ORDER BY followers_count DESC, total_likes DESC
        LIMIT ?
    '''
    
    cur.execute(query, (limit,))
    artists = cur.fetchall()
    conn.close()
    return artists

def get_user_rank(user_id, hashtag_filter=None):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    if hashtag_filter:
        query = '''
            SELECT a.owner_id, a.likes, a.art_id
            FROM arts a
            JOIN hashtags h ON a.art_id = h.art_id
            WHERE LOWER(h.hashtag) = LOWER(?)
            ORDER BY a.likes DESC, a.timestamp DESC
        '''
        cur.execute(query, (hashtag_filter,))
    else:
        query = '''
            SELECT owner_id, likes, art_id
            FROM arts 
            ORDER BY likes DESC, timestamp DESC
        '''
        cur.execute(query)
    
    all_arts = cur.fetchall()
    conn.close()
    
    user_max_likes = 0
    for art in all_arts:
        if art[0] == user_id and art[1] > user_max_likes:
            user_max_likes = art[1]
    
    if user_max_likes == 0:
        return None
    
    current_rank = 0
    last_likes = -1
    rank_counter = 0
    
    for art in all_arts:
        if art[1] != last_likes:
            rank_counter += 1
            last_likes = art[1]
        
        current_rank = rank_counter
        
        if art[0] == user_id and art[1] == user_max_likes:
            return current_rank
    
    return None

def extract_hashtags(text):
    hashtags = re.findall(r'#\w+', text)
    unique_hashtags = list(set([tag.lower() for tag in hashtags]))
    return unique_hashtags[:MAX_HASHTAGS_PER_ART]

def get_unviewed_reactions_count(owner_id):
    """Возвращает количество непросмотренных реакций"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT COUNT(*) FROM reactions r
        JOIN arts a ON r.art_id = a.art_id
        WHERE a.owner_id = ? AND r.type = 'like'
        AND NOT EXISTS (
            SELECT 1 FROM viewed_reactions vr 
            WHERE vr.user_id = ? AND vr.reaction_type = 'like' AND vr.reaction_id = r.reaction_id
        )
    ''', (owner_id, owner_id))
    unviewed_likes = cur.fetchone()[0]
    
    cur.execute('''
        SELECT COUNT(*) FROM comments c
        JOIN arts a ON c.art_id = a.art_id
        WHERE a.owner_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM viewed_reactions vr 
            WHERE vr.user_id = ? AND vr.reaction_type = 'comment' AND vr.reaction_id = c.comment_id
        )
    ''', (owner_id, owner_id))
    unviewed_comments = cur.fetchone()[0]
    
    conn.close()
    return unviewed_likes + unviewed_comments

def get_unviewed_reactions(owner_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT r.art_id, r.user_id, r.type, a.file_id, a.caption, r.reaction_id, NULL, r.timestamp
        FROM reactions r
        JOIN arts a ON r.art_id = a.art_id
        WHERE a.owner_id = ? AND r.type = 'like'
        AND NOT EXISTS (
            SELECT 1 FROM viewed_reactions vr 
            WHERE vr.user_id = ? AND vr.reaction_type = 'like' AND vr.reaction_id = r.reaction_id
        )
        ORDER BY r.timestamp DESC
        LIMIT 50
    ''', (owner_id, owner_id))
    unviewed_likes = cur.fetchall()
    
    cur.execute('''
        SELECT c.art_id, c.user_id, 'comment', a.file_id, a.caption, c.comment_id, c.text, c.timestamp
        FROM comments c
        JOIN arts a ON c.art_id = a.art_id
        WHERE a.owner_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM viewed_reactions vr 
            WHERE vr.user_id = ? AND vr.reaction_type = 'comment' AND vr.reaction_id = c.comment_id
        )
        ORDER BY c.timestamp DESC
        LIMIT 50
    ''', (owner_id, owner_id))
    unviewed_comments = cur.fetchall()
    
    conn.close()
    
    all_reactions = []
    
    for reaction in unviewed_likes + unviewed_comments:
        all_reactions.append({
            'type': reaction[2],
            'art_id': reaction[0],
            'user_id': reaction[1],
            'file_id': reaction[3],
            'caption': reaction[4],
            'reaction_id': reaction[5],
            'text': reaction[6],
            'timestamp': reaction[7]
        })
    
    all_reactions.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return all_reactions

def mark_reaction_as_viewed(user_id, reaction_type, reaction_id, art_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute(
        'INSERT OR IGNORE INTO viewed_reactions (user_id, reaction_type, reaction_id, art_id) VALUES (?, ?, ?, ?)',
        (user_id, reaction_type, reaction_id, art_id)
    )
    
    conn.commit()
    conn.close()

def mark_all_reactions_as_viewed(owner_id):
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    
    cur.execute('''
        INSERT OR IGNORE INTO viewed_reactions (user_id, reaction_type, reaction_id, art_id)
        SELECT ?, 'like', r.reaction_id, r.art_id
        FROM reactions r
        JOIN arts a ON r.art_id = a.art_id
        WHERE a.owner_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM viewed_reactions vr 
            WHERE vr.user_id = ? AND vr.reaction_type = 'like' AND vr.reaction_id = r.reaction_id
        )
    ''', (owner_id, owner_id, owner_id))
    
    cur.execute('''
        INSERT OR IGNORE INTO viewed_reactions (user_id, reaction_type, reaction_id, art_id)
        SELECT ?, 'comment', c.comment_id, c.art_id
        FROM comments c
        JOIN arts a ON c.art_id = a.art_id
        WHERE a.owner_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM viewed_reactions vr 
            WHERE vr.user_id = ? AND vr.reaction_type = 'comment' AND vr.reaction_id = c.comment_id
        )
    ''', (owner_id, owner_id, owner_id))
    
    conn.commit()
    conn.close()

# ========== СИСТЕМА УВЕДОМЛЕНИЙ О РЕАКЦИЯХ ==========

async def update_reaction_notification(context: ContextTypes.DEFAULT_TYPE, owner_id: int):
    """Обновляет или создает уведомление о реакциях"""
    try:
        unviewed_count = get_unviewed_reactions_count(owner_id)
        
        if unviewed_count == 0:
            existing_notification = get_notification_message(owner_id)
            if existing_notification:
                message_id, chat_id, _ = existing_notification
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except:
                    pass
                delete_notification_message(owner_id)
            return
        existing_notification = get_notification_message(owner_id)
        
        message_text = f"🎉 Твой арт понравился {unviewed_count} человеку!" if unviewed_count == 1 else f"🎉 Твой арт понравился {unviewed_count} людям!"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Показать", callback_data='show_reactions')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if existing_notification:
            message_id, chat_id, last_count = existing_notification
            
            if unviewed_count != last_count:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=message_text,
                        reply_markup=reply_markup
                    )
                    save_notification_message(owner_id, message_id, chat_id, unviewed_count)
                except Exception as e:
                    logging.error(f"Ошибка при обновлении уведомления: {e}")
                    await create_new_notification(context, owner_id, message_text, reply_markup)
        else:
            await create_new_notification(context, owner_id, message_text, reply_markup)
            
    except Exception as e:
        logging.error(f"Ошибка в update_reaction_notification: {e}")

async def notify_art_owner(art_id, reaction_type, comment_text, from_user, context):
    try:
        owner_id = get_art_owner(art_id)
        if not owner_id:
            return
        await create_or_update_reaction_notification(context, owner_id)
            
    except Exception as e:
        logging.error(f"Ошибка в notify_art_owner: {e}")

async def send_notification_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет напоминание о непросмотренных реакциях раз в 12 часов"""
    try:
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        
        cur.execute('''
            SELECT DISTINCT a.owner_id 
            FROM arts a
            JOIN reactions r ON a.art_id = r.art_id
            WHERE NOT EXISTS (
                SELECT 1 FROM viewed_reactions vr 
                WHERE vr.user_id = a.owner_id AND vr.reaction_type = 'like' AND vr.reaction_id = r.reaction_id
            )
            UNION
            SELECT DISTINCT a.owner_id 
            FROM arts a
            JOIN comments c ON a.art_id = c.art_id
            WHERE NOT EXISTS (
                SELECT 1 FROM viewed_reactions vr 
                WHERE vr.user_id = a.owner_id AND vr.reaction_type = 'comment' AND vr.reaction_id = c.comment_id
            )
        ''')
        
        users_with_reactions = cur.fetchall()
        conn.close()
        
        for user_row in users_with_reactions:
            user_id = user_row[0]
            await create_or_update_reaction_notification(context, user_id)
            
    except Exception as e:
        logging.error(f"Ошибка в send_notification_reminder: {e}")

# ========== СИСТЕМА ПОШАГОВОГО ПРОСМОТРА РЕАКЦИЙ С ОБНОВЛЕНИЕМ В РЕАЛЬНОМ ВРЕМЕНИ ==========

async def show_reactions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки Показать - начинает пошаговый просмотр реакций"""
    query = update.callback_query
    try:
        await query.answer()
    except telegram.error.BadRequest:
        logging.info("Query is too old, ignoring answer.")

    user_id = query.from_user.id
    
    try:
        await query.message.delete()
    except Exception:
        pass
    reactions = get_unviewed_reactions(user_id)
    
    if not reactions:
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 У вас нет новых лайков или комментариев!"
        )
        return
    
    context.user_data['reactions_to_show'] = reactions
    context.user_data['current_reaction_index'] = 0
    
    await show_single_reaction(update, context)

def escape_markdown(text):
    """Экранирует специальные символы Markdown"""
    if not text:
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def show_single_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает одну реакцию новым сообщением"""
    user_id = update.effective_user.id
    reactions = context.user_data.get('reactions_to_show', [])
    current_index = context.user_data.get('current_reaction_index', 0)
    
    if current_index >= len(reactions):
        mark_all_reactions_as_viewed(user_id)
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 Вы просмотрели все новые реакции!"
        )
        if 'reactions_to_show' in context.user_data:
            del context.user_data['reactions_to_show']
        if 'current_reaction_index' in context.user_data:
            del context.user_data['current_reaction_index']
        await start(update, context)
        return
    
    reaction = reactions[current_index]
    reactor_profile = get_user_profile(reaction['user_id'])
    is_reactor_profile_public = reactor_profile[5] if reactor_profile else False
    
    reactor_name = get_display_name(reaction['user_id'], profile_is_public=is_reactor_profile_public)
    
    if reaction['type'] == 'like':
        reaction_text = f"❤️ {reactor_name} поставил(а) лайк твоему арту!"
    else:
        comment_text = reaction['text']
        safe_comment_text = escape_markdown(comment_text) if comment_text else ""
        reaction_text = f"💬 {reactor_name} написал(а) комментарий к твоему арту:\n\n{safe_comment_text}"
    
    art = get_art_by_id(reaction['art_id'])
    if not art:
        context.user_data['current_reaction_index'] = current_index + 1
        await show_single_reaction(update, context)
        return
        
    art_id, file_id, caption, likes, dislikes = art
    
    keyboard = []
    if current_index < len(reactions) - 1:
        keyboard.append([InlineKeyboardButton("Далее ➡️", callback_data='next_reaction')])
    
    keyboard.append([InlineKeyboardButton("Завершить просмотр", callback_data='finish_reactions')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_photo(
            chat_id=user_id,
            photo=file_id,
            caption=reaction_text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке реакции: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text=reaction_text,
            reply_markup=reply_markup
        )
    
    mark_reaction_as_viewed(user_id, reaction['type'], reaction['reaction_id'], reaction['art_id'])
    context.user_data['current_reaction_index'] = current_index + 1
    await create_or_update_reaction_notification(context, user_id)
    
async def next_reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки Далее"""
    query = update.callback_query
    try:
        await query.answer()
        await query.message.delete()
    except Exception as e:
        logging.info(f"Не удалось удалить старое сообщение с кнопкой 'Далее': {e}")
    
    await show_single_reaction(update, context)

async def finish_reactions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки Завершить просмотр"""
    query = update.callback_query
    try:
        await query.answer()
        await query.message.delete()
    except Exception as e:
        logging.info(f"Не удалось удалить старое сообщение с кнопкой 'Завершить': {e}")

    user_id = query.from_user.id
    mark_all_reactions_as_viewed(user_id)
    
    if 'reactions_to_show' in context.user_data:
        del context.user_data['reactions_to_show']
    if 'current_reaction_index' in context.user_data:
        del context.user_data['current_reaction_index']
    
    await start(update, context)

async def menu_from_reactions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Этот обработчик больше не нужен, но оставим его для обратной совместимости"""
    await finish_reactions_handler(update, context)

# ========== СИСТЕМА CLIP ДЛЯ ПРОВЕРКИ ИЗОБРАЖЕНИЙ ==========

# Конфигурация устройства
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"\n{'='*60}")
print(f"🖥️  Используемое устройство: {device.upper()}")
if device == "cuda":
    print(f"   GPU: {torch.cuda.get_device_name(0)}")
    print(f"   Память: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
print(f"{'='*60}\n")
clip_model = None
clip_preprocess = None
try:
    # ViT-B/32 - быстрая модель (32M параметров)
    # ViT-L/14 - точная модель (305M параметров)
    clip_model, clip_preprocess = clip.load("ViT-L/14", device=device)
    logging.info(f"✅ CLIP модель ViT-L/14 загружена на устройство: {device}")
    print(f"✅ CLIP модель успешно загружена на {device.upper()}")
except Exception as e:
    logging.error(f"❌ Ошибка загрузки CLIP модели: {e}")
    clip_model = None
    clip_preprocess = None
    print(f"❌ Ошибка загрузки CLIP модели: {e}")

# Дополнительная модель для проверки NSFW (быстрая, специализированная)
nsfw_classifier = None
try:
    # Используем трансформер специально натренированный на NSFW
    from transformers import pipeline
    nsfw_classifier = pipeline(
        "image-classification",
        model="Falconsai/nsfw_image_detection",
        device=0 if device == "cuda" else -1
    )
    logging.info("✅ NSFW classifier загружен успешно")
    print("✅ NSFW classifier успешно загружена")
except Exception as e:
    logging.warning(f"⚠️  NSFW classifier не загружена: {e}")
    nsfw_classifier = None
    print(f"⚠️  NSFW classifier недоступна (используется только CLIP)")

nsfw_text_descriptions = [
    "realistic blood and gore", "photographic violent"
    "real murder scene", "photograph of dead body", "real corpse", "real bloody scene",
    "real weapons violence", "real gun violence", "real knife attack",
    "blood and gore art", "violent scene drawing", "illustrated violence", "cartoon violence",
    "animated blood", "digital painting of violence", "comic book violence", "violent artwork",
    "brutal fight", "terrorist attack",
    
    "real naked person", "real nudity", "real pornography", "real sexual content",
    "real explicit nudity", "real adult content", "real sexual act", "real erotic content",
    "real xxx content", "real hardcore pornography",
    "nudity art", "erotic drawing", "sexual content artwork", "animated pornography",
    "hentai", "explicit anime", "cartoon nudity", "digital art nudity", "nsfw artwork",
    "graphic sexual content",
    
    "real dismembered body", "real mutilated corpse", "real body parts", "real severed limbs",
    "real gore", "real disturbing content", "real shocking scene", "real graphic violence",
    "real brutal injury", "real mutilation",
    "gore art", "dismemberment drawing", "mutilated character art", "cartoon gore",
    "animated blood and guts", "comic book gore", "digital art gore", "body horror artwork",
    "dismemberment", "chopped up body",
    
    "peaceful landscape painting", "cute animal drawing", "building illustration",
    "person smiling art", "art drawing", "anime art", "digital painting",
    "character design", "beautiful painting", "scenic view artwork",
    "lovely pet cartoon", "nice artwork", "creative design", "fantasy character",
    "cartoon character", "beautiful sunset painting", "cute cartoon",
    "landscape illustration", "art piece", "innocent content"
]

nsfw_text_classes = [
    "violence", "violence", "violence", "violence", "violence", "violence", "violence", 
    "violence", "violence", "violence", "violence", "violence", "violence", "violence",
    "violence", "violence", "violence", "violence", "violence", "violence",
    
    "nudity", "nudity", "nudity", "nudity", "nudity", "nudity", "nudity", "nudity",
    "nudity", "nudity", "nudity", "nudity", "nudity", "nudity", "nudity", "nudity",
    "nudity", "nudity", "nudity", "nudity",
    
    "gore", "gore", "gore", "gore", "gore", "gore", "gore", "gore", "gore", "gore",
    "gore", "gore", "gore", "gore", "gore", "gore", "gore", "gore", "gore", "gore",
    
    "safe", "safe", "safe", "safe", "safe", "safe", "safe", "safe", "safe", "safe",
    "safe", "safe", "safe", "safe", "safe", "safe", "safe", "safe", "safe", "safe"
]

async def check_image_nsfw(image: Image.Image) -> dict:
    """
    Проверяет изображение на NSFW контент используя CLIP модель.
    Также использует дополнительный classifier если он доступен.
    ПРИОРИТЕТ: NSFW classifier имеет наивысший приоритет.
    """
    if clip_model is None or clip_preprocess is None:
        logging.error("CLIP модель не загружена")
        return {"error": "Модель не загружена"}
    
    try:
        # 1. Первая проверка с дополнительным NSFW классификатором (ПРИОРИТЕТ)
        nsfw_classifier_score = 0
        nsfw_classifier_confidence = 0
        if nsfw_classifier is not None:
            try:
                classifier_results = nsfw_classifier(image)
                # classifier_results = [{"label": "nsfw", "score": 0.9}, {"label": "normal", "score": 0.1}]
                for result in classifier_results:
                    if result["label"].lower() == "nsfw":
                        nsfw_classifier_score = result["score"]
                        nsfw_classifier_confidence = result["score"]
                        logging.info(f"📊 NSFW classifier результат: NSFW score = {result['score']:.2%}")
                        break
            except Exception as e:
                logging.warning(f"Ошибка при использовании дополнительного классификатора: {e}")
        
        # 2. Проверка с CLIP моделью (вспомогательная)
        image_input = clip_preprocess(image).unsqueeze(0).to(device)
        text_tokens = clip.tokenize(nsfw_text_descriptions).to(device)
        
        with torch.no_grad():
            image_features = clip_model.encode_image(image_input)
            text_features = clip_model.encode_text(text_tokens)
            
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            
            similarity = (100.0 * image_features @ text_features.T).softmax(dim=-1)
            results = similarity[0].cpu().numpy()

        category_scores = {"safe": 0, "violence": 0, "nudity": 0, "gore": 0}
        
        for i, score in enumerate(results):
            category = nsfw_text_classes[i]
            if score > category_scores[category]:
                category_scores[category] = score
        
        # 3. Применяем результат NSFW classifier с ВЫСОКИМ ПРИОРИТЕТОМ
        # Если NSFW classifier дал высокий score, это переопределяет CLIP результаты
        if nsfw_classifier_confidence > 0.5:
            # NSFW classifier уверен что это NSFW - используем его результат
            category_scores["nudity"] = nsfw_classifier_confidence
            logging.info(f"⚠️  NSFW classifier переопределяет результаты (confidence={nsfw_classifier_confidence:.2%})")
        
        # Добавляем confidence от NSFW classifier в результаты
        category_scores["nsfw_classifier_confidence"] = nsfw_classifier_confidence
        
        return category_scores
        
    except Exception as e:
        logging.error(f"Ошибка при проверке изображения с CLIP: {e}")
        return {"error": str(e)}

async def validate_image_basic(image: Image.Image) -> tuple:
    try:
        width, height = image.size
        if width < 50 or height < 50:
            return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
        
        if width > 5000 or height > 5000:
            return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
        
        ratio = max(width, height) / min(width, height)
        if ratio > 8:
            return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
        
        colors = image.getcolors(maxcolors=10000)
        if colors and len(colors) < 10:
            return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
            
        return True, "✅ Изображение прошло базовую проверку"
        
    except Exception as e:
        logging.error(f"Ошибка базовой проверки изображения: {e}")
        return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
    
async def is_image_safe(image: Image.Image) -> tuple:
    """
    Проверяет изображение на NSFW контент.
    Использует CLIP модель + опциональный дополнительный классификатор.
    
    Логика приоритетов:
    1. NSFW classifier >= 70% → БЛОКИРОВКА
    2. safe_score < 0.02 AND NSFW classifier < 10% → ПРОПУСК
    3. Остальные CLIP проверки
    """
    scores = await check_image_nsfw(image)
    
    if "error" in scores:
        return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
    
    # Получаем confidence от NSFW classifier
    nsfw_classifier_confidence = scores.get("nsfw_classifier_confidence", 0)
    
    max_nsfw_score = max(scores["violence"], scores["nudity"], scores["gore"])
    safe_score = scores["safe"]
    logging.info(
        f"📊 NSFW проверка: "
        f"безопасность={safe_score:.3f}, "
        f"насилие={scores['violence']:.3f}, "
        f"неприемлемый контент={scores['nudity']:.3f}, "
        f"тревожный контент={scores['gore']:.3f}, "
        f"NSFW classifier confidence={nsfw_classifier_confidence:.3f}"
    )
    
    # ★ ПРИОРИТЕТ 1: NSFW classifier блокирует если >= 70% вероятность
    if nsfw_classifier_confidence >= 0.7:
        logging.warning(f"🚫 NSFW classifier блокирует (уверенность {nsfw_classifier_confidence:.1%})")
        return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
    
    # ★ ПРИОРИТЕТ 2: Если safe_score очень низкий но NSFW classifier < 10%, пропускаем арт
    if safe_score < 0.02 and nsfw_classifier_confidence < 0.1:
        logging.info(f"✅ Арт пропущен по исключению: low safe_score ({safe_score:.3f}) но NSFW classifier низкий ({nsfw_classifier_confidence:.1%})")
        return True, f"✅ Изображение безопасно (низкий риск от NSFW классификатора)"
    
    blocked_categories = []
    
    if scores["violence"] > 0.4:
        blocked_categories.append(f"насилие ({scores['violence']:.1%})")
        logging.warning(f"⚠️  Обнаружено насилие: {scores['violence']:.1%}")
    if scores["nudity"] > 0.4:
        blocked_categories.append(f"неприемлемый контент ({scores['nudity']:.1%})")
        logging.warning(f"⚠️  Обнаружен неприемлемый контент: {scores['nudity']:.1%}")
    if scores["gore"] > 0.7:
        blocked_categories.append(f"тревожный контент ({scores['gore']:.1%})")
        logging.warning(f"⚠️  Обнаружено кровавое содержимое: {scores['gore']:.1%}")
    
    if blocked_categories:
        logging.info(f"❌ Изображение заблокировано: {', '.join(blocked_categories)}")
        return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
    
    if safe_score < 0.02:
        logging.warning(f"⚠️  Низкий безопасный score: {safe_score:.3f}")
        return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
    
    total_nsfw = scores["violence"] + scores["nudity"] + scores["gore"]
    if total_nsfw > 0.7:
        logging.warning(f"⚠️  Общий NSFW score слишком высок: {total_nsfw:.3f}")
        return False, "❌ Арт не может быть загружен!\n\nЕсли вы считаете, что это ошибка, обратитесь в поддержку."
    
    logging.info(f"✅ Изображение одобрено (риск CLIP: {max_nsfw_score:.1%}, NSFW classifier: {nsfw_classifier_confidence:.1%})")
    return True, f"✅ Изображение безопасно (риск: {max_nsfw_score:.1%})"

# ========== СИСТЕМА ПРОФИЛЕЙ ПОЛЬЗОВАТЕЛЕЙ ==========

def get_user_profile(user_id):
    """Получает профиль пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT user_id, username, nickname, bio, profile_avatar_file_id, is_profile_public
        FROM users WHERE user_id = ?
    ''', (user_id,))
    result = cur.fetchone()
    conn.close()
    return result

def update_user_nickname(user_id, nickname):
    """Обновляет ник пользователя (максимум 30 символов)"""
    if len(nickname) > 30:
        return False, "❌ Ник не может быть длиннее 30 символов"
    if len(nickname) < 1:
        return False, "❌ Ник не может быть пустым"
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET nickname = ? WHERE user_id = ?', (nickname, user_id))
    conn.commit()
    conn.close()
    return True, "✅ Ник обновлен"

def update_user_bio(user_id, bio):
    """Обновляет описание профиля (максимум 500 символов)"""
    if len(bio) > 500:
        return False, "❌ Описание не может быть длиннее 500 символов"
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET bio = ? WHERE user_id = ?', (bio, user_id))
    conn.commit()
    conn.close()
    return True, "✅ Описание обновлено"

def update_user_profile_avatar(user_id, file_id):
    """Обновляет аватар профиля"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET profile_avatar_file_id = ? WHERE user_id = ?', (file_id, user_id))
    conn.commit()
    conn.close()
    return True, "✅ Аватар обновлен"

def toggle_profile_privacy(user_id):
    """Переключает приватность профиля (открыт/закрыт)"""
    profile = get_user_profile(user_id)
    if not profile:
        return False, "❌ Профиль не найден"
    
    is_public = not profile[5]
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_profile_public = ? WHERE user_id = ?', (is_public, user_id))
    conn.commit()
    conn.close()
    
    status = "открыт" if is_public else "закрыт"
    return True, f"✅ Профиль теперь {status}"

def follow_user(follower_id, following_id):
    """Добавляет подписку на пользователя"""
    if follower_id == following_id:
        return False, "❌ Вы не можете подписаться на самого себя"
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM profile_followers WHERE follower_id = ? AND following_id = ?',
               (follower_id, following_id))
    if cur.fetchone():
        conn.close()
        return False, "❌ Вы уже подписаны на этого пользователя"
    
    try:
        cur.execute('''
            INSERT INTO profile_followers (follower_id, following_id)
            VALUES (?, ?)
        ''', (follower_id, following_id))
        conn.commit()
        conn.close()
        return True, "✅ Вы подписались"
    except Exception as e:
        conn.close()
        logging.error(f"Ошибка при подписке: {e}")
        return False, "❌ Ошибка при подписке"

async def notify_about_follower(context: ContextTypes.DEFAULT_TYPE, following_id: int):
    """Отправляет уведомление о новой подписке"""
    try:
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM profile_followers WHERE following_id = ?', (following_id,))
        followers_count = cur.fetchone()[0]
        conn.close()
        if followers_count % 10 == 1 and followers_count % 100 != 11:
            word = "человек"
        else:
            word = "человек"
        should_notify = False
        if followers_count == 1 or followers_count == 5:
            should_notify = True
        elif followers_count >= 10 and followers_count % 5 == 0:
            should_notify = True
        elif followers_count >= 100 and followers_count % 10 == 0:
            should_notify = True
        
        if should_notify:
            try:
                await context.bot.send_message(
                    chat_id=following_id,
                    text=f"👥 **{followers_count} {word} подписался на вас!**\n\n"
                         f"Нажмите кнопку ниже, чтобы посмотреть подписчиков.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 Посмотреть", callback_data='view_followers')]]),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logging.error(f"Ошибка при отправке уведомления о подписке: {e}")
    except Exception as e:
        logging.error(f"Ошибка при обработке уведомления о подписке: {e}")

def unfollow_user(follower_id, following_id):
    """Отписывает от пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM profile_followers WHERE follower_id = ? AND following_id = ?',
               (follower_id, following_id))
    conn.commit()
    conn.close()
    return True, "✅ Вы отписались"

def is_following(follower_id, following_id):
    """Проверяет подписан ли пользователь"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM profile_followers WHERE follower_id = ? AND following_id = ?',
               (follower_id, following_id))
    result = cur.fetchone()
    conn.close()
    return result is not None

def get_followers_count(user_id):
    """Получает количество подписчиков"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM profile_followers WHERE following_id = ?', (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_following_count(user_id):
    """Получает количество подписок пользователя"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM profile_followers WHERE follower_id = ?', (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

def search_users_by_nickname(query, limit=10):
    """Поиск пользователей по нику или юзернейму (поддерживает @username формат)"""
    clean_query = query.lstrip('@').strip()
    
    if not clean_query:
        return []
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT user_id, nickname, username, is_profile_public
        FROM users
        WHERE (nickname LIKE ? OR username LIKE ?)
        AND is_profile_public = 1
        LIMIT ?
    ''', (f'%{clean_query}%', f'%{clean_query}%', limit))
    results = cur.fetchall()
    conn.close()
    return results

def add_profile_violation(user_id, violation_type, reason):
    """Добавляет нарушение профиля"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO profile_violations (user_id, violation_type, reason)
        VALUES (?, ?, ?)
    ''', (user_id, violation_type, reason))
    conn.commit()
    conn.close()
    logging.warning(f"⚠️  Профиль {user_id} заблокирован за {violation_type}: {reason}")

def has_profile_violations(user_id):
    """Проверяет есть ли нарушения в профиле"""
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM profile_violations WHERE user_id = ?', (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

# ========== СИСТЕМА МОДЕРАЦИИ ==========

async def send_for_manual_review(context, user_id: int, caption: str, file_id: str, pending_id: int):
    try:
        review_text = (
            f"🔍 **Требуется ручная модерация**\n\n"
            f"👤 Пользователь: {escape_markdown(get_display_name(user_id, for_moderator=True))}\n"
            f"🆔 ID: {user_id}\n"
            f"📝 Подпись: {escape_markdown(caption[:500] if caption else 'Нет подписи')}\n\n"
            f"Автоматическая проверка вызвала подозрения."
        )
        
        keyboard = [
            [InlineKeyboardButton("✅ Одобрить", callback_data=f'approve_manual_{pending_id}')],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_manual_{pending_id}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_messages = []
        for support_id in SUPPORT_USER_IDS:
            try:
                message = await context.bot.send_photo(
                    chat_id=support_id,
                    photo=file_id,
                    caption=review_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
                sent_messages.append((support_id, message.message_id))
                logging.info(f"Сообщение отправлено модератору {support_id}")
            except Exception as e:
                logging.error(f"Ошибка при отправке модератору {support_id}: {e}")
        
        return len(sent_messages) > 0
    except Exception as e:
        logging.error(f"Ошибка при отправке на ручную модерацию: {e}")
        return False
    
async def send_to_support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        callback_data = query.data
        pending_id = int(callback_data.split('_')[-1])
        
        pending_art = get_pending_art(pending_id)
        if not pending_art:
            await query.answer("❌ Арт не найден в базе данных", show_alert=True)
            return
        
        pending_id, user_id, file_id, caption, hashtags_text, timestamp = pending_art
        
        success = await send_for_manual_review(context, user_id, caption, file_id, pending_id)
        
        if success:
            await query.edit_message_text("✅ Арт отправлен на проверку модератору! Ожидайте решения.")
        else:
            await query.edit_message_text("❌ Ошибка при отправке арта в поддержку.")
        
    except Exception as e:
        logging.error(f"Ошибка при отправке в поддержку: {e}")
        await query.edit_message_text("❌ Ошибка при отправке арта в поддержку.")
        
async def approve_manual_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        callback_data = query.data
        pending_id = int(callback_data.split('_')[-1])
        
        pending_art = get_pending_art(pending_id)
        if not pending_art:
            await query.answer("❌ Арт не найден в базе данных", show_alert=True)
            return
        
        pending_id, user_id, file_id, caption, hashtags_text, timestamp = pending_art
        
        hashtags = hashtags_text.split(",") if hashtags_text else []
        
        art_id, message = add_art(user_id, file_id, caption, hashtags)
        
        if art_id:
            delete_pending_art(pending_id)
            
            old_caption = query.message.caption or ""
            await query.edit_message_caption(
                caption=f"✅ **Арт одобрен модератором**\n\n{escape_markdown(old_caption)}",
                parse_mode='Markdown'
            )
            
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ Ваш арт был одобрен модератором и добавлен в галерею!"
                )
                logging.info(f"Арт {pending_id} одобрен модератором, пользователь {user_id} уведомлен")
            except Exception as e:
                logging.error(f"Ошибка при уведомлении пользователя: {e}")
        else:
            await query.answer("❌ Ошибка при добавлении арта в галерею", show_alert=True)
        
    except Exception as e:
        logging.error(f"Ошибка при одобрении арта: {e}")
        await query.answer("❌ Ошибка при одобрении арта", show_alert=True)

async def reject_manual_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        callback_data = query.data
        pending_id = int(callback_data.split('_')[-1])
        
        pending_art = get_pending_art(pending_id)
        if not pending_art:
            await query.answer("❌ Арт не найден в базе данных", show_alert=True)
            return
        
        pending_id, user_id, file_id, caption, hashtags_text, timestamp = pending_art
        
        delete_pending_art(pending_id)
        
        old_caption = query.message.caption or ""
        await query.edit_message_caption(
            caption=f"❌ **Арт отклонен модератором**\n\n{escape_markdown(old_caption)}",
            parse_mode='Markdown'
        )
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Ваш арт был отклонен модератором."
            )
            logging.info(f"Арт {pending_id} отклонен модератором, пользователь {user_id} уведомлен")
        except Exception as e:
            logging.error(f"Ошибка при уведомлении пользователя: {e}")
        
    except Exception as e:
        logging.error(f"Ошибка при отклонении арта: {e}")
        await query.answer("❌ Ошибка при отклонении арта", show_alert=True)

# ========== ОСНОВНЫЕ ФУНКЦИИ БОТА ==========

async def send_art_to_user(chat_id, context, user_id, art=None, update_message=None, hashtag_filter=None):
    """Показывает арт пользователю"""
    if not art:
        art = get_unseen_art(user_id, hashtag_filter)
    
    if art:
        art_id, file_id, caption, likes, dislikes = art
        owner_id = get_art_owner(art_id)
        owner_profile = get_user_profile(owner_id) if owner_id else None
        
        hashtags = get_art_hashtags(art_id)
        hashtags_text = " ".join(hashtags) if hashtags else ""
        text = f"Лайков: {likes} | Дизлайков: {dislikes}"
        if caption:
            text = f"{caption}\n\n{text}"
        if hashtags_text:
            text = f"{text}\n\n{hashtags_text}"
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        cur.execute('SELECT type FROM reactions WHERE user_id = ? AND art_id = ?', 
                   (user_id, art_id))
        existing_reaction = cur.fetchone()
        conn.close()
        if existing_reaction:
            keyboard = []
            if existing_reaction[0] == 'like':
                keyboard.append([
                    InlineKeyboardButton("❤️ Вы лайкнули", callback_data='already_reacted'),
                    InlineKeyboardButton("💬 Комментарий", callback_data=f'comment_{art_id}'),
                    InlineKeyboardButton("👎 Дизлайк", callback_data=f'dislike_{art_id}')
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton("❤️ Лайк", callback_data=f'like_{art_id}'),
                    InlineKeyboardButton("💬 Комментарий", callback_data=f'comment_{art_id}'),
                    InlineKeyboardButton("👎 Вы дизлайкнули", callback_data='already_reacted')
                ])
            
            row2 = []
            if owner_profile and owner_profile[5]: 
                row2.append(InlineKeyboardButton("👤 Профиль", callback_data=f'view_profile_{owner_id}'))
            if row2:
                keyboard.append(row2)
            keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
        else:
            keyboard = []
            keyboard.append([
                InlineKeyboardButton("❤️ Лайк", callback_data=f'like_{art_id}'),
                InlineKeyboardButton("💬 Комментарий", callback_data=f'comment_{art_id}'),
                InlineKeyboardButton("👎 Дизлайк", callback_data=f'dislike_{art_id}')
            ])
            
            row2 = []
            if owner_profile and owner_profile[5]:
                row2.append(InlineKeyboardButton("👤 Профиль", callback_data=f'view_profile_{owner_id}'))
            row2.append(InlineKeyboardButton("🚫 Жалоба", callback_data=f'complaint_{art_id}'))
            if row2:
                keyboard.append(row2)
            keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
        if hashtag_filter:
            keyboard.insert(-1, [InlineKeyboardButton("🔍 Сбросить фильтр", callback_data='view_arts')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            if update_message:
                await update_message.edit_media(
                    media=InputMediaPhoto(media=file_id, caption=text),
                    reply_markup=reply_markup
                )
                message = update_message
            else:
                message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=text,
                    reply_markup=reply_markup
                )
            add_active_message(message.message_id, chat_id, art_id, user_id)
            return True
            
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e):
                return True
            elif "Message to edit not found" in str(e) or "Message can't be edited" in str(e):
                try:
                    if update_message:
                        await update_message.delete()
                except:
                    pass
                
                message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=text,
                    reply_markup=reply_markup
                )
                add_active_message(message.message_id, chat_id, art_id, user_id)
                return True
            else:
                logging.error(f"Ошибка при отправке арта: {e}")
                try:
                    message = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=file_id,
                        caption=text,
                        reply_markup=reply_markup
                    )
                    add_active_message(message.message_id, chat_id, art_id, user_id)
                    return True
                except Exception as e2:
                    logging.error(f"Ошибка при повторной отправке арта: {e2}")
                    return False
        except Exception as e:
            logging.error(f"Неожиданная ошибка при отправке арта: {e}")
            return False
    else:
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]]
        
        if hashtag_filter:
            keyboard.insert(0, [InlineKeyboardButton("🔍 Сбросить фильтр", callback_data='view_arts')])
            message_text = f"🎉 Вы оценили все доступные арты с хэштегом {hashtag_filter}! Попробуйте другой хэштег или загляните позже."
        else:
            message_text = "🎉 Вы оценили все доступные арты! Загляните позже или загрузите свои работы."
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update_message:
            try:
                await update_message.edit_text(
                    message_text,
                    reply_markup=reply_markup
                )
                return False
            except Exception as e:
                logging.error(f"Ошибка при обновлении сообщения: {e}")
                try:
                    await update_message.delete()
                except:
                    pass
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=reply_markup
                )
                return False
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup
            )
            return False
        
async def show_hashtag_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, search_query=None):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
    else:
        chat_id = update.message.chat_id

    if search_query:
        found_hashtags = search_hashtags(search_query)
        if found_hashtags:
            keyboard = []
            for hashtag_text, usage_count in found_hashtags:
                keyboard.append([InlineKeyboardButton(
                    f"{hashtag_text} ({usage_count})", 
                    callback_data=f'filter_{hashtag_text}'
                )])
            
            keyboard.append([InlineKeyboardButton("🔍 Новый поиск", callback_data='hashtag_search')])
            keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔍 **Результаты поиска по: '{search_query}'**\n\n"
                    "Выберите хэштег для фильтрации:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔍 Новый поиск", callback_data='hashtag_search')],
                [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔍 **По запросу '{search_query}' ничего не найдено**\n\n"
                    "Попробуйте другой запрос.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

# ========== НОВАЯ СИСТЕМА ПРОФИЛЕЙ И ПОИСКА ==========

async def show_search_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора типа поиска"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("🏷️ Поиск по хэштегам", callback_data='search_hashtags')],
        [InlineKeyboardButton("👤 Поиск профилей", callback_data='search_profiles')],
        [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🔍 **Выберите тип поиска:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_user_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE, gallery_user_id: int, is_my_gallery: bool = False):
    """Показывает галерею пользователя"""
    query = update.callback_query
    stats, arts = get_user_arts(gallery_user_id)
    
    if not arts:
        await query.answer("🎨 Галерея пуста", show_alert=True)
        return
    context.user_data['gallery_user_id'] = gallery_user_id
    context.user_data['gallery_arts'] = arts
    context.user_data['gallery_current_index'] = 0
    context.user_data['is_my_gallery'] = is_my_gallery
    await show_gallery_page(update, context, 0)

async def show_gallery_page(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int):
    """Показывает страницу галереи"""
    query = update.callback_query
    arts = context.user_data.get('gallery_arts', [])
    gallery_user_id = context.user_data.get('gallery_user_id')
    is_my_gallery = context.user_data.get('is_my_gallery', False)
    current_user_id = query.from_user.id
    
    if not arts or index >= len(arts):
        await query.answer("❌ Арт не найден", show_alert=True)
        return
    
    art_id, file_id, caption, likes, dislikes, timestamp = arts[index]
    hashtags = get_art_hashtags(art_id)
    gallery_text = f"🎨 **Галерея** ({index + 1}/{len(arts)})\n\n"
    if caption:
        gallery_text += f"{escape_markdown(caption)}\n\n"
    
    gallery_text += f"❤️ {likes} | 👎 {dislikes}"
    if hashtags:
        hashtags_text = " ".join(hashtags)
        gallery_text += f"\n🏷️ {escape_markdown(hashtags_text)}"
    keyboard = []
    
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f'gallery_prev_{index-1}'))
    
    nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(arts)}", callback_data='gallery_info'))
    
    if index < len(arts) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f'gallery_next_{index+1}'))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    if is_my_gallery or gallery_user_id == current_user_id:
        keyboard.append([InlineKeyboardButton("🗑️ Удалить", callback_data=f'gallery_delete_{art_id}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад к профилю", callback_data=f'back_to_user_profile_{gallery_user_id}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_media(
            media=InputMediaPhoto(media=file_id, caption=gallery_text, parse_mode='Markdown'),
            reply_markup=reply_markup
        )
    except:
        await query.message.delete()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=file_id,
            caption=gallery_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    context.user_data['gallery_current_index'] = index

async def show_deleted_arts_gallery(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int = 0):
    """Показывает галерею удалённых артов с навигацией"""
    deleted_arts = None
    
    if 'deleted_arts_list' in context.user_data:
        deleted_arts = context.user_data.get('deleted_arts_list', [])
    else:
        deleted_arts = get_deleted_arts(limit=100)
        context.user_data['deleted_arts_list'] = deleted_arts
    
    if not deleted_arts:
        if update.callback_query:
            await update.callback_query.answer("❌ Нет удалённых артов", show_alert=True)
            return
        else:
            await update.message.reply_text("❌ Нет удалённых артов")
            return
    
    if index >= len(deleted_arts):
        index = len(deleted_arts) - 1
    if index < 0:
        index = 0
    
    deleted_id, art_id, owner_id, file_id, caption, deleted_at, reason = deleted_arts[index]
    owner_profile = get_user_profile(owner_id)
    is_owner_profile_public = owner_profile[5] if owner_profile else False
    
    owner_name = get_display_name(owner_id, profile_is_public=is_owner_profile_public)
    gallery_text = f"🗑️ **Удалённый арт** ({index + 1}/{len(deleted_arts)})\n\n"
    gallery_text += f"🎨 Арт #{art_id}\n"
    gallery_text += f"👤 Автор: {escape_markdown(owner_name)}\n"
    gallery_text += f"⏰ Удален: {deleted_at}\n"
    gallery_text += f"📋 Причина: {escape_markdown(reason)}\n\n"
    
    if caption:
        gallery_text += f"📝 {escape_markdown(caption)}\n"
    
    keyboard = []
    
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f'deleted_arts_prev_{index-1}'))
    
    nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(deleted_arts)}", callback_data='deleted_arts_info'))
    
    if index < len(deleted_arts) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f'deleted_arts_next_{index+1}'))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔍 Поиск по нику", callback_data='deleted_arts_search_user')])  
    keyboard.append([InlineKeyboardButton("♻️ Восстановить", callback_data=f'restore_art_{art_id}')])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='deleted_arts_back')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.user_data['deleted_arts_current_index'] = index
    if update.callback_query:
        query = update.callback_query
        try:
            await query.answer()
        except:
            pass
        
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=file_id, caption=gallery_text, parse_mode='Markdown'),
                reply_markup=reply_markup
            )
        except telegram.error.BadRequest as e:
            if "Message can't be edited" in str(e) or "Message to edit not found" in str(e):
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=file_id,
                        caption=gallery_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
            else:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=file_id,
                    caption=gallery_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logging.error(f"Ошибка при редактировании удаленного арта: {e}")
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=file_id,
                caption=gallery_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    else:

        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=file_id,
                caption=gallery_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Ошибка при отправке фото: {e}")
            await update.message.reply_text(
                gallery_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
async def show_other_user_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показывает профиль другого пользователя"""
    try:
        query = update.callback_query
        if not query:
            return
            
        profile = get_user_profile(user_id)
        
        if not profile:
            await query.answer("❌ Профиль не найден", show_alert=True)
            return
        
        nickname = profile[2] or "Не указан"
        bio = profile[3] or "Не указано"
        avatar_file_id = profile[4]
        is_public = profile[5]
        if not is_public:
            await query.answer("❌ Этот профиль закрыт", show_alert=True)
            return
        if has_profile_violations(user_id):
            await query.answer("❌ Профиль недоступен", show_alert=True)
            return
        followers_count = get_followers_count(user_id)
        following_count = get_following_count(user_id)
        art_count = get_user_art_count(user_id)
        
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        cur.execute('SELECT SUM(likes), SUM(dislikes) FROM arts WHERE owner_id = ?', (user_id,))
        result = cur.fetchone()
        total_likes = result[0] or 0
        total_dislikes = result[1] or 0
        conn.close()
        
        profile_text = f"👤 **Профиль**\n\n"
        
        if nickname and nickname != "Не указан":
            profile_text += f"{escape_markdown(nickname)}\n\n"
        
        if bio and bio != "Не указано":
            profile_text += f"{escape_markdown(bio)}\n\n"
        
        profile_text += (
            f"📊 **Статистика:**\n"
            f"🎨 Артов: {art_count}\n"
            f"❤️ Лайков: {total_likes}\n"
            f"👥 Подписчиков: {followers_count}\n"
            f"📝 Подписок: {following_count}"
        )
        
        current_user_id = query.from_user.id
        is_following_user = is_following(current_user_id, user_id)
        follow_text = "✅ Отписаться" if is_following_user else "👤 Подписаться"
        follow_data = f"unfollow_{user_id}" if is_following_user else f"follow_{user_id}"
        
        keyboard = [
            [InlineKeyboardButton(follow_text, callback_data=follow_data),
             InlineKeyboardButton("🎨 Галерея", callback_data=f'view_user_gallery_{user_id}'),
             InlineKeyboardButton("🚫 Жалоба", callback_data=f'report_profile_{user_id}')],
            [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            if avatar_file_id:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=avatar_file_id,
                    caption=profile_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=profile_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logging.error(f"Ошибка при отправке профиля: {e}")
            await query.answer("❌ Ошибка при загрузке профиля", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка в show_other_user_profile: {e}")
        try:
            if 'query' in locals() and query:
                await query.answer(f"❌ Ошибка при загрузке профиля: {str(e)[:50]}", show_alert=True)
        except:
            pass

async def show_my_profile_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает мой профиль с аватаром и статистикой"""
    user_id = update.effective_user.id
    profile = get_user_profile(user_id)
    
    if not profile:
        await update.message.reply_text("❌ Профиль не найден")
        return
    
    nickname = profile[2] or "Не указан"
    bio = profile[3] or "Не указано"
    avatar_file_id = profile[4]
    followers_count = get_followers_count(user_id)
    following_count = get_following_count(user_id)
    art_count = get_user_art_count(user_id)
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT SUM(likes), SUM(dislikes) FROM arts WHERE owner_id = ?', (user_id,))
    result = cur.fetchone()
    total_likes = result[0] or 0
    total_dislikes = result[1] or 0
    conn.close()
    
    profile_text = f"👤 **Мой профиль**\n\n"
    
    if nickname and nickname != "Не указан":
        profile_text += f"{escape_markdown(nickname)}\n\n"
    
    if bio and bio != "Не указано":
        profile_text += f"{escape_markdown(bio)}\n\n"
    
    profile_text += (
        f"📊 **Статистика:**\n"
        f"🎨 Артов: {art_count}\n"
        f"❤️ Лайков: {total_likes}\n"
        f"👥 Подписчиков: {followers_count}\n"
        f"📝 Подписок: {following_count}"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎨 Галерея", callback_data='my_gallery'),
         InlineKeyboardButton("⚙️ Настройки", callback_data='my_profile_settings_menu'),
         InlineKeyboardButton("🔙 Меню", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query = update.callback_query
    
    if query:
        if avatar_file_id:
            try:
                await query.message.edit_media(
                    media=InputMediaPhoto(media=avatar_file_id, caption=profile_text, parse_mode='Markdown'),
                    reply_markup=reply_markup
                )
            except telegram.error.BadRequest as e:
                if "Message can't be edited" in str(e) or "Message to edit not found" in str(e):
                    try:
                        await query.message.delete()
                    except:
                        pass
                    await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=avatar_file_id,
                            caption=profile_text,
                            reply_markup=reply_markup,
                            parse_mode='Markdown'
                        )
                else:
                    try:
                        await query.message.delete()
                    except:
                        pass
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=avatar_file_id,
                        caption=profile_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
            except Exception as e:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=avatar_file_id,
                    caption=profile_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        else:
            try:
                await query.edit_message_text(
                    profile_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            except telegram.error.BadRequest as e:
                if "Message can't be edited" in str(e) or "Message to edit not found" in str(e):
                    try:
                        await query.message.delete()
                    except:
                        pass
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=profile_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                else:
                    try:
                        await query.message.delete()
                    except:
                        pass
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=profile_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
            except Exception as e:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=profile_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
    else:
        if avatar_file_id:
            await update.message.reply_photo(
                photo=avatar_file_id,
                caption=profile_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                profile_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
async def show_my_profile_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню настроек профиля"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("✏️ Изменить профиль", callback_data='edit_profile_options'),
         InlineKeyboardButton("👁️ Приватность", callback_data='edit_privacy_menu')],
        [InlineKeyboardButton("🔙 В профиль", callback_data='my_profile')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            "⚙️ **Настройки профиля**\n\n"
            "Выберите что вы хотите изменить:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except:
        try:
            await query.edit_message_caption(
                caption="⚙️ **Настройки профиля**\n\n"
                "Выберите что вы хотите изменить:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚙️ **Настройки профиля**\n\n"
                "Выберите что вы хотите изменить:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

async def show_followers(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int = 0):
    """Показывает список подписчиков пользователя с возможностью пролистывания"""
    user_id = update.effective_user.id
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT u.user_id, u.username, u.nickname
        FROM profile_followers pf
        JOIN users u ON pf.follower_id = u.user_id
        WHERE pf.following_id = ?
        ORDER BY pf.timestamp DESC
    ''', (user_id,))
    followers = cur.fetchall()
    conn.close()
    
    if not followers:
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                "👥 **Подписчики**\n\n"
                "У вас пока нет подписчиков",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "👥 **Подписчики**\n\n"
                "У вас пока нет подписчиков",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        return
    
    if index >= len(followers):
        index = len(followers) - 1
    
    follower_id, follower_username, follower_nickname = followers[index]
    profile = get_user_profile(follower_id)
    display_name = follower_nickname or follower_username or "Пользователь"
    
    text = (
        f"👤 **{escape_markdown(display_name)}**\n\n"
    )
    
    if profile:
        bio = profile[3]
        if bio and bio != "Не указано":
            text += f"📝 {escape_markdown(bio)}\n\n"
        
        followers_count = get_followers_count(follower_id)
        art_count = get_user_art_count(follower_id)
        text += f"👥 Подписчиков: {followers_count}\n"
        text += f"🎨 Артов: {art_count}\n"
    is_following_flag = is_following(user_id, follower_id)
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f'followers_prev_{index-1}'))
    
    nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(followers)}", callback_data='followers_count'))
    
    if index < len(followers) - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f'followers_next_{index+1}'))
    
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("👤 Посмотреть профиль", callback_data=f'view_profile_{follower_id}')])
    keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    avatar_file_id = profile[4] if profile else None
    
    if update.callback_query:
        try:
            if avatar_file_id:
                await update.callback_query.message.delete()
                await context.bot.send_photo(
                    chat_id=update.callback_query.message.chat_id,
                    photo=avatar_file_id,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await update.callback_query.edit_message_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logging.error(f"Ошибка при показе подписчика: {e}")
            try:
                await update.callback_query.edit_message_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            except:
                pass
    else:
        if avatar_file_id:
            await context.bot.send_photo(
                chat_id=update.message.chat_id,
                photo=avatar_file_id,
                caption=text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

async def show_edit_profile_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает опции редактирования профиля"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("🖼️ Изменить аватар", callback_data='edit_avatar')],
        [InlineKeyboardButton("✏️ Изменить ник", callback_data='edit_nickname')],
        [InlineKeyboardButton("📝 Изменить о себе", callback_data='edit_bio')],
        [InlineKeyboardButton("🔙 Назад", callback_data='my_profile_settings_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(
            "✏️ **Изменить профиль**\n\n"
            "Выберите что вы хотите изменить:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except:
        try:
            await query.edit_message_caption(
                caption="✏️ **Изменить профиль**\n\n"
                "Выберите что вы хотите изменить:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="✏️ **Изменить профиль**\n\n"
                "Выберите что вы хотите изменить:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

async def show_edit_privacy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню приватности"""
    query = update.callback_query
    user_id = query.from_user.id
    profile = get_user_profile(user_id)
    is_public = profile[5] if profile else True
    
    status_text = "🔓 ОТКРЫТ (все могут видеть профиль)" if is_public else "🔒 ЗАКРЫТ (профиль скрыт)"
    toggle_text = "🔒 Закрыть профиль" if is_public else "🔓 Открыть профиль"
    
    keyboard = [
        [InlineKeyboardButton(toggle_text, callback_data='toggle_profile_privacy')],
        [InlineKeyboardButton("🔙 Назад", callback_data='my_profile_settings_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    privacy_info = (
        f"👁️ **Настройки приватности**\n\n"
        f"Текущий статус: {status_text}\n\n"
        f"🔓 **ОТКРЫТ:**\n"
        f"• Другие пользователи видят ваш профиль\n"
        f"• Видна статистика артов\n"
        f"• Видна галерея\n\n"
        f"🔒 **ЗАКРЫТ:**\n"
        f"• Профиль скрыт от других\n"
        f"• Невозможно подписаться\n"
    )
    
    try:
        await query.edit_message_text(
            privacy_info,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except:
        try:
            await query.edit_message_caption(
                caption=privacy_info,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=privacy_info,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
async def show_top_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора типа топа"""
    query = update.callback_query
    
    keyboard = [
        [InlineKeyboardButton("❤️ Топ по лайкам", callback_data='top_arts_likes')],
        [InlineKeyboardButton("👥 Топ художников по подписчикам", callback_data='top_artists_followers')],
        [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🏆 **Выберите тип топа:**\n\n"
        "❤️ - Самые популярные арты по количеству лайков\n"
        "👥 - Художники с наибольшим количеством подписчиков",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_top_arts(update: Update, context: ContextTypes.DEFAULT_TYPE, hashtag_filter=None, top_type='likes'):
    """Показывает топ артов по лайкам"""
    user_id = update.callback_query.from_user.id
    username = update.callback_query.from_user.username or update.callback_query.from_user.first_name
    
    top_arts = get_top_arts_by_likes(5, hashtag_filter)
    
    if not top_arts:
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]]
        if hashtag_filter:
            keyboard.insert(0, [InlineKeyboardButton("🔍 Сбросить фильтр", callback_data='top_arts')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        filter_text = f" с хэштегом {hashtag_filter}" if hashtag_filter else ""
        
        await update.callback_query.edit_message_text(
            f"🏆 **Топ артов по лайкам{filter_text}**\n\n"
            f"Пока нет артов{filter_text} для отображения в топе.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    user_rank = get_user_rank(user_id, hashtag_filter)
    
    context.user_data['top_arts'] = top_arts
    context.user_data['current_top_index'] = 0
    context.user_data['top_user_id'] = user_id
    context.user_data['top_username'] = username
    context.user_data['user_rank'] = user_rank
    context.user_data['top_hashtag_filter'] = hashtag_filter
    context.user_data['top_type'] = 'likes'
    
    await show_top_art_page(update, context, 0)

async def show_top_artists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает топ художников по подписчикам"""
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    
    top_artists = get_top_artists_by_followers(5)
    
    if not top_artists:
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(
                "🏆 **Топ художников по подписчикам**\n\n"
                "Пока нет художников в этом рейтинге.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "🏆 **Топ художников по подписчикам**\n\n"
                "Пока нет художников в этом рейтинге.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        return
    
    context.user_data['top_artists'] = top_artists
    context.user_data['current_top_index'] = 0
    context.user_data['top_type'] = 'followers'
    
    await show_top_artist_page(update, context, 0)

async def show_top_artist_page(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int):
    """Показывает страницу топа художников"""
    query = update.callback_query
    top_artists = context.user_data.get('top_artists', [])
    
    if not top_artists or index >= len(top_artists):
        return
    
    artist = top_artists[index]
    user_id_result = artist[0]
    username = artist[1]
    nickname = artist[2]
    followers_count = artist[3]
    art_count = artist[4]
    total_likes = artist[5]
    bio = artist[6] if len(artist) > 6 else None
    avatar_file_id = artist[7] if len(artist) > 7 else None
    
    current_user_id = query.from_user.id if query else update.effective_user.id
    is_following_flag = is_following(current_user_id, user_id_result)
    
    display_name = nickname or username or "Пользователь"
    safe_name = escape_markdown(display_name)
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    medal = medals[index] if index < len(medals) else f"{index+1}."
    
    top_text = (
        f"🏆 **Топ художников по подписчикам**\n\n"
        f"{medal} **Место #{index + 1}**\n"
        f"👤 **Имя:** {safe_name}\n"
    )
    
    if bio and bio != "Не указано":
        top_text += f"📝 {escape_markdown(bio)}\n\n"
    
    top_text += (
        f"👥 **Подписчиков:** {followers_count}\n"
        f"🎨 **Артов:** {art_count}\n"
        f"❤️ **Всего лайков:** {total_likes or 0}\n"
    )
    
    keyboard = []
    
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f'top_prev_{index-1}'))
    
    nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(top_artists)}", callback_data='top_stats'))
    
    if index < len(top_artists) - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f'top_next_{index+1}'))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    if user_id_result != current_user_id:
        keyboard.append([
            InlineKeyboardButton("👤 Просмотреть профиль", callback_data=f'view_profile_{user_id_result}'),
            InlineKeyboardButton("🚫 Жалоба на профиль", callback_data=f'report_profile_{user_id_result}')
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        try:
            if avatar_file_id:
                await query.message.delete()
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=avatar_file_id,
                    caption=top_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    top_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logging.error(f"Ошибка при редактировании сообщения топа художников: {e}")
            if avatar_file_id:
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=avatar_file_id,
                    caption=top_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=top_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
    else:
        if avatar_file_id:
            await update.message.reply_photo(
                photo=avatar_file_id,
                caption=top_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                top_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )


async def show_top_art_page(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int):
    query = update.callback_query
    top_arts = context.user_data.get('top_arts', [])
    user_id = context.user_data.get('top_user_id')
    username = context.user_data.get('top_username', 'Пользователь')
    user_rank = context.user_data.get('user_rank')
    hashtag_filter = context.user_data.get('top_hashtag_filter')
    
    if not top_arts or index >= len(top_arts):
        return
    
    art_id, file_id, caption, likes, dislikes, owner_id = top_arts[index]

    owner_profile = get_user_profile(owner_id)
    is_owner_profile_public = owner_profile[5] if owner_profile else False
    
    owner_display_name = get_display_name(owner_id, for_moderator=False, profile_is_public=is_owner_profile_public)
    
    hashtags = get_art_hashtags(art_id)
    hashtags_text = " ".join(hashtags) if hashtags else ""
    
    safe_owner_display_name = escape_markdown(owner_display_name)
    safe_caption = escape_markdown(caption) if caption else ""
    safe_hashtags_text = escape_markdown(hashtags_text) if hashtags_text else ""
    safe_filter_text = escape_markdown(hashtag_filter) if hashtag_filter else ""
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    medal = medals[index] if index < len(medals) else f"{index+1}."
    
    filter_text = f" (фильтр: {safe_filter_text})" if hashtag_filter else ""
    top_text = (
        f"🏆 **Глобальный топ артов по лайкам{filter_text}**\n\n"
    )
    
    if user_rank:
        top_text += f"👤 **Ваше лучшее место в рейтинге:** #{user_rank}\n\n"
    else:
        top_text += "👤 **У вас пока нет артов в рейтинге**\n\n"
    
    top_text += (
        f"{medal} **Место #{index + 1}**\n"
        f"❤️ **Лайков:** {likes}\n"
        f"👤 **Автор:** {safe_owner_display_name}\n"
    )
    
    if safe_hashtags_text:
        top_text += f"🏷️ **Хэштеги:** {safe_hashtags_text}\n"
    
    if safe_caption:
        top_text += f"📝 **Описание:** {safe_caption}\n"
    
    keyboard = []
    
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f'top_prev_{index-1}'))
    
    nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(top_arts)}", callback_data='top_stats'))
    
    if index < len(top_arts) - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f'top_next_{index+1}'))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    current_user_id = query.from_user.id if query else update.effective_user.id
    
    if current_user_id and owner_id != current_user_id:
        owner_profile = get_user_profile(owner_id)
        if owner_profile and owner_profile[5]:
            keyboard.append([
                InlineKeyboardButton("👤 Профиль автора", callback_data=f'view_profile_{owner_id}'),
                InlineKeyboardButton("🚫 Жалоба на арт", callback_data=f'complaint_{art_id}')
            ])
    if hashtag_filter:
        keyboard.append([InlineKeyboardButton("🔍 Сбросить фильтр", callback_data='top_arts')])
    
    keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        try:
            await query.message.edit_media(
                media=InputMediaPhoto(media=file_id, caption=top_text, parse_mode='Markdown'),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.error(f"Ошибка при редактировании сообщения топа: {e}")
            try:
                await query.message.delete()
            except Exception as e2:
                logging.error(f"Ошибка при удалении сообщения топа: {e2}")
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=file_id,
                caption=top_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    else:
        await update.message.reply_photo(
            photo=file_id,
            caption=top_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def show_complaint_reasons(update: Update, context: ContextTypes.DEFAULT_TYPE, art_id: int):
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for i, reason in enumerate(COMPLAINT_REASONS):
        keyboard.append([InlineKeyboardButton(reason, callback_data=f'complaint_reason_{art_id}_{i}')])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f'cancel_complaint_{art_id}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🚫 **Пожаловаться на арт**\n\n"
             "Пожалуйста, выберите причину жалобы:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def send_complaint_to_support(context, art_id, reporter_id, reason, comment, reporter_username):
    art = get_art_by_id(art_id)
    if not art:
        return False
    
    art_owner_id = get_art_owner(art_id)
    
    owner_display_name = get_display_name(art_owner_id, for_moderator=True)
    reporter_display_name = get_display_name(reporter_id, for_moderator=True)
    
    hashtags = get_art_hashtags(art_id) 
    hashtags_text = " ".join(hashtags) if hashtags else "Нет хэштегов"
    safe_owner_name = escape_markdown(owner_display_name)
    safe_reporter_name = escape_markdown(reporter_display_name)
    safe_reason = escape_markdown(reason)
    safe_comment = escape_markdown(comment)
    safe_hashtags = escape_markdown(hashtags_text)
    safe_caption = escape_markdown(art[2]) if art[2] else "Нет описания"
    
    complaint_text = (
        f"🚫 Поступила жалоба на арт\n\n"
        f"🆔 ID арта: {art_id}\n"
        f"👤 Автор: {safe_owner_name} (ID: {art_owner_id})\n"
        f"📢 Жалобу отправил: {safe_reporter_name} (ID: {reporter_id})\n"
        f"📋 Причина: {safe_reason}\n"
        f"💬 Комментарий: {safe_comment}\n"
        f"📝 Описание арта: {safe_caption}\n"
        f"🏷️ Хэштеги: {safe_hashtags}\n\n"
        f"📊 Статистика арта: Лайков: {art[3]}, Дизлайков: {art[4]}"
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑️ Удалить арт", callback_data=f'delete_complaint_{art_id}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_messages = []
    for support_id in SUPPORT_USER_IDS:
        try:
            await context.bot.send_photo(
                chat_id=support_id,
                photo=art[1],
                caption=complaint_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            sent_messages.append(support_id)
            logging.info(f"Жалоба отправлена модератору {support_id}")
        except Exception as e:
            logging.error(f"Ошибка при отправке жалобы модератору {support_id}: {e}")
    
    return len(sent_messages) > 0
    
async def send_profile_complaint_to_support(context, profile_user_id, reporter_id, reason, reporter_username):
    """Отправляет жалобу на профиль модераторам с возможностью блокировки"""
    profile = get_user_profile(profile_user_id)
    if not profile:
        return False
    
    nickname = profile[2] or "Не указан"
    username = profile[1] or "Не указан"
    avatar_file_id = profile[4]
    followers_count = get_followers_count(profile_user_id)
    art_count = get_user_art_count(profile_user_id)
    
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('SELECT SUM(likes), SUM(dislikes) FROM arts WHERE owner_id = ?', (profile_user_id,))
    result = cur.fetchone()
    total_likes = result[0] or 0
    total_dislikes = result[1] or 0
    conn.close()
    
    reporter_display_name = get_display_name(reporter_id, for_moderator=True)
    
    safe_nickname = escape_markdown(nickname)
    safe_username = escape_markdown(username)
    safe_reporter_name = escape_markdown(reporter_display_name)
    safe_reason = escape_markdown(reason)
    
    complaint_text = (
        f"🚫 **Жалоба на профиль**\n\n"
        f"👤 Профиль: {safe_nickname} (@{safe_username})\n"
        f"🆔 ID: {profile_user_id}\n"
        f"📢 Жалобу отправил: {safe_reporter_name} (ID: {reporter_id})\n"
        f"📋 Причина: {safe_reason}\n\n"
        f"📊 **Статистика профиля:**\n"
        f"🎨 Артов: {art_count}\n"
        f"❤️ Всего лайков: {total_likes}\n"
        f"👥 Подписчиков: {followers_count}"
    )
    
    keyboard = [
        [InlineKeyboardButton("🚫 Заблокировать профиль", callback_data=f'block_profile_{profile_user_id}')],
        [InlineKeyboardButton("👁️ Просмотреть профиль", callback_data=f'view_profile_complaint_{profile_user_id}')],
        [InlineKeyboardButton("❌ Отклонить жалобу", callback_data=f'dismiss_profile_complaint_{profile_user_id}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_messages = []
    for support_id in SUPPORT_USER_IDS:
        try:
            if avatar_file_id:
                await context.bot.send_photo(
                    chat_id=support_id,
                    photo=avatar_file_id,
                    caption=complaint_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=support_id,
                    text=complaint_text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            sent_messages.append(support_id)
            logging.info(f"Жалоба на профиль отправлена модератору {support_id}")
        except Exception as e:
            logging.error(f"Ошибка при отправке жалобы на профиль модератору {support_id}: {e}")
    
    return len(sent_messages) > 0

# ========== МЕНЮ ДЛЯ ЗАБЛОКИРОВАННЫХ ПОЛЬЗОВАТЕЛЕЙ ==========

async def show_blocked_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню для заблокированного пользователя"""
    user = update.effective_user
    block_info = get_user_block_status(user.id)
    
    if not block_info:
        return
    
    block_id, blocked_at, reason, appeal_status = block_info
    
    blocked_text = (
        f"🚫 **Ваш профиль заблокирован**\n\n"
        f"📋 Причина: {escape_markdown(reason)}\n"
        f"⏰ Дата блокировки: {blocked_at}\n\n"
    )
    
    # Получаем информацию об апелляции
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT appeal_id, reason, status, submitted_at 
        FROM appeals 
        WHERE user_id = ? 
        ORDER BY submitted_at DESC 
        LIMIT 1
    ''', (user.id,))
    appeal_info = cur.fetchone()
    conn.close()
    
    if appeal_info:
        appeal_id, appeal_reason, appeal_status_db, submitted_at = appeal_info
        
        if appeal_status_db == 'pending':
            blocked_text += "📝 Ваша апелляция на рассмотрении...\n\n"
            keyboard = [
                [InlineKeyboardButton("📝 Просмотреть апелляцию", callback_data='view_my_appeal')],
                [InlineKeyboardButton("✏️ Редактировать апелляцию", callback_data='edit_appeal')]
            ]
        elif appeal_status_db == 'approved':
            blocked_text += "✅ Ваша апелляция одобрена! Профиль восстановлен.\n\n"
            keyboard = [[InlineKeyboardButton("🔄 Перезагрузить", callback_data='start_menu')]]
        else:
            blocked_text += "❌ Ваша апелляция отклонена.\n\n"
            keyboard = [
                [InlineKeyboardButton("📝 Просмотреть апелляцию", callback_data='view_my_appeal')],
                [InlineKeyboardButton("📝 Подать новую апелляцию", callback_data='submit_appeal')]
            ]
    else:
        blocked_text += "📝 Вы можете подать апелляцию на блокировку.\n\n"
        keyboard = [
            [InlineKeyboardButton("📝 Подать апелляцию", callback_data='submit_appeal')],
            [InlineKeyboardButton("📞 Поддержка", callback_data='support_info')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            blocked_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            blocked_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def show_my_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает апелляцию пользователя"""
    user = update.effective_user
    
    # Получаем последнюю апелляцию
    conn = sqlite3.connect('database.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT appeal_id, reason, status, submitted_at 
        FROM appeals 
        WHERE user_id = ? 
        ORDER BY submitted_at DESC 
        LIMIT 1
    ''', (user.id,))
    appeal_info = cur.fetchone()
    conn.close()
    
    if not appeal_info:
        await update.callback_query.answer("❌ Апелляция не найдена", show_alert=True)
        return
    
    appeal_id, reason, status, submitted_at = appeal_info
    
    appeal_text = (
        f"📝 **Ваша апелляция**\n\n"
        f"📅 Отправлена: {submitted_at}\n"
        f"📌 Статус: "
    )
    
    if status == 'pending':
        appeal_text += "⏳ На рассмотрении\n\n"
    elif status == 'approved':
        appeal_text += "✅ Одобрена\n\n"
    else:
        appeal_text += "❌ Отклонена\n\n"
    
    appeal_text += f"**Текст апелляции:**\n{escape_markdown(reason)}\n\n"
    
    # Кнопки зависят от статуса
    if status == 'pending':
        keyboard = [
            [InlineKeyboardButton("✏️ Редактировать апелляцию", callback_data='edit_appeal')],
            [InlineKeyboardButton("🔙 Назад", callback_data='view_blocked_menu')]
        ]
    elif status == 'rejected':
        keyboard = [
            [InlineKeyboardButton("📝 Подать новую апелляцию", callback_data='submit_appeal')],
            [InlineKeyboardButton("🔙 Назад", callback_data='view_blocked_menu')]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data='view_blocked_menu')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            appeal_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            appeal_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username)
    
    context.user_data.clear()
    
    # Проверяем блокировку пользователя
    if is_user_blocked(user.id):
        # Показываем меню для заблокированного пользователя
        await show_blocked_user_menu(update, context)
        return
    
    keyboard = [
        [
            InlineKeyboardButton("🎨 Загрузить арт", callback_data='upload_art'),
            InlineKeyboardButton("👀 Смотреть арты", callback_data='view_arts')
        ],
        [
            InlineKeyboardButton("👤 Профиль", callback_data='my_profile'),
            InlineKeyboardButton("🏆 Топ", callback_data='top_arts')
        ],
        [
            InlineKeyboardButton("🔍 Поиск", callback_data='search_menu')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"Привет, {user.first_name}! Добро пожаловать в арт-сообщество!\n\n"
            "Здесь ты можешь делиться своими работами и оценивать творчество других.",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            f"Привет, {user.first_name}! Добро пожаловать в арт-сообщество!\n\n"
            "Здесь ты можешь делиться своими работами и оценивать творчество других.",
            reply_markup=reply_markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (TimedOut, NetworkError) as e:
        logging.warning(f"Ошибка подключения при ответе на кнопку: {e}. Продолжаем выполнение.")
    except telegram.error.BadRequest:
        logging.info("Query is too old, ignoring answer and continuing execution.")
    except Exception as e:
        logging.error(f"Неожиданная ошибка при ответе на кнопку: {e}")
    
    user_id = query.from_user.id
    data = query.data
    
    if data == 'upload_art':
        art_count = get_user_art_count(user_id)
        if art_count >= MAX_ARTS_PER_USER:
            await query.edit_message_text(
                f"❌ Лимит артов достигнут!\n\n"
                f"У вас {art_count}/{MAX_ARTS_PER_USER} артов.\n"
                f"Удалите некоторые арты в профиле чтобы загрузить новые.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]])
            )
            return
        
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📤 Отправь мне свое изображение с подписью или без.\n\n"
            "⚠️ Все изображения проверяются автоматически на недопустимый контент.",
            reply_markup=reply_markup
        )
        context.user_data['waiting_for_art'] = True
    
    elif data.startswith('view_art_'):
        try:
            art_id = int(data.split('_')[2])
            art = get_art_by_id(art_id)
            if art:
                art_id, file_id, caption, likes, dislikes = art
                hashtags = get_art_hashtags(art_id)
                hashtags_text = " ".join(hashtags) if hashtags else ""
                
                text = f"📊 **Статистика вашего арта:**\n❤️ Лайков: {likes} | 👎 Дизлайков: {dislikes}"
                if caption:
                    text = f"{caption}\n\n{text}"
                if hashtags_text:
                    text = f"{text}\n\n🏷️ Хэштеги: {hashtags_text}"
                
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=file_id,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logging.error(f"Ошибка при показе арта: {e}")
            await query.answer("❌ Ошибка при загрузке арта", show_alert=True)
        
    elif data == 'view_arts':
        context.user_data['last_art_message'] = query.message
        success = await send_art_to_user(query.message.chat_id, context, user_id, update_message=None)

    elif data == 'hashtag_search':
        context.user_data['waiting_for_hashtag_search'] = True
            
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_hashtag_search')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔍 **Поиск по хэштегам**\n\n"
            "Введите хэштег или часть хэштега для поиска:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'cancel_hashtag_search':
        context.user_data['waiting_for_hashtag_search'] = False
        await start(update, context)
    
    elif data.startswith('filter_'):
        hashtag = data.replace('filter_', '')
        context.user_data['current_hashtag_filter'] = hashtag
        
        context.user_data['last_art_message'] = query.message
        success = await send_art_to_user(query.message.chat_id, context, user_id, update_message=None, hashtag_filter=hashtag)
        if not success:
            await query.edit_message_text(f"Нет артов с хэштегом {hashtag}! Попробуйте другой хэштег.")
    
    elif data == 'my_profile':
        await show_my_profile_settings(update, context)
    
    elif data == 'my_profile_settings_menu':
        await show_my_profile_settings_menu(update, context)
    
    elif data == 'edit_profile_options':
        await show_edit_profile_options(update, context)
    
    elif data == 'edit_privacy_menu':
        await show_edit_privacy_menu(update, context)
    
    elif data == 'search_menu':
        await show_search_menu(update, context)
    
    elif data == 'search_hashtags':
        context.user_data['waiting_for_hashtag_search'] = True
            
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_hashtag_search')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔍 **Поиск по хэштегам**\n\n"
            "Введите хэштег или часть хэштега для поиска:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'search_profiles':
        context.user_data['waiting_for_profile_search'] = True
        
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_profile_search')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👤 **Поиск профилей**\n\n"
            "Введите ник или юзернейм для поиска:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'cancel_profile_search':
        context.user_data['waiting_for_profile_search'] = False
        await start(update, context)
    
    elif data.startswith('follow_'):
        try:
            following_id = int(data.split('_')[1])
            success, message = follow_user(user_id, following_id)
            if success:
                await query.answer(message, show_alert=True)
                await notify_about_follower(context, following_id)
                await show_other_user_profile(update, context, following_id)
            else:
                await query.answer(message, show_alert=True)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при обработке подписки: {e}")
            await query.answer("❌ Ошибка при подписке", show_alert=True)
    
    elif data.startswith('unfollow_'):
        try:
            following_id = int(data.split('_')[1])
            success, message = unfollow_user(user_id, following_id)
            if success:
                await query.answer(message, show_alert=True)
                await show_other_user_profile(update, context, following_id)
            else:
                await query.answer(message, show_alert=True)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при обработке отписки: {e}")
            await query.answer("❌ Ошибка при отписке", show_alert=True)
    
    elif data.startswith('view_user_gallery_'):
        try:
            profile_user_id = int(data.split('_')[3])
            await show_user_gallery(update, context, profile_user_id, is_my_gallery=False)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при открытии галереи: {e}")
    
    elif data == 'my_gallery':
        await show_user_gallery(update, context, user_id, is_my_gallery=True)
    
    elif data.startswith('gallery_prev_'):
        try:
            index = int(data.split('_')[2])
            await show_gallery_page(update, context, index)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при навигации в галерее: {e}")
    
    elif data.startswith('gallery_next_'):
        try:
            index = int(data.split('_')[2])
            await show_gallery_page(update, context, index)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при навигации в галерее: {e}")
    
    elif data == 'gallery_info':
        index = context.user_data.get('gallery_current_index', 0)
        arts = context.user_data.get('gallery_arts', [])
        if arts:
            await query.answer(f"Арт {index + 1} из {len(arts)}", show_alert=False)
    
    elif data.startswith('gallery_delete_'):
        try:
            art_id = int(data.split('_')[2])
            user_id = query.from_user.id
            
            conn = sqlite3.connect('database.db')
            cur = conn.cursor()
            cur.execute('SELECT owner_id FROM arts WHERE art_id = ?', (art_id,))
            result = cur.fetchone()
            conn.close()
            
            if not result:
                await query.answer("❌ Арт не найден", show_alert=True)
                return
            
            owner_id = result[0]
            if owner_id != user_id:
                await query.answer("❌ Вы не можете удалить чужой арт", show_alert=True)
                return
            
            delete_result = delete_art_by_id(art_id)
            success = delete_result[0]
            message = delete_result[1]
            
            if success:
                try:
                    await update_art_message_realtime(context, art_id)
                except Exception as e:
                    logging.error(f"Ошибка при обновлении активных сообщений: {e}")
                
                arts = context.user_data.get('gallery_arts', [])
                arts = [art for art in arts if art[0] != art_id]
                context.user_data['gallery_arts'] = arts
                
                if arts:
                    index = context.user_data.get('gallery_current_index', 0)
                    if index >= len(arts):
                        index = len(arts) - 1
                    await show_gallery_page(update, context, index)
                    await query.answer("✅ Арт удален", show_alert=True)
                else:
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="📭 Галерея пуста. Все ваши артов удалены."
                    )
            else:
                await query.answer(f"❌ {message}", show_alert=True)
        except Exception as e:
            logging.error(f"Ошибка при удалении артa: {e}")
            await query.answer("❌ Ошибка при удалении", show_alert=True)
    
    elif data.startswith('back_to_user_profile_'):
        try:
            profile_user_id = int(data.split('_')[4])
            if profile_user_id == user_id:
                await show_my_profile_settings(update, context)
            else:
                await show_other_user_profile(update, context, profile_user_id)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при возврате к профилю: {e}")
    
    elif data.startswith('report_profile_'):
        try:
            profile_user_id = int(data.split('_')[2])
            context.user_data['report_profile_id'] = profile_user_id
            context.user_data['waiting_for_profile_report'] = True
        
            top_type = context.user_data.get('top_type')
            if top_type == 'followers':
                context.user_data['report_from_top_followers'] = True
                context.user_data['report_top_index'] = context.user_data.get('current_top_index', 0)
        
            keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f'cancel_report_profile_{profile_user_id}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="🚫 **Пожаловаться на профиль**\n\n"
                 "Пожалуйста, напишите причину жалобы:\n\n"
                 "Примеры: спам, оскорбления, неприемлемый контент",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при жалобе на профиль: {e}")

    elif data == 'edit_nickname':
        context.user_data['waiting_for_nickname_edit'] = True
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_edit_nickname')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                "✏️ **Введите новый ник** (макс. 30 символов):",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except:
            try:
                await query.edit_message_caption(
                    caption="✏️ **Введите новый ник** (макс. 30 символов):",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            except:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="✏️ **Введите новый ник** (макс. 30 символов):",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
    
    elif data == 'edit_bio':
        context.user_data['waiting_for_bio_edit'] = True
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_edit_bio')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                "✏️ **Введите описание о себе** (макс. 500 символов):\n\n"
                "Можно добавить ссылку на Telegram: @username",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except:
            try:
                await query.edit_message_caption(
                    caption="✏️ **Введите описание о себе** (макс. 500 символов):\n\n"
                    "Можно добавить ссылку на Telegram: @username",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            except:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="✏️ **Введите описание о себе** (макс. 500 символов):\n\n"
                    "Можно добавить ссылку на Telegram: @username",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
    
    elif data == 'edit_avatar':
        context.user_data['waiting_for_avatar_edit'] = True
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_edit_avatar')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                "🖼️ **Отправьте новое фото для аватара профиля**\n\n"
                "⚠️ Фото будет проверено на запрещенный контент",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except:
            try:
                await query.edit_message_caption(
                    caption="🖼️ **Отправьте новое фото для аватара профиля**\n\n"
                    "⚠️ Фото будет проверено на запрещенный контент",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            except:
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="🖼️ **Отправьте новое фото для аватара профиля**\n\n"
                    "⚠️ Фото будет проверено на запрещенный контент",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
    
    elif data == 'cancel_edit_nickname':
        context.user_data['waiting_for_nickname_edit'] = False
        await show_edit_profile_options(update, context)
    
    elif data == 'cancel_edit_bio':
        context.user_data['waiting_for_bio_edit'] = False
        await show_edit_profile_options(update, context)
    
    elif data == 'cancel_edit_avatar':
        context.user_data['waiting_for_avatar_edit'] = False
        await show_edit_profile_options(update, context)
    
    elif data == 'toggle_profile_privacy':
        success, message = toggle_profile_privacy(user_id)
        if success:
            await query.answer(message, show_alert=True)
            await show_edit_privacy_menu(update, context)
        else:
            await query.answer(message, show_alert=True)
    
    elif data.startswith('view_art_author_'):
        try:
            parts = data.split('_')
            if len(parts) >= 4:
                author_id = int(parts[3])
                await show_other_user_profile(update, context, author_id)
            else:
                await query.answer("❌ Ошибка при открытии профиля", show_alert=True)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при открытии профиля автора: {e}")
            await query.answer("❌ Ошибка при открытии профиля", show_alert=True)
    
    elif data.startswith('view_profile_complaint_'):
        try:
            profile_user_id = int(data.split('_')[3])
            await show_other_user_profile(update, context, profile_user_id)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при открытии профиля из жалобы: {e}")
            await query.answer("❌ Ошибка при открытии профиля", show_alert=True)
    
    elif data.startswith('view_profile_'):
        try:
            profile_user_id = int(data.split('_')[2])
            context.user_data.clear()
            try:
                await query.message.delete()
            except:
                pass
            
            await show_other_user_profile(update, context, profile_user_id)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при открытии профиля: {e}")
            await query.answer("❌ Ошибка при открытии профиля", show_alert=True)

    elif data == 'hashtag_search':
        context.user_data['waiting_for_hashtag_search'] = True
            
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_hashtag_search')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔍 **Поиск по хэштегам**\n\n"
            "Введите хэштег или часть хэштега для поиска:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'toggle_privacy':
        current_settings = get_privacy_settings(user_id)
        new_hide_username = not current_settings['hide_username']
        
        set_privacy_settings(user_id, hide_username=new_hide_username)
        
        await show_edit_privacy_menu(update, context)
        
        status = "включена" if new_hide_username else "выключена"
        await query.answer(f"🔒 Приватность {status}!", show_alert=True)
    
    elif data == 'top_arts':
        await show_top_menu(update, context)
    
    elif data == 'top_arts_likes':
        await show_top_arts(update, context, top_type='likes')
    
    elif data == 'top_artists_followers':
        await show_top_artists(update, context)
    
    elif data.startswith('top_prev_'):
        try:
            index = int(data.split('_')[2])
            top_type = context.user_data.get('top_type', 'likes')
            if top_type == 'likes':
                await show_top_art_page(update, context, index)
            else:
                await show_top_artist_page(update, context, index)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при навигации в топе: {e}")
    
    elif data.startswith('top_next_'):
        try:
            index = int(data.split('_')[2])
            top_type = context.user_data.get('top_type', 'likes')
            if top_type == 'likes':
                await show_top_art_page(update, context, index)
            else:
                await show_top_artist_page(update, context, index)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при навигации в топе: {e}")
    
    elif data == 'top_stats':
        top_type = context.user_data.get('top_type', 'likes')
        index = context.user_data.get('current_top_index', 0)
        if top_type == 'likes':
            length = len(context.user_data.get('top_arts', []))
        else:
            length = len(context.user_data.get('top_artists', []))
        await query.answer(f"Место {index + 1} из {length}", show_alert=False)
    
    elif data == 'support_info':
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📞 **Служба поддержки**\n\n"
                 f"По всем вопросам и проблемам обращайтесь к @{SUPPORT_USERNAME}\n\n"
                 "Мы всегда готовы помочь!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back_to_profile')]]),
            parse_mode='Markdown'
        )
    
    elif data == 'back_to_profile':
        username = query.from_user.username or query.from_user.first_name
        
        try:
            await query.message.delete()
        except Exception as e:
            logging.error(f"Ошибка при удалении сообщения: {e}")
        
        await show_my_profile_settings(update, context)
    
    elif data.startswith('complaint_') and not data.startswith('complaint_reason_'):
        try:
            art_id = int(data.split('_')[1])
            await show_complaint_reasons(update, context, art_id)
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при обработке жалобы: {e}")
            await query.answer("❌ Ошибка при обработке жалобы", show_alert=True)
    
    elif data.startswith('complaint_reason_'):
        try:
            parts = data.split('_')
            if len(parts) >= 4:
                art_id = int(parts[2])
                reason_index = int(parts[3])
                reason = COMPLAINT_REASONS[reason_index]
                
                context.user_data['complaint_art_id'] = art_id
                context.user_data['complaint_reason'] = reason
                context.user_data['waiting_for_complaint_comment'] = True
                
                await query.message.delete()
                
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"🚫 **Пожаловаться на арт**\n\n"
                         f"Вы выбрали причину: {reason}\n\n"
                         "Пожалуйста, напишите дополнительный комментарий к жалобе "
                         "(или отправьте /skip чтобы пропустить):",
                    parse_mode='Markdown'
                )
            else:
                await query.answer("❌ Ошибка в данных жалобы", show_alert=True)
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при выборе причины жалобы: {e}, data: {data}")
            await query.answer("❌ Ошибка при выборе причины", show_alert=True)
    
    elif data.startswith('cancel_complaint_'):
        try:
            parts = data.split('_')
            if len(parts) >= 3:
                art_id = int(parts[2])
            
                try:
                    await query.message.delete()
                except:
                    pass
                top_type = context.user_data.get('top_type')
            
                if top_type == 'likes':
                    await show_top_art_page(update, context, context.user_data.get('current_top_index', 0))
                elif top_type == 'followers':
                    await show_top_artist_page(update, context, context.user_data.get('current_top_index', 0))
                else:
                    art = get_art_by_id(art_id)
                    if art:
                        current_hashtag = context.user_data.get('current_hashtag_filter')
                        await send_art_to_user(query.message.chat_id, context, user_id, art=art, update_message=None, hashtag_filter=current_hashtag)
                    else:
                        await context.bot.send_message(query.message.chat_id, "❌ Арт не найден")
            else:
                await query.answer("❌ Ошибка при отмене жалобы", show_alert=True)
            
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при отмене жалобы: {e}")
            await query.answer("❌ Ошибка при отмене жалобы", show_alert=True)
    elif data.startswith('cancel_report_profile_'):
        try:
            profile_user_id = int(data.split('_')[3])
        
            try:
                await query.message.delete()
            except:
                pass
            top_type = context.user_data.get('top_type')
        
            if top_type == 'followers':
                await show_top_artist_page(update, context, context.user_data.get('current_top_index', 0))
            else:
                await show_other_user_profile(update, context, profile_user_id)
            
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при отмене жалобы на профиль: {e}")
            await query.answer("❌ Ошибка при отмене жалобы", show_alert=True)

    elif data.startswith('delete_complaint_'):
        try:
            art_id = int(data.split('_')[2])
            
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав для удаления артов!", show_alert=True)
                return
            art_info = get_art_by_id(art_id)
            if not art_info:
                await query.answer("❌ Арт не найден!", show_alert=True)
                return
                
            owner_id = get_art_owner(art_id)
            file_id = art_info[1] if art_info else None
            caption = art_info[2] if art_info else None
            
            success, message = delete_art_by_id(art_id)
            
            if success:
                await query.answer("✅ Арт удален!", show_alert=True)
                
                try:
                    old_caption = query.message.caption or ""
                    await query.edit_message_caption(
                        caption=f"✅ **Арт удален модератором**\n\n{escape_markdown(old_caption)}",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logging.error(f"Ошибка при редактировании сообщения: {e}")
                
                if owner_id and file_id:
                    try:
                        await context.bot.send_message(
                            chat_id=owner_id,
                            text="🚫 Ваш арт был удален модератором по причине жалобы.\n\n"
                                 f"Если вы считаете, что это ошибка, свяжитесь с @{SUPPORT_USERNAME}"
                        )
                    except Exception as e:
                        logging.error(f"Ошибка при уведомлении владельца арта: {e}")
            else:
                await query.answer(f"❌ Ошибка при удалении: {message}", show_alert=True)
                
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при удалении арта по жалобе: {e}")
            await query.answer("❌ Ошибка при удалении арта", show_alert=True)
    
    elif data.startswith('view_complaint_'):
        try:
            art_id = int(data.split('_')[2])
            
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав для просмотра жалоб!", show_alert=True)
                return
            
            art = get_art_by_id(art_id)
            if art:
                hashtags = get_art_hashtags(art_id)
                hashtags_text = " ".join(hashtags) if hashtags else ""
                
                art_text = f"🖼️ **Арт #{art_id}**\n\nЛайков: {art[3]} | Дизлайков: {art[4]}"
                if hashtags_text:
                    art_text += f"\n🏷️ Хэштеги: {hashtags_text}"
                if art[2]:
                    art_text = f"{art[2]}\n\n{art_text}"
                
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=art[1],
                    caption=art_text,
                    parse_mode='Markdown'
                )
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при просмотре жалобы: {e}")
            await query.answer("❌ Ошибка при просмотре жалобы", show_alert=True)
    
    elif data.startswith('block_profile_'):
        try:
            profile_user_id = int(data.split('_')[2])
            
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав для блокировки профилей!", show_alert=True)
                return
            
            success, message = block_user(profile_user_id, "Блокировка модератором за жалобы", query.from_user.id)
            
            if success:
                await query.answer("✅ Профиль заблокирован!", show_alert=True)
                
                try:
                    old_caption = query.message.caption or ""
                    await query.edit_message_caption(
                        caption=f"✅ **Профиль заблокирован модератором**\n\n{escape_markdown(old_caption)}",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logging.error(f"Ошибка при редактировании сообщения: {e}")
                try:
                    await context.bot.send_message(
                        chat_id=profile_user_id,
                        text=f"🚫 **Ваш профиль был заблокирован модератором**\n\n"
                             f"📋 Причина: Блокировка модератором за жалобы\n"
                             f"📝 Вы можете подать апелляцию, нажав на кнопку 'Подать апелляцию' в меню.\n\n"
                             f"Если вы считаете, что это ошибка, свяжитесь с @{SUPPORT_USERNAME}"
                    )
                except Exception as e:
                    logging.error(f"Ошибка при уведомлении владельца профиля: {e}")
            else:
                await query.answer(f"❌ {message}", show_alert=True)
                
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при блокировке профиля: {e}")
            await query.answer("❌ Ошибка при блокировке профиля", show_alert=True)
    
    elif data.startswith('dismiss_profile_complaint_'):
        try:
            profile_user_id = int(data.split('_')[3])
            
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав для этого!", show_alert=True)
                return
            
            await query.answer("✅ Жалоба отклонена!", show_alert=True)
            
            try:
                await query.edit_message_caption(
                    caption="✅ **Жалоба отклонена модератором**",
                    parse_mode='Markdown'
                )
            except:
                try:
                    await query.edit_message_text(
                        text="✅ **Жалоба отклонена модератором**",
                        parse_mode='Markdown'
                    )
                except:
                    pass
                
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при отклонении жалобы: {e}")
            await query.answer("❌ Ошибка при отклонении жалобы", show_alert=True)
    
    elif data.startswith('deleted_arts_next_'):
        try:
            index = int(data.split('_')[3])
            deleted_arts = get_deleted_arts(limit=100)
            context.user_data['deleted_arts_list'] = deleted_arts
            await show_deleted_arts_gallery(update, context, index)
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при навигации: {e}")
            await query.answer("❌ Ошибка", show_alert=True)

    elif data.startswith('deleted_arts_prev_'):
        try:
            index = int(data.split('_')[3])
            if index < 0:
                index = 0
            deleted_arts = get_deleted_arts(limit=100)
            context.user_data['deleted_arts_list'] = deleted_arts
            await show_deleted_arts_gallery(update, context, index)
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при навигации: {e}")
            await query.answer("❌ Ошибка", show_alert=True)

    elif data == 'deleted_arts_info':
        index = context.user_data.get('deleted_arts_current_index', 0)
        deleted_arts = context.user_data.get('deleted_arts_list', [])
        if deleted_arts:
            await query.answer(f"Арт {index + 1} из {len(deleted_arts)}", show_alert=False)
    
    elif data == 'deleted_arts_back':
        await query.message.delete()
        await query.message.chat.send_message("🔙 Вернулись в главное меню")
    
    elif data == 'deleted_arts_search_user':
        context.user_data['waiting_for_deleted_arts_search'] = True
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_deleted_arts_search')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔍 **Поиск удалённых артов**\n\n"
            "Введите ник пользователя, чьи удалённые арты вы хотите просмотреть:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'cancel_deleted_arts_search':
        context.user_data['waiting_for_deleted_arts_search'] = False
        deleted_arts = context.user_data.get('deleted_arts_list', [])
        if deleted_arts:
            await show_deleted_arts_gallery(update, context, 0)
        else:
            await query.message.delete()
    
    elif data.startswith('restore_art_'):
        try:
            art_id = int(data.split('_')[2])
        
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав!", show_alert=True)
                return
        
            success, message = restore_deleted_art(art_id)
            await query.answer(message, show_alert=True)
        
            if success:
                deleted_arts = get_deleted_arts(limit=100)
                context.user_data['deleted_arts_list'] = deleted_arts
            
                if deleted_arts:
                    current_index = context.user_data.get('deleted_arts_current_index', 0)
                    if current_index >= len(deleted_arts):
                        current_index = 0
                    await show_deleted_arts_gallery(update, context, current_index)
                else:
                    await query.message.delete()
                    await query.message.chat.send_message("✅ Все удалённые арты восстановлены!")
        
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при восстановлении арта: {e}")
            await query.answer("❌ Ошибка", show_alert=True)

    elif data.startswith('approve_appeal_'):
        try:
            appeal_id = int(data.split('_')[2])
            
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав!", show_alert=True)
                return
            
            conn = sqlite3.connect('database.db')
            cur = conn.cursor()
            cur.execute('SELECT user_id FROM appeals WHERE appeal_id = ?', (appeal_id,))
            result = cur.fetchone()
            conn.close()
            
            if not result:
                await query.answer("❌ Апелляция не найдена", show_alert=True)
                return
            
            blocked_user_id = result[0]
            success, message = unblock_user(blocked_user_id)
            
            if success:
                conn = sqlite3.connect('database.db')
                cur = conn.cursor()
                cur.execute(''' 
                    UPDATE appeals SET status = 'approved', decided_by = ?, decided_at = CURRENT_TIMESTAMP
                    WHERE appeal_id = ?
                ''', (query.from_user.id, appeal_id))
                conn.commit()
                conn.close()
                
                await query.answer("✅ Апелляция одобрена!", show_alert=True)
                
                try:
                    await query.edit_message_text("✅ Апелляция одобрена и пользователь разблокирован!")
                except:
                    pass
                try:
                    await context.bot.send_message(
                        chat_id=blocked_user_id,
                        text="✅ **Ваша апелляция одобрена!**\n\n"
                             "Ваш профиль был восстановлен и все арты вернулись. "
                             "Спасибо за понимание!"
                    )
                except Exception as e:
                    logging.error(f"Ошибка при уведомлении пользователя: {e}")
            else:
                await query.answer(f"❌ {message}", show_alert=True)
            
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при одобрении апелляции: {e}")
            await query.answer("❌ Ошибка", show_alert=True)
    
    elif data.startswith('reject_appeal_'):
        try:
            appeal_id = int(data.split('_')[2])
            
            if query.from_user.id not in SUPPORT_USER_IDS:
                await query.answer("❌ У вас нет прав!", show_alert=True)
                return
            conn = sqlite3.connect('database.db')
            cur = conn.cursor()
            cur.execute('SELECT user_id FROM appeals WHERE appeal_id = ?', (appeal_id,))
            result = cur.fetchone()
            
            if result:
                blocked_user_id = result[0]
                cur.execute('''
                    UPDATE appeals SET status = 'rejected', decided_by = ?, decided_at = CURRENT_TIMESTAMP
                    WHERE appeal_id = ?
                ''', (query.from_user.id, appeal_id))
            
            conn.commit()
            conn.close()
            
            await query.answer("✅ Апелляция отклонена!", show_alert=True)
            
            try:
                await query.edit_message_text("✅ Апелляция отклонена!")
            except:
                pass
            if result:
                try:
                    await context.bot.send_message(
                        chat_id=blocked_user_id,
                        text="❌ **Ваша апелляция отклонена**\n\n"
                             "К сожалению, модератор не смог удовлетворить вашу апелляцию. "
                             "Если у вас есть вопросы, свяжитесь с администратором."
                    )
                except Exception as e:
                    logging.error(f"Ошибка при уведомлении пользователя: {e}")
            
        except (IndexError, ValueError) as e:
            logging.error(f"Ошибка при отклонении апелляции: {e}")
            await query.answer("❌ Ошибка", show_alert=True)
    
    elif data == 'submit_appeal':
        context.user_data['waiting_for_appeal'] = True
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='start_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.message.delete()
        except:
            pass
        
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="📝 **Подать апелляцию**\n\n"
                 "Напишите причину, почему вы считаете, что блокировка была ошибкой:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'view_my_appeal':
        await show_my_appeal(update, context)
    
    elif data == 'edit_appeal':
        context.user_data['waiting_for_appeal_edit'] = True
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='view_my_appeal')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.message.delete()
        except:
            pass
        
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="✏️ **Редактировать апелляцию**\n\n"
                 "Напишите новый текст апелляции:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'view_blocked_menu':
        await show_blocked_user_menu(update, context)
    
    elif data == 'start_menu':
        await start(update, context)
    
    elif data.startswith('top_prev_') or data.startswith('top_next_'):
        index = int(data.split('_')[-1])
        await show_top_art_page(update, context, index)
    
    elif data.startswith('delete_art_'):
        art_number = int(data.split('_')[-1])
        success, message = delete_art(user_id, art_number)
        
        if success:
            await query.answer(message, show_alert=True)
            await show_my_profile_settings(update, context)
        else:
            await query.answer(message, show_alert=True)
    
    elif data.startswith('like_') or data.startswith('dislike_'):
        art_id = int(data.split('_')[1])
        reaction_type = 'like' if data.startswith('like_') else 'dislike'

        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        cur.execute('SELECT * FROM reactions WHERE user_id = ? AND art_id = ?', (user_id, art_id))
        existing_reaction = cur.fetchone()
        conn.close()

        if existing_reaction:
            await query.answer("Вы уже оценили этот арт! ❌", show_alert=True)
        else:
            add_reaction(user_id, art_id, reaction_type)
            logging.info(f"Пользователь {user_id} поставил {reaction_type} арту {art_id}")
            if reaction_type == 'like':
                owner_id = get_art_owner(art_id)
                if owner_id:
                    logging.info(f"Владелец арта {art_id}: {owner_id}. Отправка уведомления о лайке.")
                    await create_or_update_reaction_notification(context, owner_id)

            reaction_text = "❤️ Лайк" if reaction_type == 'like' else "👎 Дизлайк"
            await query.answer(f"{reaction_text} засчитан! ✅")
            await update_art_message_realtime(context, art_id)
            current_hashtag = context.user_data.get('current_hashtag_filter')
            await send_art_to_user(query.message.chat_id, context, user_id, update_message=None, hashtag_filter=current_hashtag)
    
    elif data == 'already_reacted':
        await query.answer("Вы уже оценили этот арт! ❌", show_alert=True)
    
    elif data.startswith('comment_'):
        art_id = int(data.split('_')[1])
        context.user_data['waiting_for_comment'] = True
        context.user_data['comment_art_id'] = art_id
        
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data='cancel_comment')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="💬 **Добавление комментария**\n\n"
                 "Напишите ваш комментарий и отправьте его сообщением:\n\n"
                 "Или нажмите 'Отмена' для возврата.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'cancel_comment':
        context.user_data['waiting_for_comment'] = False
        context.user_data['comment_art_id'] = None
        
        try:
            await query.message.delete()
        except:
            pass
        
        current_hashtag = context.user_data.get('current_hashtag_filter')
        await send_art_to_user(query.message.chat_id, context, user_id, update_message=None, hashtag_filter=current_hashtag)
    
    elif data == 'show_reactions':
        await show_reactions_handler(update, context)
    
    elif data == 'next_reaction':
        await next_reaction_handler(update, context)
    
    elif data == 'finish_reactions':
        await finish_reactions_handler(update, context)
    
    elif data == 'menu_from_reactions':
        await menu_from_reactions_handler(update, context)
    
    elif data.startswith('send_to_support_'):
        await send_to_support_handler(update, context)
    
    elif data.startswith('approve_manual_'):
        await approve_manual_handler(update, context)
        
    elif data.startswith('reject_manual_'):
        await reject_manual_handler(update, context)
    
    elif data == 'view_followers':
        await show_followers(update, context, 0)
    
    elif data.startswith('followers_prev_'):
        try:
            index = int(data.split('_')[2])
            await show_followers(update, context, index)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при навигации подписчиков: {e}")
    
    elif data.startswith('followers_next_'):
        try:
            index = int(data.split('_')[2])
            await show_followers(update, context, index)
        except (ValueError, IndexError) as e:
            logging.error(f"Ошибка при навигации подписчиков: {e}")
    
    elif data == 'followers_count':
        pass 
    
    elif data == 'back_to_menu':
        try:
            await query.message.delete()
        except:
            pass
        
        context.user_data.clear()
        
        keyboard = [
            [
                InlineKeyboardButton("🎨 Загрузить арт", callback_data='upload_art'),
                InlineKeyboardButton("👀 Смотреть арты", callback_data='view_arts')
            ],
            [
                InlineKeyboardButton("👤 Профиль", callback_data='my_profile'),
                InlineKeyboardButton("🏆 Топ", callback_data='top_arts')
            ],
            [
                InlineKeyboardButton("🔍 Поиск", callback_data='search_menu')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Привет, {query.from_user.first_name}! Добро пожаловать в арт-сообщество!\n\n"
                 "Здесь ты можешь делиться своими работами и оценивать творчество других.",
            reply_markup=reply_markup
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return 
    add_user(user.id, user.username)

    user_id = user.id
    text = (update.message.text or "").strip()
    
    # Проверяем, не заблокирован ли пользователь - если заблокирован, его сообщение считается апилкой
    if is_user_blocked(user_id):
        # Если заблокированный пользователь пишет что-либо, это считается апелляцией
        if text and text != "/start" and text != "🔙 В меню":
            # Проверяем, есть ли уже апелляция в статусе pending
            conn = sqlite3.connect('database.db')
            cur = conn.cursor()
            cur.execute('''
                SELECT appeal_id, status FROM appeals 
                WHERE user_id = ? 
                ORDER BY submitted_at DESC 
                LIMIT 1
            ''', (user_id,))
            appeal_info = cur.fetchone()
            
            if appeal_info and appeal_info[1] == 'pending':
                # Обновляем существующую апелляцию
                cur.execute('''
                    UPDATE appeals 
                    SET reason = ?, submitted_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND status = 'pending'
                ''', (text, user_id))
                conn.commit()
                conn.close()
                
                await update.message.reply_text(
                    "✅ Ваша апелляция обновлена! Модераторы рассмотрят новую информацию.",
                    reply_markup=get_persistent_menu()
                )
            else:
                # Создаём новую апелляцию
                cur.execute('''
                    INSERT INTO appeals (user_id, reason, submitted_at, status)
                    VALUES (?, ?, CURRENT_TIMESTAMP, 'pending')
                ''', (user_id, text))
                conn.commit()
                conn.close()
                
                await update.message.reply_text(
                    "✅ Апелляция отправлена модераторам! Они рассмотрят вашу просьбу в течение 24 часов.",
                    reply_markup=get_persistent_menu()
                )
            return
        
        # Если это команда меню или /start, показываем меню заблокированного пользователя
        if text == "🔙 В меню" or text == "/start":
            await show_blocked_user_menu(update, context)
            return
    if text == "🔙 В меню" or text == "/start":
        context.user_data.clear()
        keyboard = [
            [
                InlineKeyboardButton("🎨 Загрузить арт", callback_data='upload_art'),
                InlineKeyboardButton("👀 Смотреть арты", callback_data='view_arts')
            ],
            [
                InlineKeyboardButton("👤 Профиль", callback_data='my_profile'),
                InlineKeyboardButton("🏆 Топ", callback_data='top_arts')
            ],
            [
                InlineKeyboardButton("🔍 Поиск", callback_data='search_menu')
            ]
        ]
        await update.message.reply_text(
            f"Привет, {update.effective_user.first_name}! Добро пожаловать в арт-сообщество!\n\n"
            "Здесь ты можешь делиться своими работами и оценивать творчество других.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    if text == "/skip" and context.user_data.get('waiting_for_complaint_comment'):
        art_id = context.user_data.get('complaint_art_id')
        reason = context.user_data.get('complaint_reason')
        
        if art_id and reason:
            comment = "Без комментария"
            username = update.effective_user.username or update.effective_user.first_name
            
            add_complaint(art_id, user_id, reason, comment)
            
            success = await send_complaint_to_support(context, art_id, user_id, reason, comment, username)
            
            context.user_data['waiting_for_complaint_comment'] = False
            context.user_data['complaint_art_id'] = None
            context.user_data['complaint_reason'] = None
            
            if success:
                await update.message.reply_text(
                    "✅ Ваша жалоба отправлена модераторам! Спасибо за обратную связь. 📝"
                )
                complaint_from_top = context.user_data.get('complaint_from_top')
                if complaint_from_top:
                    top_index = context.user_data.get('complaint_top_index', 0)
                    if complaint_from_top == 'likes':
                        await show_top_art_page(update, context, top_index)
                    elif complaint_from_top == 'followers':
                        await show_top_artist_page(update, context, top_index)
                    context.user_data.pop('complaint_from_top', None)
                    context.user_data.pop('complaint_top_index', None)
                else:
                    current_hashtag = context.user_data.get('current_hashtag_filter')
                    await send_art_to_user(update.message.chat_id, context, user_id, update_message=None, hashtag_filter=current_hashtag)
            else:
                await update.message.reply_text(
                    "❌ Произошла ошибка при отправке жалобы. Попробуйте позже."
                )
        return
    if context.user_data.get('waiting_for_nickname_edit'):
        success, message = update_user_nickname(user_id, text)
        context.user_data['waiting_for_nickname_edit'] = False
        
        if success:
            await update.message.reply_text(message)
            keyboard = [
                [InlineKeyboardButton("🖼️ Изменить аватар", callback_data='edit_avatar')],
                [InlineKeyboardButton("✏️ Изменить ник", callback_data='edit_nickname')],
                [InlineKeyboardButton("📝 Изменить о себе", callback_data='edit_bio')],
                [InlineKeyboardButton("🔙 Назад", callback_data='my_profile_settings_menu')]
            ]
            await update.message.reply_text(
                "✏️ **Изменить профиль**\n\n"
                "Выберите что вы хотите изменить:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(message)
        return
    if context.user_data.get('waiting_for_bio_edit'):
        success, message = update_user_bio(user_id, text)
        context.user_data['waiting_for_bio_edit'] = False
        
        if success:
            await update.message.reply_text(message)
            keyboard = [
                [InlineKeyboardButton("🖼️ Изменить аватар", callback_data='edit_avatar')],
                [InlineKeyboardButton("✏️ Изменить ник", callback_data='edit_nickname')],
                [InlineKeyboardButton("📝 Изменить о себе", callback_data='edit_bio')],
                [InlineKeyboardButton("🔙 Назад", callback_data='my_profile_settings_menu')]
            ]
            await update.message.reply_text(
                "✏️ **Изменить профиль**\n\n"
                "Выберите что вы хотите изменить:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(message)
        return
    if context.user_data.get('waiting_for_profile_search'):
        context.user_data['waiting_for_profile_search'] = False
        results = search_users_by_nickname(text, limit=10)
        
        if not results:
            keyboard = [
                [InlineKeyboardButton("🔍 Новый поиск", callback_data='search_profiles')],
                [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
            ]
            await update.message.reply_text(
                f"👤 **По запросу '{text}' профилей не найдено**\n\n"
                "Попробуйте другой запрос.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        
        keyboard = []
        for user_id_result, nickname, username, is_public in results:
            # Приоритет: ник → юзернейм → ID
            display_name = nickname or username or f"Пользователь #{user_id_result}"
            display_text = f"👤 {display_name}"
            if username and not nickname:
                display_text += f" (@{username})"
            
            keyboard.append([InlineKeyboardButton(
                display_text,
                callback_data=f'view_profile_{user_id_result}'
            )])
        
        keyboard.append([InlineKeyboardButton("🔍 Новый поиск", callback_data='search_profiles')])
        keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')])
        
        await update.message.reply_text(
            f"👤 **Результаты поиска по: '{text}'**\n\nНайдено {len(results)} профилей",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return
    
    if context.user_data.get('waiting_for_art'):
        if update.message.photo:
            checking_msg = await update.message.reply_text("🔍 Проверяем изображение на безопасность...")
            
            file_id = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            
            try:
                photo_file = await update.message.photo[-1].get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                image = Image.open(BytesIO(photo_bytes))
                
                basic_safe, basic_message = await validate_image_basic(image)
                if not basic_safe:
                    hashtags = extract_hashtags(caption)
                    clean_caption = re.sub(r'#\w+', '', caption).strip()
                    pending_id = add_pending_art(user_id, file_id, clean_caption, hashtags)
                    
                    keyboard = [
                        [InlineKeyboardButton("📞 Отправить в поддержку", callback_data=f'send_to_support_{pending_id}')],
                        [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await checking_msg.edit_text(
                        f"{basic_message}\n\n",
                        reply_markup=reply_markup
                    )
                    context.user_data['waiting_for_art'] = False
                    return
                
                is_safe, safety_message = await is_image_safe(image)
                
                if not is_safe:
                    hashtags = extract_hashtags(caption)
                    clean_caption = re.sub(r'#\w+', '', caption).strip()
                    pending_id = add_pending_art(user_id, file_id, clean_caption, hashtags)
                    
                    keyboard = [
                        [InlineKeyboardButton("📞 Отправить в поддержку", callback_data=f'send_to_support_{pending_id}')],
                        [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await checking_msg.edit_text(
                        f"{safety_message}",
                        reply_markup=reply_markup
                    )
                    context.user_data['waiting_for_art'] = False
                    return
                
                await checking_msg.edit_text("✅ Изображение безопасно! Добавляем в галерею...")
                
            except Exception as e:
                logging.error(f"Ошибка при проверке изображения: {e}")
                await checking_msg.edit_text("❌ Ошибка при проверке изображения. Попробуйте еще раз.")
                return
            
            hashtags = extract_hashtags(caption)
            clean_caption = re.sub(r'#\w+', '', caption).strip()
            
            art_id, message = add_art(user_id, file_id, clean_caption, hashtags)
            
            if art_id:
                context.user_data['waiting_for_art'] = False
                
                art_count = get_user_art_count(user_id)
                can_upload_more = art_count < MAX_ARTS_PER_USER
                
                hashtags_info = ""
                if hashtags:
                    hashtags_info = f"\n🏷️ **Добавленные хэштеги:** {', '.join(hashtags)}"
                else:
                    hashtags_info = "\nℹ️ Хэштеги не добавлены. Вы можете добавить их в подпись к фото."
                
                keyboard = []
                if can_upload_more:
                    keyboard.append([InlineKeyboardButton("📤 Загрузить ещё арт", callback_data='upload_art')])
                keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data='back_to_menu')])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await checking_msg.edit_text(
                    "✅ Твой арт успешно добавлен! "
                    "Теперь другие пользователи смогут его оценить!" +
                    hashtags_info +
                    (f"\n\n🎨 У вас {art_count}/{MAX_ARTS_PER_USER} артов" if can_upload_more else ""),
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await checking_msg.edit_text(f"❌ Ошибка: {message}")
                context.user_data['waiting_for_art'] = False
        else:
            await update.message.reply_text(
                "❌ Пожалуйста, отправьте изображение с подписью или без."
            )
    
    elif context.user_data.get('waiting_for_complaint_comment'):
        art_id = context.user_data.get('complaint_art_id')
        reason = context.user_data.get('complaint_reason')
        
        if art_id and reason:
            comment = text
            username = update.effective_user.username or update.effective_user.first_name
            
            add_complaint(art_id, user_id, reason, comment)
            
            success = await send_complaint_to_support(context, art_id, user_id, reason, comment, username)
            
            context.user_data['waiting_for_complaint_comment'] = False
            context.user_data['complaint_art_id'] = None
            context.user_data['complaint_reason'] = None
            
            if success:
                await update.message.reply_text(
                    "✅ Ваша жалоба отправлена модераторам! Спасибо за обратную связь. 📝"
                )
                complaint_from_top = context.user_data.get('complaint_from_top')
                if complaint_from_top:
                    top_index = context.user_data.get('complaint_top_index', 0)
                    if complaint_from_top == 'likes':
                        await show_top_art_page(update, context, top_index)
                    elif complaint_from_top == 'followers':
                        await show_top_artist_page(update, context, top_index)
                    context.user_data.pop('complaint_from_top', None)
                    context.user_data.pop('complaint_top_index', None)
                else:
                    current_hashtag = context.user_data.get('current_hashtag_filter')
                    await send_art_to_user(update.message.chat_id, context, user_id, update_message=None, hashtag_filter=current_hashtag)
            else:
                await update.message.reply_text(
                    "❌ Произошла ошибка при отправке жалобы. Попробуйте позже."
                )
        else:
            context.user_data['waiting_for_complaint_comment'] = False
            await update.message.reply_text(
                "❌ Ошибка при обработке жалобы. Попробуйте позже."
            )
    
    elif context.user_data.get('waiting_for_comment'):
        art_id = context.user_data.get('comment_art_id')
        
        if art_id and text:
            success, message = add_comment(user_id, art_id, text)
            
            if success:
                await update.message.reply_text(
                    "✅ Комментарий успешно добавлен! 💬"
                )
                
                owner_id = get_art_owner(art_id)
                if owner_id:
                    logging.info(f"Владелец арта {art_id}: {owner_id}. Отправка уведомления о комментарии.")
                    await create_or_update_reaction_notification(context, owner_id)
            else:
                await update.message.reply_text(
                    f"❌ Ошибка: {message}"
                )
            
            context.user_data['waiting_for_comment'] = False
            context.user_data['comment_art_id'] = None
            
            current_hashtag = context.user_data.get('current_hashtag_filter')
            await send_art_to_user(update.message.chat_id, context, user_id, update_message=None, hashtag_filter=current_hashtag)
        else:
            context.user_data['waiting_for_comment'] = False
            context.user_data['comment_art_id'] = None
            await update.message.reply_text(
                "❌ Комментарий не может быть пустым."
            )
    
    elif context.user_data.get('waiting_for_profile_report'):
        profile_user_id = context.user_data.get('report_profile_id')
        
        if profile_user_id and text:
            username = update.effective_user.username or update.effective_user.first_name
            
            success = await send_profile_complaint_to_support(context, profile_user_id, user_id, text, username)
            
            context.user_data['waiting_for_profile_report'] = False
            context.user_data['report_profile_id'] = None
            
            if success:
                await update.message.reply_text(
                    "✅ Ваша жалоба на профиль отправлена модераторам! 📝"
                )
                if context.user_data.get('report_from_top_followers'):
                    top_index = context.user_data.get('report_top_index', 0)
                    await show_top_artist_page(update, context, top_index)
                    context.user_data.pop('report_from_top_followers', None)
                    context.user_data.pop('report_top_index', None)
                else:
                    await show_other_user_profile(update, context, profile_user_id)
            else:
                await update.message.reply_text(
                    "❌ Произошла ошибка при отправке жалобы. Попробуйте позже."
                )
        else:
            context.user_data['waiting_for_profile_report'] = False
            await update.message.reply_text(
                "❌ Ошибка при обработке жалобы. Попробуйте позже."
            )
    
    elif context.user_data.get('waiting_for_hashtag_search'):
        context.user_data['waiting_for_hashtag_search'] = False
        await show_hashtag_search_results(update, context, text)
    
    elif context.user_data.get('waiting_for_avatar_edit'):
        if update.message.photo:
            checking_msg = await update.message.reply_text("🔍 Проверяем изображение на безопасность...")
            
            file_id = update.message.photo[-1].file_id
            
            try:
                photo_file = await update.message.photo[-1].get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                image = Image.open(BytesIO(photo_bytes))
                
                is_safe, safety_message = await is_image_safe(image)
                
                if not is_safe:
                    await checking_msg.edit_text(
                        f"❌ Это изображение не может быть использовано в качестве аватара!\n\n"
                        f"Причина: {safety_message}"
                    )
                    context.user_data['waiting_for_avatar_edit'] = False
                    return
                
                await checking_msg.edit_text("✅ Изображение безопасно! Обновляем аватар...")
                
                success, message = update_user_profile_avatar(user_id, file_id)
                
                if success:
                    await checking_msg.edit_text(
                        "✅ Аватар успешно обновлен! 🖼️"
                    )
                else:
                    await checking_msg.edit_text(
                        f"❌ Ошибка: {message}"
                    )
                
            except Exception as e:
                logging.error(f"Ошибка при проверке изображения аватара: {e}")
                await checking_msg.edit_text("❌ Ошибка при проверке изображения. Попробуйте еще раз.")
            
            context.user_data['waiting_for_avatar_edit'] = False
        else:
            await update.message.reply_text(
                "❌ Пожалуйста, отправьте изображение для аватара."
            )
    
    elif context.user_data.get('waiting_for_appeal'):
        success, message = submit_appeal(user_id, text)
        
        if success:
            await update.message.reply_text(
                message,
                reply_markup=get_persistent_menu()
            )
        else:
            await update.message.reply_text(
                message,
                reply_markup=get_persistent_menu()
            )
        
        context.user_data['waiting_for_appeal'] = False
    
    elif context.user_data.get('waiting_for_appeal_edit'):
        conn = sqlite3.connect('database.db')
        cur = conn.cursor()
        cur.execute('''
            SELECT appeal_id FROM appeals 
            WHERE user_id = ? AND status = 'pending'
            ORDER BY submitted_at DESC 
            LIMIT 1
        ''', (user_id,))
        appeal_info = cur.fetchone()
        
        if appeal_info:
            appeal_id = appeal_info[0]
            cur.execute('''
                UPDATE appeals 
                SET reason = ?, submitted_at = CURRENT_TIMESTAMP
                WHERE appeal_id = ?
            ''', (text, appeal_id))
            conn.commit()
            await update.message.reply_text(
                "✅ Ваша апелляция обновлена! Модераторы рассмотрят новую информацию.",
                reply_markup=get_persistent_menu()
            )
        else:
            await update.message.reply_text(
                "❌ Не найдено апелляции для редактирования.",
                reply_markup=get_persistent_menu()
            )
        
        conn.close()
        context.user_data['waiting_for_appeal_edit'] = False

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.user_data.get('waiting_for_avatar_edit'):
        checking_msg = await update.message.reply_text("🔍 Проверяем аватар на безопасность...")
        
        file_id = update.message.photo[-1].file_id
        
        try:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image = Image.open(BytesIO(photo_bytes))
            
            basic_safe, basic_message = await validate_image_basic(image)
            if not basic_safe:
                await checking_msg.edit_text("❌ Аватар не прошел проверку размера\n\n" + basic_message)
                context.user_data['waiting_for_avatar_edit'] = False
                return
            
            is_safe, safety_message = await is_image_safe(image)
            
            if not is_safe:
                await checking_msg.edit_text("❌ Аватар содержит запрещенный контент\n\n" + safety_message)
                context.user_data['waiting_for_avatar_edit'] = False
                add_profile_violation(user_id, 'avatar', safety_message)
                return
            success, message = update_user_profile_avatar(user_id, file_id)
            
            await checking_msg.edit_text("✅ Аватар профиля успешно обновлен!")
            context.user_data['waiting_for_avatar_edit'] = False
            
            await show_my_profile_settings(update, context)
            
        except Exception as e:
            logging.error(f"Ошибка при проверке аватара: {e}")
            await checking_msg.edit_text("❌ Ошибка при проверке аватара. Попробуйте еще раз.")
            context.user_data['waiting_for_avatar_edit'] = False
        
        return
    
    if context.user_data.get('waiting_for_art'):
        checking_msg = await update.message.reply_text("🔍 Проверяем изображение на безопасность...")
        
        file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        
        try:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image = Image.open(BytesIO(photo_bytes))
            
            basic_safe, basic_message = await validate_image_basic(image)
            if not basic_safe:
                hashtags = extract_hashtags(caption)
                clean_caption = re.sub(r'#\w+', '', caption).strip()
                pending_id = add_pending_art(user_id, file_id, clean_caption, hashtags)
                
                keyboard = [
                    [InlineKeyboardButton("📞 Отправить в поддержку", callback_data=f'send_to_support_{pending_id}')],
                    [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await checking_msg.edit_text(
                    f"{basic_message}\n\n",
                    reply_markup=reply_markup
                )
                context.user_data['waiting_for_art'] = False
                return
            
            is_safe, safety_message = await is_image_safe(image)
            
            if not is_safe:
                hashtags = extract_hashtags(caption)
                clean_caption = re.sub(r'#\w+', '', caption).strip()
                pending_id = add_pending_art(user_id, file_id, clean_caption, hashtags)
                
                keyboard = [
                    [InlineKeyboardButton("📞 Отправить в поддержку", callback_data=f'send_to_support_{pending_id}')],
                    [InlineKeyboardButton("🔙 В меню", callback_data='back_to_menu')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await checking_msg.edit_text(
                    f"{safety_message}",
                    reply_markup=reply_markup
                )
                context.user_data['waiting_for_art'] = False
                return
            
            await checking_msg.edit_text("✅ Изображение безопасно! Добавляем в галерею...")
            
        except Exception as e:
            logging.error(f"Ошибка при проверке изображения: {e}")
            await checking_msg.edit_text("❌ Ошибка при проверке изображения. Попробуйте еще раз.")
            return
        
        hashtags = extract_hashtags(caption)
        clean_caption = re.sub(r'#\w+', '', caption).strip()
        
        art_id, message = add_art(user_id, file_id, clean_caption, hashtags)
        
        if art_id:
            context.user_data['waiting_for_art'] = False
            
            art_count = get_user_art_count(user_id)
            can_upload_more = art_count < MAX_ARTS_PER_USER
            
            hashtags_info = ""
            if hashtags:
                hashtags_info = f"\n🏷️ **Добавленные хэштеги:** {', '.join(hashtags)}"
            else:
                hashtags_info = "\nℹ️ Хэштеги не добавлены. Вы можете добавить их в подпись к фото."
            
            keyboard = []
            if can_upload_more:
                keyboard.append([InlineKeyboardButton("📤 Загрузить ещё арт", callback_data='upload_art')])
            keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data='back_to_menu')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await checking_msg.edit_text(
                "✅ Твой арт успешно добавлен! "
                "Теперь другие пользователи смогут его оценить!" +
                hashtags_info +
                (f"\n\n🎨 У вас {art_count}/{MAX_ARTS_PER_USER} артов" if can_upload_more else ""),
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await checking_msg.edit_text(
                f"{message}\n\n"
                "Перейди в профиль чтобы удалить старые арты.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 Профиль", callback_data='profile')]])
            )

# ========== КОМАНДЫ ДЛЯ МОДЕРАТОРОВ ==========

async def deleted_arts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /deleted_arts - показывает удалённые арты"""
    user_id = update.effective_user.id
    
    if user_id not in SUPPORT_USER_IDS:
        await update.message.reply_text("❌ У вас нет доступа к этой команде!")
        return
    deleted_arts = get_deleted_arts(limit=100)
    
    if not deleted_arts:
        await update.message.reply_text("📭 Нет удалённых артов за последний день")
        return
    context.user_data['deleted_arts_list'] = deleted_arts
    context.user_data['deleted_arts_current_index'] = 0
    index = 0
    deleted_id, art_id, owner_id, file_id, caption, deleted_at, reason = deleted_arts[index]
    owner_profile = get_user_profile(owner_id)
    is_owner_profile_public = owner_profile[5] if owner_profile else False
    
    owner_name = get_display_name(owner_id, profile_is_public=is_owner_profile_public)
    gallery_text = f"🗑️ **Удалённый арт** ({index + 1}/{len(deleted_arts)})\n\n"
    gallery_text += f"🎨 Арт #{art_id}\n"
    gallery_text += f"👤 Автор: {escape_markdown(owner_name)}\n"
    gallery_text += f"⏰ Удален: {deleted_at}\n"
    gallery_text += f"📋 Причина: {escape_markdown(reason)}\n\n"
    
    if caption:
        gallery_text += f"📝 {escape_markdown(caption)}\n"
    
    keyboard = []
    
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f'deleted_arts_prev_{index-1}'))
    
    nav_buttons.append(InlineKeyboardButton(f"{index + 1}/{len(deleted_arts)}", callback_data='deleted_arts_info'))
    
    if index < len(deleted_arts) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f'deleted_arts_next_{index+1}'))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔍 Поиск по нику", callback_data='deleted_arts_search_user')])
    
    keyboard.append([InlineKeyboardButton("♻️ Восстановить", callback_data=f'restore_art_{art_id}')])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='deleted_arts_back')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=file_id,
            caption=gallery_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке фото: {e}")
        await update.message.reply_text(
            gallery_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def appeals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /appeals - показывает апелляции от заблокированных пользователей"""
    user_id = update.effective_user.id
    
    if user_id not in SUPPORT_USER_IDS:
        await update.message.reply_text("❌ У вас нет доступа к этой команде!")
        return
    
    appeals = get_pending_appeals()
    
    if not appeals:
        await update.message.reply_text("📭 Нет ожидающих апеляций")
        return
    
    message_text = "📋 **Ожидающие апелляции**\n\n"
    keyboard = []
    
    for appeal_id, user_id_appeal, reason, submitted_at in appeals:
        user_name = get_display_name(user_id_appeal, for_moderator=True)
        reason_preview = (reason[:50] + "...") if len(reason) > 50 else reason
        
        message_text += f"👤 {escape_markdown(user_name)} (ID: {user_id_appeal})\n"
        message_text += f"📝 {escape_markdown(reason_preview)}\n"
        message_text += f"⏰ {submitted_at}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"✅ Одобрить #{appeal_id}", callback_data=f'approve_appeal_{appeal_id}'),
            InlineKeyboardButton(f"❌ Отклонить #{appeal_id}", callback_data=f'reject_appeal_{appeal_id}')
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ========== ЗАПУСК БОТА ==========

def main():
    # Инициализация базы данных
    init_db()
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавление обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("deleted_arts", deleted_arts_command))
    
    # Добавление обработчиков кнопок
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Добавление обработчиков сообщений
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Добавление обработчиков для кнопок реакций
    application.add_handler(CallbackQueryHandler(show_reactions_handler, pattern='^show_reactions$'))
    application.add_handler(CallbackQueryHandler(next_reaction_handler, pattern='^next_reaction$'))
    application.add_handler(CallbackQueryHandler(finish_reactions_handler, pattern='^finish_reactions$'))
    application.add_handler(CallbackQueryHandler(menu_from_reactions_handler, pattern='^menu_from_reactions$'))
    
    # Добавление обработчиков для ручной модерации
    application.add_handler(CallbackQueryHandler(send_to_support_handler, pattern='^send_to_support_'))
    application.add_handler(CallbackQueryHandler(approve_manual_handler, pattern='^approve_manual_'))
    application.add_handler(CallbackQueryHandler(reject_manual_handler, pattern='^reject_manual_'))
    
    # Планировщик для обновлений в реальном времени
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(realtime_updater, interval=3600, first=10)  # Каждый час
        job_queue.run_repeating(send_notification_reminder, interval=43200, first=60)  # Каждые 12 часов
        logging.info("Планировщики задач запущены")
    
    # Запуск бота
    logging.info("Бот запускается...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()