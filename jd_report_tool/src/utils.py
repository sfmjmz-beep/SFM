from __future__ import annotations

import json
import math
import os
import platform
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_dirs() -> None:
    for rel in [
        "config", "data/raw/商品明细_SPU", "data/raw/商品明细_SKU", "data/raw/商品排名定位",
        "data/base", "data/output", "data/database", "logs/screenshots", "samples",
    ]:
        (PROJECT_ROOT / rel).mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


def normalize_date(value: Any, default: str | None = None) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return default
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    m = re.search(r"(20\d{2})[-_/\.](\d{1,2})[-_/\.](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"]:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return default


def safe_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, ".15g")
    text = str(value).strip()
    if text.endswith(".0") and re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def clean_column_name(value: Any) -> str:
    text = safe_text(value)
    text = re.sub(r"\s+", "", text)
    text = text.replace("\n", "").replace("\r", "")
    return text


def to_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = safe_text(value)
    if not text or text.lower() in {"nan", "none", "--", "-"}:
        return None
    text = text.replace("￥", "").replace("¥", "").replace(",", "").replace(" ", "")
    text = text.replace("元", "")
    if text.endswith("%"):
        return to_percent(text)
    try:
        return float(text)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(m.group(0)) if m else None


def to_int(value: Any) -> int | None:
    num = to_number(value)
    if num is None or math.isnan(num):
        return None
    return int(round(num))


def to_percent(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 100 if abs(float(value)) > 1 else float(value)
    text = safe_text(value).replace(" ", "")
    if not text or text in {"--", "-"}:
        return None
    has_pct = text.endswith("%")
    text = text.rstrip("%").replace(",", "")
    try:
        num = float(text)
    except ValueError:
        return None
    return num / 100 if has_pct or abs(num) > 1 else num


def ratio(numerator: Any, denominator: Any) -> float | None:
    n = to_number(numerator)
    d = to_number(denominator)
    if n is None or d in (None, 0):
        return None
    return n / d


def open_folder(path: Path) -> None:
    path = path.resolve()
    if platform.system() == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    return path


def find_latest_file(folder: Path, patterns: Iterable[str] = ("*.xlsx", "*.xls")) -> Path | None:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(folder.glob(pattern))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None
