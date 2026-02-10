# modules/tt_dl.py
"""
<manifest>
name: TikTok downloader
version: 1.1.0
author: SynForge
source: https://raw.githubusercontent.com/AresUser1/Tiktok/main/tt_dl.py
channel_url: https://t.me/SynForge
description: Скачивание видео из TikTok без водяного знака.
</manifest>
"""

import os
import aiohttp
import asyncio
import io
from telethon import events
from core import register, inline_handler
from utils.message_builder import build_and_edit
from telethon.tl.types import MessageEntityBold, MessageEntityItalic

API_URL = "https://www.tikwm.com/api/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

async def download_tiktok(url):
    """Получает данные через TikWM API."""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.post(API_URL, data={'url': url}) as response:
            res = await response.json()
            if res.get('code') == 0:
                data = res['data']
                # Если это фото-пост (слайдшоу)
                if 'images' in data and data['images']:
                    return "images", data['images'], data.get('title', 'TikTok Photos')
                # Если это обычное видео
                return "video", data['play'], data.get('title', 'TikTok Video')
            return "error", None, res.get('msg', 'Unknown error')

@register("tt", incoming=True)
async def tiktok_cmd(event):
    """Скачать контент из TikTok (видео или фото).
    
    Usage: .tt <ссылка>
    """
    args = event.pattern_match.group(1)
    if not args:
        return await build_and_edit(event, [{"text": "❌ Укажите ссылку на TikTok.", "entity": MessageEntityBold}])

    url = args.strip().split()[0]
    await build_and_edit(event, [{"text": "📥 Обработка контента...", "entity": MessageEntityItalic}])

    type, content, title = await download_tiktok(url)
    
    if type == "error":
        return await build_and_edit(event, [{"text": f"❌ Ошибка: {title}"}])

    await event.delete()
    
    try:
        caption = f"🎬 <b>{title}</b>\n\n📥 <i>Скачано через KoteLoader</i>"
        
        if type == "video":
            await event.client.send_file(
                event.chat_id, 
                content, 
                caption=caption,
                parse_mode='html'
            )
        elif type == "images":
            # Поэтапное скачивание для стабильности
            media = []
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                for i, img_url in enumerate(content[:20]):
                    try:
                        async with session.get(img_url, timeout=15) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                file = io.BytesIO(data)
                                file.name = f"photo_{i}.jpg"
                                media.append(file)
                    except:
                        continue
            
            if media:
                # Разбиваем на чанки по 10
                for i in range(0, len(media), 10):
                    chunk = media[i:i+10]
                    curr_caption = caption if i == 0 else ""
                    await event.client.send_file(
                        event.chat_id,
                        chunk,
                        caption=curr_caption,
                        parse_mode='html'
                    )
            else:
                await event.respond("❌ Не удалось загрузить изображения.")
    except Exception as e:
        await event.respond(f"❌ Не удалось отправить: {e}")
