from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import init_db
from .exceptions import AppException
from .schemas import (
    ApprovalAction,
    AuditExportRequest,
    AuditExportResponse,
    BatchApprovalRequest,
    BatchApprovalResult,
    DelegationCreate,
    DelegationOut,
    DelegationRecordOut,
    ErrorResponse,
    EscalationCreate,
    FilterCreate,
    FilterOut,
    OperatorAction,
    ReminderCreate,
    ReminderRecordOut,
    TaskCreate,
    TaskFromTemplate,
    TaskListItem,
    TaskOut,
    TaskResubmit,
    TemplateCreate,
    TemplateOut,
    UserOut,
    ApprovalRecord,
)
from .services import (
    approve_task,
    archive_task,
    batch_approve,
    create_delegation,
    create_task,
    create_task_from_template,
    create_template,
    delete_filter,
    delete_template,
    escalate_task,
    export_audit_data,
    get_delegation,
    get_delegation_records,
    get_filter,
    get_reminder_records,
    get_task,
    get_task_history,
    get_template,
    list_delegations,
    list_filters,
    list_tasks,
    list_templates,
    list_users,
    reject_task,
    remind_task,
    resubmit_task,
    revoke_delegation,
    save_filter,
    submit_task,
)

app = FastAPI(
    title="任务审批系统",
    description="支持单人审批与多人会签的任务审批系统，包含状态流转、历史追踪、过滤保存等功能",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(AppException)
async def app_exception_handler(request, exc: AppException):
    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "detail": exc.detail},
    )


@app.get("/", tags=["系统"])
def root():
    return {
        "name": "任务审批系统",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "running",
    }


@app.get("/health", tags=["系统"])
def health_check():
    return {"status": "ok"}


@app.get("/users", response_model=list[UserOut], tags=["用户"])
def api_list_users():
    return list_users()


@app.post("/tasks", response_model=TaskOut, tags=["任务管理"])
def api_create_task(payload: TaskCreate):
    return create_task(
        creator_id=payload.creator_id,
        title=payload.title,
        description=payload.description,
        department=payload.department,
        priority=payload.priority,
        approval_mode=payload.approval_mode,
        approver_ids=payload.approver_ids,
    )


