"""README 用のスクリーンショットをデモデータで撮る。

実データでは絶対に撮らない。画面を変えたらこれを流し直す。

    python scripts/demo_data.py
    python scripts/capture_screenshots.py

Playwright と Chrome が必要（開発時のみ）:

    pip install playwright
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEMO_DB = ROOT / "data" / "demo.db"
OUTPUT = ROOT / "docs" / "screenshots"

# (保存名, サイドバーの項目, 追加で待つ秒数)
PAGES = [
    ("dashboard", "ダッシュボード", 1.0),
    ("companies", "企業管理", 1.0),
    ("review", "添削", 1.5),
    ("analytics", "分析", 2.0),
]

VIEWPORT = {"width": 1440, "height": 1000}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_up(port: int, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                time.sleep(3)  # 初回描画が落ち着くまで待つ
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError("アプリが起動しませんでした")


def main() -> None:
    from playwright.sync_api import sync_playwright

    if not DEMO_DB.exists():
        sys.exit("先に python scripts/demo_data.py を実行してください")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    environment = {**os.environ, "SHUKATSU_DB": str(DEMO_DB)}
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until_up(port)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome")
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=60_000)

            for name, label, pause in PAGES:
                page.get_by_test_id("stSidebar").get_by_text(label, exact=True).click()
                page.wait_for_timeout(int(pause * 1000))
                page.wait_for_load_state("networkidle")
                destination = OUTPUT / f"{name}.png"
                page.screenshot(path=str(destination))
                print(f"撮影: {destination.relative_to(ROOT)}")

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=20)


if __name__ == "__main__":
    main()
