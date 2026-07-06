import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _call_keywords(name: str) -> dict[str, object]:
    tree = ast.parse((ROOT / "TokenSpider.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == name:
            return {
                keyword.arg: ast.literal_eval(keyword.value)
                for keyword in node.keywords
                if keyword.arg is not None
            }
    raise AssertionError(f"{name} call not found in TokenSpider.spec")


def test_pyqtgraph_startup_modules_are_packaged():
    options = _call_keywords("Analysis")
    excluded = set(options["excludes"])
    required = {
        "pyqtgraph.imageview",
        "pyqtgraph.multiprocess",
        "pyqtgraph.parametertree",
    }

    assert required.isdisjoint(excluded)
    assert {
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
    } <= set(options["hiddenimports"])


def test_windows_executable_uses_project_icon():
    options = _call_keywords("EXE")

    assert options["icon"] == ["assets/TokenSpider.ico"]
