# INSTRUMENTARIUM — Техническое описание и спецификация

*Версия документа: 2026-06-27*
*Статус: Активная разработка (v0.1.0, ветка v0.1.0)*

---

## Содержание

1. [Обзор приложения](#1-обзор-приложения)
2. [Функциональные требования](#2-функциональные-требования)
3. [Технические требования](#3-технические-требования)
4. [Архитектура](#4-архитектура)
5. [API](#5-api)
6. [Пользовательский интерфейс](#6-пользовательский-интерфейс)
7. [Поддерживаемые платформы и форматы](#7-поддерживаемые-платформы-и-форматы)
8. [Сборка и развёртывание](#8-сборка-и-развёртывание)
9. [Ограничения и известные проблемы](#9-ограничения-и-известные-проблемы)

---

## 1. Обзор приложения

### Что это

**Instrumentarium** — портативное десктопное приложение для скачивания видео и аудио с YouTube, Twitter/X, TikTok, Instagram, Facebook, LinkedIn и 1000+ других сайтов через yt-dlp.

### Ключевой принцип

> Распаковал → запустил → вставил ссылку → скачал.

Без установщиков. Без командной строки. Без настройки. Всё в одной папке.

### Целевая аудитория

Пользователи, которым нужно быстро скачать видео без сложных инструкций.

---

## 2. Функциональные требования

### 2.1. Первый запуск (Setup Wizard)

| # | Требование | Описание |
|---|-----------|----------|
| FW-01 | Проверка Python | Автоматический поиск Python 3.7+ в системе |
| FW-02 | Установка Python | На Windows — silent install с PrependPath. На Linux/Mac — инструкции пользователю |
| FW-03 | Установка yt-dlp | Автоматическая загрузка в `.bin/` при первом запуске |
| FW-04 | Установка ffmpeg | Автоматическая загрузка на Windows (BtbN builds). На других ОС — инструкции |
| FW-05 | Прогресс-бар | Отображение прогресса установки с shimmer-анимацией |
| FW-06 | Маркёр настройки | Файл `.setup_done` — при повторном запуске wizard пропускается |
| FW-07 | Тихая проверка | При повторном запуске — фоновая проверка зависимостей без UI |

### 2.2. Скачивание видео

| # | Требование | Описание |
|---|-----------|----------|
| DW-01 | Ввод URL | Поле ввода с валидацией URL в реальном времени |
| DW-02 | Определение платформы | Автоматическое определение по домену URL |
| DW-03 | Бейдж платформы | Цветной индикатор: 🔴 YouTube, 🐦 Twitter/X, 🎵 TikTok, 📸 Instagram, 📘 Facebook, 💼 LinkedIn, 🌐 Другое |
| DW-04 | Пробинг форматов | Запрос `/probe` → получение доступных разрешений и аудиоформатов |
| DW-05 | Кнопки разрешений | Динамические кнопки на основе данных yt-dlp (1080p, 720p и т.д.) |
| DW-06 | Размер файла | Отображение размера файла на каждой кнопке. При отсутствии размера в /probe — запускается асинхронный расчёт (probe-meta), на кнопке показывается "Определяю…" с пульсирующей анимацией. Результат кэшируется по (url, format_id) и сохраняется при переключении режимов (video↔audio). |
| DW-07 | Аудио дорожка | Все видео скачиваются с аудио (`+bestaudio/best`) |
| DW-08 | Вертикальные видео | Корректное отображение разрешения для Shorts/Reels (eff_height = width) |
| DW-09 | Прогресс загрузки | Прогресс-бар с реальным процентом из yt-dlp output |
| DW-10 | Уведомление об ошибке | Toast с описанием ошибки + hint "Подробности в instrumentarium.log" |
| DW-11 | Открытие папки | Кнопка "📁 Загрузки" → открытие папки downloads в файловом менеджере |
|| DW-12 | Отмена загрузки | Кнопка "✕ Остановить" → `POST /cancel` → kill process tree (taskkill на Windows, killpg на Linux/macOS) + очистка .part/.ytdl файлов. Кнопка скрыта по умолчанию, появляется только при активной загрузке. После отмены: плавное исчезновение прогресс-бара через 2 сек. |
| DW-13 | Ограничение имени | Длина имени файла ограничена 120 символами для совместимости с Windows |

### 2.3. Скачивание аудио

| # | Требование | Описание |
|---|-----------|----------|
| AW-01 | Переключатель режимов | 🎥 Видео (MP4) / 🎵 Аудио (MP3) |
| AW-02 | Кнопки аудио | Динамические кнопки с битрейтом (слева) и размером (справа) |
| AW-03 | Формат | Извлечение аудио → MP3 через ffmpeg |
| AW-04 | Битрейт | Отображение битрейта (128kbps, 320kbps и т.д.) |

### 2.4. Безопасность и стабильность

| # | Требование | Описание |
|---|-----------|----------|
| SX-01 | Один инстанс | Lock-файл предотвращает запуск второй копии |
| SX-02 | Корректное закрытие | `/shutdown` → kill yt-dlp → stop server → daemon threads die |
| SX-03 | Нет зомби | Активный subprocess отслеживается и убивается при закрытии |
| SX-04 | Нет консоли | На Windows — `console=False`, `CREATE_NO_WINDOW`, stdout → devnull |
| SX-05 | Rate limiting | Token bucket для /probe (3 req/s) и /download (0.2 req/s) |
| SX-06 | CORS | Все JSON-ответы включают `Access-Control-Allow-Origin: *` |
| SX-07 | Path traversal | Валидация путей в `_serve_html_file` через `os.path.realpath()` |
| SX-08 | Cookies validation | Проверка формата Netscape cookie file, ограничение размера 1MB |

---

## 3. Технические требования

### 3.1. Системные требования

| Компонент | Минимум | Рекомендуемое |
|-----------|---------|---------------|
| ОС | Windows 10+, Linux (GTK/Qt), macOS 10.14+ | Последняя версия |
| Python | 3.7+ | 3.12+ |
| RAM | 256 MB | 512 MB |
| Диск | ~50 MB (билд) + место для загрузок | Зависит от контента |
| Сеть | Для загрузки yt-dlp/ffmpeg и видео | Стабильное соединение |

### 3.2. Зависимости

| Компонент | Версия | Назначение | Установка |
|-----------|--------|------------|-----------|
| Python | 3.7+ | Среда выполнения | Авто или системный |
| yt-dlp | latest | Загрузка видео | Авто в `.bin/` |
| ffmpeg | latest | Мердж видео+аудио, конвертация | Авто на Windows (BtbN) |
| pywebview | latest | Нативное окно | В билде |
| PyInstaller | latest | Сборка .exe | Только для сборки |

### 3.3. Портативность

**Критическое требование:** Все рабочие файлы — в одной папке с исполняемым файлом.

```
<папка с .exe>/
├── Instrumentarium.exe      # Исполняемый файл
├── download.html            # UI
├── instrumentarium.log      # Лог
├── .setup_done              # Маркёр настройки
├── .instrumentarium.lock    # Lock-файл
├── .cookies.txt             # Cookies (опционально)
└── .bin/
    ├── yt-dlp.exe
    ├── ffmpeg.exe
    └── ffprobe.exe
```

Все скачанные файлы сохраняются в **системную папку «Загрузки»** пользователя (`~/Downloads` на Linux/macOS, `%USERPROFILE%\Downloads` на Windows).

**Ничего не пишется в AppData, ~/.instrumentarium, или куда-либо ещё.**

---

## 4. Архитектура

### 4.1. Потоки

```
Main Thread (app.py)
  └── pywebview event loop (блокирует main thread)

Server Thread (daemon, ThreadingHTTPServer)
  └── Обработка HTTP-запросов (многопоточная)

Setup Thread (daemon)
  └── Проверка/установка Python, yt-dlp, ffmpeg

JobLogger Thread (daemon)
  └── subprocess yt-dlp → parse stdout → download_jobs[jid]

ProbeMeta Thread (daemon)
  └── yt-dlp probe download → estimate filesize → probe_meta_jobs[jid]

Cleanup Thread (daemon)
  └── TTL cleanup каждые 5 минут
```

### 4.2. Компоненты

| Файл | Назначение |
|------|-----------|
| `app.py` | Десктоп-лаунчер: lock, логирование, запуск сервера in-process, окно pywebview |
| `server_main.py` | HTTP server + handler: импортит из server/ подпакета |
| `server/__init__.py` | Реэкспорт всех публичных API подпакета |
| `server/state.py` | AppState — thread-safe контейнер состояния |
| `server/utils.py` | Утилиты: detect_platform, human_size, popen_no_console, find_ffmpeg |
| `server/errors.py` | Маппинг ошибок yt-dlp → русские сообщения |
| `server/setup.py` | Setup wizard: проверка/установка Python, yt-dlp, ffmpeg |
| `server/download.py` | JobLogger + probe-meta (async) |
| `download.html` | UI: setup screen + download screen (HTML/CSS/JS) |

### 4.3. Ключевые решения

- **fetch() API** — для /cancel запроса (совместимость с WebView2/pywebview)
- **ThreadingHTTPServer** — многопоточный HTTP-сервер (не блокируется на медленных запросах)
- **Rate limiting** — token bucket для /probe и /download
- **CORS headers** — все JSON-ответы включают `Access-Control-Allow-Origin: *`
- **Path traversal protection** — валидация путей через `os.path.realpath()`
- **Thread-safe AppState** — все атрибуты защищены `threading.Lock`
- **TTL cleanup** — автоматическая очистка старых jobs каждые 5 минут
- **Экспоненциальный бэк-офф** — probe-meta polling: 1s, 2s, 4s, 8s... до 16s
- **Реальный прогресс-бар** — парсинг процента из yt-dlp output
- **Popen с CREATE_NEW_PROCESS_GROUP** — для убийства дерева процессов на Windows
- **taskkill /F /T** — убийство process tree на Windows

---

## 5. API

### 5.1. HTTP Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/`, `/index.html` | Отдаёт download.html |
| GET | `/status` | JSON: setup_state |
| GET | `/probe?url=URL` | JSON: {title, duration, thumbnail, formats, audio_formats} |
| GET | `/probe-meta?url=URL&format_id=ID&duration=N` | JSON: {job_id} |
| GET | `/probe-meta-status?id=ID` | JSON: {status, filesize, probe_duration} |
| GET | `/log?job=ID&offset=N` | JSON: {lines, status, speed, filesize, downloaded_bytes, cancelled, stall_warning} |
| GET | `/open-folder` | Открывает папку downloads |
| GET | `/cancel` | Отмена активной загрузки (legacy) |
| POST | `/cancel` | Отмена активной загрузки + очистка .part/.ytdl |
| POST | `/setup` | Запускает setup wizard |
| POST | `/download` | Запускает скачивание {url, mode, format_id} |
| POST | `/cookies` | Сохранить/очистить cookies |
| POST | `/shutdown` | Kill subprocess + stop server |

### 5.2. Формат /probe response

```json
{
  "title": "Video title",
  "duration": 123,
  "thumbnail": "https://...",
  "formats": [
    {"format_id": "137", "height": 1080, "display_label": "1080p", "filesize": 52428800, "ext": "mp4"},
    {"format_id": "136", "height": 720, "display_label": "720p", "filesize": 20971520, "ext": "mp4"}
  ],
  "audio_formats": [
    {"format_id": "140", "abr": 129.5, "filesize": 5242880, "ext": "m4a"},
    {"format_id": "251", "abr": 126.6, "filesize": 4194304, "ext": "webm"}
  ]
}
```

### 5.3. Форматы скачивания

| Режим | Формат yt-dlp | Дополнительно |
|-------|---------------|---------------|
| Видео (кнопка) | `format_id+bestaudio/best` | `--merge-output-format mp4` |
| Видео (авто, с ffmpeg) | `bestvideo+bestaudio/best` | `--merge-output-format mp4 --postprocessor-args ffmpeg:-c:a aac -b:a 128k` |
| Видео (авто, без ffmpeg) | `best[ext=mp4]/best` | — |
| Аудио | `bestaudio[ext=m4a]/bestaudio` | `--extract-audio --audio-format mp3 --audio-quality 0` |

### 5.4. Обработка ошибок yt-dlp

Функция `map_ytdlp_error()` маппит stderr yt-dlp в понятные сообщения на русском:

| Ошибка yt-dlp | Сообщение пользователю |
|---|---|
| `Unsupported URL` | Неправильная ссылка или сайт не поддерживается |
| `Video unavailable` | Видео недоступно или удалено |
| `Private video` | Видео приватное — нужны cookies для доступа |
| `Login required` | Требуется вход в аккаунт — загрузите cookies |
| `403 Forbidden` | Доступ заблокирован — попробуйте позже или используйте cookies |
| `404 Not Found` | Страница не найдена (404) |
| `429 Rate limit` | Слишком много запросов — подождите и попробуйте снова |
| `Network error` | Ошибка сети — проверьте подключение к интернету |
| `Geo-blocked` | Видео недоступно в вашем регионе |
| `Removed/Deleted` | Видео было удалено |
| `Copyright/DMCA` | Видео заблокировано по запросу правообладателя |
| `Age restricted` | Видео с возрастным ограничением — нужны cookies |
| Другое | Не удалось обработать ссылку — проверьте правильность или используйте cookies |

---

## 6. Пользовательский интерфейс

### 6.1. Экраны

**Setup Screen** (первый запуск):
- Иконка ⚙️ с pulse-анимацией
- Заголовок "Первоначальная настройка"
- Кнопка "🚀 Настроить и запустить"
- Прогресс-бар с shimmer-анимацией
- Автоматический переход на Download Screen

**Download Screen** (основной):
- Заголовок "🎬 Video Downloader"
- Поле ввода URL с валидацией (блокируется во время загрузки)
- Бейдж платформы
- Переключатель 🎥 Видео / 🎵 Аудио (блокируется во время загрузки)
- Динамические кнопки разрешений/аудио
- Прогресс-бар загрузки (реальный процент)
- Toast для ошибок
- Кнопка "✕ Остановить" (скрыта по умолчанию, появляется при загрузке, исчезает через 2 сек после отмены с fade-out)
- Footer: v0.1.0 (слева), 🍪 Cookies ? (центр), 📁 Загрузки (справа)

### 6.2. Размер окна

- **Размер:** 620×720 пикселей
- **Resizable:** Да (можно изменять размер)
- **Скролл:** Контент скроллится если не помещается

### 6.3. Цветовая схема

- Фон: `#0f0f1a` (тёмно-фиолетовый)
- Карточка: `#1a1a2e`
- Акцент: `#6c5ce7` → `#a78bfa` (градиент)
- Текст: `#e0e0e0`
- Успех: `#4caf50`
- Ошибка: `#ff4757`

### 6.4. Cookies Dialog

**Назначение**: загрузка cookies для доступа к приватному контенту (LinkedIn и др.)

**Элементы**:
- Drag & drop зона (или click → `<input type="file" accept=".txt">`)
- Textarea для ручной вставки содержимого cookies.txt
- ?-тултип рядом с «Как получить cookies.txt ?» — инструкция по экспорту из браузера
- Кнопки: Отмена, Очистить, Сохранить

**Поведение**:
- При сохранении: кнопка блокируется → «⏳ Сохраняю…» → «✅ Готово» → автозакрытие через 1.2с
- При ошибке: разблокировка кнопки, красное сообщение об ошибке
- Cookies сохраняются в `.cookies.txt` (_BASE_DIR), используются yt-dlp через `--cookies`
- Валидация: проверка формата Netscape cookie file, ограничение размера 1MB

**pywebview нюанс**: тултипы через JS `onmouseenter`/`onmouseleave` (CSS `:hover` не работает)

### 6.5. Локализация

Интерфейс полностью на русском языке.

---

## 7. Поддерживаемые платформы и форматы

### 7.1. Определение платформы

| Платформа | Домены |
|-----------|--------|
| YouTube | `youtube.com`, `youtu.be` |
| Twitter/X | `twitter.com`, `x.com` |
| TikTok | `tiktok.com` |
| Instagram | `instagram.com` |
| Facebook | `facebook.com`, `fb.com`, `fb.watch` |
| LinkedIn | `linkedin.com` |
| Другое | Все остальные (через yt-dlp generic) |

### 7.2. Особенности платформ

| Платформа | Особенность | Решение |
|-----------|-------------|---------|
| LinkedIn | `vcodec=None`, `video_ext=mp4` | Определение по `video_ext` |
| LinkedIn | Нет данных о разрешении | Label "Скачать видео" |
| LinkedIn | Длинные title с UTM | Обрезка до 120 символов |
| Instagram | `format_note="DASH video"` | Пропуск DASH, использование разрешения |
| Facebook | Форматы `sd`/`hd` без разрешения | Label из `format_id.upper()` |
| YouTube Shorts | Вертикальное видео 1080×1920 | `eff_height = width` → "1080p" |

### 7.3. Дедупликация форматов

**Видео:** Группировка по стандартным бакетам: 144, 240, 360, 480, 720, 1080, 1440, 2160, 4320.

**Аудио:** Группировка по битрейту (шаг 16kbps), сортировка по убыванию, максимум 3 формата.

---

## 8. Сборка и развёртывание

### 8.1. Быстрый запуск (без сборки)

```bash
# Windows
start.bat

# Linux/macOS/WSL
bash start.sh
```

### 8.2. PyInstaller

```bash
# Windows
pyinstaller video-downloader-win.spec --clean

# Linux / macOS
pyinstaller video-downloader.spec --clean
```

### 8.3. CI/CD (GitHub Actions)

**Триггеры:** push в main, тег v*, ручной запуск

**Пайплайн:**
1. **test** — pytest (57 тестов)
2. **build** — PyInstaller для Linux/Windows/macOS
3. **release** — GitHub Release (только для тегов v*)

**Артефакты:**
- `Instrumentarium-linux.tar.gz`
- `Instrumentarium-windows.zip`
- `Instrumentarium-macos.tar.gz`

### 8.4. Правила PyInstaller one-file

1. `_BASE_DIR = dirname(sys.executable)` — папка с .exe
2. **НИКОГДА** `subprocess.Popen([sys.executable, ...])` — fork bomb!
3. Сервер in-process через `import server_main` + `threading.Thread`
4. `os.chdir(_BASE_DIR)` в server thread
5. `CREATE_NO_WINDOW` для всех subprocess на Windows
6. `sys.stdout = sys.stderr = open(os.devnull, 'w')` до любого импорта

---

## 9. Ограничения и известные проблемы

### 9.1. Текущие ограничения

| # | Ограничение | Описание |
|---|-------------|----------|
| L-01 | Без ffmpeg качество ограничено | Без ffmpeg нельзя мержить DASH-потоки. Shorts до ~360p |
| L-02 | Нет выбора папки сохранения | Всегда `~/Downloads` |
| L-03 | Нет очереди загрузок | Одна загрузка за раз |
| L-04 | Нет паузы/возобновления | Нельзя приостановить или возобновить загрузку |
| L-05 | Нет прокси | Прокси не поддерживается |
| L-06 | Нет выбора формата аудио | Только MP3 |

### 9.2. Текущая ветка: v0.1.0 (2026-06-27)

**Решено:**
- ✅ Отмена загрузки с убийством process tree
- ✅ Блокировка URL-поля и кнопок режимов во время загрузки
- ✅ Плавное исчезновение прогресс-бара после отмены
- ✅ Debug-консоль (F12) для диагностики
- ✅ Убийство process tree на Windows (taskkill /F /T /PID)

**Текущая версия:** v0.1.0 (branch `v0.1.0`)

### 9.3. Планы

| # | План | Приоритет |
|---|------|-----------|
| P-01 | Очередь загрузок | Средний |
| P-02 | Выбор папки сохранения | Средний |
| P-03 | Пауза/возобновление | Низкий |
| P-04 | Tauri-рефакторинг | Низкий (долгосрочно) |

### A. Структура Git-репозитория

```
instrumentarium/
├── app.py                      # Десктоп-лаунчер
├── server_main.py              # HTTP server + handler
├── download.html               # UI
├── server/                     # Подпакет
│   ├── __init__.py
│   ├── state.py
│   ├── utils.py
│   ├── errors.py
│   ├── setup.py
│   └── download.py
├── start.sh / start.bat        # Быстрый запуск
├── assets/                     # Иконки
├── tests/                      # Тесты
│   ├── test_server.py          # Unit-тесты (36)
│   └── test_integration.py     # Интеграционные тесты (21)
├── video-downloader.spec       # PyInstaller spec (Linux/Mac)
├── video-downloader-win.spec   # PyInstaller spec (Windows)
├── .github/workflows/build.yml # CI/CD
├── CONTEXT.md                  # Рабочий контект
├── BUILD.md                    # Архитектурная документация
├── SPEC.md                     # Этот файл
└── README.md                   # Описание для GitHub
```

### B. Переменные окружения

| Переменная | Значение | Описание |
|------------|----------|----------|
| `INSTRUMENTARIUM_LOG` | `0` или `1` | Отключение логирования (по умолчанию: вкл) |

### C. Логирование

- **Файл:** `instrumentarium.log` (рядом с .exe)
- **Формат:** `%(asctime)s [%(levelname)s] %(message)s`
- **Уровень:** DEBUG
- **Ротация:** Нет (файл перезаписывается при каждом запуске)

---

*Последнее обновление: 2026-06-09*
