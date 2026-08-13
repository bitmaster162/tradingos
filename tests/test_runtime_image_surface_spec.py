from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy" / "Dockerfile"
DOCKERIGNORE = ROOT / "deploy" / "Dockerfile.dockerignore"

RUNTIME_MODULES = [
    "tradingos_delivery_guard.py",
    "tradingos_preflight_bridge.py",
    "tradingos_send_authorization_review.py",
    "tradingos_send_executor_state.py",
    "tradingos_telegram_request_compiler.py",
    "tradingos_telegram_transport.py",
    "tradingos_runtime_service.py",
]


def test_runtime_image_and_build_context_are_explicitly_narrow():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")

    assert "COPY --chown=tradingos:tradingos tools/ /app/tools/" not in dockerfile
    assert "COPY --chown=tradingos:tradingos configs/ /app/configs/" not in dockerfile
    assert "COPY --chown=tradingos:tradingos deploy/ /app/deploy/" not in dockerfile

    for module in RUNTIME_MODULES:
        assert f"tools/{module} /app/tools/" in dockerfile
        assert f"!tools/{module}" in dockerignore

    lines = [line.strip() for line in dockerignore.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert lines[0] == "**"
    assert "!deploy/Dockerfile" in lines
    assert "!deploy/Dockerfile.dockerignore" in lines
    assert "!deploy/.env" not in lines
    assert "!tests/" not in lines
    assert "!configs/" not in lines
