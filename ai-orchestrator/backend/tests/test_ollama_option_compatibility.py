"""New managed-Ollama options must not invalidate pre-upgrade options."""
import json
from pathlib import Path


def test_new_ollama_options_are_optional_with_new_install_defaults():
    config = json.loads((Path(__file__).parents[2] / 'config.json').read_text())
    assert config['schema']['ollama_mode'] == 'list(auto|internal|external)?'
    assert config['schema']['internal_ollama_port'] == 'int(1024,65535)?'
    assert config['options']['ollama_mode'] == 'auto'
    assert config['options']['internal_ollama_port'] == 11435
