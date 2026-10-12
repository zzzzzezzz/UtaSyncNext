from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))


from core.translator import SubtitleTranslator, _is_non_retryable_api_error, _retry_delay_seconds
from engine.pipeline_worker import _update_translation_completion_marker
from ui.log_formatter import format_console_line
from ui.output_actions import OutputActionRunner
import ui.output_actions as output_actions_module


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeWorkspace:
    def update_outputs(self, _artifacts: dict[str, object]) -> None:
        pass


class FakeBurnThread:
    def __init__(self, alive: bool = False) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class FakeHost:
    def __init__(self, media: Path) -> None:
        self.media_path = str(media)
        self.controller = SimpleNamespace(is_running=False)
        self.burn_thread = FakeBurnThread(False)
        self.last_outputs: dict[str, str] = {}
        self.workspace = FakeWorkspace()
        self.logs: list[str] = []

    def append_log(self, text: str) -> None:
        self.logs.append(text)


class FakeCancelEvent:
    def __init__(self, cancel_on_wait: bool = False) -> None:
        self.cancel_on_wait = cancel_on_wait
        self.waits: list[int] = []
        self.cancelled = False

    def is_set(self) -> bool:
        return self.cancelled

    def wait(self, seconds: int) -> bool:
        self.waits.append(seconds)
        if self.cancel_on_wait:
            self.cancelled = True
        return self.cancelled


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **_kwargs: object) -> object:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def fake_response(content: str) -> object:
    return SimpleNamespace(
        id="local-response",
        model="local-test",
        system_fingerprint="",
        created=0,
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))],
    )


def test_cache_cleanup(root: Path) -> None:
    media = root / "fixture.mp4"
    media.write_bytes(b"media")
    task = root / "output" / media.stem
    audio = task / "audio"
    separated = task / "separated"
    subtitles = task / "subtitles"
    logs = task / "logs"
    for path in (audio, separated, subtitles, logs):
        path.mkdir(parents=True, exist_ok=True)
    (audio / "source.wav").write_bytes(b"audio-cache")
    (separated / "vocals.wav").write_bytes(b"separation-cache")
    source_srt = subtitles / f"{media.stem}_极致打轴.srt"
    batch_cache = subtitles / f"{media.stem}_极致打轴_翻译缓存.json"
    error_log = logs / "utasyncnext_error_fixture.txt"
    source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n原文\n", encoding="utf-8")
    batch_cache.write_text('{"batch": "translated"}', encoding="utf-8")
    error_log.write_text("error details", encoding="utf-8")

    host = FakeHost(media)
    runner = OutputActionRunner(host)
    original_root = output_actions_module.app_root
    original_warning = output_actions_module.messagebox.showwarning
    original_info = output_actions_module.messagebox.showinfo
    original_question = output_actions_module.messagebox.askyesno
    warnings: list[str] = []
    try:
        output_actions_module.app_root = lambda: root  # type: ignore[assignment]
        output_actions_module.messagebox.showwarning = (  # type: ignore[assignment]
            lambda _title, message, **_kwargs: warnings.append(str(message))
        )
        output_actions_module.messagebox.showinfo = lambda *_args, **_kwargs: None  # type: ignore[assignment]
        output_actions_module.messagebox.askyesno = lambda *_args, **_kwargs: True  # type: ignore[assignment]

        host.controller.is_running = True
        runner.clear_cache()
        require(audio.exists() and separated.exists(), "运行中仍删除了任务缓存")
        require(warnings and "任务仍在使用" in warnings[-1], "运行中清理没有给出普通用户提示")

        host.controller.is_running = False
        runner.clear_cache()
        require(not audio.exists() and not separated.exists(), "空闲时没有清理音频中间缓存")
        require(source_srt.is_file(), "缓存清理误删原文 SRT")
        require(batch_cache.is_file(), "缓存清理误删翻译断点")
        require(error_log.is_file(), "缓存清理误删错误日志")
    finally:
        output_actions_module.app_root = original_root  # type: ignore[assignment]
        output_actions_module.messagebox.showwarning = original_warning  # type: ignore[assignment]
        output_actions_module.messagebox.showinfo = original_info  # type: ignore[assignment]
        output_actions_module.messagebox.askyesno = original_question  # type: ignore[assignment]


def test_translation_cache_and_completion(root: Path) -> None:
    source_srt = root / "fixture.srt"
    source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n原文\n", encoding="utf-8")
    translator = SubtitleTranslator(
        api_key="local-only",
        base_url="http://127.0.0.1:1/v1",
        primary_model="local-test",
        srt_path=str(source_srt),
        output_mode="bilingual",
    )
    translator._save_to_cache("batch-a", "translated-a")
    translator._save_to_cache("batch-b", "translated-b")
    saved = json.loads(translator.cache_file.read_text(encoding="utf-8"))
    require(saved == {"batch-a": "translated-a", "batch-b": "translated-b"}, "翻译缓存原子保存结果异常")
    require(not translator.cache_file.with_suffix(translator.cache_file.suffix + ".tmp").exists(), "残留翻译缓存临时文件")

    marker = root / "translation.hash"
    marker.write_text("stale", encoding="utf-8")
    completed = _update_translation_completion_marker(marker, "new-hash", failed_batches=1)
    require(not completed and not marker.exists(), "部分失败仍留下翻译完成指纹")
    completed = _update_translation_completion_marker(marker, "new-hash", failed_batches=0)
    require(completed and marker.read_text(encoding="utf-8") == "new-hash", "全部成功后没有写入完成指纹")


