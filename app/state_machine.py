from enum import Enum


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVING = "approving"
    REJECTED = "rejected"
    APPROVED = "approved"
    ARCHIVED = "archived"


class TaskAction(str, Enum):
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    RESUBMIT = "resubmit"
    ARCHIVE = "archive"
    VOTE_APPROVE = "vote_approve"
    VOTE_REJECT = "vote_reject"
    REMIND = "remind"
    ESCALATE = "escalate"


class DelegationStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ReminderType(str, Enum):
    NORMAL = "normal"
    ESCALATION = "escalation"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


PRIORITY_DEADLINE_HOURS = {
    Priority.LOW: 72,
    Priority.MEDIUM: 24,
    Priority.HIGH: 8,
}


class ApprovalMode(str, Enum):
    SINGLE = "single"
    COUNTERSIGN = "countersign"


class UserRole(str, Enum):
    USER = "user"
    MANAGER = "manager"


STATUS_DISPLAY = {
    TaskStatus.DRAFT: "草稿",
    TaskStatus.PENDING_APPROVAL: "待审批",
    TaskStatus.APPROVING: "审批中",
    TaskStatus.REJECTED: "已驳回",
    TaskStatus.APPROVED: "已通过",
    TaskStatus.ARCHIVED: "已归档",
}

PRIORITY_DISPLAY = {
    Priority.LOW: "低",
    Priority.MEDIUM: "中",
    Priority.HIGH: "高",
}


TRANSITION_RULES = {
    TaskStatus.DRAFT: {
        TaskAction.SUBMIT: TaskStatus.PENDING_APPROVAL,
    },
    TaskStatus.PENDING_APPROVAL: {
        TaskAction.APPROVE: TaskStatus.APPROVED,
        TaskAction.REJECT: TaskStatus.REJECTED,
        TaskAction.VOTE_APPROVE: TaskStatus.APPROVING,
        TaskAction.VOTE_REJECT: TaskStatus.REJECTED,
    },
    TaskStatus.APPROVING: {
        TaskAction.VOTE_APPROVE: TaskStatus.APPROVING,
        TaskAction.VOTE_REJECT: TaskStatus.REJECTED,
    },
    TaskStatus.REJECTED: {
        TaskAction.RESUBMIT: TaskStatus.PENDING_APPROVAL,
    },
    TaskStatus.APPROVED: {
        TaskAction.ARCHIVE: TaskStatus.ARCHIVED,
    },
    TaskStatus.ARCHIVED: {},
}


def is_valid_transition(current_status: TaskStatus, action: TaskAction) -> bool:
    return action in TRANSITION_RULES.get(current_status, {})


def get_next_status(current_status: TaskStatus, action: TaskAction) -> TaskStatus:
    return TRANSITION_RULES[current_status][action]
