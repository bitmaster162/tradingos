
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_bind_contract_is_explicit_and_loopback_exposed_only():
    runtime=(ROOT/"tools"/"tradingos_runtime_service.py").read_text()
    docker=(ROOT/"deploy"/"Dockerfile").read_text()
    compose=(ROOT/"deploy"/"docker-compose.yml").read_text()
    assert 's.add_argument("--bind", default="127.0.0.1")' in runtime
    assert '"--bind", "0.0.0.0"' in docker
    assert '"127.0.0.1:8787:8787"' in compose
    assert '"0.0.0.0:8787:8787"' not in compose
