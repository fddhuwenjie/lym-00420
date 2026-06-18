import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from .database import get_db
from .exceptions import (
    AlreadyEscalatedException,
    AppException,
    BatchApprovalException,
    DelegationNotFoundException,
    DuplicateOperationException,
    FilterNotFoundException,
    InvalidDelegationException,
    InvalidStatusTransitionException,
    PermissionDeniedException,
    TaskNotOverdueException,
    TaskNotFoundException,
    TemplateNotFoundException,
    UserNotFoundException,
    ValidationException,
)
from .state_machine import (
    ApprovalMode,
    DelegationStatus,
    Priority,
    PRIORITY_DEADLINE_HOURS,
    ReminderType,
    TaskAction,
    TaskStatus,
    TRANSITION_RULES,
    UserRole,
    get_next_status,
    is_valid_transition,
)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row else None


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def _validate_department(value: str) -> None:
    if not value or not value.strip():
        raise ValidationException("department", "部门不能为空")


def _validate_priority(value: str) -> None:
    valid = {Priority.LOW, Priority.MEDIUM, Priority.HIGH}
    if value not in valid:
        raise ValidationException("priority", f"优先级必须为 {valid} 之一")


def _validate_approval_mode(value: str) -> None:
    valid = {ApprovalMode.SINGLE, ApprovalMode.COUNTERSIGN}
    if value not in valid:
        raise ValidationException("approval_mode", f"审批模式必须为 {valid} 之一")


