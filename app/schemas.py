from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .state_machine import (
    ApprovalMode,
    Priority,
    TaskStatus,
    UserRole,
    DelegationStatus,
    ReminderType,
)


class UserBase(BaseModel):
    username: str
    name: str
    department: str
    role: UserRole = UserRole.USER


class UserOut(UserBase):
    id: int

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    creator_id: int
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    department: str
    priority: Priority = Priority.MEDIUM
    approval_mode: ApprovalMode = ApprovalMode.SINGLE
    approver_ids: list[int]


class TaskResubmit(BaseModel):
    operator_id: int
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[Priority] = None
    department: Optional[str] = None


class ApprovalAction(BaseModel):
    operator_id: int
    comment: str = ""


class OperatorAction(BaseModel):
    operator_id: int


class ApproverInfo(BaseModel):
    id: int
    task_id: int
    user_id: int
    user_name: Optional[str] = None
    user_department: Optional[str] = None
    has_voted: int
    vote_result: Optional[str] = None
    voted_at: Optional[datetime] = None


class ApprovalRecord(BaseModel):
    id: int
    task_id: int
    operator_id: int
    operator_name: Optional[str] = None
    action: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    comment: str
    operated_at: datetime


class FilterCreate(BaseModel):
    user_id: int
    filter_name: str
    filter_data: dict[str, Any]


class FilterOut(BaseModel):
    id: int
    user_id: int
    filter_name: str
    filter_data: dict[str, Any]
    created_at: datetime


class ErrorResponse(BaseModel):
    code: int
    detail: str


class DelegationCreate(BaseModel):
    delegator_id: int
    delegatee_id: int
    start_time: datetime
    end_time: datetime


class DelegationOut(BaseModel):
    id: int
    delegator_id: int
    delegator_name: Optional[str] = None
    delegatee_id: int
    delegatee_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    status: str
    created_at: datetime


class DelegationRecordOut(BaseModel):
    id: int
    task_id: int
    delegation_id: int
    original_approver_id: int
    original_approver_name: Optional[str] = None
    delegatee_id: int
    delegatee_name: Optional[str] = None
    delegated_at: datetime
    reverted_at: Optional[datetime] = None


class ReminderRecordOut(BaseModel):
    id: int
    task_id: int
    operator_id: int
    operator_name: Optional[str] = None
    reminder_type: str
    escalation_level: int
    comment: str
    reminded_at: datetime


class ReminderCreate(BaseModel):
    operator_id: int
    comment: str = ""


class EscalationCreate(BaseModel):
    operator_id: int
    comment: str = ""


class TemplateCreate(BaseModel):
    creator_id: int
    template_name: str = Field(..., min_length=1, max_length=100)
    department: str
    priority: Priority = Priority.MEDIUM
    approval_mode: ApprovalMode = ApprovalMode.SINGLE
    approver_ids: list[int]


class TemplateOut(BaseModel):
    id: int
    creator_id: int
    creator_name: Optional[str] = None
    template_name: str
    department: str
    priority: str
    approval_mode: str
    approver_ids: list[int]
    created_at: datetime


class TaskFromTemplate(BaseModel):
    creator_id: int
    template_id: int
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""


class BatchApprovalRequest(BaseModel):
    operator_id: int
    task_ids: list[int]
    action: str = Field(..., pattern="^(approve|reject)$")
    comment: str = ""


class BatchApprovalResult(BaseModel):
    success_count: int
    failed_count: int
    success_ids: list[int]
    failed_details: list[dict]


class AuditExportRequest(BaseModel):
    department: Optional[str] = None
    status: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class AuditExportResponse(BaseModel):
    tasks: list[dict]
    approval_records: list[dict]
    delegation_records: list[dict]
    reminder_records: list[dict]
    export_time: datetime
    total_tasks: int
    total_records: int


class TaskOut(BaseModel):
    id: int
    title: str
    description: str
    department: str
    priority: str
    status: str
    creator_id: int
    creator_name: Optional[str] = None
    current_handler_id: Optional[int] = None
    current_handler_name: Optional[str] = None
    original_approver_id: Optional[int] = None
    approval_mode: str
    deadline: Optional[datetime] = None
    reminder_count: int = 0
    escalated: int = 0
    approvers: list[ApproverInfo] = []
    last_operation: Optional[ApprovalRecord] = None
    created_at: datetime
    updated_at: datetime


class TaskListItem(BaseModel):
    id: int
    title: str
    department: str
    priority: str
    status: str
    creator_id: int
    creator_name: Optional[str] = None
    current_handler_id: Optional[int] = None
    current_handler_name: Optional[str] = None
    deadline: Optional[datetime] = None
    reminder_count: int = 0
    escalated: int = 0
    last_operation: Optional[ApprovalRecord] = None
    created_at: datetime
    updated_at: datetime
