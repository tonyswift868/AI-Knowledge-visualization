"""Deterministic Chinese read-aloud normalization; no model or network calls."""
from __future__ import annotations

import html
import re
import unicodedata

DIGITS = "零一二三四五六七八九"
ABBREVIATIONS = {"API": "接口", "AI": "人工智能", "CPU": "中央处理器", "GPU": "图形处理器",
    "HTTP": "网络协议", "HTTPS": "安全网络协议", "SQL": "数据库查询语言", "URL": "网址",
    "HTML": "超文本标记语言", "JSON": "杰森", "RAM": "运行内存", "SSD": "固态硬盘",
    "GPT": "吉皮提", "TTS": "语音合成", "ASR": "语音识别", "PDF": "皮迪艾弗"}
LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", ("诶", "比", "西", "迪", "伊", "艾弗", "吉", "诶尺", "艾", "杰", "开", "艾勒", "艾姆", "恩", "欧", "皮", "丘", "阿尔", "艾斯", "提", "优", "维", "达不溜", "艾克斯", "歪", "贼德")))
UNITS = {"kg": "千克", "mg": "毫克", "g": "克", "km": "千米", "cm": "厘米", "mm": "毫米", "m": "米",
    "ms": "毫秒", "s": "秒", "min": "分钟", "h": "小时", "GB": "吉字节", "MB": "兆字节", "KB": "千字节",
    "GHz": "吉赫兹", "MHz": "兆赫兹", "Hz": "赫兹", "ml": "毫升", "mL": "毫升", "L": "升", "℃": "摄氏度", "°C": "摄氏度"}


def _integer(value):
    if len(value) > 16 or len(value) > 1 and value.startswith("0"):
        return "".join(DIGITS[int(d)] for d in value)
    number = int(value)
    if number == 0:
        return "零"
    groups = []
    while number:
        groups.append(number % 10000)
        number //= 10000
    result, zero = "", False
    for group_index in range(len(groups) - 1, -1, -1):
        group = groups[group_index]
        if not group:
            zero = bool(result)
            continue
        if result and (zero or group < 1000):
            result += "零"
        zero, small = False, ""
        for index, digit in enumerate(f"{group:04d}"):
            if digit == "0":
                if small:
                    zero = True
            else:
                small += ("零" if zero else "") + DIGITS[int(digit)] + ("千", "百", "十", "")[index]
                zero = False
        result += small + ("", "万", "亿", "万亿")[group_index]
        zero = False
    return result[1:] if result.startswith("一十") else result


def _number(value):
    sign = "负" if value.startswith("-") else ""
    parts = value.lstrip("+-").split(".", 1)
    return sign + _integer(parts[0]) + ("点" + "".join(DIGITS[int(d)] for d in parts[1]) if len(parts) > 1 else "")


def prepare_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if len(text) > 100000:
        raise ValueError("朗读文本超过十万字，请分段提交。")
    text = unicodedata.normalize("NFKC", html.unescape(text)).replace("\r\n", "\n")
    text = re.sub(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)", "。这里有一段代码。", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$[^$\n]+\$", "这里有一个公式。", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"图片：\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)|\[([^\]]+)\]\[[^\]]*\]", lambda m: m[1] or m[2], text)
    text = re.sub(r"(?m)^\s*\[[^\]]+\]:\s*\S+.*$", "", text)
    text = re.sub(r"(?:https?://|www\.)[^\s<>，。！？；）\]]+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|>+\s*|[-*+]\s+|\d+[.)、]\s+)", "", text)
    text = re.sub(r"(?m)^\s*[-:|=_]{3,}\s*$", "", text)
    text = re.sub(r"[`*_]", "", text).replace("~~", "").replace("|", "，")
    text = re.sub(r"\b([vV])([0-9]+(?:\.[0-9]+){1,3})\b", lambda m: "版本" + "点".join(_integer(n) for n in m[2].split(".")), text)
    text = re.sub(r"(?<!\d)(\d{4})(?=年)", lambda m: "".join(DIGITS[int(d)] for d in m[1]), text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)[eE]([+-]?\d+)\b", lambda m: _number(m[1]) + "乘以十的" + _number(m[2]) + "次方", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*[-–—~]\s*(\d+(?:\.\d+)?)", lambda m: _number(m[1]) + "到" + _number(m[2]), text)
    text = text.replace("~", "")
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*%", lambda m: "百分之" + _number(m[1]), text)
    unit_pattern = "|".join(re.escape(unit) for unit in sorted(UNITS, key=len, reverse=True))
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s*(" + unit_pattern + r")(?![A-Za-z])", lambda m: _number(m[1]) + UNITS[m[2]], text)
    text = re.sub(r"(?<![0-9A-Za-z])(-?\d+(?:\.\d+)?)|\d+(?:\.\d+)?", lambda m: _number(m[0]), text)
    text = re.sub(r"\b[A-Z]{2,8}\b", lambda m: ABBREVIATIONS.get(m[0], "".join(LETTERS[c] for c in m[0])), text)
    for symbol, word in (("+", "加"), ("=", "等于"), ("×", "乘以"), ("÷", "除以"), ("≤", "小于等于"), ("≥", "大于等于"), ("≈", "约等于")):
        text = text.replace(symbol, word)
    text = re.sub(r"\\[A-Za-z]+|[{}$^\\]", "", text)
    text = text.replace("\n", "。")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[。.,，]{2,}", "。", text)
    text = re.sub(r"^[。，,\s]+|[\s]+$", "", text)
    return text.strip()


def split_sentences(text: str, max_chars: int = 60) -> tuple[str, ...]:
    if not 10 <= max_chars <= 60:
        raise ValueError("max_chars must be between 10 and 60")
    text = prepare_text(text)
    result = []
    for sentence in re.findall(r"[^。！？!?；;]+[。！？!?；;]*", text):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(p, 0, max_chars) for p in ("，", ",", "：", ":", "、", " ")) + 1
            if cut < max_chars // 3:
                cut = max_chars
            part, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if any(c.isalnum() for c in part):
                result.append(part)
        if any(c.isalnum() for c in sentence):
            result.append(sentence)
    return tuple(result)
