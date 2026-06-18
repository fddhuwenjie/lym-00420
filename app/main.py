from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import init_db
from .exceptions import AppException
from .schemas import (
    ApprovalAction,
    ErrorResponse,
    FilterCreate,
    FilterOut,
    OperatorAction,
    TaskCreate,
    TaskListItem,
    TaskOut,
    TaskResubmit,
    UserOut,
    ApprovalRecord,
)
from .services import (
    approve_task,
    archive_task,
    create_task,
    delete_filter,
    get_filter,
    get_task,
    get_task_history,
    list_filters,
    list_tasks,
    list_users,
    reject_task,
    resubmit_task,
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
