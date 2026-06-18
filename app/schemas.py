from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .state_machine import ApprovalMode, Priority, TaskStatus, UserRole


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
    approval_mode: str
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
    last_operation: Optional[ApprovalRecord] = None
    created_at: datetime
    updated_at: datetime


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
