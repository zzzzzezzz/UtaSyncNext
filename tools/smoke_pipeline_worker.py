from __future__ import annotations

import multiprocessing as mp
import queue
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.pipeline_worker import run_pipeline_worker


def main() -> int:
    msg_queue: mp.Queue = mp.Queue()
    cancel_event = mp.Event()
    process = mp.Process(
        target=run_pipeline_worker,
        args=(msg_queue, cancel_event, {"media_path": "", "lyrics": "", "settings": {}}),
    )
    process.start()
    messages: list[dict[str, object]] = []

    while process.is_alive():
        try:
            messages.append(msg_queue.get(timeout=0.1))
        except queue.Empty:
            pass

    process.join(timeout=1)
    while True:
        try:
            messages.append(msg_queue.get_nowait())
        except queue.Empty:
            break

    print(f"exitcode={process.exitcode}")
    print(f"message_count={len(messages)}")
    print(f"messages={messages}")
    return 0 if process.exitcode == 0 and any(m.get("type") == "error" for m in messages) else 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
