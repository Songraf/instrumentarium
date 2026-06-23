#!/usr/bin/env python3
"""Instrumentarium — yt-dlp error mapping.

Maps yt-dlp stderr output to user-friendly Russian messages.
"""


def map_ytdlp_error(err_text):
    """Map yt-dlp error output to a user-friendly Russian message.

    Args:
        err_text: Raw stderr/stdout text from yt-dlp.

    Returns:
        A user-friendly error message in Russian.
    """
    err = (err_text or "").lower()

    if "unsupported url" in err:
        return "Неправильная ссылка или сайт не поддерживается"
    if "video unavailable" in err or "content is not available" in err:
        return "Видео недоступно или удалено"
    if "private video" in err or "private" in err:
        return "Видео приватное — нужны cookies для доступа"
    if "login" in err or "sign in" in err or "authentication" in err:
        return "Требуется вход в аккаунт — загрузите cookies"
    if "blocked" in err or "banned" in err or "403" in err:
        return "Доступ заблокирован — попробуйте позже или используйте cookies"
    if "404" in err or "not found" in err:
        return "Страница не найдена (404)"
    if "429" in err or "rate limit" in err or "too many requests" in err:
        return "Слишком много запросов — подождите и попробуйте снова"
    if "network" in err or "connection" in err or "timeout" in err:
        return "Ошибка сети — проверьте подключение к интернету"
    if "geo" in err or "region" in err or "country" in err:
        return "Видео недоступно в вашем регионе"
    if "removed" in err or "deleted" in err:
        return "Видео было удалено"
    if "copyright" in err or "dmca" in err:
        return "Видео заблокировано по запросу правообладателя"
    if "age" in err or "restricted" in err:
        return "Видео с возрастным ограничением — нужны cookies"

    return "Не удалось обработать ссылку — проверьте правильность или используйте cookies"
