from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEPENDENCIES = {
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "playwright": "playwright",
    "customtkinter": "customtkinter",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
}


def module_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def check_python_version() -> dict[str, Any]:
    ok = sys.version_info >= (3, 10)
    return {
        "name": "Python >= 3.10",
        "ok": ok,
        "detail": sys.version.split()[0],
        "fix": "请安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。",
    }


def check_dependencies() -> list[dict[str, Any]]:
    results = [check_python_version()]
    for display, module in DEPENDENCIES.items():
        ok = module_installed(module)
        results.append(
            {
                "name": display,
                "ok": ok,
                "detail": "已安装" if ok else "未安装",
                "fix": "运行 install.bat，或在项目目录执行 pip install -r requirements.txt。",
            }
        )
    results.extend(check_directories())
    results.append(check_chromium())
    return results


def check_directories() -> list[dict[str, Any]]:
    checks = []
    for rel in ["data/database", "data/output", "data/raw", "logs"]:
        path = PROJECT_ROOT / rel
        checks.append(
            {
                "name": rel,
                "ok": path.exists() and path.is_dir(),
                "detail": str(path),
                "fix": "目录缺失时可重新运行程序，或手动创建该目录。",
            }
        )
    return checks


def check_chromium() -> dict[str, Any]:
    if not module_installed("playwright"):
        return {
            "name": "Playwright Chromium",
            "ok": False,
            "detail": "playwright 未安装，无法检测 Chromium",
            "fix": "先运行 pip install -r requirements.txt，再运行 python -m playwright install chromium。",
        }
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            executable = Path(p.chromium.executable_path)
            return {
                "name": "Playwright Chromium",
                "ok": executable.exists(),
                "detail": str(executable),
                "fix": "运行 python -m playwright install chromium，或直接运行 install.bat。",
            }
    except Exception as exc:
        return {
            "name": "Playwright Chromium",
            "ok": False,
            "detail": str(exc),
            "fix": "运行 python -m playwright install chromium，或直接运行 install.bat。",
        }


def missing_runtime_dependencies(include_gui: bool = True) -> list[str]:
    required = ["pandas", "openpyxl", "playwright"]
    if include_gui:
        required.append("customtkinter")
    return [name for name in required if not module_installed(name)]


def format_environment_report(results: list[dict[str, Any]] | None = None) -> str:
    results = results or check_dependencies()
    lines = []
    for item in results:
        mark = "✅" if item["ok"] else "❌"
        lines.append(f"{mark} {item['name']}: {item['detail']}")
        if not item["ok"]:
            lines.append(f"   修复建议: {item['fix']}")
    return "\n".join(lines)