@app.get("/tasks", response_model=list[TaskListItem], tags=["任务管理"])
def api_list_tasks(
    department: Optional[str] = Query(None, description="按部门过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    priority: Optional[str] = Query(None, description="按优先级过滤"),
    keyword: Optional[str] = Query(None, description="标题/描述关键字搜索"),
):
    return list_tasks(
        department=department,
        status=status,
        priority=priority,
        keyword=keyword,
    )


@app.get("/tasks/{task_id}", response_model=TaskOut, tags=["任务管理"])
def api_get_task(task_id: int):
    return get_task(task_id)


@app.get("/tasks/{task_id}/history", response_model=list[ApprovalRecord], tags=["任务管理"])
def api_get_task_history(task_id: int):
    return get_task_history(task_id)


@app.post("/tasks/{task_id}/submit", response_model=TaskOut, tags=["审批流程"])
def api_submit_task(task_id: int, payload: OperatorAction):
    return submit_task(task_id, payload.operator_id)


@app.post("/tasks/{task_id}/approve", response_model=TaskOut, tags=["审批流程"])
def api_approve_task(task_id: int, payload: ApprovalAction):
    return approve_task(task_id, payload.operator_id, payload.comment)


@app.post("/tasks/{task_id}/reject", response_model=TaskOut, tags=["审批流程"])
def api_reject_task(task_id: int, payload: ApprovalAction):
    return reject_task(task_id, payload.operator_id, payload.comment)


@app.post("/tasks/{task_id}/resubmit", response_model=TaskOut, tags=["审批流程"])
def api_resubmit_task(task_id: int, payload: TaskResubmit):
    return resubmit_task(
        task_id=task_id,
        operator_id=payload.operator_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        department=payload.department,
    )


@app.post("/tasks/{task_id}/archive", response_model=TaskOut, tags=["审批流程"])
def api_archive_task(task_id: int, payload: OperatorAction):
    return archive_task(task_id, payload.operator_id)


@app.post("/filters", response_model=FilterOut, tags=["过滤条件"])
def api_save_filter(payload: FilterCreate):
    return save_filter(payload.user_id, payload.filter_name, payload.filter_data)


@app.get("/filters", response_model=list[FilterOut], tags=["过滤条件"])
def api_list_filters(user_id: int):
    return list_filters(user_id)


@app.get("/filters/{filter_id}", response_model=FilterOut, tags=["过滤条件"])
def api_get_filter(filter_id: int, user_id: int):
    return get_filter(filter_id, user_id)


@app.delete("/filters/{filter_id}", tags=["过滤条件"])
def api_delete_filter(filter_id: int, user_id: int):
    delete_filter(filter_id, user_id)
    return {"ok": True}


@app.post("/delegations", response_model=DelegationOut, tags=["审批委托"])
def api_create_delegation(payload: DelegationCreate):
    return create_delegation(
        delegator_id=payload.delegator_id,
        delegatee_id=payload.delegatee_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )


@app.get("/delegations", response_model=list[DelegationOut], tags=["审批委托"])
def api_list_delegations(delegator_id: Optional[int] = Query(None, description="按委托人过滤")):
    return list_delegations(delegator_id=delegator_id)


@app.get("/delegations/{delegation_id}", response_model=DelegationOut, tags=["审批委托"])
def api_get_delegation(delegation_id: int):
    return get_delegation(delegation_id)


@app.post("/delegations/{delegation_id}/revoke", response_model=DelegationOut, tags=["审批委托"])
def api_revoke_delegation(delegation_id: int, payload: OperatorAction):
    return revoke_delegation(delegation_id, payload.operator_id)


@app.get("/delegation-records", response_model=list[DelegationRecordOut], tags=["审批委托"])
def api_get_delegation_records(task_id: Optional[int] = Query(None, description="按任务过滤")):
    return get_delegation_records(task_id=task_id)


@app.post("/tasks/{task_id}/remind", response_model=TaskOut, tags=["超时催办"])
def api_remind_task(task_id: int, payload: ReminderCreate):
    return remind_task(task_id, payload.operator_id, payload.comment)


@app.post("/tasks/{task_id}/escalate", response_model=TaskOut, tags=["超时催办"])
def api_escalate_task(task_id: int, payload: EscalationCreate):
    return escalate_task(task_id, payload.operator_id, payload.comment)


@app.get("/reminder-records", response_model=list[ReminderRecordOut], tags=["超时催办"])
def api_get_reminder_records(task_id: Optional[int] = Query(None, description="按任务过滤")):
    return get_reminder_records(task_id=task_id)


@app.post("/batch-approve", response_model=BatchApprovalResult, tags=["批量审批"])
def api_batch_approve(payload: BatchApprovalRequest):
    return batch_approve(
        operator_id=payload.operator_id,
        task_ids=payload.task_ids,
        action=payload.action,
        comment=payload.comment,
    )


@app.post("/templates", response_model=TemplateOut, tags=["审批模板"])
def api_create_template(payload: TemplateCreate):
    return create_template(
        creator_id=payload.creator_id,
        template_name=payload.template_name,
        department=payload.department,
        priority=payload.priority,
        approval_mode=payload.approval_mode,
        approver_ids=payload.approver_ids,
    )


@app.get("/templates", response_model=list[TemplateOut], tags=["审批模板"])
def api_list_templates(creator_id: Optional[int] = Query(None, description="按创建人过滤")):
    return list_templates(creator_id=creator_id)


@app.get("/templates/{template_id}", response_model=TemplateOut, tags=["审批模板"])
def api_get_template(template_id: int):
    return get_template(template_id)


@app.delete("/templates/{template_id}", tags=["审批模板"])
def api_delete_template(template_id: int, operator_id: int):
    delete_template(template_id, operator_id)
    return {"ok": True}


@app.post("/tasks/from-template", response_model=TaskOut, tags=["审批模板"])
def api_create_task_from_template(payload: TaskFromTemplate):
    return create_task_from_template(
        creator_id=payload.creator_id,
        template_id=payload.template_id,
        title=payload.title,
        description=payload.description,
    )


@app.post("/audit/export", response_model=AuditExportResponse, tags=["审计导出"])
def api_export_audit_data(payload: AuditExportRequest):
    return export_audit_data(
        department=payload.department,
        status=payload.status,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
