from __future__ import annotations

from pathlib import Path

from btcusdt_bot.utils.envfile import load_env_file


def test_load_env_file_sets_missing_values(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("ALPHA=1\nBETA='two words'\n# comment\n", encoding="utf-8")
    monkeypatch.delenv("ALPHA", raising=False)
    monkeypatch.delenv("BETA", raising=False)

    loaded = load_env_file(env_path)

    assert loaded is True
    assert Path(env_path).exists()
    assert __import__("os").environ["ALPHA"] == "1"
    assert __import__("os").environ["BETA"] == "two words"


def test_load_env_file_does_not_override_existing_by_default(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("ALPHA=from_file\n", encoding="utf-8")
    monkeypatch.setenv("ALPHA", "from_env")

    load_env_file(env_path)

    assert __import__("os").environ["ALPHA"] == "from_env"
