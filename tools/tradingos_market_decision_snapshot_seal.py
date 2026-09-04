#!/usr/bin/env python3
"""TradingOS R77.3 — full-chain verified provenance seal.

Verification chain:
  raw Watchtower capture
    -> exact canonical Watchtower build_watchtower()
  raw Liquidity capture
    -> exact canonical Liquidity Lens build_lens()
  verified Watchtower + verified Liquidity
    -> exact canonical Market Radar build_radar()
  raw Watchtower capture + verified Watchtower + verified Radar
    -> exact R77 Market Decision Bridge build_bridge()
  exact supplied artifacts
    -> provenance-sealed Decision Brief snapshot

The canonical source modules are pinned by exact Git blob SHA-1 before use.

No network, credentials, AI inference, signals, orders, execution, or capital effects.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

SCHEMA = "tradingos.market_decision_snapshot_seal.v3"
VERSION = "3.0.0"
PRODUCER = "tools/tradingos_market_decision_snapshot_seal.py"

TOOLS_DIR = Path(__file__).resolve().parent
WATCHTOWER_PATH = TOOLS_DIR / "tradingos_watchtower.py"
LIQUIDITY_PATH = TOOLS_DIR / "tradingos_liquidity_lens_core.py"
RADAR_PATH = TOOLS_DIR / "tradingos_market_radar.py"
BRIDGE_PATH = TOOLS_DIR / "tradingos_market_decision_bridge.py"

EXPECTED_WATCHTOWER_GIT_BLOB_SHA1 = "628140300801a4631e1b45c6f7b3a9953629ae63"
EXPECTED_LIQUIDITY_GIT_BLOB_SHA1 = "193ac1c869dd479dac47c35cede777cc34bce687"
EXPECTED_RADAR_GIT_BLOB_SHA1 = "db00fe10b499a6e7f35f96081ba76afa3f09ca9f"
EXPECTED_BRIDGE_GIT_BLOB_SHA1 = "3ec351af707fc84f7d549c3f3eb5bac359ce4da4"

EXPECTED_WATCHTOWER_PRODUCER_SHA256 = (
    "278a9fe4b5fd4c6f909375f26780409bc56a9bc44a59c57c7e4ebc5ab9493e57"
)
EXPECTED_LIQUIDITY_PRODUCER_SHA256 = (
    "870f2734de73af0974433a0dccd7750fc932117ace1ab2819ca952840780e699"
)

WATCHTOWER_PRODUCER = "tools/tradingos_watchtower.py"
LIQUIDITY_PRODUCER = "tools/tradingos_liquidity_lens_core.py"
RADAR_PRODUCER = "tools/tradingos_market_radar.py"
BRIDGE_PRODUCER = "tools/tradingos_market_decision_bridge.py"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_BINDING_KEYS = {
    "watchtower_capture_sha256",
    "watchtower_report_sha256",
    "watchtower_producer_sha256",
    "radar_report_sha256",
    "liquidity_report_sha256",
    "liquidity_capture_sha256",
    "liquidity_producer_sha256",
}
EXPECTED_SOURCE_KINDS = {"ohlcv", "open_interest", "funding", "spot_flow"}

SEAL_SAFETY = {
    "read_only": True,
    "network_fetch": False,
    "ai_generated_market_facts": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}


def stable_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(stable_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob_sha1(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field}: invalid sha256")
    return value


def require_git_sha1(value: Any, field: str) -> str:
    if not isinstance(value, str) or _GIT_SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field}: invalid git blob sha1")
    return value


def _load_verified_module(path: Path, expected_blob_sha1: str, label: str) -> ModuleType:
    require_git_sha1(expected_blob_sha1, f"{label}.expected_blob_sha1")
    if not path.is_file():
        raise ValueError(f"{label}: canonical source file missing")
    actual_blob = git_blob_sha1(path)
    if actual_blob != expected_blob_sha1:
        raise ValueError(f"{label}: canonical source Git blob mismatch")
    spec = importlib.util.spec_from_file_location(f"_tradingos_r77_3_{label}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{label}: cannot load canonical source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _modules() -> tuple[ModuleType, ModuleType, ModuleType, ModuleType]:
    watchtower = _load_verified_module(
        WATCHTOWER_PATH, EXPECTED_WATCHTOWER_GIT_BLOB_SHA1, "watchtower"
    )
    liquidity = _load_verified_module(
        LIQUIDITY_PATH, EXPECTED_LIQUIDITY_GIT_BLOB_SHA1, "liquidity"
    )
    radar = _load_verified_module(
        RADAR_PATH, EXPECTED_RADAR_GIT_BLOB_SHA1, "radar"
    )
    bridge = _load_verified_module(
        BRIDGE_PATH, EXPECTED_BRIDGE_GIT_BLOB_SHA1, "bridge"
    )
    for module, function_name, label in (
        (watchtower, "build_watchtower", "watchtower"),
        (liquidity, "build_lens", "liquidity"),
        (radar, "build_radar", "radar"),
        (bridge, "build_bridge", "bridge"),
    ):
        if not callable(getattr(module, function_name, None)):
            raise ValueError(f"{label}: canonical builder missing")
    return watchtower, liquidity, radar, bridge


def _verify_producer(
    report: dict[str, Any],
    *,
    producer: str,
    producer_sha256: str,
    label: str,
) -> None:
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"{label}: provenance missing")
    if provenance.get("producer") != producer:
        raise ValueError(f"{label}: producer path mismatch")
    if require_sha256(
        provenance.get("producer_sha256"), f"{label}.producer_sha256"
    ) != producer_sha256:
        raise ValueError(f"{label}: producer sha256 mismatch")


def _verify_watchtower(
    watchtower_module: ModuleType,
    capture: dict[str, Any],
    supplied_watchtower: dict[str, Any],
) -> dict[str, Any]:
    canonical = watchtower_module.build_watchtower(capture)
    if supplied_watchtower != canonical:
        raise ValueError("supplied Watchtower does not match deterministic reconstruction")
    _verify_producer(
        canonical,
        producer=WATCHTOWER_PRODUCER,
        producer_sha256=EXPECTED_WATCHTOWER_PRODUCER_SHA256,
        label="watchtower",
    )
    provenance = canonical["provenance"]
    if require_sha256(
        provenance.get("capture_sha256"), "watchtower.capture_sha256"
    ) != stable_sha256(capture):
        raise ValueError("Watchtower capture binding mismatch")
    if canonical.get("safety", {}).get("can_trade") is not False:
        raise ValueError("unsafe Watchtower report")
    return canonical


def _verify_liquidity(
    liquidity_module: ModuleType,
    liquidity_capture: dict[str, Any],
    supplied_liquidity: dict[str, Any],
) -> dict[str, Any]:
    canonical = liquidity_module.build_lens(liquidity_capture)
    if supplied_liquidity != canonical:
        raise ValueError("supplied Liquidity report does not match deterministic reconstruction")
    _verify_producer(
        canonical,
        producer=LIQUIDITY_PRODUCER,
        producer_sha256=EXPECTED_LIQUIDITY_PRODUCER_SHA256,
        label="liquidity",
    )
    provenance = canonical["provenance"]
    if require_sha256(
        provenance.get("capture_sha256"), "liquidity.capture_sha256"
    ) != stable_sha256(liquidity_capture):
        raise ValueError("Liquidity capture binding mismatch")
    if canonical.get("safety", {}).get("can_trade") is not False:
        raise ValueError("unsafe Liquidity report")
    return canonical


def _verify_radar(
    radar_module: ModuleType,
    watchtower: dict[str, Any],
    liquidity_capture: dict[str, Any],
    liquidity: dict[str, Any],
    supplied_radar: dict[str, Any],
) -> dict[str, Any]:
    canonical = radar_module.build_radar(watchtower, liquidity)
    if supplied_radar != canonical:
        raise ValueError("supplied Market Radar does not match deterministic reconstruction")
    provenance = canonical.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("radar provenance missing")
    expected = {
        "watchtower_report_sha256": stable_sha256(watchtower),
        "watchtower_capture_sha256": watchtower["provenance"]["capture_sha256"],
        "watchtower_producer_sha256": EXPECTED_WATCHTOWER_PRODUCER_SHA256,
        "liquidity_report_sha256": stable_sha256(liquidity),
        "liquidity_capture_sha256": stable_sha256(liquidity_capture),
        "liquidity_producer_sha256": EXPECTED_LIQUIDITY_PRODUCER_SHA256,
    }
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise ValueError(f"radar {key} binding mismatch")
    if canonical.get("safety", {}).get("can_trade") is not False:
        raise ValueError("unsafe Radar report")
    return canonical


def _require_exact_source_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("producer") != BRIDGE_PRODUCER:
        raise ValueError("R77 snapshot producer mismatch")
    sources = provenance.get("sources")
    if not isinstance(sources, list) or len(sources) != len(EXPECTED_SOURCE_KINDS):
        raise ValueError("R77 snapshot source list mismatch")
    kinds = [row.get("kind") for row in sources if isinstance(row, dict)]
    if len(kinds) != len(sources) or set(kinds) != EXPECTED_SOURCE_KINDS:
        raise ValueError("R77 snapshot source kind mismatch")
    source_ids: list[str] = []
    for i, row in enumerate(sources):
        source_id = row.get("source_id")
        observed_at = row.get("observed_at")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"R77 source[{i}] source_id invalid")
        if not isinstance(observed_at, str) or not observed_at:
            raise ValueError(f"R77 source[{i}] observed_at invalid")
        source_ids.append(source_id)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("R77 source_id reuse")
    return sources


def reconstruct_verified_chain(
    watchtower_capture: dict[str, Any],
    supplied_watchtower: dict[str, Any],
    liquidity_capture: dict[str, Any],
    supplied_liquidity: dict[str, Any],
    supplied_radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> dict[str, Any]:
    watchtower_module, liquidity_module, radar_module, bridge_module = _modules()

    canonical_watchtower = _verify_watchtower(
        watchtower_module, watchtower_capture, supplied_watchtower
    )
    canonical_liquidity = _verify_liquidity(
        liquidity_module, liquidity_capture, supplied_liquidity
    )
    canonical_radar = _verify_radar(
        radar_module,
        canonical_watchtower,
        liquidity_capture,
        canonical_liquidity,
        supplied_radar,
    )

    canonical_result = bridge_module.build_bridge(
        watchtower_capture, canonical_watchtower, canonical_radar
    )
    bridge_module.validate_bridge(canonical_result)
    bridge_module.validate_bridge(supplied_result)
    if supplied_result != canonical_result:
        raise ValueError("supplied R77 result does not match deterministic full-chain reconstruction")

    snapshot = canonical_result.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("can_trade") is not False:
        raise ValueError("unsafe/missing R77 snapshot")
    _require_exact_source_rows(snapshot)

    binding = canonical_result.get("input_binding")
    if not isinstance(binding, dict) or set(binding) != EXPECTED_BINDING_KEYS:
        raise ValueError("R77 input_binding key set mismatch")
    for key in sorted(EXPECTED_BINDING_KEYS):
        require_sha256(binding.get(key), f"R77.input_binding.{key}")

    expected_binding = {
        "watchtower_capture_sha256": stable_sha256(watchtower_capture),
        "watchtower_report_sha256": stable_sha256(canonical_watchtower),
        "watchtower_producer_sha256": EXPECTED_WATCHTOWER_PRODUCER_SHA256,
        "radar_report_sha256": stable_sha256(canonical_radar),
        "liquidity_report_sha256": stable_sha256(canonical_liquidity),
        "liquidity_capture_sha256": stable_sha256(liquidity_capture),
        "liquidity_producer_sha256": EXPECTED_LIQUIDITY_PRODUCER_SHA256,
    }
    if binding != expected_binding:
        raise ValueError("R77 input_binding does not match verified full chain")

    digest = require_sha256(
        canonical_result.get("snapshot_sha256"), "R77.snapshot_sha256"
    )
    if digest != stable_sha256(snapshot):
        raise ValueError("R77 snapshot digest mismatch")
    return {
        "watchtower": canonical_watchtower,
        "liquidity": canonical_liquidity,
        "radar": canonical_radar,
        "result": canonical_result,
    }


def seal_snapshot(
    watchtower_capture: dict[str, Any],
    supplied_watchtower: dict[str, Any],
    liquidity_capture: dict[str, Any],
    supplied_liquidity: dict[str, Any],
    supplied_radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> dict[str, Any]:
    chain = reconstruct_verified_chain(
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    source_snapshot = chain["result"]["snapshot"]
    sealed = deepcopy(source_snapshot)
    sealed["provenance"] = {
        "producer": PRODUCER,
        "producer_sha256": file_sha256(Path(__file__)),
        "verification": {
            "method": (
                "REBUILD_WATCHTOWER_FROM_CAPTURE__REBUILD_LIQUIDITY_FROM_CAPTURE__"
                "REBUILD_RADAR_FROM_VERIFIED_REPORTS__REBUILD_R77__REQUIRE_EXACT_EQUALITY"
            ),
            "verified": True,
            "canonical_git_blobs": {
                "watchtower": EXPECTED_WATCHTOWER_GIT_BLOB_SHA1,
                "liquidity": EXPECTED_LIQUIDITY_GIT_BLOB_SHA1,
                "radar": EXPECTED_RADAR_GIT_BLOB_SHA1,
                "bridge": EXPECTED_BRIDGE_GIT_BLOB_SHA1,
            },
        },
        "source_chain": {
            "watchtower_capture_sha256": stable_sha256(watchtower_capture),
            "watchtower_report_sha256": stable_sha256(chain["watchtower"]),
            "liquidity_capture_sha256": stable_sha256(liquidity_capture),
            "liquidity_report_sha256": stable_sha256(chain["liquidity"]),
            "radar_report_sha256": stable_sha256(chain["radar"]),
            "r77_result_sha256": stable_sha256(chain["result"]),
            "r77_snapshot_sha256": stable_sha256(source_snapshot),
        },
        "upstream_binding": deepcopy(chain["result"]["input_binding"]),
        "sources": deepcopy(source_snapshot["provenance"]["sources"]),
    }
    sealed["can_trade"] = False
    validate_sealed_snapshot(
        sealed,
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    return sealed


def validate_sealed_snapshot(
    sealed: Any,
    watchtower_capture: dict[str, Any],
    supplied_watchtower: dict[str, Any],
    liquidity_capture: dict[str, Any],
    supplied_liquidity: dict[str, Any],
    supplied_radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> None:
    chain = reconstruct_verified_chain(
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    source_snapshot = chain["result"]["snapshot"]
    if not isinstance(sealed, dict):
        raise ValueError("sealed snapshot must be object")
    if sealed.get("can_trade") is not False:
        raise ValueError("sealed snapshot can_trade drift")
    provenance = sealed.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("producer") != PRODUCER:
        raise ValueError("sealed snapshot producer mismatch")
    if require_sha256(
        provenance.get("producer_sha256"), "sealed.provenance.producer_sha256"
    ) != file_sha256(Path(__file__)):
        raise ValueError("sealed producer sha256 mismatch")

    expected_verification = {
        "method": (
            "REBUILD_WATCHTOWER_FROM_CAPTURE__REBUILD_LIQUIDITY_FROM_CAPTURE__"
            "REBUILD_RADAR_FROM_VERIFIED_REPORTS__REBUILD_R77__REQUIRE_EXACT_EQUALITY"
        ),
        "verified": True,
        "canonical_git_blobs": {
            "watchtower": EXPECTED_WATCHTOWER_GIT_BLOB_SHA1,
            "liquidity": EXPECTED_LIQUIDITY_GIT_BLOB_SHA1,
            "radar": EXPECTED_RADAR_GIT_BLOB_SHA1,
            "bridge": EXPECTED_BRIDGE_GIT_BLOB_SHA1,
        },
    }
    if provenance.get("verification") != expected_verification:
        raise ValueError("sealed verification contract mismatch")

    expected_chain = {
        "watchtower_capture_sha256": stable_sha256(watchtower_capture),
        "watchtower_report_sha256": stable_sha256(chain["watchtower"]),
        "liquidity_capture_sha256": stable_sha256(liquidity_capture),
        "liquidity_report_sha256": stable_sha256(chain["liquidity"]),
        "radar_report_sha256": stable_sha256(chain["radar"]),
        "r77_result_sha256": stable_sha256(chain["result"]),
        "r77_snapshot_sha256": stable_sha256(source_snapshot),
    }
    if provenance.get("source_chain") != expected_chain:
        raise ValueError("sealed source-chain binding mismatch")
    if provenance.get("upstream_binding") != chain["result"]["input_binding"]:
        raise ValueError("sealed upstream binding mismatch")
    if provenance.get("sources") != source_snapshot["provenance"]["sources"]:
        raise ValueError("sealed sources drift")

    semantic_copy = deepcopy(sealed)
    semantic_copy["provenance"] = deepcopy(source_snapshot["provenance"])
    if semantic_copy != source_snapshot:
        raise ValueError("seal changed market snapshot semantics")


def build_envelope(
    watchtower_capture: dict[str, Any],
    supplied_watchtower: dict[str, Any],
    liquidity_capture: dict[str, Any],
    supplied_liquidity: dict[str, Any],
    supplied_radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> dict[str, Any]:
    chain = reconstruct_verified_chain(
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    sealed = seal_snapshot(
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    envelope = {
        "schema": SCHEMA,
        "version": VERSION,
        "sealed_snapshot": sealed,
        "sealed_snapshot_sha256": stable_sha256(sealed),
        "verified_artifact_sha256": {
            "watchtower_capture": stable_sha256(watchtower_capture),
            "watchtower": stable_sha256(chain["watchtower"]),
            "liquidity_capture": stable_sha256(liquidity_capture),
            "liquidity": stable_sha256(chain["liquidity"]),
            "radar": stable_sha256(chain["radar"]),
            "r77_result": stable_sha256(chain["result"]),
        },
        "safety": dict(SEAL_SAFETY),
    }
    validate_envelope(
        envelope,
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    return envelope


def validate_envelope(
    envelope: Any,
    watchtower_capture: dict[str, Any],
    supplied_watchtower: dict[str, Any],
    liquidity_capture: dict[str, Any],
    supplied_liquidity: dict[str, Any],
    supplied_radar: dict[str, Any],
    supplied_result: dict[str, Any],
) -> None:
    chain = reconstruct_verified_chain(
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    if not isinstance(envelope, dict):
        raise ValueError("seal envelope must be object")
    if envelope.get("schema") != SCHEMA or envelope.get("version") != VERSION:
        raise ValueError("unsupported seal envelope contract")
    if envelope.get("safety") != SEAL_SAFETY:
        raise ValueError("unsafe seal envelope")
    sealed = envelope.get("sealed_snapshot")
    validate_sealed_snapshot(
        sealed,
        watchtower_capture,
        supplied_watchtower,
        liquidity_capture,
        supplied_liquidity,
        supplied_radar,
        supplied_result,
    )
    if require_sha256(
        envelope.get("sealed_snapshot_sha256"), "sealed_snapshot_sha256"
    ) != stable_sha256(sealed):
        raise ValueError("sealed snapshot envelope digest mismatch")
    expected = {
        "watchtower_capture": stable_sha256(watchtower_capture),
        "watchtower": stable_sha256(chain["watchtower"]),
        "liquidity_capture": stable_sha256(liquidity_capture),
        "liquidity": stable_sha256(chain["liquidity"]),
        "radar": stable_sha256(chain["radar"]),
        "r77_result": stable_sha256(chain["result"]),
    }
    if envelope.get("verified_artifact_sha256") != expected:
        raise ValueError("verified artifact digest mismatch")
