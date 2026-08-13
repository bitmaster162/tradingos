from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = [
    ROOT / "tools" / "tradingos_delivery_guard.py",
    ROOT / "tools" / "tradingos_preflight_bridge.py",
    ROOT / "tools" / "tradingos_send_authorization_review.py",
    ROOT / "tools" / "tradingos_send_executor_state.py",
    ROOT / "tools" / "tradingos_telegram_request_compiler.py",
    ROOT / "tools" / "tradingos_telegram_transport.py",
    ROOT / "tools" / "tradingos_runtime_service.py",
]
FORBIDDEN = [
    "tradingos_feedback_actions",
    "tradingos_feedback_callback",
    "tradingos_operator_impact",
    "tradingos_market_memory",
    "tradingos_value",
    "tradingos_decision_cockpit",
    "tradingos_market_radar",
    "tradingos_watchtower",
]

def test_runtime_core_import_closure_excludes_deferred_product_modules():
    for path in CORE:
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN:
            assert forbidden not in text, f"{path}: forbidden deferred dependency {forbidden}"
