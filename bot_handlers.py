from .bot_logic import *

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
