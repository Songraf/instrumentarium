"""
UI-тест Instrumentarium: проверка отображения размера файла на кнопках.
Использует Playwright + Xvfb (виртуальный дисплей :99).

Что проверяет:
1. Размер файла отображается на кнопках (не "~", не пустой)
2. Размер стабильный при повторных запросах
3. Размер не сбрасывается при переключении видео<->аудио
4. Аудио тоже показывает размер
"""
import sys
import time
import subprocess
from playwright.sync_api import sync_playwright

URL = "https://disk.yandex.ru/i/287xCMK-eam2AQ"
SERVER_URL = "http://127.0.0.1:18765/"

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK: {msg}")
    else:
        failed += 1
        print(f"  FAIL: {msg}")


def check_sizes(page, label):
    """Check all download buttons have size info."""
    buttons = page.locator("button.res-btn")
    count = buttons.count()
    print(f"\n=== {label} ({count} buttons) ===")

    for i in range(count):
        btn = buttons.nth(i)
        text = btn.inner_text()
        has_size = "Определяю" not in text and "~" not in text
        has_mb = "MB" in text or "GB" in text or "KB" in text
        print(f"  [{i}] {text.strip()[:60]}")
        check(has_size, f"Button [{i}]: no file size found")
        check(has_mb, f"Button [{i}]: no unit (MB/GB)")

    return [buttons.nth(i).inner_text().strip() for i in range(count)]


def main():
    global passed, failed

    # Kill existing server
    subprocess.run(['pkill', '-f', 'server_main.py'], capture_output=True)
    time.sleep(1)

    # Start server
    print("Starting server...")
    server = subprocess.Popen(
        [sys.executable, "server_main.py"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    time.sleep(3)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--remote-allow-origins=*",
                    "--disable-gpu",
                ],
            )
            page = browser.new_page()

            # Load page
            print(f"Loading {SERVER_URL}...")
            page.goto(SERVER_URL)
            page.wait_for_load_state("networkidle")
            time.sleep(1)

            # Enter URL
            print(f"Entering link: {URL[:40]}...")
            input_el = page.locator("input[type='url']").first
            input_el.click()
            input_el.fill(URL)
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle")
            time.sleep(3)

            # Wait for probe-meta to complete (buttons should have sizes)
            print("Waiting for size calculation...")
            page.wait_for_function(
                """() => {
                    const btns = document.querySelectorAll('button.res-btn');
                    if (btns.length === 0) return false;
                    for (const b of btns) {
                        if (b.textContent.includes('Определяю') || b.textContent.includes('~')) return false;
                    }
                    return true;
                }""",
                timeout=120000,
            )
            time.sleep(1)

            # Test 1: Video sizes should be populated
            print("\n--- Test 1: Video sizes ---")
            video_sizes = check_sizes(page, "Video")

            # Test 2: Switch to audio and check sizes
            print("\n--- Test 2: Switch to audio ---")
            audio_tab = page.locator("#optAudio").first
            if audio_tab.count() > 0:
                audio_tab.click()
                time.sleep(2)

                # Wait for audio probe-meta
                try:
                    page.wait_for_function(
                        """() => {
                            const btns = document.querySelectorAll('button.res-btn');
                            if (btns.length === 0) return false;
                            for (const b of btns) {
                                if (b.textContent.includes('Определяю') || b.textContent.includes('~')) return false;
                            }
                            return true;
                        }""",
                        timeout=120000,
                    )
                except:
                    print("  WARN: Audio size timeout (maybe no audio streams)")
                time.sleep(1)

                audio_sizes = check_sizes(page, "Audio")
            else:
                print("  WARN: Audio tab not found")

            # Test 3: Switch back to video, sizes should be cached
            print("\n--- Test 3: Return to video (cache) ---")
            video_tab = page.locator("#optVideo").first
            if video_tab.count() > 0:
                video_tab.click()
                time.sleep(2)
                cached_sizes = check_sizes(page, "Video (cached)")
                # Sizes should be same as before
                for i, (old, new) in enumerate(zip(video_sizes, cached_sizes)):
                    check(old == new, f"Button [{i}]: size changed after return")

            # Test 4: Determinism — re-probe same URL
            print("\n--- Test 4: Size determinism ---")
            page.reload()
            page.wait_for_load_state("networkidle")
            time.sleep(1)
            input_el = page.locator("input[type='url']").first
            input_el.click()
            input_el.fill(URL)
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle")
            time.sleep(3)

            page.wait_for_function(
                """() => {
                    const btns = document.querySelectorAll('button.res-btn');
                    if (btns.length === 0) return false;
                    for (const b of btns) {
                        if (b.textContent.includes('Определяю') || b.textContent.includes('~')) return false;
                    }
                    return true;
                }""",
                timeout=120000,
            )
            time.sleep(1)

            det_sizes = check_sizes(page, "Video (re-probe)")
            for i, (old, new) in enumerate(zip(video_sizes, det_sizes)):
                check(old == new, f"Button [{i}]: size unstable")

            browser.close()

    finally:
        server.terminate()
        server.wait(timeout=5)

    print(f"\n{'='*50}")
    print(f"Result: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
    else:
        print("All good!")


if __name__ == "__main__":
    main()
