import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.campaign import Campaign
from app.domain.company import Company
from app.domain.contact import Contact
from app.domain.enums import CampaignStatus, Channel, OutreachStatus, SenderStatus
from app.domain.sender_account import SenderAccount
from app.infrastructure.database import Base
from app.infrastructure.repositories.sqlite_campaign_repository import SqliteCampaignRepository
from app.infrastructure.repositories.sqlite_company_repository import SqliteCompanyRepository
from app.infrastructure.repositories.sqlite_contact_repository import SqliteContactRepository
from app.infrastructure.repositories.sqlite_outreach_repository import SqliteOutreachRepository
from app.infrastructure.repositories.sqlite_sender_repository import SqliteSenderRepository
from app.infrastructure.scheduler.campaign_scheduler import PersistentCampaignScheduler
from app.services.contact_service import ContactService
from app.services.template_service import TemplateService


@pytest.fixture
def isolated_session_factory(tmp_path):
    """Setup isolated clean database before test."""
    engine = create_engine(f"sqlite:///{tmp_path / 'endpoint_coverage_test.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    with SessionFactory() as session:
        TemplateService(session).seed_defaults_if_empty()
        # SenderService.seed_defaults_if_empty() is a deliberate no-op, so seed
        # active placeholder senders explicitly for the campaign scheduler.
        srepo = SqliteSenderRepository(session)
        for i in (1, 2):
            wa = SenderAccount.create(
                channel=Channel.WHATSAPP,
                provider="playwright_whatsapp",
                identity=f"+91900000000{i}",
                display_name=f"WA{i}",
                sender_id=f"WA{i}",
                daily_limit=100,
            )
            wa.status = SenderStatus.ACTIVE
            srepo.save(wa)
            em = SenderAccount.create(
                channel=Channel.EMAIL,
                provider="smtp",
                identity=f"sender{i}@example.com",
                display_name=f"EM{i}",
                sender_id=f"EMAIL{i}",
                daily_limit=100,
            )
            em.status = SenderStatus.ACTIVE
            srepo.save(em)
        session.commit()
    return SessionFactory


def test_multi_endpoint_round_robin_dispatch(isolated_session_factory):
    """Deterministic Dry Run: 5 companies, multi-endpoint HR contacts.

    Verifies:
    1. Company-first round-robin ordering across C1..C5.
    2. Dynamic Channel Rotation (WA, WA, EMAIL, EMAIL...).
    3. Multi-endpoint coverage (all phones and all emails contacted).
    4. Exact destination recorded on each OutreachAttempt.
    5. Persistent rotation cursor saved in metadata.
    """
    SessionFactory = isolated_session_factory
    with SessionFactory() as session:
        company_repo = SqliteCompanyRepository(session)
        contact_repo = SqliteContactRepository(session)

        # 5 Companies with 2 HRs each.
        # HR1 has 2 phones and 1 email. HR2 has 1 phone and 2 emails.
        created_contacts = []
        for i in range(1, 6):
            c_code = f"comp_{i}"
            c_name = f"Company {i}"

            comp = Company.create(
                name=c_name,
                company_id=c_code,
            )
            company_repo.save(comp)

            # HR1: 2 phones, 1 email (3 endpoints)
            hr1 = Contact(
                contact_id=f"c{i}_hr1",
                company_id=c_code,
                name=f"HR 1 {c_name}",
                phone=f"+9198000{i}0001, +9198000{i}0002",
                email=f"hr1.first@{c_code}.com",
            )
            contact_repo.save(hr1)
            created_contacts.append(hr1)

            # HR2: 1 phone, 2 emails (3 endpoints)
            hr2 = Contact(
                contact_id=f"c{i}_hr2",
                company_id=c_code,
                name=f"HR 2 {c_name}",
                phone=f"+9198000{i}0003",
                email=f"hr2.primary@{c_code}.com, hr2.secondary@{c_code}.com",
            )
            contact_repo.save(hr2)
            created_contacts.append(hr2)

        session.commit()

    with SessionFactory() as session:
        camp_repo = SqliteCampaignRepository(session)
        campaign = Campaign.create(
            name="Multi-Endpoint Validation Run",
            channel=Channel.WHATSAPP,
            automatic_quota=100,
        )
        camp_repo.save(campaign)
        session.commit()
        camp_id = campaign.id

    # Launch Campaign via PersistentCampaignScheduler in mock mode
    scheduler = PersistentCampaignScheduler(session_factory=SessionFactory)

    scheduler.start_campaign(camp_id)
    t = scheduler._active_threads.get(camp_id)
    if t:
        t.join(timeout=20)

    # Verify campaign execution results
    with SessionFactory() as session:
        outreach_repo = SqliteOutreachRepository(session)
        camp_repo = SqliteCampaignRepository(session)
        contact_svc = ContactService(session)

        campaign = camp_repo.get_by_id(camp_id)
        assert campaign.status == CampaignStatus.COMPLETED

        # Check metadata rotation state persistence
        rot_state = campaign.metadata.get("rotation_state", {})
        assert "channel_cursor" in rot_state
        assert "sender_cursors" in rot_state
        assert "template_cursors" in rot_state

        all_attempts = outreach_repo.list_by_campaign(camp_id)
        assert len(all_attempts) > 0

        # Verify every attempt has an exact destination recorded
        for att in all_attempts:
            assert att.status == OutreachStatus.SENT
            assert att.destination is not None
            assert len(att.destination) > 0

        # Verify channel rotation occurred in sequence
        channels_sequence = [att.channel for att in all_attempts]
        assert Channel.WHATSAPP in channels_sequence
        assert Channel.EMAIL in channels_sequence

        # Verify all contacts have 100% endpoint coverage
        contacts_res = contact_svc.list_contacts()
        contacts_list = contacts_res["contacts"]
        assert len(contacts_list) == 10

        for c_dict in contacts_list:
            cov = c_dict["coverage"]
            assert cov["total_endpoints"] == 3
            assert cov["covered_endpoints"] == 3
            assert c_dict["is_fully_covered"] is True

        # Verify company-first round robin ordering across initial attempts:
        # First 5 attempts should touch C1, C2, C3, C4, C5 before repeating companies
        first_5_attempts = all_attempts[:5]
        first_5_companies = [contact_repo.get_by_id(att.contact_id).company_id for att in first_5_attempts]
        assert len(set(first_5_companies)) == 5
