"""Pydantic models for Postfix policy delegation protocol messages."""

"""Pydantic models for Postfix policy delegation protocol messages."""

from typing import Dict, Optional
from pydantic import BaseModel, Field

class PolicyRequest(BaseModel):
    """Represents a single policy request from Postfix via the delegation protocol.

    Each field corresponds to a ``key=value`` line sent by Postfix.
    See https://www.postfix.org/SMTPD_POLICY_README.html for the full spec.
    """
    """Represents a single policy request from Postfix via the delegation protocol.

    Each field corresponds to a ``key=value`` line sent by Postfix.
    See https://www.postfix.org/SMTPD_POLICY_README.html for the full spec.
    """
    request: Optional[str] = None
    protocol_state: Optional[str] = None
    protocol_name: Optional[str] = None
    queue_id: Optional[str] = None
    client_address: Optional[str] = None
    client_name: Optional[str] = None
    reverse_client_name: Optional[str] = None
    instance: Optional[str] = None
    sasl_method: Optional[str] = None
    sasl_username: Optional[str] = None
    sasl_sender: Optional[str] = None
    size: Optional[str] = None
    ccert_subject: Optional[str] = None
    ccert_issuer: Optional[str] = None
    ccert_fingerprint: Optional[str] = None
    encryption_protocol: Optional[str] = None
    encryption_cipher: Optional[str] = None
    encryption_keysize: Optional[str] = None
    etrn_domain: Optional[str] = None
    stress: Optional[str] = None
    sender: Optional[str] = None
    recipient: Optional[str] = None
    recipient_count: Optional[str] = None
    helo_name: Optional[str] = None
    policy_context: Optional[str] = None
    server_address: Optional[str] = None
    server_name: Optional[str] = None

    @classmethod
    def parse_from_dict(cls, data: Dict[str, str]) -> "PolicyRequest":
        """Create a PolicyRequest from a dict of raw key=value pairs."""
        """Create a PolicyRequest from a dict of raw key=value pairs."""
        return cls(**data)

    @property
    def sender_domain(self) -> str:
        if not self.sender:
            return ""
        parts = self.sender.split("@")
        if len(parts) == 2:
            return parts[1]
        return ""

    @property
    def recipient_domain(self) -> str:
        if not self.recipient:
            return ""
        parts = self.recipient.split("@")
        if len(parts) == 2:
            return parts[1]
        return ""

    def __str__(self) -> str:
        return f"sender={self.sender} recipient={self.recipient} client={self.client_address} sasl_user={self.sasl_username}"


class PolicyResponse(BaseModel):
    """Response sent back to Postfix via the policy delegation protocol."""
    action: str
    message: Optional[str] = None

    def format(self) -> str:
        if self.message:
            return f"action={self.action} {self.message}\n\n"
        return f"action={self.action}\n\n"

    @classmethod
    def create_dunno(cls) -> "PolicyResponse":
        return cls(action="DUNNO")

    @classmethod
    def create_defer(cls, message: str) -> "PolicyResponse":
        return cls(action="DEFER", message=message)

    @classmethod
    def create_reject(cls, message: str) -> "PolicyResponse":
        return cls(action="REJECT", message=message)