def test_retry_policy_and_logs() -> None:
    for code in (400, 401, 403, 404, 422):
        require(_is_non_retryable_api_error(f"Error code: {code} - invalid request"), f"HTTP {code} 未被识别为配置错误")
    for code in (408, 429, 500, 503):
        require(not _is_non_retryable_api_error(f"Error code: {code}"), f"HTTP {code} 不应直接停止重试")
    require(_retry_delay_seconds("Error code: 429", 0) == 8, "429 首次退避时间错误")
    require(_retry_delay_seconds("connection failed", 1) == 6, "普通网络错误退避时间错误")

    retry_line = format_console_line("批次 2/4 失败，3s 后重试: ConnectError connection refused")
    require("第 2/4 批" in retry_line and "无法连接翻译服务" in retry_line, "网络失败日志仍然晦涩")
    failed_line = format_console_line("批次 4/4 多次失败，保留原文不中断全局: timeout")
    require("重新启动会只补这个批次" in failed_line, "部分失败日志没有解释恢复方式")
    error_line = format_console_line("[错误] 翻译阶段失败: Error code: 404")
    require("具体原因已保存到错误日志" in error_line and "404" not in error_line, "主界面仍直接暴露 HTTP 术语")


def test_real_retry_loop(root: Path) -> None:
    source_srt = root / "retry-fixture.srt"
    source_text = "1\n00:00:00,000 --> 00:00:01,000\nテスト原文\n"
    translated_text = "1\n00:00:00,000 --> 00:00:01,000\nテスト原文\n测试译文\n"
    source_srt.write_text(source_text, encoding="utf-8")

    def translator() -> SubtitleTranslator:
        return SubtitleTranslator(
            api_key="local-only",
            base_url="http://127.0.0.1:1/v1",
            primary_model="local-test",
            srt_path=str(source_srt),
            output_mode="bilingual",
        )

    permanent = translator()
    permanent_blocks = permanent.parse_srt()
    permanent_client = FakeCompletions([RuntimeError("Error code: 404 - model not found")])
    permanent.client = SimpleNamespace(chat=SimpleNamespace(completions=permanent_client))
    try:
        permanent.translate_single_batch(permanent_blocks, "", 0, 1, "", "local-test")
        raise AssertionError("404 配置错误没有立即终止")
    except RuntimeError as exc:
        require("404" in str(exc), "404 配置错误被替换成了无关异常")
    require(permanent_client.calls == 1, "404 配置错误发生了无意义重试")
    require(permanent.fatal_error_event.is_set(), "永久配置错误没有停止剩余批次")
    try:
        permanent.translate_single_batch(permanent_blocks, "", 0, 1, "", "local-test")
        raise AssertionError("永久配置错误后仍启动后续批次")
    except RuntimeError as exc:
        require("翻译配置错误" in str(exc), "后续批次没有收到配置错误停止信号")
    require(permanent_client.calls == 1, "永久配置错误后仍请求了翻译服务")

    transient = translator()
    transient_blocks = transient.parse_srt()
    transient_event = FakeCancelEvent(False)
    transient.cancel_event = transient_event
    transient_client = FakeCompletions([RuntimeError("Error code: 500"), fake_response(translated_text)])
    transient.client = SimpleNamespace(chat=SimpleNamespace(completions=transient_client))
    _, translated = transient.translate_single_batch(transient_blocks, "", 0, 1, "", "local-test")
    require(transient_client.calls == 2, "500 临时错误没有成功重试")
    require(transient_event.waits == [3], "500 重试没有执行预期退避")
    require("测试译文" in translated, "500 重试成功后没有返回译文")

    cancelled = translator()
    cancelled.translation_cache = {}
    cancelled.cache_file.unlink(missing_ok=True)
    cancelled_blocks = cancelled.parse_srt()
    cancelled_event = FakeCancelEvent(True)
    cancelled.cancel_event = cancelled_event
    cancelled_client = FakeCompletions([RuntimeError("Error code: 500")])
    cancelled.client = SimpleNamespace(chat=SimpleNamespace(completions=cancelled_client))
    try:
        cancelled.translate_single_batch(cancelled_blocks, "", 0, 1, "", "local-test")
        raise AssertionError("重试等待期间取消没有终止翻译")
    except RuntimeError as exc:
        require("用户中断" in str(exc), "取消没有返回用户中断状态")
    require(cancelled_client.calls == 1, "取消后仍继续请求翻译服务")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasyncnext-production-stability-") as temp_dir:
        root = Path(temp_dir)
        test_cache_cleanup(root)
        test_translation_cache_and_completion(root)
        test_retry_policy_and_logs()
        test_real_retry_loop(root)
    print("PASS: production stability safeguards")
    print("- active-task cache cleanup blocked: yes")
    print("- subtitles, translation checkpoints and logs retained: yes")
    print("- translation batch cache writes are atomic: yes")
    print("- permanent API configuration errors fail fast: yes")
    print("- transient server errors retry and cancellation interrupts backoff: yes")
    print("- partial failures remain resumable: yes")
    print("- ordinary-language UI logs: yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
