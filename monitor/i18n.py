"""繁體 / 简体 切換。

介面文字只維護繁體一份；選簡體時用 OpenCC（tw2sp：台灣正體 → 大陸簡體並轉換常用詞）即時轉換。
含日文假名的詞（商品標題、ジャンク 等關鍵字）不轉換，避免把日文漢字改掉。
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import config

ZH_TW = "zh-TW"
ZH_CN = "zh-CN"

# OpenCC 轉換後再修正的用詞
_FIXES = [
    ("缺省", "默认"),
    ("激活", "启用"),
    ("日圆", "日元"),
    ("关键字", "关键词"),
    ("接口", "界面"),          # 繁體原文的「介面」都是指使用者介面
    ("推播", "推送"),
    ("数字签章", "数字签名"),
    ("原代码", "源代码"),
    ("在背景", "在后台"),
    ("遗失", "丢失"),
    ("范例", "示例"),
    (" 笔", " 条"),            # 「50 筆」→「50 条」
    ("系统匣", "系统托盘"),
    ("线上", "在线"),
    ("屏幕烙印", "屏幕烧屏"),
    ("萤幕", "屏幕"),
    ("影片", "视频"),
    ("登录帐号", "登录账号"),
    ("帐号", "账号"),
]
# 不轉換的固定字串（日文商品用語等，沒有假名所以要特別列出）
_PROTECT = ["有機EL", "落札相場", "定額", "即決", "現状品", "動作確認"]

# 假名（不含中日文共用的「・」U+30FB）
_KANA = re.compile(r"[぀-ヺー-ヿㇰ-ㇿｦ-ﾟ]")
_CJK_RUN = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ㐀-鿿豈-﫿々〆ー]+")

_cc = None


def _converter():
    global _cc
    if _cc is None:
        from opencc import OpenCC
        _cc = OpenCC("tw2sp")
    return _cc


@lru_cache(maxsize=4096)
def to_simplified(text: str) -> str:
    if not text:
        return text
    placeholders: list[str] = []

    def keep(s: str) -> str:
        placeholders.append(s)
        return f"\u0000{len(placeholders) - 1}\u0000"

    for p in _PROTECT:
        text = text.replace(p, keep(p))
    # 含假名的連續漢字 / 假名段 = 日文，原樣保留
    text = _CJK_RUN.sub(lambda m: keep(m.group(0)) if _KANA.search(m.group(0)) else m.group(0), text)
    out = _converter().convert(text)
    for a, b in _FIXES:
        out = out.replace(a, b)
    return re.sub("\u0000(\\d+)\u0000", lambda m: placeholders[int(m.group(1))], out)


def lang() -> str:
    return config.LANG


def tr(text: str) -> str:
    """把繁體介面文字轉成目前語言。只能用在介面文字，不要用在商品資料上。"""
    return to_simplified(text) if config.LANG == ZH_CN else text
