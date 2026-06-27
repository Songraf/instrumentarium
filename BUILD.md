# INSTRUMENTARIUM — Build & Architecture Reference

*Версия документа: 2026-06-27*
*Текущая ветка: v0.1.0*
*Этот документ — полное описание архитектуры, файлов, потоков данных и поведения приложения. Читать при начале любой новой сессии работы.*

---

## Содержание

1. [Что это за приложение](#1-что-это-за-приложение)
2. [Структура файлов](#2-структура-файлов)
3. [Архитектура: как всё работает](#3-архитектура-как-всё-работает)
4. [Потоки (Threads)](#4-потоки-threads)
5. [HTTP API](#5-http-api)
6. [Рабочие файлы](#6-рабочие-файлы)
7. [Форматы видео/аудио](#7-форматы-видеоаудио)
8. [Сборка (PyInstaller)](#8-сборка-pyinstaller)
9. [CI/CD](#9-cicd)
10. [Тестирование](#10-тестирование)
11. [Известные ограничения и планы](#11-известные-ограничения-и-планы)

---

## 1. Что это за приложение

**Instrumentarium** — портативное десктопное приложение для скачивания видео с YouTube, Twitter/X, TikTok, Instagram, Facebook, LinkedIn и 1000+ других сайтов.

**Ключевой принцип:** Распаковал → запустил → вставил ссылку → скачал. Без установщиков, без командной строки, без настройки. Всё в одной папке.

**Целевые платформы:** Windows, Linux, macOS

**Технологический стек:**
- Python 3.7+ (встроенный в билд)
- yt-dlp (скачивается автоматически при первом запуске)
- ffmpeg (скачивается автоматически на Windows)
- pywebview (нативное окно с HTML/CSS/JS UI)
- PyInstaller (сборка в standalone-бинарь)
- pytest (58 тестов)

---

## 2. Структура файлов

```
instrumentarium/
├── app.py                      # Точка входа: лаунчер, сервер, окно
├── server_main.py              # HTTP server + handler
├── download.html               # UI: setup wizard + загрузчик (тёмная тема, русский)
├── server/                     # Подпакет с бизнес-логикой
│   ├── __init__.py             # Реэкспорт всех публичных API
│   ├── state.py                # AppState — thread-safe контейнер состояния
│   ├── utils.py                # Утилиты (detect_platform, human_size, popen, etc.)
│   ├── errors.py               # Маппинг ошибок yt-dlp → русские сообщения
│   ├── setup.py                # Setup wizard (Python, yt-dlp, ffmpeg)
│   └── download.py             # JobLogger + probe-meta (async)
├── start.sh                    # Быстрый запуск (Linux/macOS/WSL)
├── start.bat                   # Быстрый запуск (Windows)
├── assets/
│   ├── icon.svg                # Исходная иконка (вектор)
│   ├── icon.png                # Иконка 512×512 (сгенерирована из SVG, в git)
│   ├── icon.ico                # Иконка 256×256 для Windows (сгенерирована, в git)
│   └── icon_build.py           # Утилита SVG → .ico/.png (не нужна в CI)
├── tests/
│   ├── test_server.py          # Unit-тесты (36 тестов)
│   └── test_integration.py     # Интеграционные тесты (21 тест)
├── video-downloader.spec       # PyInstaller spec (Linux/macOS)
├── video-downloader-win.spec   # PyInstaller spec (Windows, v1.2.0)
├── pytest.ini                  # Конфиг pytest
├── .github/workflows/build.yml # CI/CD
├── .gitignore                  # CONTEXT.md игнорируется
├── BUILD.md                    # Этот файл
├── SPEC.md                     # Техническая спецификация
├── USER_GUIDE.md               # Руководство пользователя
└── README.md                   # Описание проекта для GitHub
```

### Файлы, генерируемые при работе

```
<папка с .exe>/
├── instrumentarium.log         # Лог приложения (debug level)
├── .setup_done                 # Маркёр завершённой настройки (timestamp)
├── .instrumentarium.lock       # Lock-файл для единственного инстанса
├── .bin/                       # Скачанные бинарники
│   ├── yt-dlp.exe              # (Windows) или yt-dlp (Linux/macOS)
│   ├── ffmpeg.exe              # (Windows, скачивается автоматически)
│   └── ffprobe.exe             # (Windows, скачивается автоматически)
└── downloads/                  # Скачанные видео (системная папка ~/Downloads)
    ├── youtube/
    ├── twitter/
    ├── tiktok/
    ├── instagram/
    ├── facebook/
    ├── linkedin/
    └── other/
```

### Системная папка данных (скрытая)

```
Windows: %APPDATA%/.instrumentarium/
Linux:   ~/.instrumentarium/
macOS:   ~/Library/Application Support/.instrumentarium/
├── .cookies.txt                # Cookies (сохраняются между перезапусками)
└── instrumentarium.log          # Лог (также дублируется рядом с .exe)
```

**Важно:** Скачанные видео сохраняются в системную папку `~/Downloads`. Рабочие данные (cookies, logs) — в системной папке данных. Ничего не пишется в Temp или AppData\Local.

---

## 3. Архитектура: как всё работает

### 3.1. Точка входа: app.py

```
app.py (main thread)
  │
  ├── 1. Настройка stdout/stderr → devnull (Windows, console=False)
  │
  ├── 2. Вычисление _BASE_DIR:
  │       PyInstaller: dirname(sys.executable) — папка с .exe
  │       Dev:         dirname(__file__) — папка со скриптом
  │
  ├── 3. Настройка logging → FileHandler(_BASE_DIR/instrumentarium.log)
  │
  ├── 4. Single-instance lock:
  │       _BASE_DIR/.instrumentarium.lock
  │       Windows: msvcrt.locking()
  │       Unix:    fcntl.flock()
  │       Если lock не взят → выход (уже запущен)
  │
  ├── 5. Запуск server_thread (daemon):
  │       ├── import server_main
  │       ├── os.chdir(_BASE_DIR)
  │       ├── Проверка .setup_done:
  │       │   Есть → phase="silent_check", _ensure_deps() in bg thread
  │       │   Нет  → run_setup() in bg thread (показать wizard)
  │       └── ThreadingHTTPServer на 0.0.0.0:18765 (timeout=0.5s)
  │
  ├── 6. Ожидание готовности сервера (max 5s, polling 127.0.0.1:18765)
  │
  └── 7. Открытие окна pywebview:
          Размер: 620×720, resizable=True
          Windows: edgechromium → auto-detect (CEF удалён для уменьшения размера билда)
          Linux:   auto-detect (GTK/Qt)
          macOS:   auto-detect (Cocoa)
          При закрытии → HTTP POST /shutdown → kill subprocess → stop server (без sleep, мгновенный exit)
```

### 3.2. Backend: server_main.py + server/ подпакет

```
server_main.py (импортируется как модуль из app.py)
  │
  ├── Конфигурация:
  │     PORT = 18765
  │     _BASE_DIR = вычисляется так же как в app.py
  │     SETUP_MARKER = _BASE_DIR/.setup_done
  │     OUTPUT_BASE  = ~/Downloads (системная папка)
  │     _BIN_CANDIDATES = [_BASE_DIR/.bin, _EXE_DIR/.bin, _MEIPASS/.bin]
  │     YT_DLP = _BIN_CANDIDATES[0]/yt-dlp.exe
  │
  ├── state (singleton из server/state.py):
  │     setup_phase: idle | checking | silent_check | installing_python |
  │                   installing_ytdlp | installing_ffmpeg | done | error
  │     setup_progress: 0-100
  │     setup_messages: [{text, type, time}]
  │     python_ok, ytdlp_ok, server_started, error
  │     cookies_path: str | None
  │     active_proc: subprocess.Popen | None
  │     output_base: str | None
  │     bin_candidates: list | None
  │
  ├── Rate limiters:
  │     _probe_limiter: token bucket (max_tokens=3, refill_rate=0.5/s)
  │     _download_limiter: token bucket (max_tokens=2, refill_rate=0.2/s)
  │
  ├── HTTP Handler (Handler):
  │     GET  /              → download.html (с path traversal protection)
  │     GET  /status        → JSON state.get_setup_response() + cookies_path + cookies_file_exists
  │     GET  /probe         → JSON {title, formats, audio_formats} (rate limited)
  │     GET  /probe-meta    → JSON {job_id} (async, rate limited)
  │     GET  /probe-meta-status → JSON {status, filesize}
  │     GET  /cookies       → JSON {ok, content, path} — текущие cookies (cache-bust)
  │     GET  /log           → JSON {lines, status}
  │     GET  /open-folder   → открыть папку downloads
  | GET  /cancel        → kill active download (legacy, use POST) |
  | POST /cancel        → kill active download + cleanup .part/.ytdl files (tracked cleanup) |
  │     POST /setup         → запустить setup wizard
  │     POST /download      → запустить скачивание (rate limited)
  │     POST /cookies       → сохранить/очистить cookies (с валидацией, thread-safe)
  │     POST /shutdown      → kill subprocess + stop server
  │
  └── JobLogger (threading.Thread, daemon):
        - Video: format_id+bestaudio/best → --merge-output-format mp4
        - Audio: bestaudio[ext=m4a]/bestaudio → --extract-audio → mp3
        - Если ffmpeg: --recode-video mp4, --embed-metadata, --embed-thumbnail
        - Имя файла: %(title).120s [%(id)s].%(ext)s (ограничение 120 символов)
```

---

## 4. Потоки (Threads)

```
Main Thread (app.py)
  └── pywebview event loop (блокирует main thread)

Server Thread (daemon, app.py → _start_server_in_thread)
  └── ThreadingHTTPServer.serve_forever(timeout=0.5)
        └── Handler.do_GET/do_POST (вызывается из HTTP-потока сервера)

Setup Thread (daemon, server/setup.py → run_setup или _ensure_deps)
  └── Проверка/установка Python, yt-dlp, ffmpeg

Download Thread (daemon, server/download.py → JobLogger)
  └── subprocess.Popen(yt-dlp) → proc.communicate() → parse stdout

ProbeMeta Thread (daemon, server/download.py → _run_probe_meta)
  └── yt-dlp --download-sections "*0-30" → скачивает 30 сек видеоконтента → экстраполирует на полную длительность
  └── Кэш результатов ключён по (url, format_id) — сохраняется при переключении режимов

Cleanup Thread (daemon, server_main.py → _cleanup_worker)
  └── Каждые 5 минут: cleanup_old_jobs(max_age_seconds=3600)
```

**Важно:**
- Server thread — daemon, при завершении main thread убивается
- При закрытии окна pywebview → `_on_closing()` → HTTP POST /shutdown → kill subprocess → server.stop → daemon threads die
- `os.chdir(_BASE_DIR)` выполняется в server thread — это меняет CWD для всего процесса
- `state.active_proc` — thread-safe через `threading.Lock`

---

## 5. HTTP API

### GET / или /index.html
Отдаёт `download.html` (ищет в _BIN_CANDIDATES, затем _MEIPASS, затем SCRIPT_DIR). Path traversal protection через `os.path.realpath()`.

### GET /status
```json
{
  "phase": "idle|checking|silent_check|installing_python|installing_ytdlp|installing_ffmpeg|done|error",
  "progress": 0-100,
  "messages": [{"text": "...", "type": "info|ok|err", "time": 1234567890}],
  "python_ok": true|false,
  "ytdlp_ok": true|false,
  "server_started": true|false,
  "error": null|"error message",
  "setup_done": true|false
}
```

### GET /probe?url=URL
```json
{
  "title": "Video title",
  "duration": 123,
  "thumbnail": "https://...",
  "formats": [
    {"format_id": "137", "height": 1080, "display_label": "1080p", "filesize": 52428800, "ext": "mp4"}
  ],
  "audio_formats": [
    {"format_id": "140", "abr": 129.5, "filesize": 5242880, "ext": "m4a"}
  ]
}
```

### GET /probe-meta?url=URL&format_id=ID&duration=N
```json
{"job_id": "abc12345"}
```

### GET /probe-meta-status?id=ID
```json
{"status": "running|done|error", "filesize": 12345, "probe_duration": 15}
```

### GET /log?job=JOB_ID&offset=N
```json
{"lines": ["[yt-dlp] ...", "[download] ..."], "status": "running|done|error"}
```

### GET /open-folder
Открывает папку downloads в файловом менеджере:
- Windows: `explorer <path>`
- macOS: `open <path>`
- Linux: `xdg-open <path>`

### GET /cancel
Убивает активный yt-dlp subprocess (legacy, используйте POST).

### POST /cancel
Убивает активный yt-dlp subprocess и все дочерние процессы (ffmpeg, aria2c):
- Windows: `taskkill /F /T /PID` (убивает дерево процессов)
- Linux/macOS: `os.killpg(SIGKILL)` (убивает process group)
- Очищает `.part`, `.ytdl` файлы из папки загрузок
- Помечает job как `cancelled: true`
- Возвращает `{"ok": true, "message": "Download cancelled"}`

### POST /setup
Запускает setup wizard. Если уже настроено — возвращает `{"already_done": true}`.

### POST /download
```json
// Request body:
{"url": "https://youtube.com/watch?v=...", "mode": "video|audio", "format_id": "137", "acodec": "aac"}

// Response (success):
{"job_id": "abc12345", "platform": "youtube"}

// Response (deps not ready):
{"deps_ready": true}  → JS повторяет запрос через 500ms

// Response (error):
{"error": "..."}
```

### POST /cookies
```json
// Save:
{"content": "base64 encoded cookies.txt"} → {"ok": true, "path": "/path/to/.cookies.txt"}

// Clear:
{} → {"ok": true, "path": null}
```

Валидация: проверка формата Netscape cookie file, ограничение размера 1MB.

### POST /shutdown
Убивает активный yt-dlp subprocess (если есть), останавливает HTTP сервер.

---

## 6. Рабочие файлы

### 6.1. Лог файл: `instrumentarium.log`
- **Расположение:** Рядом с .exe / скриптом (`_BASE_DIR`)
- **Формат:** `%(asctime)s [%(levelname)s] %(message)s`
- **Уровень:** DEBUG
- **Нет StreamHandler** — только FileHandler (чтобы не создавать консоль на Windows)
- **Отключение:** `INSTRUMENTARIUM_LOG=0`

### 6.2. Маркёр настройки: `.setup_done`
- **Расположение:** `_BASE_DIR/.setup_done`
- **Содержимое:** Timestamp завершения настройки (`YYYY-MM-DD HH:MM:SS`)
- **Создаётся:** После успешного `run_setup()` или `_ensure_deps()`
- **Удаляется:** В начале `run_setup()` (при повторной настройке)
- **Проверяется:** При каждом запуске для определения — показывать wizard или нет

### 6.3. Lock-файл: `.instrumentarium.lock`
- **Расположение:** `_BASE_DIR/.instrumentarium.lock`
- **Механизм:** `msvcrt.locking()` (Windows) / `fcntl.flock()` (Unix)
- **Назначение:** Запуск только одного инстанса приложения
- **Освобождение:** При выходе через `atexit`

### 6.4. Папка `.bin/`
- **Расположение:** `_BASE_DIR/.bin/`
- **Содержимое:**
  - `yt-dlp.exe` / `yt-dlp` — скачивается при первом запуске
  - `ffmpeg.exe` — скачивается автоматически (Windows, BtbN builds)
  - `ffprobe.exe` — скачивается автоматически (Windows, BtbN builds)

### 6.5. Папка `downloads/`
- **Расположение:** `~/Downloads` (системная папка)
- **Подпапки:** `youtube/`, `twitter/`, `tiktok/`, `instagram/`, `facebook/`, `linkedin/`, `other/`
- **Формат файлов:** `%(title).120s [%(id)s].%(ext)s` (ограничение 120 символов)

### 6.6. Cookies файл: `.cookies.txt`
- **Расположение:** `DATA_DIR/.cookies.txt` (системная папка: `%APPDATA%/.instrumentarium/` на Windows, `~/.instrumentarium/` на Linux)
- **Назначение:** авторизация на платформах, требующих вход (LinkedIn и др.)
- **Формат:** Netscape HTTP Cookie File
- **Управление:** через диалог 🍪 Cookies в UI (drag & drop, вставка текста, очистка, ✕ для сохранения)
- **Использование:** yt-dlp `--cookies <path>` при каждом запросе
- **Валидация:** проверка формата, ограничение размера 1MB
- **Thread-safe:** все операции защищены `_cookies_lock` (threading.Lock)
- **Persistence:** сохраняется между перезапусками программы
- **В .gitignore**: да, не коммитится

---

## 7. Форматы видео/аудио

### 7.1. Определение видео форматов

```python
# Обрабатывает LinkedIn (vcodec=None, video_ext=mp4) и другие платформы
is_video = (vcodec != "none" and vcodec is not None) or \
           (video_ext != "none" and video_ext is not None)
```

### 7.2. Эффективное разрешение

```python
# Для вертикальных видео (Shorts 1080x1920):
is_vertical = height > width
eff_height = width if is_vertical else height  # 1080, а не 1920
```

### 7.3. Логика display_label для кнопок

| Приоритет | Условие | Label |
|-----------|---------|-------|
| 1 | `format_note` существует и не содержит "DASH" | `format_note` (например "1080p") |
| 2 | `eff_height > 0` | `"{eff_height}p"` (например "720p") |
| 3 | `format_id` существует | `format_id.upper()` (например "SD", "HD" для Facebook) |
| 4 | Иначе | `"Скачать видео"` |

### 7.4. Форматы скачивания

```
Видео (конкретный формат из кнопки):
  format_id+bestaudio/best → --merge-output-format mp4

Видео (авто, с ffmpeg):
  bestvideo+bestaudio/best → --merge-output-format mp4 --postprocessor-args ffmpeg:-c:a aac -b:a 128k

Видео (авто, без ffmpeg):
  best[ext=mp4]/best

Аудио:
  bestaudio[ext=m4a]/bestaudio → --extract-audio --audio-format mp3 --audio-quality 0
```

### 7.5. Дедупликация форматов

**Видео:** группировка по стандартным бакетам (144, 240, 360, 480, 720, 1080, 1440, 2160, 4320).

**Аудио:** группировка по битрейту (шаг 16kbps), сортировка по убыванию, максимум 3 формата.

### 7.6. Особенности платформ

| Платформа | Особенность | Решение |
|-----------|-------------|---------|
| LinkedIn | `vcodec=None`, `video_ext=mp4` | Определение по `video_ext` |
| LinkedIn | Нет данных о разрешении | Label "Скачать видео" |
| LinkedIn | Длинные title с UTM | Обрезка до 120 символов |
| Instagram | `format_note="DASH video"` | Пропуск DASH, использование разрешения |
| Facebook | Форматы `sd`/`hd` без разрешения | Label из `format_id.upper()` |
| YouTube Shorts | Вертикальное видео 1080×1920 | `eff_height = width` → "1080p" |

---

## 8. Сборка (PyInstaller)

### 8.1. Spec-файлы

**Windows:** `video-downloader-win.spec`
- `console=False` (без консоли)
- `--onefile` (всё в один .exe)
- Включает: `app.py`, `server_main.py`, `download.html`, `server/` подпакет, webview
- Иконка: `assets/icon.ico`
- Версия: `1.2.0`

**Linux/macOS:** `video-downloader.spec`
- Аналогично, но без cefpython3
- Иконка: `assets/icon.png`

### 8.2. Ключевые правила для PyInstaller one-file

1. **`sys._MEIPASS`** — путь к временной папке, куда PyInstaller извлекает файлы
2. **`sys.executable`** — путь к .exe → `_BASE_DIR = dirname(sys.executable)`
3. **НИКОГДА** не использовать `subprocess.Popen([sys.executable, ...])` — fork bomb!
4. **Сервер запускается in-process** через `import server_main` + `threading.Thread`
5. **`os.chdir(_BASE_DIR)`** — критически важна
6. **`CREATE_NO_WINDOW`** для всех subprocess на Windows
7. **`sys.stdout = sys.stderr = open(os.devnull, 'w')`** — до любого импорта

### 8.3. Команды сборки

```bash
# Windows
pyinstaller video-downloader-win.spec --clean

# Linux / macOS
pyinstaller video-downloader.spec --clean
```

---

## 9. CI/CD

### Workflow: `.github/workflows/build.yml`

**Триггеры:**
- `push` в `main`
- Тег `v*` (например, `v1.2.0`)
- Ручной запуск (`workflow_dispatch`)

**Пайплайн:**

```
test (ubuntu)
  └── pip install pytest → python -m pytest tests/ -v

build (matrix: linux, windows, macos; fail-fast: false)
  └── pip install pyinstaller pywebview
  └── Windows: pip install cefpython3
  └── pyinstaller <spec> --clean --distpath dist --workpath build
  └── Архивация: tar.gz (linux/macos) или raw binary (windows)

release (только при теге v*)
  └── GitHub Release с тремя файлами
```

**Артефакты:**
- `Instrumentarium-linux` — Linux binary (~20 MB)
- `Instrumentarium.exe` — Windows binary (~62 MB)
- `Instrumentarium-macos.tar.gz` — macOS binary (~20 MB)

---

## 9.1 Релизный процесс (критически важно)

### Правило: Все релизы — только после одобрения

Разработка и релизы строго разделены. Автоматические релизы из `main` **отключены по договорённости**.

### Пайплайн разработки

```
┌─────────────────────────────────────────────────────────────────────┐
│                        1. РАЗРАБОТКА                                │
│                                                                     │
│   feature branch (например, v0.1.0) от main                         │
│     └── Код → Тесты → Локальная проверка (Playwright)               │
│     └── Агент НЕ пушит в main без явного указания пользователя      │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        2. ТЕСТИРОВАНИЕ ПОЛЬЗОВАТЕЛЕМ               │
│                                                                     │
│   Пользователь скачивает билд из CI (артефакт workflow)             │
│     └── Тестирует на реальной машине                                │
│     └── Если баги → возвращаемся в feature branch                   │
│     └── Если всё ок → "давай релиз"                                 │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        3. МЁРДЖ В MAIN                              │
│                                                                     │
│   feature branch → main (через PR или напрямую)                     │
│     └── Агент пушит в main ТОЛЬКО после явного одобрения            │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        4. ТЕГ И РЕЛИЗ                               │
│                                                                     │
│   Создание тега версии (например, v0.1.0)                            │
│     └── CI автоматически: test → build → release                    │
│     └── Тег создаётся ТОЛЬКО после мёрджа в main                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Что НЕЛЬЗЯ делать без одобрения

| Действие | Когда допустимо |
|----------|----------------|
| Push в feature branch | Всегда (это и есть работа) |
| Push в main | Только после одобрения пользователя |
| Создание тега `v*` | Только после мёрджа в main |
| Создание Release | Автоматически CI при теге (не вручную) |
| Сборка PyInstaller локально | Всегда (для тестирования) |

### Версионирование

- Начинаем с `v0.1.0`
- Каждый релиз увеличивает версию: `v0.1.0` → `v0.1.1` → `v0.2.0`
- Версия отображается в UI (footer, левый нижний угол)
- Версия НЕ пишется в `version=` в EXE() — это вызывает FileNotFoundError

### Локальная сборка для тестирования

```bash
# Собрать билд локально (для тестирования перед релизом)
python3 -m PyInstaller video-downloader.spec --clean

# Запустить собранный билд
./dist/Instrumentarium          # Linux
dist\Instrumentarium.exe       # Windows

# Или запустить из исходников (для быстрой итерации)
python3 server_main.py
```

### Тестирование UI в Docker-контейнере

```bash
# В контейнере есть Xvfb + fluxbox + chromium + Playwright
DISPLAY=:99 python3 -c "
from playwright.sync_api import sync_playwright
# ... см. tests/test_ui_sizes.py
"
```

---

## 10. Тестирование

```bash
python -m pytest tests/ -v
```

**57 тестов:**

### Unit-тесты (tests/test_server.py, 36 тестов)
- `detect_platform()` — 10 тестов (YouTube, Twitter, TikTok, Instagram, Facebook, LinkedIn, other, case-insensitive, empty URL, subdomains)
- `human_size()` — 5 тестов (bytes, KB, MB, GB, TB)
- `find_system_python()` — 3 теста (found, not found, too old)
- `get_python_install_url()` — 2 теста (Windows 64-bit, Linux)
- `check_ytdlp()` — 2 теста (found in bin, not found)
- `/status` endpoint — 1 тест (JSON response)
- `_map_ytdlp_error()` — 13 тестов (все типы ошибок, edge cases)
- `AppState` — 6 тестов (cookies_path, active_proc, reset_setup, add_message, get_setup_response, thread safety)
- `parse_speed()` — 5 тестов (MiB/s, KiB/s, B/s, fallback, empty)

### Интеграционные тесты (tests/test_integration.py, 21 тест)
- HTTP endpoints: /status, /probe, /probe-meta, /probe-meta-status, /download, /cancel, /cookies, /log, /setup
- CORS headers
- Rate limiting
- Path traversal protection
- Thread safety (concurrent add_message, concurrent get/set)

---

## 11. Известные ограничения и планы

### ✅ Решено (v0.1.0, 2026-06-27)
- Нативное окно приложения (pywebview + edgechromium)
- Портативность: всё в одной папке с .exe (видео в ~/Downloads)
- Setup wizard не появляется при повтором запуске (.setup_done)
- Закрытие без зависания (daemon thread + /shutdown endpoint)
- Zombie process: /shutdown убивает активный yt-dlp subprocess
- Glow animation: progress-bar получает класс .done (зелёный, без shimmer)
- Кнопка 📁 Загрузки открывает папку с файловым менеджере
- FFmpeg auto-install (Windows, BtbN builds)
- Нет консольных окон на Windows (CREATE_NO_WINDOW, devnull stdout)
- CI/CD для всех трёх платформ
- 58 тестов, все проходят
- Lock-файл (один инстанс)
- LinkedIn видео: поддержка vcodec=None, video_ext=mp4
- Аудио дорожка: +bestaudio/best в формате скачивания
- Аудио UI: битрейт + размер на кнопках
- Переменный размер окна (resizable=True)
- Вертикальные видео (Shorts): корректное отображение разрешения
- Cookies система: drag & drop / вставка cookies.txt, LinkedIn авторизация
- Cookies persistence: хранение в системной папке, восстановление при перезапуске
- Cookies thread-safe: _cookies_lock для ThreadingHTTPServer
- Безопасная очистка при отмене (tracked cleanup — только файлы yt-dlp, не snapshot diff)
- ?-тултипы: JS onmouseenter/onmouseleave (CSS hover не работает в pywebview)
- Визуальный фидбек кнопок: scale(.96) при :active, блокировка во время запроса
- Маппинг ошибок yt-dlp: `_map_ytdlp_error()` → понятные сообщения на русском
- Оптимизация Windows-сборки: удалён CEF (~100+ MB меньше)
- **Размеры файлов на кнопках** — показываются сразу из /probe
- **Асинхронный probe-meta** — не блокирует сервер, кэшируется по (url, format_id)
- **ThreadingHTTPServer** — многопоточный HTTP-сервер
- **Rate limiting** — защита от злоупотреблений
- **CORS headers** — совместимость с разными origin
- **Path traversal protection** — безопасность
- **Thread-safe AppState** — потокобезопасность
- **TTL cleanup** — автоматическая очистка старых jobs
- **fetch() API** — вместо XMLHttpRequest для /cancel (совместимость с WebView2)
- **Реальный прогресс-бар** — парсинг процента из yt-dlp output
- **Отмена загрузки** — POST /cancel с убийством process tree (taskkill на Windows)
- **Очистка .part/.ytdl** — удаление незавершённых файлов при отмене
- **Блокировка URL-поля** — отключается во время загрузки
- **~ для примерного размера** — знак тильды когда размер определён неточно
- **Debug-панель (F12)** — перехват console.log/error/warn для диагностики
- **Плавное исчезновение прогресса** — fade-out через 2 сек после отмены
- **Блокировка переключения режимов** — кнопки Видео/Аудио отключаются во время загрузки
- **Валидация cookies** — проверка формата и размера
- **Интеграционные тесты** — 21 тест для HTTP endpoints
- **Рефакторинг** — разделение на server/ подпакет
- **Footer layout** — v0.1.0 слева, 🍪 Cookies по центру, 📁 Загрузки справа
- **Cookie tooltip** — кнопка ? с инструкцией при наведении

### ⬜ Планы/в работе
- Расширить тесты: HTTP-эндпоинты, JobLogger
- Tauri-рефакторинг (долгосрочно)

---

*Последнее обновление: 2026-06-27*
