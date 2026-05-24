from pathlib import Path


PRODUCTION_FILES = [
    Path(__file__).resolve().parents[1] / "app",
    Path(__file__).resolve().parents[1] / "scripts",
]

FORBIDDEN_PATTERNS = [
    "_fallback_embedding",
    "fallback_dimensions",
    "dummy_vector",
    "mock_embedding",
    "hash_vector",
    "random_vector",
    "zero_vector",
]


def test_no_fallback_embeddings_in_production_code() -> None:
    contents = []
    for root in PRODUCTION_FILES:
        for path in root.rglob("*.py"):
            contents.append(path.read_text(encoding="utf-8"))
    combined = "\n".join(contents)
    for pattern in FORBIDDEN_PATTERNS:
        assert pattern not in combined
