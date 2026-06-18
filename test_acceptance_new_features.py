import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DB_FILE = Path(__file__).parent / "approval_system.db"
if DB_FILE.exists():
    DB_FILE.unlink()

from app.database import init_db
from app.exceptions import (
    AppException,
    BatchApprovalException,
    InvalidDelegationException,
    PermissionDeniedException,
    TaskNotOverdueException,
    ValidationException,
)
from app.state_machine import ApprovalMode, Priority, TaskStatus, UserRole
from app.services import (
    approve_task,
    batch_approve,
    create_delegation,
    create_task,
    create_task_from_template,
    create_template,
    delete_template,
    escalate_task,
    export_audit_data,
    get_delegation_records,
    get_reminder_records,
    get_task,
    get_task_history,
    list_delegations,
    list_tasks,
    list_templates,
    list_users,
    remind_task,
    reject_task,
    resubmit_task,
    revoke_delegation,
    submit_task,
)

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []


def check(name, condition):
    status = PASS if condition else FAIL
    results.append((name, status))
    print(f"{status} - {name}")
    return condition


def expect_exception(name, exc_type, func, *args, **kwargs):
    try:
        func(*args, **kwargs)
        results.append((name, FAIL))
        print(f"{FAIL} - {name} (未抛出期望的 {exc_type.__name__})")
        return False
    except exc_type as e:
        results.append((name, PASS))
        print(f"{PASS} - {name} (错误: {e.detail})")
        return True
    except Exception as e:
        results.append((name, FAIL))
        print(f"{FAIL} - {name} (抛出了错误的异常: {type(e).__name__}: {e})")
        return False


print("=" * 70)
print("【初始化数据库和种子用户】")
print("=" * 70)
init_db()
users = list_users()
check("初始化 6 个种子用户", len(users) == 6)
for u in users:
    print(f"  用户 id={u['id']}: {u['username']}({u['name']}) - {u['department']} - {u['role']}")

alice_id = 1
bob_id = 2
charlie_id = 3
david_id = 4
eve_id = 5
frank_id = 6

print()
print("=" * 70)
print("【测试 1：审批委托功能 - 委托生效与回收】")
print("=" * 70)

print()
print("--- 1.1 创建委托 ---")
now = datetime.now()
start_time = now - timedelta(hours=1)
end_time = now + timedelta(hours=24)

expect_exception(
    "普通用户 alice 不能创建委托",
    InvalidDelegationException,
    create_delegation, alice_id, bob_id, start_time, end_time
)

expect_exception(
    "不能委托给自己",
    InvalidDelegationException,
    create_delegation, frank_id, frank_id, start_time, end_time
)

expect_exception(
    "开始时间不能晚于结束时间",
    InvalidDelegationException,
    create_delegation, frank_id, bob_id, end_time, start_time
)

delegation = create_delegation(
    delegator_id=frank_id,
    delegatee_id=bob_id,
    start_time=start_time,
    end_time=end_time,
)
check("经理 frank 创建委托成功，状态为 active", delegation["status"] == "active")
check("委托人是 frank", delegation["delegator_id"] == frank_id)
check("代理人是 bob", delegation["delegatee_id"] == bob_id)
delegation_id = delegation["id"]

delegations = list_delegations(delegator_id=frank_id)
check("列出 frank 的委托，数量为 1", len(delegations) == 1)

