import ast
import io
import re
import tokenize
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "trend_sift"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _has_cjk(value: str) -> bool:
    return CJK_RE.search(value) is not None


def test_python_comments_and_docstrings_are_english() -> None:
    violations: list[str] = []

    for path in PACKAGE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if not node.body:
                continue
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
                and _has_cjk(first.value.value)
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{first.lineno}: docstring")

        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and _has_cjk(token.string):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{token.start[0]}: comment")

    assert violations == []


def test_example_environment_comments_are_english() -> None:
    path = PROJECT_ROOT / ".env.example"
    violations = [
        f"{path.name}:{line_number}"
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.lstrip().startswith("#") and _has_cjk(line)
    ]
    assert violations == []
