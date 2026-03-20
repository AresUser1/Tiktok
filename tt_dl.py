# modules/tt_dl.py
"""
<manifest>
name: TikTok downloader
version: 1.3.0
author: SynForge
source: https://raw.githubusercontent.com/AresUser1/Tiktok/main/tt_dl.py
channel_url: https://t.me/SynForge
description: Скачивание видео из TikTok (видео, гифки, фото-альбомы).
</manifest>
"""

import os
import aiohttp
import asyncio
import io
from telethon import events
from core import register, Module
from utils.message_builder import build_and_edit
from utils.security import check_permission
from telethon.tl.types import MessageEntityBold, MessageEntityItalic

API_URL = "https://www.tikwm.com/api/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.tikwm.com/"
}

# Настройка подписи: True = с названием видео, False = только "Скачано через KoteLoader"
TT_CAPTION_FULL = True

# Количество попыток скачать каждый файл
DOWNLOAD_RETRIES = 3


async def _fetch_bytes(
    session: aiohttp.ClientSession,
    url: str,
    retries: int = DOWNLOAD_RETRIES,
) -> tuple[bytes | None, str]:
    """
    Скачивает байты по URL с повторными попытками.
    Возвращает (данные, content_type), например (b'...', 'video/mp4').
    content_type пустая строка если не удалось скачать.
    """
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status == 200:
                    ct = resp.content_type or ""
                    return await resp.read(), ct
        except Exception:
            if attempt < retries - 1:
                await asyncio.sleep(2.0)
    return None, ""


async def _api_request(session: aiohttp.ClientSession, url: str, extra: dict = None) -> dict | None:
    """POST-запрос к tikwm API. Возвращает data-словарь или None при ошибке."""
    params = {'url': url}
    if extra:
        params.update(extra)
    try:
        async with session.post(API_URL, data=params) as response:
            res = await response.json(content_type=None)
            if res.get('code') == 0:
                return res['data']
    except Exception:
        pass
    return None


