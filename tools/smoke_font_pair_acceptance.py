"""Check the user-facing font-pair workflow without touching real user presets."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ass_maker import ASS_PRESETS
from ui.font_catalog import load_user_font_pairs, save_user_font_pairs


def main() -> None:
    base_name = "默认双语 (纯白+浅灰)"
    original = copy.deepcopy(ASS_PRESETS[base_name])
    new_name = "majiko 验收预设"
    pair = ("M PLUS 1p", "Zen Kaku Gothic New")
    with TemporaryDirectory(prefix="utasync-font-pair-acceptance-") as temp_dir:
        pair_file = Path(temp_dir) / "user_font_pairs.json"
        save_user_font_pairs({"majiko": pair}, pair_file)
        if load_user_font_pairs(pair_file).get("majiko") != pair:
            raise AssertionError("named pair could not be loaded")

        created = copy.deepcopy(ASS_PRESETS[base_name])
        created["ch_font"], created["jp_font"] = pair
        created["description"] = "手动编辑的字幕样式"
        simulated_presets = {base_name: copy.deepcopy(ASS_PRESETS[base_name]), new_name: created}
        serialized = json.loads(json.dumps(simulated_presets, ensure_ascii=False))
        if serialized[base_name] != original:
            raise AssertionError("creating a new preset changed the original preset")
        if (serialized[new_name]["ch_font"], serialized[new_name]["jp_font"]) != pair:
            raise AssertionError("new preset did not retain the selected font pair")

    print("PASS: named font pair creates an independent preset without overwriting the original")


if __name__ == "__main__":
    main()
