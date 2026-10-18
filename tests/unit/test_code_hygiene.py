"""业务护栏：禁止调试输出、裸 except 与同包导入兜底回流。"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# 这些文件允许保留兜底：脱离 AstrBot 运行、可选第三方依赖、日志降级出口
IMPORT_FALLBACK_ALLOWED = {
    "shared/logging.py",
    "plugin/runtime_config.py",
    "infrastructure/clients/qqofficial_panel.py",
}
PRINT_ALLOWED = {"shared/logging.py"}


def _iter_python_files():
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path, path.relative_to(SRC).as_posix()


def test_no_debug_print_in_source():
    offenders = []
    for path, relative in _iter_python_files():
        if relative in PRINT_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], f"使用 logger 代替 print: {offenders}"


def test_no_bare_except():
    offenders = []
    for path, relative in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], f"禁止裸 except: {offenders}"


def test_import_error_fallback_only_where_allowed():
    offenders = []
    for path, relative in _iter_python_files():
        if relative in IMPORT_FALLBACK_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            names = [item.id for item in ast.walk(node.type) if isinstance(item, ast.Name)]
            if "ImportError" in names:
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], f"同包导入不应再做 ImportError 兜底: {offenders}"
