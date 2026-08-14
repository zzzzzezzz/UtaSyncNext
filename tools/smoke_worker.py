from __future__ import annotations

import multiprocessing as mp
import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.worker_entry import run_worker_demo


def main() -> None:
    msg_queue = mp.Queue()
    cancel_event = mp.Event()
    process = mp.Process(target=run_worker_demo, args=(msg_queue, cancel_event))

    process.start()
    messages = []
    deadline = time.monotonic() + 15

    while time.monotonic() < deadline:
        while True:
            try:
                messages.append(msg_queue.get_nowait())
            except queue.Empty:
                break

        if not process.is_alive():
            break

        time.sleep(0.05)

    if process.is_alive():
        cancel_event.set()
        process.terminate()
        process.join(2)
        raise SystemExit("worker timed out")

    process.join(2)

    while True:
        try:
            messages.append(msg_queue.get_nowait())
        except queue.Empty:
            break

    print(f"exitcode={process.exitcode}")
    print(f"message_count={len(messages)}")
    print(f"last_message={messages[-1] if messages else None}")
    print("types=" + ",".join(str(message.get("type", "")) for message in messages))

    if process.exitcode != 0:
        raise SystemExit(process.exitcode)
    if not messages or messages[-1].get("type") != "done":
        raise SystemExit("worker did not report done")


if __name__ == "__main__":
    mp.freeze_support()
    main()