print()
print("--- 1.2 委托期间新任务自动分配给代理人 ---")
task_delegate = create_task(
    creator_id=alice_id,
    title="委托测试任务",
    description="验证委托期间任务自动分配给代理人",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
check("任务创建时当前处理人为原审批人 frank", task_delegate["current_handler_id"] == frank_id)
task_delegate_id = task_delegate["id"]

task_delegate = submit_task(task_delegate_id, alice_id)
check("提交后当前处理人变为代理人 bob", task_delegate["current_handler_id"] == bob_id)
check("任务状态为 pending_approval", task_delegate["status"] == TaskStatus.PENDING_APPROVAL)
check("任务有 deadline 字段", task_delegate.get("deadline") is not None)

delegation_records = get_delegation_records(task_id=task_delegate_id)
check("委托记录存在，记录了委托关系", len(delegation_records) == 1)
check("委托记录中原始审批人为 frank", delegation_records[0]["original_approver_id"] == frank_id)
check("委托记录中代理人为 bob", delegation_records[0]["delegatee_id"] == bob_id)

history = get_task_history(task_delegate_id)
delegate_actions = [h for h in history if h["action"] == "delegate"]
check("审批历史包含 delegate 记录", len(delegate_actions) > 0)

expect_exception(
    "原审批人 frank 不能审批（任务已委托给 bob）",
    PermissionDeniedException,
    approve_task, task_delegate_id, frank_id, "越权审批"
)

task_delegate = approve_task(task_delegate_id, bob_id, "代理人 bob 审批通过")
check("代理人 bob 可以正常审批", task_delegate["status"] == TaskStatus.APPROVED)

print()
print("--- 1.3 撤销委托，恢复原处理人 ---")
task_for_revoke = create_task(
    creator_id=alice_id,
    title="待撤销委托测试任务",
    description="验证撤销委托后恢复原处理人",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task_for_revoke_id = task_for_revoke["id"]
task_for_revoke = submit_task(task_for_revoke_id, alice_id)
check("提交后当前处理人为 bob（委托生效）", task_for_revoke["current_handler_id"] == bob_id)

task_for_revoke2 = create_task(
    creator_id=alice_id,
    title="待撤销委托测试任务2",
    description="验证撤销委托后恢复原处理人2",
    department="技术部",
    priority=Priority.LOW,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task_for_revoke2_id = task_for_revoke2["id"]
task_for_revoke2 = submit_task(task_for_revoke2_id, alice_id)
check("第二个任务也分配给 bob", task_for_revoke2["current_handler_id"] == bob_id)

expect_exception(
    "非委托人不能撤销委托",
    PermissionDeniedException,
    revoke_delegation, delegation_id, alice_id
)

revoke_result = revoke_delegation(delegation_id, frank_id)
check("撤销委托成功，状态变为 revoked", revoke_result["status"] == "revoked")
check("撤销影响了 2 个任务", revoke_result["reverted_tasks_count"] == 2)

task_for_revoke = get_task(task_for_revoke_id)
check("撤销后任务1处理人恢复为 frank", task_for_revoke["current_handler_id"] == frank_id)
check("撤销后任务1 original_approver_id 为 None", task_for_revoke.get("original_approver_id") is None)

task_for_revoke2 = get_task(task_for_revoke2_id)
check("撤销后任务2处理人恢复为 frank", task_for_revoke2["current_handler_id"] == frank_id)

delegation_records_after = get_delegation_records(task_id=task_for_revoke_id)
check("委托记录中 reverted_at 已填写", delegation_records_after[0]["reverted_at"] is not None)

task_for_revoke = approve_task(task_for_revoke_id, frank_id, "恢复后原审批人审批")
check("原审批人 frank 现在可以审批了", task_for_revoke["status"] == TaskStatus.APPROVED)

print()
print("--- 1.4 委托过期后新任务不分配 ---")
past_start = now - timedelta(hours=48)
past_end = now - timedelta(hours=24)
delegation2 = create_delegation(
    delegator_id=david_id,
    delegatee_id=charlie_id,
    start_time=past_start,
    end_time=past_end,
)
check("创建过期委托成功", delegation2["status"] == "active")

task_past = create_task(
    creator_id=charlie_id,
    title="过期委托测试",
    description="委托过期后不应分配",
    department="产品部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[david_id],
)
task_past_id = task_past["id"]
task_past = submit_task(task_past_id, charlie_id)
check("过期委托不生效，处理人仍为 david", task_past["current_handler_id"] == david_id)

print()
print("=" * 70)
print("【测试 2：超时催办与升级机制】")
print("=" * 70)

print()
print("--- 2.1 截止时间计算 ---")
task_high = create_task(
    creator_id=alice_id,
    title="高优先级任务",
    description="测试高优先级截止时间 8 小时",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task_high_id = task_high["id"]
submit_before = datetime.now()
task_high = submit_task(task_high_id, alice_id)
submit_after = datetime.now()

deadline = datetime.fromisoformat(task_high["deadline"]) if isinstance(task_high["deadline"], str) else task_high["deadline"]
expected_min = submit_before + timedelta(hours=8)
expected_max = submit_after + timedelta(hours=8)
check("高优先级截止时间为提交后 8 小时", expected_min <= deadline <= expected_max)

task_medium = create_task(
    creator_id=alice_id,
    title="中优先级任务",
    description="测试中优先级截止时间 24 小时",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task_medium_id = task_medium["id"]
task_medium = submit_task(task_medium_id, alice_id)
deadline_medium = datetime.fromisoformat(task_medium["deadline"]) if isinstance(task_medium["deadline"], str) else task_medium["deadline"]
check("中优先级截止时间为提交后 24 小时", abs((deadline_medium - datetime.now()).total_seconds() - 24 * 3600) < 60)

print()
print("--- 2.2 未超时不能催办 ---")
expect_exception(
    "未超时任务不能催办",
    TaskNotOverdueException,
    remind_task, task_high_id, alice_id, "催办一下"
)

print()
print("--- 2.3 模拟超时后催办（直接修改数据库）---")
task_overdue = create_task(
    creator_id=alice_id,
    title="超时催办测试任务",
    description="测试超时催办功能",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task_overdue_id = task_overdue["id"]
task_overdue = submit_task(task_overdue_id, alice_id)

from app.database import get_db
with get_db() as conn:
    past_deadline = datetime.now() - timedelta(hours=1)
    conn.execute(
        "UPDATE tasks SET deadline = ? WHERE id = ?",
        (past_deadline, task_overdue_id)
    )

task_overdue = get_task(task_overdue_id)
check("任务已模拟超时", task_overdue["reminder_count"] == 0)

task_overdue = remind_task(task_overdue_id, alice_id, "请尽快审批")
check("催办成功，催办次数 +1", task_overdue["reminder_count"] == 1)

reminder_records = get_reminder_records(task_id=task_overdue_id)
check("催办记录存在，类型为 normal", len(reminder_records) == 1)
check("催办记录类型正确", reminder_records[0]["reminder_type"] == "normal")
check("催办记录 escalation_level 为 0", reminder_records[0]["escalation_level"] == 0)

history = get_task_history(task_overdue_id)
remind_actions = [h for h in history if h["action"] == "remind"]
check("审批历史包含 remind 记录", len(remind_actions) == 1)

task_overdue = remind_task(task_overdue_id, alice_id, "第二次催办")
check("第二次催办成功，催办次数为 2", task_overdue["reminder_count"] == 2)

print()
print("--- 2.4 超时升级到上级审批人 ---")
task_escalate = create_task(
    creator_id=alice_id,
    title="升级测试任务",
    description="测试超时升级功能",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[bob_id],
)
task_escalate_id = task_escalate["id"]
task_escalate = submit_task(task_escalate_id, alice_id)
check("提交后处理人为 bob", task_escalate["current_handler_id"] == bob_id)

with get_db() as conn:
    past_deadline = datetime.now() - timedelta(hours=1)
    conn.execute(
        "UPDATE tasks SET deadline = ? WHERE id = ?",
        (past_deadline, task_escalate_id)
    )

expect_exception(
    "未超时任务不能升级",
    TaskNotOverdueException,
    escalate_task, task_high_id, alice_id
)

task_escalate = escalate_task(task_escalate_id, alice_id, "超时未处理，升级")
check("升级成功，escalated 标记为 1", task_escalate["escalated"] == 1)
check("升级后处理人变为经理 frank（技术部经理）", task_escalate["current_handler_id"] == frank_id)
check("催办次数增加", task_escalate["reminder_count"] >= 1)

reminder_records = get_reminder_records(task_id=task_escalate_id)
escalation_record = [r for r in reminder_records if r["reminder_type"] == "escalation"]
check("催办记录包含 escalation 类型", len(escalation_record) == 1)
check("升级级别大于 0", escalation_record[0]["escalation_level"] > 0)

history = get_task_history(task_escalate_id)
escalate_actions = [h for h in history if h["action"] == "escalate"]
check("审批历史包含 escalate 记录", len(escalate_actions) == 1)

task_escalate = approve_task(task_escalate_id, frank_id, "上级经理审批通过")
check("升级后上级经理可以审批", task_escalate["status"] == TaskStatus.APPROVED)

print()
print("--- 2.5 重提后重置催办和升级状态 ---")
task_reset = create_task(
    creator_id=alice_id,
    title="重提重置测试",
    description="重提后应重置催办和升级状态",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task_reset_id = task_reset["id"]
task_reset = submit_task(task_reset_id, alice_id)

with get_db() as conn:
    past_deadline = datetime.now() - timedelta(hours=1)
    conn.execute(
        "UPDATE tasks SET deadline = ?, reminder_count = 3, escalated = 1 WHERE id = ?",
        (past_deadline, task_reset_id)
    )

task_reset = reject_task(task_reset_id, frank_id, "驳回测试")
check("驳回成功", task_reset["status"] == TaskStatus.REJECTED)

task_reset = resubmit_task(task_reset_id, alice_id, description="重提后内容")
check("重提成功", task_reset["status"] == TaskStatus.PENDING_APPROVAL)
check("重提后催办次数重置为 0", task_reset["reminder_count"] == 0)
check("重提后升级状态重置为 0", task_reset["escalated"] == 0)
check("重提后 deadline 已更新", task_reset["deadline"] is not None)

print()
print("=" * 70)
print("【测试 3：批量审批 - 只处理合法目标】")
print("=" * 70)

print()
print("--- 3.1 创建多个待审批任务 ---")
batch_tasks = []
for i in range(5):
    t = create_task(
        creator_id=alice_id,
        title=f"批量审批测试任务 {i+1}",
        description=f"批量审批测试 {i+1}",
        department="技术部",
        priority=Priority.MEDIUM,
        approval_mode=ApprovalMode.SINGLE,
        approver_ids=[frank_id],
    )
    t = submit_task(t["id"], alice_id)
    batch_tasks.append(t)
batch_task_ids = [t["id"] for t in batch_tasks]
check("创建 5 个待审批任务成功", len(batch_task_ids) == 5)

print()
print("--- 3.2 批量审批全部通过 ---")
result = batch_approve(
    operator_id=frank_id,
    task_ids=batch_task_ids,
    action="approve",
    comment="批量审批通过"
)
check("批量审批成功，成功 5 个", result["success_count"] == 5)
check("失败 0 个", result["failed_count"] == 0)
check("成功 ID 列表正确", sorted(result["success_ids"]) == sorted(batch_task_ids))

for tid in batch_task_ids:
    t = get_task(tid)
    check(f"任务 {tid} 状态变为 approved", t["status"] == TaskStatus.APPROVED)

print()
print("--- 3.3 混合状态拦截（包含已处理任务）---")
mixed_tasks = []
t1 = create_task(
    creator_id=alice_id,
    title="混合测试-待审批",
    description="混合状态测试-待审批",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
t1 = submit_task(t1["id"], alice_id)
mixed_tasks.append(t1["id"])

t2 = create_task(
    creator_id=alice_id,
    title="混合测试-已通过",
    description="混合状态测试-已通过",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
t2 = submit_task(t2["id"], alice_id)
t2 = approve_task(t2["id"], frank_id)
mixed_tasks.append(t2["id"])

expect_exception(
    "混合状态（包含已处理）应被拦截",
    BatchApprovalException,
    batch_approve, frank_id, mixed_tasks, "approve", "批量"
)

print()
print("--- 3.4 无权限拦截 ---")
no_perm_tasks = []
t3 = create_task(
    creator_id=alice_id,
    title="无权限测试",
    description="无权限批量审批测试",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
t3 = submit_task(t3["id"], alice_id)
no_perm_tasks.append(t3["id"])

result = batch_approve(
    operator_id=bob_id,
    task_ids=no_perm_tasks,
    action="approve",
    comment="越权批量审批"
)
check("无权限任务失败数为 1", result["failed_count"] == 1)
check("失败详情包含权限错误", "无权限" in result["failed_details"][0]["error"])

print()
print("--- 3.5 批量驳回 ---")
reject_tasks = []
for i in range(3):
    t = create_task(
        creator_id=alice_id,
        title=f"批量驳回测试 {i+1}",
        description=f"批量驳回测试 {i+1}",
        department="技术部",
        priority=Priority.LOW,
        approval_mode=ApprovalMode.SINGLE,
        approver_ids=[frank_id],
    )
    t = submit_task(t["id"], alice_id)
    reject_tasks.append(t["id"])

result = batch_approve(
    operator_id=frank_id,
    task_ids=reject_tasks,
    action="reject",
    comment="批量驳回"
)
check("批量驳回成功 3 个", result["success_count"] == 3)
for tid in reject_tasks:
    t = get_task(tid)
    check(f"任务 {tid} 状态变为 rejected", t["status"] == TaskStatus.REJECTED)

print()
print("--- 3.6 不存在的任务 ---")
invalid_ids = [99999, 99998]
result = batch_approve(
    operator_id=frank_id,
    task_ids=invalid_ids,
    action="approve",
    comment="测试不存在任务"
)
check("不存在的任务都失败", result["failed_count"] == 2)

print()
print("=" * 70)
print("【测试 4：审批模板 - 保存与复用】")
print("=" * 70)

print()
print("--- 4.1 创建模板 ---")
expect_exception(
    "模板名称不能为空",
    ValidationException,
    create_template,
    alice_id, "", "技术部", Priority.HIGH, ApprovalMode.SINGLE, [frank_id]
)

template1 = create_template(
    creator_id=alice_id,
    template_name="技术部常规审批",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
check("创建单人审批模板成功", template1["template_name"] == "技术部常规审批")
check("模板审批人为 frank", template1["approver_ids"] == [frank_id])
template1_id = template1["id"]

template2 = create_template(
    creator_id=alice_id,
    template_name="重大事项会签",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.COUNTERSIGN,
    approver_ids=[david_id, frank_id],
)
check("创建多人会签模板成功", template2["approval_mode"] == ApprovalMode.COUNTERSIGN)
check("会签模板审批人数为 2", len(template2["approver_ids"]) == 2)
template2_id = template2["id"]

templates = list_templates(creator_id=alice_id)
check("列出 alice 的模板，数量为 2", len(templates) == 2)

print()
print("--- 4.2 套用模板创建任务 ---")
task_from_template = create_task_from_template(
    creator_id=alice_id,
    template_id=template1_id,
    title="从模板创建的任务",
    description="使用技术部常规审批模板",
)
check("从模板创建任务成功", task_from_template["title"] == "从模板创建的任务")
check("任务部门与模板一致", task_from_template["department"] == "技术部")
check("任务优先级与模板一致", task_from_template["priority"] == Priority.MEDIUM)
check("任务审批模式与模板一致", task_from_template["approval_mode"] == ApprovalMode.SINGLE)
check("任务处理人与模板一致", task_from_template["current_handler_id"] == frank_id)

task_detail = get_task(task_from_template["id"])
check("任务审批人列表与模板一致", [a["user_id"] for a in task_detail["approvers"]] == [frank_id])

task_from_countersign = create_task_from_template(
    creator_id=charlie_id,
    template_id=template2_id,
    title="从会签模板创建的任务",
    description="使用重大事项会签模板",
)
check("从会签模板创建任务成功", task_from_countersign["approval_mode"] == ApprovalMode.COUNTERSIGN)
check("会签任务初始无处理人", task_from_countersign["current_handler_id"] is None)
task_countersign_detail = get_task(task_from_countersign["id"])
check("会签审批人列表与模板一致", sorted([a["user_id"] for a in task_countersign_detail["approvers"]]) == sorted([david_id, frank_id]))

print()
print("--- 4.3 模板持久化验证（模拟重启）---")
print("  记录模板快照...")
template_snapshot = {
    "template1": get_task(task_from_template["id"])["approval_mode"],
    "template2": get_task(task_from_countersign["id"])["approval_mode"],
    "templates_count": len(list_templates(alice_id)),
    "template1_name": list_templates(alice_id)[0]["template_name"],
}
print(f"  模板快照: {json.dumps(template_snapshot, ensure_ascii=False, indent=2)}")

print("  模拟系统重启（重新初始化连接）...")
import importlib
import app.database as db_mod
importlib.reload(db_mod)
from app.database import init_db as init_db2
init_db2()

print("  重启后验证模板...")
templates_after = list_templates(creator_id=alice_id)
check("重启后模板数量一致", len(templates_after) == template_snapshot["templates_count"])
check("重启后模板名称一致", templates_after[0]["template_name"] == template_snapshot["template1_name"])

template1_after = list_templates(alice_id)[0] if len(list_templates(alice_id)) > 0 else None
check("重启后模板审批人列表一致", template1_after["approver_ids"] == [frank_id])

task_after_reboot = create_task_from_template(
    creator_id=alice_id,
    template_id=template1_id,
    title="重启后套用模板",
    description="验证模板重启后可用",
)
check("重启后套用模板成功", task_after_reboot["approval_mode"] == ApprovalMode.SINGLE)
check("重启后任务处理人正确", task_after_reboot["current_handler_id"] == frank_id)

print()
print("--- 4.4 删除模板 ---")
expect_exception(
    "非创建人不能删除模板",
    PermissionDeniedException,
    delete_template, template1_id, bob_id
)

delete_template(template1_id, alice_id)
check("删除模板成功", True)

templates_after_delete = list_templates(creator_id=alice_id)
check("删除后模板数量为 1", len(templates_after_delete) == 1)

print()
print("=" * 70)
print("【测试 5：审计导出 - 多维度导出与一致性】")
print("=" * 70)

print()
print("--- 5.1 导出全部数据 ---")
export_all = export_audit_data()
check("导出成功，包含任务数据", export_all["total_tasks"] > 0)
check("导出包含审批历史", len(export_all["approval_records"]) > 0)
check("导出包含委托记录", len(export_all["delegation_records"]) > 0)
check("导出包含催办记录", len(export_all["reminder_records"]) > 0)
check("导出时间已填写", export_all["export_time"] is not None)
print(f"  导出统计: {export_all['total_tasks']} 个任务, {export_all['total_records']} 条记录")

print()
print("--- 5.2 按部门过滤导出 ---")
export_tech = export_audit_data(department="技术部")
all_tech = all(t["department"] == "技术部" for t in export_tech["tasks"])
check("按技术部过滤，所有任务都是技术部的", all_tech)
print(f"  技术部任务数: {export_tech['total_tasks']}")

export_product = export_audit_data(department="产品部")
all_product = all(t["department"] == "产品部" for t in export_product["tasks"])
check("按产品部过滤，所有任务都是产品部的", all_product)
print(f"  产品部任务数: {export_product['total_tasks']}")

print()
print("--- 5.3 按状态过滤导出 ---")
export_approved = export_audit_data(status=TaskStatus.APPROVED)
all_approved = all(t["status"] == TaskStatus.APPROVED for t in export_approved["tasks"])
check("按已通过过滤，所有任务都是已通过的", all_approved)
print(f"  已通过任务数: {export_approved['total_tasks']}")

export_pending = export_audit_data(status=TaskStatus.PENDING_APPROVAL)
all_pending = all(t["status"] == TaskStatus.PENDING_APPROVAL for t in export_pending["tasks"])
check("按待审批过滤，所有任务都是待审批的", all_pending)
print(f"  待审批任务数: {export_pending['total_tasks']}")

print()
print("--- 5.4 按时间范围过滤导出 ---")
time_now = datetime.now()
time_past = time_now - timedelta(days=1)
time_future = time_now + timedelta(days=1)

export_time_range = export_audit_data(
    start_time=time_past,
    end_time=time_future
)
check("时间范围过滤有结果", export_time_range["total_tasks"] > 0)

no_result_start = time_now + timedelta(days=365)
no_result_end = time_now + timedelta(days=366)
export_no_result = export_audit_data(
    start_time=no_result_start,
    end_time=no_result_end
)
check("未来时间范围无结果", export_no_result["total_tasks"] == 0)

print()
print("--- 5.5 多条件组合过滤 ---")
export_combo = export_audit_data(
    department="技术部",
    status=TaskStatus.APPROVED,
)
all_combo = all(
    t["department"] == "技术部" and t["status"] == TaskStatus.APPROVED
    for t in export_combo["tasks"]
)
check("组合过滤（技术部+已通过）正确", all_combo)
print(f"  技术部已通过任务数: {export_combo['total_tasks']}")

print()
print("--- 5.6 导出数据与实际查询一致性验证 ---")
query_result = list_tasks(department="技术部", status=TaskStatus.APPROVED)
export_result = export_audit_data(department="技术部", status=TaskStatus.APPROVED)

query_ids = sorted([t["id"] for t in query_result])
export_ids = sorted([t["id"] for t in export_result["tasks"]])
check("导出任务ID与直接查询一致", query_ids == export_ids)

sample_task_id = query_ids[0] if query_ids else None
if sample_task_id:
    query_task = next(t for t in query_result if t["id"] == sample_task_id)
    export_task = next(t for t in export_result["tasks"] if t["id"] == sample_task_id)
    check("导出任务状态与查询一致", query_task["status"] == export_task["status"])
    check("导出任务优先级与查询一致", query_task["priority"] == export_task["priority"])

    query_history = get_task_history(sample_task_id)
    export_history = [r for r in export_result["approval_records"] if r["task_id"] == sample_task_id]
    check("导出审批历史数量与查询一致", len(query_history) == len(export_history))

    query_delegation = get_delegation_records(task_id=sample_task_id)
    export_delegation = [r for r in export_result["delegation_records"] if r["task_id"] == sample_task_id]
    check("导出委托记录数量与查询一致", len(query_delegation) == len(export_delegation))

    query_reminder = get_reminder_records(task_id=sample_task_id)
    export_reminder = [r for r in export_result["reminder_records"] if r["task_id"] == sample_task_id]
    check("导出催办记录数量与查询一致", len(query_reminder) == len(export_reminder))

print()
print("=" * 70)
print("【验收总结 - 新增功能】")
print("=" * 70)
passed = sum(1 for _, s in results if s == PASS)
total = len(results)
print(f"共 {total} 项验收：{passed} 通过，{total - passed} 失败")
for name, status in results:
    print(f"  {status}  {name}")

if passed == total:
    print("\n🎉 全部新增功能验收通过！")
else:
    print("\n⚠️  存在未通过项，请检查。")
    sys.exit(1)
