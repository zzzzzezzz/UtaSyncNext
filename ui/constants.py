# ==========================================
# Font filtering constants used by the Next UI.
# ==========================================

FONT_WHITELIST = [
    "Source Han Sans SC", "Source Han Sans CN", "思源黑体",
    "Source Han Serif SC", "Source Han Serif CN", "思源宋体",
    "LXGW WenKai", "霞鹜文楷", "Smiley Sans", "得意黑",
    "Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans CJK JP", "Noto Serif CJK JP",
    "FOT-Rodin Pro M", "Hiragino Sans", "Hiragino Mincho ProN",
    "Microsoft YaHei", "微软雅黑", "SimHei", "黑体",
    "SimSun", "宋体", "KaiTi", "楷体", "LiSu", "隶书", "YouYuan", "幼圆",
    "STKaiti", "华文楷体", "STSong", "华文宋体", "STXingkai", "华文行楷", "STXinwei", "华文新魏",
    "FZHei-B01", "方正黑体", "FZKai-Z03", "方正楷体",
    "DengXian", "等线", "Calibri", "Cambria",
    "Meiryo", "Yu Gothic", "Yu Mincho", "MS Gothic", "MS Mincho",
    "Arial", "Impact", "Consolas", "Segoe UI",
]

# Keep the default settings panel intentionally small.  The full system font
# inventory is not a useful primary control: it is noisy, can contain fonts
# that libass cannot render, and makes the panel expand unpredictably.  A
# dedicated user-font manager can add more families later without changing
# this default list.
CURATED_FONT_FAMILIES = [
    "Microsoft YaHei",
    "SimHei",
    "Meiryo",
    "Yu Gothic",
    "Source Han Sans SC",
    "Source Han Serif SC",
    "Noto Sans CJK SC",
    "Noto Serif CJK SC",
    "Noto Sans CJK JP",
    "Noto Serif CJK JP",
    "Noto Sans JP",
    "Noto Serif JP",
    "M PLUS 1p",
    "Zen Kaku Gothic New",
    "LXGW WenKai",
]

GARBAGE_FONT_KEYWORDS = {
    "@", "wingdings", "webdings", "symbol", "marlett", "bookshelf", "hololens", "mt extra",
    "baltic", "ce", "cyr", "greek", "tur", "arabic", "himalaya", "tai lue",
    "phagspa", "tai le", "yi baiti", "mongolian", "myanmar", "syriac", "hebrew",
    "algerian", "bauhaus", "bodoni", "bradley", "broadway", "chiller",
    "colonna", "comic", "curlz", "edwardian", "elephant", "engravers",
    "forte", "freestyle", "french", "gabriola", "gigi", "gloucester", "goudy",
    "harlow", "harrington", "imprint", "informal", "ink free", "javanese",
    "jokerman", "juice", "kristen", "kunstler", "magneto", "matura", "mistral",
    "niagara", "ocr a", "old english", "onyx", "palace", "papyrus", "parchment",
    "playbill", "poor richard", "pristina", "rage", "ravie", "showcard", "snap",
    "stencil", "viner", "vivaldi", "vladimir", "wide latin", "mv boli",
}

FONT_ALIAS_MAP = {
    "Source Han Sans SC": "思源黑体",
    "Source Han Sans CN": "思源黑体",
    "Source Han Serif SC": "思源宋体",
    "Source Han Serif CN": "思源宋体",
    "Noto Sans CJK SC": "思源黑体 (Noto版)",
    "Noto Serif CJK SC": "思源宋体 (Noto版)",
    "Noto Sans CJK JP": "思源黑体 (日文版)",
    "Noto Serif CJK JP": "思源宋体 (日文版)",
    "LXGW WenKai": "霞鹜文楷",
    "Smiley Sans": "得意黑",
    "Microsoft YaHei": "微软雅黑",
    "Microsoft YaHei UI": "微软雅黑 UI",
    "SimHei": "系统黑体",
    "SimSun": "系统宋体",
    "KaiTi": "系统楷体",
    "LiSu": "隶书",
    "YouYuan": "幼圆",
    "Meiryo": "明朝体 (标准日文)",
    "Yu Gothic": "游黑体 (标准日文)",
    "Yu Mincho": "游明朝 (标准日文)",
    "MS Gothic": "微软日文黑体",
    "MS Mincho": "微软日文明朝",
    "Hiragino Sans": "冬青黑体 (苹果日文)",
    "Hiragino Mincho ProN": "冬青明朝 (苹果日文)",
    "FOT-Rodin Pro M": "Rodin (EVA/极简日文)",
    "DengXian": "等线",
    "FZHei-B01": "方正黑体",
    "FZKai-Z03": "方正楷体",
    "Arial": "经典无衬线英文",
    "Impact": "粗体海报英文",
    "Consolas": "等宽代码英文",
    "Segoe UI": "微软系统UI字体",
    "Source Han Sans SC": "思源黑体（简中）",
    "Source Han Serif SC": "思源宋体（简中）",
    "Noto Sans CJK SC": "Noto 无衬线（简中）",
    "Noto Serif CJK SC": "Noto 衬线（简中）",
    "Noto Sans CJK JP": "Noto 无衬线（日文）",
    "Noto Serif CJK JP": "Noto 衬线（日文）",
    "Noto Sans JP": "Noto 无衬线（日文）",
    "Noto Serif JP": "Noto 衬线（日文）",
    "M PLUS 1p": "M PLUS 日文正文",
    "Zen Kaku Gothic New": "Zen 角ゴシック",
    "LXGW WenKai": "霞鹜文楷",
}