def _get_user_or_404(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise UserNotFoundException(user_id)
    return _row_to_dict(row)


def _get_task_or_404(conn: sqlite3.Connection, task_id: int) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise TaskNotFoundException(task_id)
    return _row_to_dict(row)


def _add_approval_record(
    conn: sqlite3.Connection,
    task_id: int,
    operator_id: int,
    action: str,
    from_status: Optional[str],
    to_status: Optional[str],
    comment: str = "",
) -> None:
    conn.execute(
        """INSERT INTO approval_records
           (task_id, operator_id, action, from_status, to_status, comment)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (task_id, operator_id, action, from_status, to_status, comment),
    )


_UNCHANGED = object()


def _update_task_status(
    conn: sqlite3.Connection,
    task_id: int,
    new_status: str,
    current_handler_id: Optional[Any] = _UNCHANGED,
) -> None:
    if current_handler_id is _UNCHANGED:
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, datetime.now(), task_id),
        )
    else:
        conn.execute(
            "UPDATE tasks SET status = ?, current_handler_id = ?, updated_at = ? WHERE id = ?",
            (new_status, current_handler_id, datetime.now(), task_id),
        )


def _get_task_with_handler(conn: sqlite3.Connection, task_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT t.*,
                  u_creator.name AS creator_name,
                  u_handler.name AS current_handler_name
           FROM tasks t
           LEFT JOIN users u_creator ON t.creator_id = u_creator.id
           LEFT JOIN users u_handler ON t.current_handler_id = u_handler.id
           WHERE t.id = ?""",
        (task_id,),
    ).fetchone()
    return _row_to_dict(row)


def _get_tasks_list_with_handler(
    conn: sqlite3.Connection,
    sql: str,
    params: list,
) -> list[dict]:
    rows = conn.execute(sql, params).fetchall()
    tasks = _rows_to_list(rows)
    for t in tasks:
        t["last_operation"] = _get_last_operation(conn, t["id"])
    return tasks


def _get_last_operation(conn: sqlite3.Connection, task_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT ar.*, u.name AS operator_name
           FROM approval_records ar
           LEFT JOIN users u ON ar.operator_id = u.id
           WHERE ar.task_id = ?
           ORDER BY ar.operated_at DESC
           LIMIT 1""",
        (task_id,),
    ).fetchone()
    return _row_to_dict(row)


def list_users() -> list[dict]:
    with get_db() as conn:
        return _rows_to_list(conn.execute("SELECT * FROM users ORDER BY id").fetchall())


def create_task(
    creator_id: int,
    title: str,
    description: str,
    department: str,
    priority: str,
    approval_mode: str,
    approver_ids: list[int],
) -> dict:
    if not title or not title.strip():
        raise ValidationException("title", "标题不能为空")
    _validate_department(department)
    _validate_priority(priority)
    _validate_approval_mode(approval_mode)

    if approval_mode == ApprovalMode.SINGLE and len(approver_ids) != 1:
        raise ValidationException(
            "approver_ids", "单人审批模式必须指定且仅指定 1 个审批人"
        )
    if approval_mode == ApprovalMode.COUNTERSIGN and len(approver_ids) < 2:
        raise ValidationException(
            "approver_ids", "多人会签模式必须指定至少 2 个审批人"
        )

    with get_db() as conn:
        creator = _get_user_or_404(conn, creator_id)

        for aid in approver_ids:
            _get_user_or_404(conn, aid)

        first_handler_id = approver_ids[0] if approval_mode == ApprovalMode.SINGLE else None

        cursor = conn.execute(
            """INSERT INTO tasks
               (title, description, department, priority, status, creator_id,
                current_handler_id, approval_mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title.strip(),
                description,
                department,
                priority,
                TaskStatus.DRAFT,
                creator_id,
                first_handler_id,
                approval_mode,
            ),
        )
        task_id = cursor.lastrowid

        for aid in approver_ids:
            conn.execute(
                "INSERT INTO approvers (task_id, user_id) VALUES (?, ?)",
                (task_id, aid),
            )

        _add_approval_record(
            conn, task_id, creator_id, "create", None, TaskStatus.DRAFT,
            f"创建人 {creator['name']} 创建任务"
        )

        return _get_task_with_handler(conn, task_id)


def submit_task(task_id: int, operator_id: int) -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        current_status = TaskStatus(task["status"])
        action = TaskAction.SUBMIT

        if task["creator_id"] != operator_id:
            raise PermissionDeniedException(
                operator_id, action.value, task_id,
                "只有创建人可以提交审批"
            )

        if not is_valid_transition(current_status, action):
            raise InvalidStatusTransitionException(
                task_id, current_status.value,
                TRANSITION_RULES[current_status].get(action, "N/A").value
                if action in TRANSITION_RULES[current_status] else "不可达",
                action.value
            )

        next_status = get_next_status(current_status, action)
        submit_time = datetime.now()
        deadline = _calculate_deadline(task["priority"], submit_time)

        handler_id = task["current_handler_id"]
        if task["approval_mode"] == ApprovalMode.SINGLE and handler_id:
            handler_id = _apply_delegation_if_needed(conn, task_id, handler_id)

        conn.execute(
            """UPDATE tasks
               SET status = ?, deadline = ?, current_handler_id = ?, updated_at = ?
               WHERE id = ?""",
            (next_status.value, deadline, handler_id, submit_time, task_id),
        )

        _add_approval_record(
            conn, task_id, operator_id, action.value,
            current_status.value, next_status.value,
            f"提交审批，截止时间: {deadline.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return _get_task_with_handler(conn, task_id)


def _is_task_approver(conn: sqlite3.Connection, task_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM approvers WHERE task_id = ? AND user_id = ?",
        (task_id, user_id),
    ).fetchone()
    return row is not None


def _countersign_check_approved(conn: sqlite3.Connection, task_id: int) -> bool:
    rows = conn.execute(
        "SELECT user_id, has_voted, vote_result FROM approvers WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    total = len(rows)
    if total == 0:
        return False
    approved = sum(1 for r in rows if r["has_voted"] and r["vote_result"] == "approve")
    return approved == total


def approve_task(task_id: int, operator_id: int, comment: str = "") -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        current_status = TaskStatus(task["status"])

        if task["approval_mode"] == ApprovalMode.SINGLE:
            action = TaskAction.APPROVE
            if not is_valid_transition(current_status, action):
                raise InvalidStatusTransitionException(
                    task_id, current_status.value, "不可达", action.value
                )
            if task["current_handler_id"] != operator_id:
                raise PermissionDeniedException(
                    operator_id, action.value, task_id,
                    "单人审批模式下只有当前处理人可以审批"
                )
            next_status = get_next_status(current_status, action)
            _update_task_status(conn, task_id, next_status.value, current_handler_id=None)
            _add_approval_record(
                conn, task_id, operator_id, action.value,
                current_status.value, next_status.value, comment or "审批通过"
            )
        else:
            action = TaskAction.VOTE_APPROVE
            if not is_valid_transition(current_status, action):
                raise InvalidStatusTransitionException(
                    task_id, current_status.value, "不可达", action.value
                )
            if not _is_task_approver(conn, task_id, operator_id):
                raise PermissionDeniedException(
                    operator_id, action.value, task_id,
                    "多人会签模式下只有审批人列表中的用户可以投票"
                )
            approver_row = conn.execute(
                "SELECT has_voted FROM approvers WHERE task_id = ? AND user_id = ?",
                (task_id, operator_id),
            ).fetchone()
            if approver_row["has_voted"]:
                raise DuplicateOperationException(task_id, action.value, operator_id)

            conn.execute(
                """UPDATE approvers
                   SET has_voted = 1, vote_result = 'approve', voted_at = ?
                   WHERE task_id = ? AND user_id = ?""",
                (datetime.now(), task_id, operator_id),
            )

            next_status = get_next_status(current_status, action)
            all_approved = _countersign_check_approved(conn, task_id)
            if all_approved:
                final_status = TaskStatus.APPROVED
                _update_task_status(conn, task_id, final_status.value, current_handler_id=None)
                _add_approval_record(
                    conn, task_id, operator_id, "approve_final",
                    current_status.value, final_status.value,
                    comment or "全票通过，审批完成"
                )
            else:
                _update_task_status(conn, task_id, next_status.value)
                _add_approval_record(
                    conn, task_id, operator_id, action.value,
                    current_status.value, next_status.value,
                    comment or "投赞成票，等待其他审批人"
                )

        return _get_task_with_handler(conn, task_id)


def reject_task(task_id: int, operator_id: int, comment: str = "") -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        current_status = TaskStatus(task["status"])

        if task["approval_mode"] == ApprovalMode.SINGLE:
            action = TaskAction.REJECT
            if not is_valid_transition(current_status, action):
                raise InvalidStatusTransitionException(
                    task_id, current_status.value, "不可达", action.value
                )
            if task["current_handler_id"] != operator_id:
                raise PermissionDeniedException(
                    operator_id, action.value, task_id,
                    "单人审批模式下只有当前处理人可以驳回"
                )
        else:
            action = TaskAction.VOTE_REJECT
            if not is_valid_transition(current_status, action):
                raise InvalidStatusTransitionException(
                    task_id, current_status.value, "不可达", action.value
                )
            if not _is_task_approver(conn, task_id, operator_id):
                raise PermissionDeniedException(
                    operator_id, action.value, task_id,
                    "多人会签模式下只有审批人列表中的用户可以投票"
                )
            approver_row = conn.execute(
                "SELECT has_voted FROM approvers WHERE task_id = ? AND user_id = ?",
                (task_id, operator_id),
            ).fetchone()
            if approver_row["has_voted"]:
                raise DuplicateOperationException(task_id, action.value, operator_id)

            conn.execute(
                """UPDATE approvers
                   SET has_voted = 1, vote_result = 'reject', voted_at = ?
                   WHERE task_id = ? AND user_id = ?""",
                (datetime.now(), task_id, operator_id),
            )

        next_status = get_next_status(current_status, action)
        _update_task_status(conn, task_id, next_status.value, current_handler_id=task["creator_id"])
        _add_approval_record(
            conn, task_id, operator_id, action.value,
            current_status.value, next_status.value, comment or "驳回"
        )
        return _get_task_with_handler(conn, task_id)


def resubmit_task(
    task_id: int,
    operator_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    department: Optional[str] = None,
) -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        current_status = TaskStatus(task["status"])
        action = TaskAction.RESUBMIT

        if task["creator_id"] != operator_id:
            raise PermissionDeniedException(
                operator_id, action.value, task_id,
                "只有创建人可以重提"
            )
        if not is_valid_transition(current_status, action):
            raise InvalidStatusTransitionException(
                task_id, current_status.value, "不可达", action.value
            )

        update_fields = []
        update_values = []

        if title is not None:
            if not title.strip():
                raise ValidationException("title", "标题不能为空")
            update_fields.append("title = ?")
            update_values.append(title.strip())
        if description is not None:
            update_fields.append("description = ?")
            update_values.append(description)
        if priority is not None:
            _validate_priority(priority)
            update_fields.append("priority = ?")
            update_values.append(priority)
        if department is not None:
            _validate_department(department)
            update_fields.append("department = ?")
            update_values.append(department)

        if update_fields:
            update_fields.append("updated_at = ?")
            update_values.append(datetime.now())
            update_values.append(task_id)
            conn.execute(
                f"UPDATE tasks SET {', '.join(update_fields)} WHERE id = ?",
                update_values,
            )

        next_status = get_next_status(current_status, action)
        resubmit_time = datetime.now()
        deadline = _calculate_deadline(task["priority"], resubmit_time)

        if task["approval_mode"] == ApprovalMode.SINGLE:
            approver_row = conn.execute(
                "SELECT user_id FROM approvers WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            handler_id = approver_row["user_id"] if approver_row else None
            if handler_id:
                handler_id = _apply_delegation_if_needed(conn, task_id, handler_id)
        else:
            handler_id = None
            conn.execute(
                "UPDATE approvers SET has_voted = 0, vote_result = NULL, voted_at = NULL WHERE task_id = ?",
                (task_id,),
            )

        conn.execute(
            """UPDATE tasks
               SET status = ?, deadline = ?, current_handler_id = ?,
                   reminder_count = 0, escalated = 0, updated_at = ?
               WHERE id = ?""",
            (next_status.value, deadline, handler_id, resubmit_time, task_id),
        )

        _add_approval_record(
            conn, task_id, operator_id, action.value,
            current_status.value, next_status.value,
            f"驳回后重提，历史保留，新截止时间: {deadline.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return _get_task_with_handler(conn, task_id)


def archive_task(task_id: int, operator_id: int) -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        current_status = TaskStatus(task["status"])
        action = TaskAction.ARCHIVE

        if task["creator_id"] != operator_id:
            raise PermissionDeniedException(
                operator_id, action.value, task_id,
                "只有创建人可以归档"
            )
        if not is_valid_transition(current_status, action):
            raise InvalidStatusTransitionException(
                task_id, current_status.value, "不可达", action.value
            )

        next_status = get_next_status(current_status, action)
        _update_task_status(conn, task_id, next_status.value)
        _add_approval_record(
            conn, task_id, operator_id, action.value,
            current_status.value, next_status.value, "归档"
        )
        return _get_task_with_handler(conn, task_id)


def get_task(task_id: int) -> dict:
    with get_db() as conn:
        task = _get_task_with_handler(conn, task_id)
        if not task:
            raise TaskNotFoundException(task_id)
        approvers = _rows_to_list(conn.execute(
            """SELECT a.*, u.name AS user_name, u.department AS user_department
               FROM approvers a
               LEFT JOIN users u ON a.user_id = u.id
               WHERE a.task_id = ?
               ORDER BY a.id""",
            (task_id,),
        ).fetchall())
        task["approvers"] = approvers
        task["last_operation"] = _get_last_operation(conn, task_id)
        return task


def list_tasks(
    department: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    keyword: Optional[str] = None,
) -> list[dict]:
    with get_db() as conn:
        sql = """SELECT t.*,
                        u_creator.name AS creator_name,
                        u_handler.name AS current_handler_name
                 FROM tasks t
                 LEFT JOIN users u_creator ON t.creator_id = u_creator.id
                 LEFT JOIN users u_handler ON t.current_handler_id = u_handler.id
                 WHERE 1=1"""
        params = []
        if department:
            sql += " AND t.department = ?"
            params.append(department)
        if status:
            sql += " AND t.status = ?"
            params.append(status)
        if priority:
            sql += " AND t.priority = ?"
            params.append(priority)
        if keyword:
            sql += " AND (t.title LIKE ? OR t.description LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        sql += " ORDER BY t.updated_at DESC"
        return _get_tasks_list_with_handler(conn, sql, params)


def get_task_history(task_id: int) -> list[dict]:
    with get_db() as conn:
        _get_task_or_404(conn, task_id)
        rows = conn.execute(
            """SELECT ar.*, u.name AS operator_name
               FROM approval_records ar
               LEFT JOIN users u ON ar.operator_id = u.id
               WHERE ar.task_id = ?
               ORDER BY ar.operated_at ASC, ar.id ASC""",
            (task_id,),
        ).fetchall()
        return _rows_to_list(rows)


def save_filter(user_id: int, filter_name: str, filter_data: dict) -> dict:
    if not filter_name or not filter_name.strip():
        raise ValidationException("filter_name", "过滤条件名称不能为空")
    with get_db() as conn:
        _get_user_or_404(conn, user_id)
        filter_json = json.dumps(filter_data, ensure_ascii=False)
        cursor = conn.execute(
            """INSERT INTO saved_filters (user_id, filter_name, filter_json)
               VALUES (?, ?, ?)""",
            (user_id, filter_name.strip(), filter_json),
        )
        filter_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM saved_filters WHERE id = ?", (filter_id,)
        ).fetchone()
        result = _row_to_dict(row)
        result["filter_data"] = json.loads(result["filter_json"])
        del result["filter_json"]
        return result


def list_filters(user_id: int) -> list[dict]:
    with get_db() as conn:
        _get_user_or_404(conn, user_id)
        rows = conn.execute(
            "SELECT * FROM saved_filters WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        result = _rows_to_list(rows)
        for r in result:
            r["filter_data"] = json.loads(r["filter_json"])
            del r["filter_json"]
        return result


def get_filter(filter_id: int, user_id: int) -> dict:
    with get_db() as conn:
        _get_user_or_404(conn, user_id)
        row = conn.execute(
            "SELECT * FROM saved_filters WHERE id = ? AND user_id = ?",
            (filter_id, user_id),
        ).fetchone()
        if not row:
            raise FilterNotFoundException(filter_id)
        result = _row_to_dict(row)
        result["filter_data"] = json.loads(result["filter_json"])
        del result["filter_json"]
        return result


def delete_filter(filter_id: int, user_id: int) -> None:
    with get_db() as conn:
        _get_user_or_404(conn, user_id)
        row = conn.execute(
            "SELECT id FROM saved_filters WHERE id = ? AND user_id = ?",
            (filter_id, user_id),
        ).fetchone()
        if not row:
            raise FilterNotFoundException(filter_id)
        conn.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))


def _get_active_delegation(conn: sqlite3.Connection, delegator_id: int) -> Optional[dict]:
    now = datetime.now()
    row = conn.execute(
        """SELECT d.*,
                  u_delegator.name AS delegator_name,
                  u_delegatee.name AS delegatee_name
           FROM delegations d
           LEFT JOIN users u_delegator ON d.delegator_id = u_delegator.id
           LEFT JOIN users u_delegatee ON d.delegatee_id = u_delegatee.id
           WHERE d.delegator_id = ?
             AND d.status = ?
             AND d.start_time <= ?
             AND d.end_time >= ?
           ORDER BY d.created_at DESC
           LIMIT 1""",
        (delegator_id, DelegationStatus.ACTIVE, now, now),
    ).fetchone()
    return _row_to_dict(row)


def _apply_delegation_if_needed(
    conn: sqlite3.Connection,
    task_id: int,
    original_approver_id: int,
) -> int:
    delegation = _get_active_delegation(conn, original_approver_id)
    if delegation:
        delegatee_id = delegation["delegatee_id"]
        conn.execute(
            """INSERT INTO delegation_records
               (task_id, delegation_id, original_approver_id, delegatee_id)
               VALUES (?, ?, ?, ?)""",
            (task_id, delegation["id"], original_approver_id, delegatee_id),
        )
        conn.execute(
            "UPDATE tasks SET original_approver_id = ?, current_handler_id = ? WHERE id = ?",
            (original_approver_id, delegatee_id, task_id),
        )
        _add_approval_record(
            conn, task_id, original_approver_id, "delegate",
            None, None,
            f"审批委托生效：{delegation['delegator_name']} 委托给 {delegation['delegatee_name']}，有效期至 {delegation['end_time']}"
        )
        return delegatee_id
    return original_approver_id


def create_delegation(
    delegator_id: int,
    delegatee_id: int,
    start_time: datetime,
    end_time: datetime,
) -> dict:
    if delegator_id == delegatee_id:
        raise InvalidDelegationException("不能委托给自己")
    if start_time >= end_time:
        raise InvalidDelegationException("开始时间必须早于结束时间")

    with get_db() as conn:
        delegator = _get_user_or_404(conn, delegator_id)
        if delegator["role"] != UserRole.MANAGER:
            raise InvalidDelegationException("只有经理可以设置委托")
        _get_user_or_404(conn, delegatee_id)

        existing = conn.execute(
            """SELECT 1 FROM delegations
               WHERE delegator_id = ?
                 AND status = ?
                 AND start_time < ?
                 AND end_time > ?""",
            (delegator_id, DelegationStatus.ACTIVE, end_time, start_time),
        ).fetchone()
        if existing:
            raise InvalidDelegationException("该时间段内已有生效的委托")

        cursor = conn.execute(
            """INSERT INTO delegations
               (delegator_id, delegatee_id, start_time, end_time, status)
               VALUES (?, ?, ?, ?, ?)""",
            (delegator_id, delegatee_id, start_time, end_time, DelegationStatus.ACTIVE),
        )
        delegation_id = cursor.lastrowid

        row = conn.execute(
            """SELECT d.*,
                      u_delegator.name AS delegator_name,
                      u_delegatee.name AS delegatee_name
               FROM delegations d
               LEFT JOIN users u_delegator ON d.delegator_id = u_delegator.id
               LEFT JOIN users u_delegatee ON d.delegatee_id = u_delegatee.id
               WHERE d.id = ?""",
            (delegation_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_delegations(delegator_id: Optional[int] = None) -> list[dict]:
    with get_db() as conn:
        sql = """SELECT d.*,
                        u_delegator.name AS delegator_name,
                        u_delegatee.name AS delegatee_name
                 FROM delegations d
                 LEFT JOIN users u_delegator ON d.delegator_id = u_delegator.id
                 LEFT JOIN users u_delegatee ON d.delegatee_id = u_delegatee.id
                 WHERE 1=1"""
        params = []
        if delegator_id is not None:
            sql += " AND d.delegator_id = ?"
            params.append(delegator_id)
        sql += " ORDER BY d.created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return _rows_to_list(rows)


def get_delegation(delegation_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            """SELECT d.*,
                      u_delegator.name AS delegator_name,
                      u_delegatee.name AS delegatee_name
               FROM delegations d
               LEFT JOIN users u_delegator ON d.delegator_id = u_delegator.id
               LEFT JOIN users u_delegatee ON d.delegatee_id = u_delegatee.id
               WHERE d.id = ?""",
            (delegation_id,),
        ).fetchone()
        if not row:
            raise DelegationNotFoundException(delegation_id)
        return _row_to_dict(row)


def revoke_delegation(delegation_id: int, operator_id: int) -> dict:
    with get_db() as conn:
        delegation = get_delegation(delegation_id)
        if delegation["delegator_id"] != operator_id:
            raise PermissionDeniedException(
                operator_id, "revoke", delegation_id,
                "只有委托人可以撤销委托"
            )
        if delegation["status"] != DelegationStatus.ACTIVE:
            raise InvalidDelegationException("委托已失效，无法撤销")

        conn.execute(
            "UPDATE delegations SET status = ? WHERE id = ?",
            (DelegationStatus.REVOKED, delegation_id),
        )

        now = datetime.now()
        task_rows = conn.execute(
            """SELECT dr.task_id FROM delegation_records dr
               INNER JOIN tasks t ON dr.task_id = t.id
               WHERE dr.delegation_id = ?
                 AND dr.reverted_at IS NULL
                 AND t.status IN (?, ?)""",
            (delegation_id, TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVING),
        ).fetchall()
        task_ids_to_revert = [r["task_id"] for r in task_rows]
        affected_tasks = len(task_ids_to_revert)

        if task_ids_to_revert:
            placeholders = ",".join("?" * len(task_ids_to_revert))
            conn.execute(
                f"""UPDATE delegation_records
                   SET reverted_at = ?
                   WHERE delegation_id = ? AND task_id IN ({placeholders})""",
                (now, delegation_id, *task_ids_to_revert),
            )

            conn.execute(
                f"""UPDATE tasks
                   SET current_handler_id = original_approver_id,
                       original_approver_id = NULL,
                       updated_at = ?
                   WHERE id IN ({placeholders})""",
                (now, *task_ids_to_revert),
            )

        if affected_tasks > 0:
            placeholders = ",".join("?" * len(task_ids_to_revert))
            conn.execute(
                f"""UPDATE approval_records
                   SET action = 'revert_delegation',
                       comment = comment || '；委托已撤销，恢复原审批人'
                   WHERE task_id IN ({placeholders}) AND action = 'delegate'""",
                (*task_ids_to_revert,),
            )

        row = conn.execute(
            """SELECT d.*,
                      u_delegator.name AS delegator_name,
                      u_delegatee.name AS delegatee_name
               FROM delegations d
               LEFT JOIN users u_delegator ON d.delegator_id = u_delegator.id
               LEFT JOIN users u_delegatee ON d.delegatee_id = u_delegatee.id
               WHERE d.id = ?""",
            (delegation_id,),
        ).fetchone()
        result = _row_to_dict(row)
        result["reverted_tasks_count"] = affected_tasks
        return result


def get_delegation_records(task_id: Optional[int] = None) -> list[dict]:
    with get_db() as conn:
        sql = """SELECT dr.*,
                        u_original.name AS original_approver_name,
                        u_delegatee.name AS delegatee_name
                 FROM delegation_records dr
                 LEFT JOIN users u_original ON dr.original_approver_id = u_original.id
                 LEFT JOIN users u_delegatee ON dr.delegatee_id = u_delegatee.id
                 WHERE 1=1"""
        params = []
        if task_id is not None:
            sql += " AND dr.task_id = ?"
            params.append(task_id)
        sql += " ORDER BY dr.delegated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return _rows_to_list(rows)


def _calculate_deadline(priority: str, submit_time: datetime) -> datetime:
    hours = PRIORITY_DEADLINE_HOURS.get(Priority(priority), 24)
    return submit_time + timedelta(hours=hours)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return None


def _is_overdue(conn: sqlite3.Connection, task_id: int) -> bool:
    row = conn.execute(
        "SELECT deadline, status FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if not row or not row["deadline"]:
        return False
    if row["status"] not in {TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVING}:
        return False
    deadline = _parse_datetime(row["deadline"])
    if not deadline:
        return False
    return datetime.now() > deadline


def remind_task(task_id: int, operator_id: int, comment: str = "") -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        if task["status"] not in {TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVING}:
            raise InvalidStatusTransitionException(
                task_id, task["status"], "不可达", TaskAction.REMIND.value
            )
        _get_user_or_404(conn, operator_id)

        if not _is_overdue(conn, task_id):
            raise TaskNotOverdueException(task_id)

        conn.execute(
            "UPDATE tasks SET reminder_count = reminder_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now(), task_id),
        )

        conn.execute(
            """INSERT INTO reminder_records
               (task_id, operator_id, reminder_type, escalation_level, comment)
               VALUES (?, ?, ?, ?, ?)""",
            (task_id, operator_id, ReminderType.NORMAL, 0, comment or "超时催办"),
        )

        _add_approval_record(
            conn, task_id, operator_id, TaskAction.REMIND.value,
            task["status"], task["status"],
            comment or f"超时催办（第 {task['reminder_count'] + 1} 次）"
        )

        return _get_task_with_handler(conn, task_id)


def escalate_task(task_id: int, operator_id: int, comment: str = "") -> dict:
    with get_db() as conn:
        task = _get_task_or_404(conn, task_id)
        if task["status"] not in {TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVING}:
            raise InvalidStatusTransitionException(
                task_id, task["status"], "不可达", TaskAction.ESCALATE.value
            )
        if task["escalated"]:
            raise AlreadyEscalatedException(task_id)
        if not _is_overdue(conn, task_id):
            raise TaskNotOverdueException(task_id)

        _get_user_or_404(conn, operator_id)

        current_handler = task["current_handler_id"]
        if not current_handler:
            approver_row = conn.execute(
                "SELECT user_id FROM approvers WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            if not approver_row:
                raise ValidationException("task", "任务没有审批人，无法升级")
            current_handler = approver_row["user_id"]

        handler = conn.execute(
            "SELECT department FROM users WHERE id = ?",
            (current_handler,),
        ).fetchone()

        higher_manager = conn.execute(
            """SELECT id, name FROM users
               WHERE department = ? AND role = ? AND id != ?
               ORDER BY id LIMIT 1""",
            (handler["department"], UserRole.MANAGER, current_handler),
        ).fetchone()

        if not higher_manager:
            higher_manager = conn.execute(
                """SELECT id, name FROM users
                   WHERE role = ? AND id != ?
                   ORDER BY id LIMIT 1""",
                (UserRole.MANAGER, current_handler),
            ).fetchone()

        if not higher_manager:
            raise ValidationException("task", "找不到上级审批人，无法升级")

        escalation_level = task["reminder_count"] + 1

        conn.execute(
            """UPDATE tasks
               SET current_handler_id = ?,
                   escalated = 1,
                   reminder_count = reminder_count + 1,
                   updated_at = ?
               WHERE id = ?""",
            (higher_manager["id"], datetime.now(), task_id),
        )

        conn.execute(
            """INSERT INTO reminder_records
               (task_id, operator_id, reminder_type, escalation_level, comment)
               VALUES (?, ?, ?, ?, ?)""",
            (
                task_id, operator_id, ReminderType.ESCALATION,
                escalation_level,
                comment or f"任务超时升级，转由 {higher_manager['name']} 处理"
            ),
        )

        _add_approval_record(
            conn, task_id, operator_id, TaskAction.ESCALATE.value,
            task["status"], task["status"],
            f"超时升级：原处理人 -> {higher_manager['name']}（升级级别 {escalation_level}）"
        )

        return _get_task_with_handler(conn, task_id)


def get_reminder_records(task_id: Optional[int] = None) -> list[dict]:
    with get_db() as conn:
        sql = """SELECT rr.*,
                        u.name AS operator_name
                 FROM reminder_records rr
                 LEFT JOIN users u ON rr.operator_id = u.id
                 WHERE 1=1"""
        params = []
        if task_id is not None:
            sql += " AND rr.task_id = ?"
            params.append(task_id)
        sql += " ORDER BY rr.reminded_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return _rows_to_list(rows)


def batch_approve(
    operator_id: int,
    task_ids: list[int],
    action: str,
    comment: str = "",
) -> dict:
    if not task_ids:
        raise ValidationException("task_ids", "任务ID列表不能为空")
    if action not in {"approve", "reject"}:
        raise ValidationException("action", "操作必须是 approve 或 reject")

    success_ids = []
    failed_details = []

    with get_db() as conn:
        _get_user_or_404(conn, operator_id)

        tasks = conn.execute(
            """SELECT t.*,
                      u_creator.name AS creator_name,
                      u_handler.name AS current_handler_name
               FROM tasks t
               LEFT JOIN users u_creator ON t.creator_id = u_creator.id
               LEFT JOIN users u_handler ON t.current_handler_id = u_handler.id
               WHERE t.id IN ({})""".format(",".join("?" * len(task_ids))),
            task_ids,
        ).fetchall()
        task_map = {t["id"]: dict(t) for t in tasks}

        statuses = {task_map[tid]["status"] for tid in task_ids if tid in task_map}
        pending_statuses = {TaskStatus.PENDING_APPROVAL, TaskStatus.APPROVING}
        if not statuses.issubset(pending_statuses):
            non_pending = [
                tid for tid in task_ids
                if tid in task_map and task_map[tid]["status"] not in pending_statuses
            ]
            raise BatchApprovalException(
                f"存在非待审批状态的任务: {non_pending}",
                failed_tasks=non_pending
            )

        for task_id in task_ids:
            if task_id not in task_map:
                failed_details.append({
                    "task_id": task_id,
                    "error": "任务不存在",
                })
                continue

            task = task_map[task_id]

            try:
                if task["approval_mode"] == ApprovalMode.SINGLE:
                    if task["current_handler_id"] != operator_id:
                        raise PermissionDeniedException(
                            operator_id, action, task_id,
                            "单人审批模式下只有当前处理人可以操作"
                        )
                else:
                    if not _is_task_approver(conn, task_id, operator_id):
                        raise PermissionDeniedException(
                            operator_id, action, task_id,
                            "多人会签模式下只有审批人列表中的用户可以操作"
                        )

                if action == "approve":
                    approve_task(task_id, operator_id, comment)
                else:
                    reject_task(task_id, operator_id, comment)

                success_ids.append(task_id)
            except AppException as e:
                failed_details.append({
                    "task_id": task_id,
                    "error": e.detail,
                })
            except Exception as e:
                failed_details.append({
                    "task_id": task_id,
                    "error": str(e),
                })

    return {
        "success_count": len(success_ids),
        "failed_count": len(failed_details),
        "success_ids": success_ids,
        "failed_details": failed_details,
    }


def create_template(
    creator_id: int,
    template_name: str,
    department: str,
    priority: str,
    approval_mode: str,
    approver_ids: list[int],
) -> dict:
    if not template_name or not template_name.strip():
        raise ValidationException("template_name", "模板名称不能为空")
    _validate_department(department)
    _validate_priority(priority)
    _validate_approval_mode(approval_mode)

    if approval_mode == ApprovalMode.SINGLE and len(approver_ids) != 1:
        raise ValidationException(
            "approver_ids", "单人审批模式必须指定且仅指定 1 个审批人"
        )
    if approval_mode == ApprovalMode.COUNTERSIGN and len(approver_ids) < 2:
        raise ValidationException(
            "approver_ids", "多人会签模式必须指定至少 2 个审批人"
        )

    with get_db() as conn:
        _get_user_or_404(conn, creator_id)
        for aid in approver_ids:
            _get_user_or_404(conn, aid)

        approver_ids_json = json.dumps(approver_ids)
        cursor = conn.execute(
            """INSERT INTO approval_templates
               (creator_id, template_name, department, priority, approval_mode, approver_ids_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (creator_id, template_name.strip(), department, priority, approval_mode, approver_ids_json),
        )
        template_id = cursor.lastrowid

        row = conn.execute(
            """SELECT t.*, u.name AS creator_name
               FROM approval_templates t
               LEFT JOIN users u ON t.creator_id = u.id
               WHERE t.id = ?""",
            (template_id,),
        ).fetchone()
        result = _row_to_dict(row)
        result["approver_ids"] = json.loads(result["approver_ids_json"])
        del result["approver_ids_json"]
        return result


def list_templates(creator_id: Optional[int] = None) -> list[dict]:
    with get_db() as conn:
        sql = """SELECT t.*, u.name AS creator_name
                 FROM approval_templates t
                 LEFT JOIN users u ON t.creator_id = u.id
                 WHERE 1=1"""
        params = []
        if creator_id is not None:
            sql += " AND t.creator_id = ?"
            params.append(creator_id)
        sql += " ORDER BY t.created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        result = _rows_to_list(rows)
        for r in result:
            r["approver_ids"] = json.loads(r["approver_ids_json"])
            del r["approver_ids_json"]
        return result


def get_template(template_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            """SELECT t.*, u.name AS creator_name
               FROM approval_templates t
               LEFT JOIN users u ON t.creator_id = u.id
               WHERE t.id = ?""",
            (template_id,),
        ).fetchone()
        if not row:
            raise TemplateNotFoundException(template_id)
        result = _row_to_dict(row)
        result["approver_ids"] = json.loads(result["approver_ids_json"])
        del result["approver_ids_json"]
        return result


def delete_template(template_id: int, operator_id: int) -> None:
    with get_db() as conn:
        template = get_template(template_id)
        if template["creator_id"] != operator_id:
            raise PermissionDeniedException(
                operator_id, "delete_template", template_id,
                "只有模板创建人可以删除模板"
            )
        conn.execute("DELETE FROM approval_templates WHERE id = ?", (template_id,))


def create_task_from_template(
    creator_id: int,
    template_id: int,
    title: str,
    description: str = "",
) -> dict:
    template = get_template(template_id)
    return create_task(
        creator_id=creator_id,
        title=title,
        description=description,
        department=template["department"],
        priority=template["priority"],
        approval_mode=template["approval_mode"],
        approver_ids=template["approver_ids"],
    )


def _format_datetime_for_sql(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def export_audit_data(
    department: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> dict:
    with get_db() as conn:
        task_sql = """SELECT t.*,
                             u_creator.name AS creator_name,
                             u_handler.name AS current_handler_name
                      FROM tasks t
                      LEFT JOIN users u_creator ON t.creator_id = u_creator.id
                      LEFT JOIN users u_handler ON t.current_handler_id = u_handler.id
                      WHERE 1=1"""
        params = []
        if department:
            task_sql += " AND t.department = ?"
            params.append(department)
        if status:
            task_sql += " AND t.status = ?"
            params.append(status)
        if start_time:
            task_sql += " AND t.created_at >= ?"
            params.append(_format_datetime_for_sql(start_time))
        if end_time:
            task_sql += " AND t.created_at <= ?"
            params.append(_format_datetime_for_sql(end_time))
        task_sql += " ORDER BY t.created_at DESC"

        task_rows = conn.execute(task_sql, params).fetchall()
        tasks = _rows_to_list(task_rows)
        task_ids = [t["id"] for t in tasks]

        approval_records = []
        delegation_records = []
        reminder_records = []

        if task_ids:
            placeholders = ",".join("?" * len(task_ids))

            approval_rows = conn.execute(
                f"""SELECT ar.*, u.name AS operator_name
                    FROM approval_records ar
                    LEFT JOIN users u ON ar.operator_id = u.id
                    WHERE ar.task_id IN ({placeholders})
                    ORDER BY ar.operated_at ASC""",
                task_ids,
            ).fetchall()
            approval_records = _rows_to_list(approval_rows)

            delegation_rows = conn.execute(
                f"""SELECT dr.*,
                                  u_original.name AS original_approver_name,
                                  u_delegatee.name AS delegatee_name
                           FROM delegation_records dr
                           LEFT JOIN users u_original ON dr.original_approver_id = u_original.id
                           LEFT JOIN users u_delegatee ON dr.delegatee_id = u_delegatee.id
                           WHERE dr.task_id IN ({placeholders})
                           ORDER BY dr.delegated_at ASC""",
                task_ids,
            ).fetchall()
            delegation_records = _rows_to_list(delegation_rows)

            reminder_rows = conn.execute(
                f"""SELECT rr.*, u.name AS operator_name
                    FROM reminder_records rr
                    LEFT JOIN users u ON rr.operator_id = u.id
                    WHERE rr.task_id IN ({placeholders})
                    ORDER BY rr.reminded_at ASC""",
                task_ids,
            ).fetchall()
            reminder_records = _rows_to_list(reminder_rows)

        total_records = len(approval_records) + len(delegation_records) + len(reminder_records)

        return {
            "tasks": tasks,
            "approval_records": approval_records,
            "delegation_records": delegation_records,
            "reminder_records": reminder_records,
            "export_time": datetime.now(),
            "total_tasks": len(tasks),
            "total_records": total_records,
        }
