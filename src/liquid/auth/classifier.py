from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from liquid.models.schema import AuthRequirement


class EscalationInfo(BaseModel):
    tier: str
    action_required: str
    docs_url: str | None = None
    instructions: str = ""


class AuthClassifier:
    """Classifies auth requirements and generates escalation info."""

    def classify(self, auth: AuthRequirement) -> EscalationInfo:
        match auth.tier:
            case "A":
                return EscalationInfo(
                    tier="A",
                    action_required="none",
                    docs_url=auth.docs_url,
                    instructions="OAuth flow can proceed automatically. Redirect user to authorize.",
                )
            case "B":
                return EscalationInfo(
                    tier="B",
                    action_required="admin_registration",
                    docs_url=auth.docs_url,
                    instructions=(
                        "This service requires creating a developer application first. "
                        "An admin needs to register the app at the service's developer portal, "
                        "then provide client_id and client_secret."
                    ),
                )
            case "C":
                return self._classify_tier_c(auth)
            case _:
                return EscalationInfo(
                    tier=auth.tier,
                    action_required="manual",
                    docs_url=auth.docs_url,
                    instructions="Unknown auth tier. Manual configuration required.",
                )

    def _classify_tier_c(self, auth: AuthRequirement) -> EscalationInfo:
        match auth.type:
            case "api_key":
                return EscalationInfo(
                    tier="C",
                    action_required="provide_api_key",
                    docs_url=auth.docs_url,
                    instructions="This service requires an API key. Obtain it from the service dashboard.",
                )
            case "basic":
                return EscalationInfo(
                    tier="C",
                    action_required="provide_credentials",
                    docs_url=auth.docs_url,
                    instructions="This service requires username and password for Basic auth.",
                )
            case _:
                return EscalationInfo(
                    tier="C",
                    action_required="manual_configuration",
                    docs_url=auth.docs_url,
                    instructions="This service requires custom authentication. Contact support for setup.",
                )
