"""Channel-agnostic, explicit per-change human approvals."""

from liquid.approval.defaults import (
    CLIApproverChannel,
    InMemoryApprovalStore,
    InMemoryApproverChannel,
    LiquidWriteTarget,
    StaticApprovalPolicy,
)
from liquid.approval.manager import ApprovalConflictError, ApprovalManager
from liquid.approval.models import AuditRecord, ChannelResponse, Proposal
from liquid.approval.protocols import (
    ApprovalPolicy,
    ApprovalStore,
    ApproverChannel,
    ChangeTarget,
    ResultVerifier,
    ScheduledRule,
    StateDescriber,
)
from liquid.approval.sqlite import SQLiteApprovalStore, SQLiteKnowledgeStateStore

__all__ = [
    "ApprovalConflictError",
    "ApprovalManager",
    "ApprovalPolicy",
    "ApprovalStore",
    "ApproverChannel",
    "AuditRecord",
    "CLIApproverChannel",
    "ChangeTarget",
    "ChannelResponse",
    "InMemoryApprovalStore",
    "InMemoryApproverChannel",
    "LiquidWriteTarget",
    "Proposal",
    "ResultVerifier",
    "SQLiteApprovalStore",
    "SQLiteKnowledgeStateStore",
    "ScheduledRule",
    "StateDescriber",
    "StaticApprovalPolicy",
]
