"""The evaluator's independence is a design invariant, so it is asserted from
source rather than trusted to review.

If evaluator.py ever imported spec.py or an encoder, the equivalence matrix
would be comparing the encoders against something downstream of the very
threshold they are being checked on, and a wrong u would agree with itself.
"""

import ast
from pathlib import Path

import nk2.evaluator

FORBIDDEN = {
    "nk2.spec",
    "nk2.encode_subsets",
    "nk2.encode_seqcount",
    "nk2.encode_totalizer",
    "nk2.solve",
    "nk2.dimacs",
    "nk2.witness",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="ascii"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_evaluator_imports_neither_spec_nor_any_encoder():
    imports = imported_modules(Path(nk2.evaluator.__file__))
    assert imports & FORBIDDEN == set(), sorted(imports & FORBIDDEN)
    assert imports == {"nk2.aps", "nk2.aps.iter_aps", "__future__", "__future__.annotations"}


def test_evaluator_source_mentions_no_threshold_arithmetic():
    source = Path(nk2.evaluator.__file__).read_text(encoding="ascii")
    for token in ("u_threshold", "avoid_bounds", "at_most", "//-2", "// -2"):
        assert token not in source, token


def test_evaluator_uses_no_floating_point():
    tree = ast.parse(Path(nk2.evaluator.__file__).read_text(encoding="ascii"))
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float))
        assert not isinstance(node, ast.Div)  # true division would leave the integers


def test_aps_is_independent_too():
    # The evaluator's one dependency has to stay clean as well.
    imports = imported_modules(Path(nk2.aps.__file__))
    assert imports & FORBIDDEN == set()
