"""Prove that preset deletions and renames survive a restart.

Pure checks cover the store helpers. Then two fresh Tk processes drive the real
workbench: the first deletes a built-in preset and renames another, the second
proves the state persisted and that "restore defaults" brings the built-ins back
without touching presets the user authored themselves.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

DELETED_PRESET = "莫兰迪绿 (清新治愈风)"
RENAMED_FROM = "默认双语 (纯白+浅灰)"
RENAMED_TO = "robot-renamed-default"
USER_STYLE = "robot-kept-style"


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


def _pure_checks() -> dict[str, object]:
    from core.ass_maker import BUILTIN_PRESETS
    from ui import preset_store

    with tempfile.TemporaryDirectory(prefix="utasync-preset-store-") as temp_dir:
        path = Path(temp_dir) / "user_presets.json"
        presets = {name: dict(config) for name, config in BUILTIN_PRESETS.items()}
        renamed = presets.pop(RENAMED_FROM)
        renamed["ch_color"] = "#123456"
        presets[RENAMED_TO] = renamed
        presets[USER_STYLE] = dict(BUILTIN_PRESETS[DELETED_PRESET])
        del presets[DELETED_PRESET]
        preset_store.write_preset_document(
            presets,
            removed=[DELETED_PRESET],
            renamed={RENAMED_FROM: RENAMED_TO},
            path=path,
        )

        saved, meta = preset_store.read_preset_document(path)
        _require(meta["removed"] == [DELETED_PRESET], "删除记录没有写入文件")
        _require(meta["renamed"] == {RENAMED_FROM: RENAMED_TO}, "改名记录没有写入文件")
        merged = preset_store.merge_presets(BUILTIN_PRESETS, saved, meta)
        _require(DELETED_PRESET not in merged, "被删除的内置预设又被合并回来")
        _require(RENAMED_TO in merged and RENAMED_FROM not in merged, "改名没有生效")
        _require(merged[RENAMED_TO]["ch_color"] == "#123456", "改名后的用户改动丢失")
        _require(USER_STYLE in merged, "用户自建预设没有被保留")

        restored = preset_store.restore_builtin_presets(BUILTIN_PRESETS, merged, meta)
        _require(DELETED_PRESET in restored, "恢复默认没有还原被删除的预设")
        _require(RENAMED_FROM in restored and RENAMED_TO not in restored, "恢复默认没有还原名称")
        _require(USER_STYLE in restored, "恢复默认误删了用户自建预设")

        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["AI: 旧配色"] = dict(BUILTIN_PRESETS[DELETED_PRESET])
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        saved, _meta = preset_store.read_preset_document(path)
        _require(not any(name.startswith("AI:") for name in saved), "旧 AI 配色没有被过滤")
        return {"phase": "pure", "tombstones": True, "renames": True, "restore": True, "ai_filter": True}


def _phase(phase: str, sandbox_root: Path) -> dict[str, object]:
    os.environ["UTASYNCNEXT_APP_ROOT"] = str(sandbox_root)
    os.chdir(sandbox_root)
    if str(NEXT_ROOT) not in sys.path:
        sys.path.insert(0, str(NEXT_ROOT))

    import customtkinter as ctk

    from core.ass_maker import ASS_PRESETS
    from ui import gallery as gallery_module

    gallery_module.StyleGalleryWindow._request_ass_preview = lambda self, config: None

    root = ctk.CTk()
    root.withdraw()
    gallery = None
    confirmations: list[str] = []
    prompts: list[str] = []
    original_askstring = gallery_module.simpledialog.askstring
    original_askyesno = gallery_module.messagebox.askyesno

    def confirm(title: str, message: str, **_kwargs: object) -> bool:
        confirmations.append(message)
        return True

    def answer(*_args: object, **_kwargs: object) -> str:
        return prompts.pop(0) if prompts else ""

    gallery_module.simpledialog.askstring = answer
    gallery_module.messagebox.askyesno = confirm
    try:
        gallery = gallery_module.StyleGalleryWindow(
            master=root,
            current_preset=DELETED_PRESET if phase == "edit" else RENAMED_TO,
            apply_callback=lambda _name: None,
            get_settings_cb=lambda: {},
            sync_presets_cb=lambda: None,
        )
        root.update()

        if phase == "edit":
            _require(DELETED_PRESET in ASS_PRESETS, "初始状态缺少内置预设")
            gallery._select_preset(DELETED_PRESET)
            root.update()
            _find_button(gallery, "删除预设").invoke()
            root.update()
            _require(DELETED_PRESET not in ASS_PRESETS, "删除没有生效")
            _require(any(DELETED_PRESET in message for message in confirmations), "删除没有二次确认")

            gallery._select_preset(RENAMED_FROM)
            root.update()
            prompts.append(RENAMED_TO)
            _find_button(gallery, "重命名预设").invoke()
            root.update()
            _require(RENAMED_TO in ASS_PRESETS, "重命名没有生成新名字")
            _require(RENAMED_FROM not in ASS_PRESETS, "重命名后旧名字还在")
            _require((sandbox_root / "user_presets.json").is_file(), "预设文件没有写入")
            return {"phase": phase, "deleted": True, "renamed": True}

        _require(DELETED_PRESET not in ASS_PRESETS, "重启后被删除的内置预设复活了")
        _require(RENAMED_TO in ASS_PRESETS and RENAMED_FROM not in ASS_PRESETS, "重启后改名丢失")
        _find_button(gallery, "恢复默认预设").invoke()
        root.update()
        _require(DELETED_PRESET in ASS_PRESETS, "恢复默认没有还原被删除的预设")
        _require(RENAMED_FROM in ASS_PRESETS, "恢复默认没有还原原名称")
        _require(RENAMED_TO not in ASS_PRESETS, "恢复默认后仍留着改名结果")
        return {"phase": phase, "restart_persistence": True, "restore_defaults": True}
    finally:
        gallery_module.simpledialog.askstring = original_askstring
        gallery_module.messagebox.askyesno = original_askyesno
        if gallery is not None and gallery.winfo_exists():
            gallery.destroy()
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("edit", "reopen"))
    parser.add_argument("--sandbox-root", type=Path)
    parser.add_argument("--report", type=Path, default=NEXT_ROOT / "test_logs" / "preset_store.json")
    args = parser.parse_args()

    if args.phase:
        if args.sandbox_root is None:
            parser.error("--sandbox-root is required with --phase")
        print(json.dumps(_phase(args.phase, args.sandbox_root.resolve()), ensure_ascii=False))
        return 0

    report: dict[str, object] = {"status": "failed", "checks": []}
    try:
        checks = report["checks"]
        assert isinstance(checks, list)
        checks.append(_pure_checks())
        with tempfile.TemporaryDirectory(prefix="utasync-preset-robot-") as temp_dir:
            sandbox_root = Path(temp_dir)
            env = os.environ.copy()
            env["UTASYNCNEXT_APP_ROOT"] = str(sandbox_root)
            for phase in ("edit", "reopen"):
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--phase",
                    phase,
                    "--sandbox-root",
                    str(sandbox_root),
                ]
                run = subprocess.run(
                    command, cwd=sandbox_root, env=env, capture_output=True, text=True, timeout=60
                )
                if run.returncode != 0:
                    report["failed_phase"] = phase
                    report["error"] = (run.stderr or run.stdout).strip()[-4000:]
                    break
                checks.append(json.loads(run.stdout.strip().splitlines()[-1]))
            else:
                report["status"] = "passed"
    except AssertionError as exc:
        report["error"] = str(exc)

    report_path = args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] == "passed":
        print("PASS: preset deletions, renames and restore-defaults persist across a restart")
        print(f"- report: {report_path}")
        return 0
    print(f"FAIL: {report.get('error')}")
    print(f"- report: {report_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
