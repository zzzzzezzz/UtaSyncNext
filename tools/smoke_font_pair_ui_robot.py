"""Exercise the real style-workbench font-pair workflow in an isolated app root.

The parent process launches two fresh Tk processes. The first creates a named
pair and an independent style; the second verifies restart persistence, then
renames and deletes the pair while retaining the style and font files.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRESET = "默认双语 (纯白+浅灰)"
PAIR_NAME = "robot-majiko"
RENAMED_PAIR = "robot-majiko-renamed"
STYLE_NAME = "robot-majiko-style"
CH_FONT = "M PLUS 1p"
JP_FONT = "Zen Kaku Gothic New"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _find_button(widget: object, label: str) -> object:
    import customtkinter as ctk

    if isinstance(widget, ctk.CTkButton) and str(widget.cget("text")) == label:
        return widget
    for child in widget.winfo_children():
        try:
            return _find_button(child, label)
        except LookupError:
            continue
    raise LookupError(f"找不到按钮：{label}")


def _phase(phase: str, sandbox_root: Path) -> dict[str, object]:
    os.environ["UTASYNCNEXT_APP_ROOT"] = str(sandbox_root)
    os.chdir(sandbox_root)
    if str(NEXT_ROOT) not in sys.path:
        sys.path.insert(0, str(NEXT_ROOT))

    import customtkinter as ctk

    from core.ass_maker import ASS_PRESETS
    from ui import gallery as gallery_module
    from ui.font_catalog import import_font, load_user_font_pairs, user_fonts_dir

    # Keep this test focused on the UI/persistence flow. Real libass rendering
    # and NVIDIA subtitle burning have separate regression checks.
    gallery_module.StyleGalleryWindow._request_ass_preview = lambda self, config: None

    baseline = copy.deepcopy(ASS_PRESETS[DEFAULT_PRESET])
    if phase == "create":
        for filename in ("MPLUS1p-Regular.ttf", "ZenKakuGothicNew-Medium.ttf"):
            import_font(NEXT_ROOT / "fonts" / filename)

    root = ctk.CTk()
    root.withdraw()
    gallery = None
    original_askstring = gallery_module.simpledialog.askstring
    original_askyesno = gallery_module.messagebox.askyesno
    prompt_answers: list[str] = []
    confirmations: list[str] = []

    def answer_prompt(*_args: object, **_kwargs: object) -> str:
        _require(prompt_answers, "出现了未预期的输入对话框")
        return prompt_answers.pop(0)

    def confirm(_title: str, message: str, **_kwargs: object) -> bool:
        confirmations.append(message)
        return True

    gallery_module.simpledialog.askstring = answer_prompt
    gallery_module.messagebox.askyesno = confirm
    try:
        gallery = gallery_module.StyleGalleryWindow(
            master=root,
            current_preset=DEFAULT_PRESET if phase == "create" else STYLE_NAME,
            apply_callback=lambda _name: None,
            get_settings_cb=lambda: {},
            sync_presets_cb=lambda: None,
        )
        root.update()
        if phase == "create":
            _require(CH_FONT in gallery.ch_font_option.cget("values"), "中文导入字体未出现在工作台")
            _require(JP_FONT in gallery.jp_font_option.cget("values"), "日文导入字体未出现在工作台")
            gallery.ch_font_option.set(CH_FONT)
            gallery.jp_font_option.set(JP_FONT)
            prompt_answers.extend((PAIR_NAME, STYLE_NAME))
            _find_button(gallery, "保存命名搭配").invoke()
            _require(load_user_font_pairs().get(PAIR_NAME) == (CH_FONT, JP_FONT), "搭配没有保存")
            _find_button(gallery, "展开编辑").invoke()
            deadline = time.monotonic() + 3
            while gallery._editor_animating and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
            _require(not gallery._editor_animating, "样式编辑区展开超时")
            _find_button(gallery, "另存为新预设").invoke()
            _require(ASS_PRESETS[DEFAULT_PRESET] == baseline, "另存时覆盖了原预设")
            created = ASS_PRESETS.get(STYLE_NAME, {})
            _require((created.get("ch_font"), created.get("jp_font")) == (CH_FONT, JP_FONT), "新预设未带上字体搭配")
            _require(not prompt_answers, "输入对话框没有全部执行")
            _require((sandbox_root / "user_presets.json").is_file(), "新预设未写入磁盘")
            return {"phase": phase, "pair_saved": True, "style_saved": True, "original_unchanged": True}

        _require(load_user_font_pairs().get(PAIR_NAME) == (CH_FONT, JP_FONT), "重启后搭配丢失")
        _require(STYLE_NAME in ASS_PRESETS, "重启后新预设丢失")
        _require(ASS_PRESETS[DEFAULT_PRESET] == baseline, "重启后原预设发生变化")
        gallery.font_pair_option.set(f"我的：{PAIR_NAME}")
        gallery._on_font_pair_preview(gallery.font_pair_option.get())
        _require(gallery.rename_pair_btn.cget("state") == "normal", "我的搭配无法重命名")
        prompt_answers.append(RENAMED_PAIR)
        gallery.rename_pair_btn.invoke()
        pairs = load_user_font_pairs()
        _require(RENAMED_PAIR in pairs and PAIR_NAME not in pairs, "重命名未持久化")
        _require(gallery.font_pair_option.get() == f"我的：{RENAMED_PAIR}", "重命名后选择状态不正确")
        gallery.delete_pair_btn.invoke()
        _require(not load_user_font_pairs(), "删除搭配后文件仍保留旧数据")
        _require(any(STYLE_NAME in message for message in confirmations), "删除确认未告知使用中的预设")
        _require(ASS_PRESETS[STYLE_NAME]["ch_font"] == CH_FONT, "删除搭配修改了已有预设")
        _require(ASS_PRESETS[DEFAULT_PRESET] == baseline, "删除搭配修改了原预设")
        _require(len(list(user_fonts_dir().glob("*.ttf"))) == 2, "删除搭配误删字体文件")
        return {
            "phase": phase,
            "restart_persistence": True,
            "rename_persistence": True,
            "delete_warning_for_used_style": True,
            "style_and_files_preserved": True,
        }
    finally:
        gallery_module.simpledialog.askstring = original_askstring
        gallery_module.messagebox.askyesno = original_askyesno
        if gallery is not None and gallery.winfo_exists():
            gallery.destroy()
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("create", "reopen"))
    parser.add_argument("--sandbox-root", type=Path)
    parser.add_argument("--report", type=Path, default=NEXT_ROOT / "test_logs" / "font_pair_ui_robot.json")
    args = parser.parse_args()

    if args.phase:
        if args.sandbox_root is None:
            parser.error("--sandbox-root is required with --phase")
        result = _phase(args.phase, args.sandbox_root.resolve())
        print(json.dumps(result, ensure_ascii=False))
        return 0

    report: dict[str, object] = {"status": "failed", "checks": []}
    with tempfile.TemporaryDirectory(prefix="utasync-font-ui-robot-") as temp_dir:
        sandbox_root = Path(temp_dir)
        env = os.environ.copy()
        env["UTASYNCNEXT_APP_ROOT"] = str(sandbox_root)
        for phase in ("create", "reopen"):
            command = [sys.executable, str(Path(__file__).resolve()), "--phase", phase, "--sandbox-root", str(sandbox_root)]
            run = subprocess.run(command, cwd=sandbox_root, env=env, capture_output=True, text=True, timeout=45)
            if run.returncode != 0:
                report["failed_phase"] = phase
                report["error"] = (run.stderr or run.stdout).strip()[-4000:]
                break
            checks = report["checks"]
            assert isinstance(checks, list)
            checks.append(json.loads(run.stdout.strip().splitlines()[-1]))
        else:
            report["status"] = "passed"

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] != "passed":
        print(f"FAIL: UI robot stopped at {report.get('failed_phase')}: {report.get('error')}")
        return 1
    print("PASS: isolated UI robot created, reopened, renamed, and deleted a font pair")
    print(f"- report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
