"""Unit tests for Phase 8 Sender Authentication state machine and multi-session management."""

import pytest
from app.domain.enums import Channel, SenderStatus
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import SessionFactory
from app.infrastructure.providers.session_manager import WhatsAppSessionManager
from app.services.sender_service import SenderService


def test_sender_status_enum_values():
    """Verify all Phase 8 sender statuses exist and have correct usability flags."""
    assert SenderStatus.ACTIVE.is_usable is True
    assert SenderStatus.NOT_CONFIGURED.is_usable is False
    assert SenderStatus.QR_REQUIRED.is_usable is False
    assert SenderStatus.AUTHENTICATING.is_usable is False
    assert SenderStatus.AUTH_REQUIRED.is_usable is False
    assert SenderStatus.DISCONNECTED.is_usable is False
    assert SenderStatus.ERROR.is_usable is False


def test_whatsapp_session_manager_state_machine():
    """Verify in-memory state tracking and mock QR generation in WhatsAppSessionManager."""
    mgr = WhatsAppSessionManager()
    
    # 1. Default initial state
    st = mgr.get_auth_state("WA_SESSION_TEST")
    assert st["status"] == SenderStatus.NOT_CONFIGURED.value
    assert st["qr_code"] is None

    # 2. Start mock QR authentication
    events_captured = []
    def callback(evt, payload):
        events_captured.append((evt, payload))

    res = mgr.start_qr_authentication("WA_SESSION_TEST", on_event_callback=callback)
    assert res["status"] in (SenderStatus.AUTHENTICATING.value, SenderStatus.QR_REQUIRED.value)

    # In mock mode, wait or poll state
    import time
    time.sleep(0.4)
    st_after = mgr.get_auth_state("WA_SESSION_TEST")
    assert st_after["status"] == SenderStatus.QR_REQUIRED.value
    assert st_after["qr_code"] is not None
    assert st_after["qr_code"].startswith("data:image/svg+xml;base64,")

    # 3. Confirm authentication
    confirm_res = mgr.confirm_mock_auth("WA_SESSION_TEST", on_event_callback=callback)
    assert confirm_res["status"] == SenderStatus.ACTIVE.value
    assert confirm_res["qr_code"] is None


def test_sender_service_configure_sessions():
    """Verify dynamic session count configuration for arbitrary N WhatsApp sessions."""
    with SessionFactory() as session:
        svc = SenderService(session)
        
        # Configure exactly 3 WhatsApp sessions
        sessions = svc.configure_whatsapp_sessions(3)
        assert len(sessions) == 3
        ids = {s["id"] for s in sessions}
        assert "WA_SESSION_1" in ids
        assert "WA_SESSION_2" in ids
        assert "WA_SESSION_3" in ids

        # Verify readiness check aggregation
        readiness = svc.get_senders_readiness()
        assert readiness["whatsapp"]["total_count"] == 3
        assert "overall_ready" in readiness
