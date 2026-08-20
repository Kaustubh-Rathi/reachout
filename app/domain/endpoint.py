"""Communication Endpoint Domain Value Object and Normalization Logic.

Models individual phone and email communication endpoints, independent normalization,
deduplication, and validation rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from app.domain.enums import Channel


def normalize_phone_numbers(phone_str: Optional[str]) -> List[str]:
    """Parse, validate, and normalize one or more phone numbers from raw string input.

    Rules:
    - Trim whitespace
    - Split comma-separated, semicolon-separated, or newline-separated values
    - Ignore empty values
    - Extract numeric digits (and optional leading '+')
    - Validate each number independently (minimum 7 digits)
    - Deduplicate identical normalized numbers while strictly preserving original input order
    - Never treat comma-separated strings as a single phone number
    """
    if not phone_str or not isinstance(phone_str, str):
        return []

    # Split by comma, semicolon, newline, or slash
    raw_tokens = re.split(r"[,;\n/]+", phone_str)
    normalized_list: List[str] = []
    seen = set()

    for token in raw_tokens:
        clean = token.strip()
        if not clean:
            continue
        # Extract digits and check length
        digits = re.sub(r"\D", "", clean)
        if len(digits) < 7:
            continue
        
        # Keep canonical digits format (e.g. '919876543210')
        norm = digits
        if norm not in seen:
            seen.add(norm)
            normalized_list.append(norm)

    return normalized_list


def normalize_email_addresses(email_str: Optional[str]) -> List[str]:
    """Parse, validate, and normalize one or more email addresses from raw string input.

    Rules:
    - Trim whitespace
    - Split comma-separated, semicolon-separated, or newline-separated values
    - Ignore empty values
    - Validate email structure independently
    - Convert to lowercase
    - Deduplicate identical normalized emails while strictly preserving original input order
    """
    if not email_str or not isinstance(email_str, str):
        return []

    raw_tokens = re.split(r"[,;\n/]+", email_str)
    normalized_list: List[str] = []
    seen = set()

    email_regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    for token in raw_tokens:
        clean = token.strip().lower()
        if not clean:
            continue
        if not email_regex.match(clean):
            continue
        if clean not in seen:
            seen.add(clean)
            normalized_list.append(clean)

    return normalized_list


@dataclass(frozen=True)
class CommunicationEndpoint:
    """Represents a discrete communication destination for a contact.

    Attributes:
        channel: Delivery channel (WHATSAPP or EMAIL).
        address: Original display address representation.
        normalized_address: Canonical normalized address used for dispatch and matching.
        ordinal: 0-indexed position of this endpoint within its channel for the contact.
    """
    channel: Channel
    address: str
    normalized_address: str
    ordinal: int = 0

    @property
    def key(self) -> str:
        """Deterministic key for endpoint identification: 'CHANNEL:NORMALIZED_ADDRESS'."""
        return f"{self.channel.value}:{self.normalized_address}"

    def matches(self, channel_or_dest: Any, destination: Optional[str] = None) -> bool:
        """Check if this endpoint matches a given destination or (channel, destination)."""
        if isinstance(channel_or_dest, Channel):
            target_channel = channel_or_dest
            target_dest = destination
        else:
            target_channel = self.channel
            target_dest = channel_or_dest

        if not target_dest or self.channel != target_channel:
            return False
        dest_clean = str(target_dest).strip().lower()
        if self.channel == Channel.WHATSAPP:
            dest_digits = re.sub(r"\D", "", dest_clean)
            return self.normalized_address == dest_digits or self.address == target_dest
        else:
            return self.normalized_address == dest_clean or self.address.lower() == dest_clean


def extract_endpoints_from_raw(
    phone_raw: Optional[str],
    email_raw: Optional[str],
) -> List[CommunicationEndpoint]:
    """Construct all valid CommunicationEndpoints from raw phone and email strings."""
    endpoints: List[CommunicationEndpoint] = []

    phones = normalize_phone_numbers(phone_raw)
    for idx, p in enumerate(phones):
        endpoints.append(
            CommunicationEndpoint(
                channel=Channel.WHATSAPP,
                address=p,
                normalized_address=p,
                ordinal=idx,
            )
        )

    emails = normalize_email_addresses(email_raw)
    for idx, e in enumerate(emails):
        endpoints.append(
            CommunicationEndpoint(
                channel=Channel.EMAIL,
                address=e,
                normalized_address=e,
                ordinal=idx,
            )
        )

    return endpoints
