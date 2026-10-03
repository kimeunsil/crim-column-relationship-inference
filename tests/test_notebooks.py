"""공개 노트북 위생 검사: 출력 없음, 로컬 경로·API 키 없음."""

import json
import re
from pathlib import Path

import pytest

NOTEBOOKS = sorted((Path(__file__).resolve().parents[1] / "notebooks").glob("*.ipynb"))


def test_notebooks_exist():
    assert len(NOTEBOOKS) == 8


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_notebook_is_clean(path):
    nb = json.loads(path.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4
    assert nb["cells"][0]["cell_type"] == "markdown"  # 노트북 설명 헤더
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [] and cell["execution_count"] is None
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"/Users/|C:\\\\Users", text), "로컬 절대경로"
    assert not re.search(r"sk-(proj-)?[A-Za-z0-9_-]{20,}", text), "API 키"
