from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "Dockerfile"


def test_ollama_install_is_fail_fast_and_has_zstd_extractor():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "zstd" in dockerfile
    assert "curl -fsSL https://ollama.com/install.sh | sh || echo" not in dockerfile
    assert "command -v ollama" in dockerfile
