"""Reachout CRM Core Domain Model.

Pure domain layer adhering to Hexagonal Architecture / DDD.
Free of external framework, database, or network I/O dependencies.
"""

from app.domain.campaign import Campaign
from app.domain.company import Company, normalize_company_name
from app.domain.contact import Contact, extract_first_name
from app.domain.endpoint import (
    CommunicationEndpoint,
    extract_endpoints_from_raw,
    normalize_email_addresses,
    normalize_phone_numbers,
)
from app.domain.enums import (
    AttemptType,
    CampaignStatus,
    Channel,
    CRMOutcome,
    InterviewState,
    OutreachStatus,
    ReminderStatus,
    SenderStatus,
)
from app.domain.message_template import MessageTemplate, RenderedMessage
from app.domain.outreach_attempt import OutreachAttempt, generate_idempotency_key
from app.domain.policies import (
    DEFAULT_FOLLOW_UP_THRESHOLD_DAYS,
    DEFAULT_ROTATION_SEQUENCE,
    ChannelDispatchDecision,
    ChannelFallbackPolicy,
    ChannelRotationPolicy,
    CompanyRoundMetrics,
    ContactPrioritizer,
    EligibilityResult,
    FollowUpEligibility,
    SenderRotationPolicy,
    calculate_company_round_state,
    check_contact_follow_up_eligibility,
    evaluate_automatic_eligibility,
    generate_due_reminders,
    get_contact_endpoint_metrics,
    get_next_uncovered_endpoint,
    get_uncovered_endpoints,
    is_contact_fully_covered,
    is_endpoint_covered,
    prepare_manual_resend,
    prioritize_company_first,
    select_template_round_robin,
)
from app.domain.reminder import FollowUpReminder
from app.domain.sender_account import SenderAccount
from app.domain.source_record import SourceRecord, compute_source_fingerprint
from app.domain.suppression import SuppressionRecord

__all__ = [
    # Enums
    "Channel",
    "CampaignStatus",
    "OutreachStatus",
    "AttemptType",
    "CRMOutcome",
    "InterviewState",
    "SenderStatus",
    "ReminderStatus",
    # Entities & Value Objects
    "Company",
    "normalize_company_name",
    "Contact",
    "extract_first_name",
    "CommunicationEndpoint",
    "normalize_phone_numbers",
    "normalize_email_addresses",
    "extract_endpoints_from_raw",
    "Campaign",
    "OutreachAttempt",
    "generate_idempotency_key",
    "SenderAccount",
    "MessageTemplate",
    "RenderedMessage",
    "FollowUpReminder",
    "SourceRecord",
    "compute_source_fingerprint",
    "SuppressionRecord",
    # Policies
    "prioritize_company_first",
    "calculate_company_round_state",
    "CompanyRoundMetrics",
    "ContactPrioritizer",
    "evaluate_automatic_eligibility",
    "EligibilityResult",
    "prepare_manual_resend",
    "check_contact_follow_up_eligibility",
    "generate_due_reminders",
    "FollowUpEligibility",
    "DEFAULT_FOLLOW_UP_THRESHOLD_DAYS",
    "select_template_round_robin",
    "SenderRotationPolicy",
    "ChannelFallbackPolicy",
    "ChannelRotationPolicy",
    "ChannelDispatchDecision",
    "DEFAULT_ROTATION_SEQUENCE",
    "is_endpoint_covered",
    "get_uncovered_endpoints",
    "is_contact_fully_covered",
    "get_next_uncovered_endpoint",
    "get_contact_endpoint_metrics",
]
