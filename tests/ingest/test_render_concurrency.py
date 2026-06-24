# tests/ingest/test_render_concurrency.py
import threading
import time

import directory.ingest.fetch as fetch


def test_render_slot_caps_concurrency(monkeypatch):
    """At most render_concurrency browser sessions hold a slot at once, even when
    far more workers contend for one — the discovery render-timeout guard."""
    monkeypatch.setattr(fetch, "_render_semaphore", threading.BoundedSemaphore(2))

    lock = threading.Lock()
    current = 0
    peak = 0

    def worker():
        nonlocal current, peak
        with fetch._render_slot():
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.05)
            with lock:
                current -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak <= 2  # never more than the cap, despite 8 contending workers


def test_render_semaphore_sized_from_settings(monkeypatch):
    monkeypatch.setattr(fetch, "_render_semaphore", None)
    monkeypatch.setenv("DIRECTORY_RENDER_CONCURRENCY", "3")
    sem = fetch._render_semaphore_for()
    assert sem._value == 3  # BoundedSemaphore initialised from the setting