async def download_tiktok(url: str):
    """
    Получает данные о контенте через TikWM API.
    Для слайдшоу сначала пробует web=1 чтобы получить live_photo (mp4 на слайд).
    Возвращает: (type, content, title, error)
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        data = await _api_request(session, url)
        if not data:
            data = await _api_request(session, url, {'cursor': '0'})
        if not data:
            return "error", None, None, "API не ответил или вернул ошибку"

        title = data.get('title', '')

        # Слайдшоу
        if 'images' in data and data['images']:
            images = data['images']

            # Пробуем web=1 — там появляется live_photo (mp4 на каждый слайд)
            data_web = await _api_request(session, url, {'web': '1', 'hd': '1'})
            live_urls = []
            if data_web:
                live_urls = data_web.get('live_photo') or data_web.get('images_video') or []
                # Некоторые версии кладут mp4 прямо в images при web=1
                if not live_urls:
                    web_images = data_web.get('images', [])
                    if web_images and isinstance(web_images[0], str) and ".mp4" in web_images[0]:
                        live_urls = web_images

            if live_urls:
                return "live_photo", {"photos": images, "videos": live_urls}, title, None

            return "images", images, title, None

        # Обычное видео / GIF
        play_url = data.get('play', '')
        duration = data.get('duration', 1)
        has_music = bool(data.get('music_info', {}).get('play', ''))
        if duration <= 5 and not has_music:
            return "gif", play_url, title, None

        return "video", play_url, title, None


def _make_caption(title: str, full: bool) -> str:
    """Формирует подпись в зависимости от режима."""
    credit = "📥 <i>Скачано через KoteLoader</i>"
    if full and title:
        return f"🎬 <b>{title}</b>\n\n{credit}"
    return credit


class TikTokModule(Module):

    async def client_ready(self, client, db):
        self.client = client
        self.db = db
        # Загружаем настройку режима подписи из БД (по умолчанию — полная)
        self.caption_full = self.db.get_module_data(
            "tt_dl", "caption_full", default=True
        )

    @register("tt", incoming=True)
    async def tiktok_cmd(self, event):
        """Скачать контент из TikTok (видео, гиф, фото).

        Usage:
          {prefix}tt <ссылка>          — скачать
          {prefix}tt caption full      — режим: название + подпись
          {prefix}tt caption short     — режим: только подпись
        """
        if not check_permission(event, min_level="TRUSTED"):
            return

        args = (event.pattern_match.group(1) or "").strip()

        # --- Переключение режима подписи ---
        if args.startswith("caption"):
            parts = args.split()
            mode = parts[1].lower() if len(parts) > 1 else ""
            if mode == "full":
                self.caption_full = True
                self.db.set_module_data("tt_dl", "caption_full", True)
                return await build_and_edit(event, [
                    {"text": "✅ Режим подписи: ", "entity": MessageEntityBold},
                    {"text": "полный (название + кредиты)"},
                ])
            elif mode == "short":
                self.caption_full = False
                self.db.set_module_data("tt_dl", "caption_full", False)
                return await build_and_edit(event, [
                    {"text": "✅ Режим подписи: ", "entity": MessageEntityBold},
                    {"text": "краткий (только кредиты)"},
                ])
            else:
                current = "full" if self.caption_full else "short"
                return await build_and_edit(event, [
                    {"text": f"ℹ️ Текущий режим: {current}\nИспользуй: .tt caption full / short", "entity": MessageEntityItalic},
                ])

        # --- Основная логика скачивания ---
        if not args:
            return await build_and_edit(event, [
                {"text": "❌ Укажите ссылку на TikTok.", "entity": MessageEntityBold}
            ])

        url = args.split()[0]
        await build_and_edit(event, [{"text": "🔍 Получаю информацию...", "entity": MessageEntityItalic}])

        content_type, content, title, error = await download_tiktok(url)

        if content_type == "error":
            return await build_and_edit(event, [
                {"text": f"❌ Ошибка API: {error}"}
            ])

        await build_and_edit(event, [{"text": "📥 Скачиваю...", "entity": MessageEntityItalic}])
        caption = _make_caption(title, self.caption_full)

        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:

                # ── ВИДЕО ────────────────────────────────────────────────────
                if content_type == "video":
                    data, _ = await _fetch_bytes(session, content)
                    if not data:
                        return await build_and_edit(event, [
                            {"text": "❌ Не удалось скачать видео после нескольких попыток."}
                        ])
                    file = io.BytesIO(data)
                    file.name = "video.mp4"
                    await event.delete()
                    await event.client.send_file(
                        event.chat_id,
                        file,
                        caption=caption,
                        parse_mode='html',
                        supports_streaming=True
                    )

                # ── GIF (короткое видео без звука) ───────────────────────────
                elif content_type == "gif":
                    data, _ = await _fetch_bytes(session, content)
                    if not data:
                        return await build_and_edit(event, [
                            {"text": "❌ Не удалось скачать GIF после нескольких попыток."}
                        ])
                    file = io.BytesIO(data)
                    file.name = "animation.mp4"
                    await event.delete()
                    # gif=True — Telegram покажет как зацикленную анимацию без звука
                    await event.client.send_file(
                        event.chat_id,
                        file,
                        caption=caption,
                        parse_mode='html',
                        gif=True
                    )

                # ── LIVE PHOTO слайдшоу (фото + mp4 на каждый слайд) ────────
                elif content_type == "live_photo":
                    photo_urls = content["photos"]
                    video_urls = content["videos"]
                    failed = 0
                    first_caption_sent = False

                    # Пробуем отправить mp4-версию каждого слайда
                    # Если не скачалась — фолбек на jpg
                    for i, (photo_url, video_url) in enumerate(
                        zip(photo_urls, video_urls)
                    ):
                        vid_data, _ = await _fetch_bytes(session, video_url)
                        if vid_data:
                            f = io.BytesIO(vid_data)
                            f.name = f"clip_{i}.mp4"
                            vid_caption = caption if not first_caption_sent else ""
                            await event.client.send_file(
                                event.chat_id,
                                f,
                                caption=vid_caption,
                                parse_mode='html',
                                gif=True,
                            )
                            first_caption_sent = True
                        else:
                            # mp4 не скачался — шлём jpg
                            img_data, _ = await _fetch_bytes(session, photo_url)
                            if img_data:
                                f = io.BytesIO(img_data)
                                f.name = f"photo_{i}.jpg"
                                img_caption = caption if not first_caption_sent else ""
                                await event.client.send_file(
                                    event.chat_id,
                                    f,
                                    caption=img_caption,
                                    parse_mode='html',
                                )
                                first_caption_sent = True
                            else:
                                failed += 1
                        await asyncio.sleep(0.5)

                    # Если видео меньше чем фото — досылаем оставшиеся jpg альбомом
                    extra_photos = []
                    for i in range(len(video_urls), len(photo_urls)):
                        img_data, _ = await _fetch_bytes(session, photo_urls[i])
                        if img_data:
                            f = io.BytesIO(img_data)
                            f.name = f"photo_{i}.jpg"
                            extra_photos.append(f)
                        else:
                            failed += 1
                    if extra_photos:
                        for chunk_start in range(0, len(extra_photos), 10):
                            chunk = extra_photos[chunk_start:chunk_start + 10]
                            chunk_caption = caption if not first_caption_sent else ""
                            await event.client.send_file(
                                event.chat_id, chunk,
                                caption=chunk_caption, parse_mode='html',
                            )
                            first_caption_sent = True
                            if chunk_start + 10 < len(extra_photos):
                                await asyncio.sleep(1.5)

                    await event.delete()
                    if failed:
                        await event.respond(
                            f"⚠️ {failed} слайд(ов) не удалось скачать.",
                            parse_mode='html',
                        )

                # ── ОБЫЧНЫЙ ФОТО-АЛЬБОМ ──────────────────────────────────────
                elif content_type == "images":
                    photos = []
                    failed = 0
                    for i, img_url in enumerate(content):
                        data, _ = await _fetch_bytes(session, img_url)
                        if data:
                            f = io.BytesIO(data)
                            f.name = f"photo_{i}.jpg"
                            photos.append(f)
                        else:
                            failed += 1

                    await event.delete()

                    if not photos:
                        return await event.respond("❌ Не удалось загрузить ни одного фото.")

                    for chunk_start in range(0, len(photos), 10):
                        chunk = photos[chunk_start:chunk_start + 10]
                        chunk_caption = caption if chunk_start == 0 else ""
                        await event.client.send_file(
                            event.chat_id,
                            chunk,
                            caption=chunk_caption,
                            parse_mode='html',
                        )
                        if chunk_start + 10 < len(photos):
                            await asyncio.sleep(1.5)

                    if failed:
                        await event.respond(
                            f"⚠️ {failed} из {len(content)} фото не удалось скачать.",
                            parse_mode='html',
                        )

        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await event.respond(f"❌ Критическая ошибка: {e}")
            except Exception:
                pass
