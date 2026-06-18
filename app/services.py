import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from .database import get_db
from .exceptions import (
    DuplicateOperationException,
    FilterNotFoundException,
    InvalidStatusTransitionException,
    PermissionDeniedException,
    TaskNotFoundException,
    UserNotFoundException,
    ValidationException,
)
from .state_machine import (
    ApprovalMode,
    Priority,
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
        _update_task_status(conn, task_id, next_status.value)
        _add_approval_record(
            conn, task_id, operator_id, action.value,
            current_status.value, next_status.value, "提交审批"
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

        if task["approval_mode"] == ApprovalMode.SINGLE:
            approver_row = conn.execute(
                "SELECT user_id FROM approvers WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            handler_id = approver_row["user_id"] if approver_row else None
        else:
            handler_id = None
            conn.execute(
                "UPDATE approvers SET has_voted = 0, vote_result = NULL, voted_at = NULL WHERE task_id = ?",
                (task_id,),
            )

        _update_task_status(conn, task_id, next_status.value, current_handler_id=handler_id)
        _add_approval_record(
            conn, task_id, operator_id, action.value,
            current_status.value, next_status.value, "驳回后重提，历史保留"
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
        rows = conn.execute(sql, params).fetchall()
        tasks = _rows_to_list(rows)
        for t in tasks:
            t["last_operation"] = _get_last_operation(conn, t["id"])
        return tasks


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
