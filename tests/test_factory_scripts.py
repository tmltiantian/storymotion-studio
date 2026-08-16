from pathlib import Path


def test_start_script_uses_the_unified_factory_entrypoint():
    script = Path("scripts/start_factory.sh").read_text(encoding="utf-8")

    assert "/Users/tml/" not in script
    assert '"$ROOT_DIR/.venv/bin/python"' in script
    assert "factory create" in script
    assert "factory run" in script
    assert "factory status" in script
    assert "run-project" not in script
    assert "enqueue" not in script
    assert "worker" not in script


def test_bootstrap_installs_pinned_dependencies_and_runs_tests():
    script = Path("scripts/bootstrap_factory.sh").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "-m venv" in script
    assert "python3.12" in script
    assert "sys.version_info >= (3, 10)" in script
    assert "--clear" in script
    assert "pip install -r" in script
    assert "-m pytest tests -q" in script
    assert 'command -v node' in script
    assert 'command -v npm' in script
    assert 'node --version' in script
    assert 'npm --version' in script
    assert 'cd "$ROOT_DIR/sites/storymotion-studio"' in script
    assert "npm ci" in script
    assert "Pillow==" in requirements
    assert "requests==" in requirements
    assert "pytest==" in requirements
    assert "ruff==" in requirements
