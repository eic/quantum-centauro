"""Keep the single public demo and its bundled inputs available."""

from __future__ import annotations

import json
from pathlib import Path


def test_demo_has_code_and_bundled_data() -> None:
    root = Path(__file__).parents[2]
    notebook = json.loads((root / "notebooks/demo.ipynb").read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert any(cell["cell_type"] == "code" and cell["source"] for cell in notebook["cells"])
    for name in ("particles.csv", "jets.csv", "jet_constituents.csv", "reconstruction_steps.csv"):
        assert (root / "notebooks/data" / name).is_file()
