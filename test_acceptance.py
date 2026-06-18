import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DB_FILE = Path(__file__).parent / "approval_system.db"
if DB_FILE.exists():
    DB_FILE.unlink()

from app.database import init_db
from app.exceptions import (
    AppException,
    DuplicateOperationException,
    InvalidStatusTransitionException,
    PermissionDeniedException,
    TaskNotFoundException,
)
from app.state_machine import ApprovalMode, Priority, TaskStatus
from app.services import (
    approve_task,
    archive_task,
    create_task,
    get_task,
    get_task_history,
    list_tasks,
    list_users,
    reject_task,
    resubmit_task,
    save_filter,
    list_filters,
    get_filter,
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
frank_id = 6

print()
print("=" * 70)
print("【测试 1：单人审批完整流转（创建 → 提交 → 通过 → 归档）】")
print("=" * 70)

task1 = create_task(
    creator_id=alice_id,
    title="采购办公用品",
    description="采购 10 台笔记本电脑",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
check("创建任务成功，状态为 draft", task1["status"] == TaskStatus.DRAFT)
check("单人审批模式指定当前处理人为 frank", task1["current_handler_id"] == frank_id)
check("任务创建人是 alice", task1["creator_id"] == alice_id)
task1_id = task1["id"]

task1 = submit_task(task1_id, alice_id)
check("提交审批成功，状态变为 pending_approval", task1["status"] == TaskStatus.PENDING_APPROVAL)

expect_exception("非当前处理人 bob 不能审批", PermissionDeniedException,
                 approve_task, task1_id, bob_id, "同意")

task1 = approve_task(task1_id, frank_id, "审批通过，同意采购")
check("frank 审批通过，状态变为 approved", task1["status"] == TaskStatus.APPROVED)
check("通过后当前处理人清空", task1["current_handler_id"] is None)

expect_exception("approved 状态下不能再次审批", InvalidStatusTransitionException,
                 approve_task, task1_id, frank_id)

expect_exception("非创建人不能归档", PermissionDeniedException,
                 archive_task, task1_id, frank_id)

task1 = archive_task(task1_id, alice_id)
check("创建人 alice 归档成功，状态变为 archived", task1["status"] == TaskStatus.ARCHIVED)

history1 = get_task_history(task1_id)
check("审批历史至少 4 条（创建/提交/通过/归档）", len(history1) >= 4)
print("  历史记录:")
for h in history1:
    print(f"    [{h['operated_at']}] {h['operator_name']} - {h['action']}: "
          f"{h['from_status']} → {h['to_status']} ({h['comment']})")

print()
print("=" * 70)
print("【测试 2：驳回后重提，保留历史】")
print("=" * 70)

task2 = create_task(
    creator_id=charlie_id,
    title="需求评审方案",
    description="新版 App 需求文档",
    department="产品部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[david_id],
)
task2_id = task2["id"]

task2 = submit_task(task2_id, charlie_id)
check("提交审批成功", task2["status"] == TaskStatus.PENDING_APPROVAL)

task2 = reject_task(task2_id, david_id, "需求描述不够详细，请补充流程图")
check("驳回成功，状态变为 rejected", task2["status"] == TaskStatus.REJECTED)
check("驳回后当前处理人回到创建人 charlie", task2["current_handler_id"] == charlie_id)

task2 = resubmit_task(
    task2_id, charlie_id,
    title="需求评审方案（V2）",
    description="新版 App 需求文档，已补充详细流程图",
)
check("重提成功，状态回到 pending_approval", task2["status"] == TaskStatus.PENDING_APPROVAL)
check("重提后标题已更新", task2["title"] == "需求评审方案（V2）")
check("重提后当前处理人回到 david", task2["current_handler_id"] == david_id)

history2 = get_task_history(task2_id)
actions = [h["action"] for h in history2]
check("历史记录包含完整链条：create/submit/reject/resubmit",
      "create" in actions and "submit" in actions and "reject" in actions and "resubmit" in actions)
print(f"  历史动作序列: {actions}")
for h in history2:
    print(f"    [{h['operated_at']}] {h['operator_name']} - {h['action']}: "
          f"{h['from_status']} → {h['to_status']}")

print()
print("=" * 70)
print("【测试 3：多人会签模式（全票通过才通过，一票否决即驳回）】")
print("=" * 70)

task3 = create_task(
    creator_id=alice_id,
    title="年度预算申请",
    description="2026 年度技术部预算",
    department="技术部",
    priority=Priority.HIGH,
    approval_mode=ApprovalMode.COUNTERSIGN,
    approver_ids=[david_id, frank_id],
)
task3_id = task3["id"]
check("多人会签任务创建，初始无 current_handler", task3["current_handler_id"] is None)

task3 = submit_task(task3_id, alice_id)
check("提交审批成功", task3["status"] == TaskStatus.PENDING_APPROVAL)

task3 = approve_task(task3_id, david_id, "产品部同意")
check("david 投票通过后，状态变为 approving（等待其他审批人）",
      task3["status"] == TaskStatus.APPROVING)

expect_exception("david 重复投票应报错", DuplicateOperationException,
                 approve_task, task3_id, david_id)

task3 = approve_task(task3_id, frank_id, "技术部经理同意，全票通过")
check("frank 投票通过后，状态变为 approved（全票通过）",
      task3["status"] == TaskStatus.APPROVED)

task3_detail = get_task(task3_id)
approvers_voted = [a["vote_result"] for a in task3_detail["approvers"] if a["has_voted"]]
check("两个审批人都投了 approve", approvers_voted == ["approve", "approve"])

print()
print("--- 会签测试二：一票否决 ---")
task4 = create_task(
    creator_id=charlie_id,
    title="差旅报销申请",
    description="出差报销 5000 元",
    department="产品部",
    priority=Priority.LOW,
    approval_mode=ApprovalMode.COUNTERSIGN,
    approver_ids=[david_id, frank_id],
)
task4_id = task4["id"]
submit_task(task4_id, charlie_id)

task4 = reject_task(task4_id, david_id, "金额超标准，驳回")
check("david 一票否决，状态直接变为 rejected", task4["status"] == TaskStatus.REJECTED)

print()
print("=" * 70)
print("【测试 4：非法状态变更和越权操作】")
print("=" * 70)

task5 = create_task(
    creator_id=alice_id,
    title="测试异常任务",
    description="异常场景验证",
    department="技术部",
    priority=Priority.MEDIUM,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)
task5_id = task5["id"]

expect_exception("草稿状态直接审批报错", InvalidStatusTransitionException,
                 approve_task, task5_id, frank_id)
expect_exception("草稿状态直接归档报错", InvalidStatusTransitionException,
                 archive_task, task5_id, alice_id)
expect_exception("bob 不是创建人不能提交", PermissionDeniedException,
                 submit_task, task5_id, bob_id)

submit_task(task5_id, alice_id)
expect_exception("charlie 不是审批人不能审批", PermissionDeniedException,
                 approve_task, task5_id, charlie_id)

expect_exception("查询不存在的任务报错", TaskNotFoundException,
                 get_task, 99999)

create_task(
    creator_id=bob_id,
    title="仅创建不提交的任务",
    description="保持草稿状态用于过滤测试",
    department="技术部",
    priority=Priority.LOW,
    approval_mode=ApprovalMode.SINGLE,
    approver_ids=[frank_id],
)

print()
print("=" * 70)
print("【测试 5：过滤条件保存与查询】")
print("=" * 70)

filter_data = {
    "department": "技术部",
    "status": TaskStatus.PENDING_APPROVAL,
    "priority": Priority.HIGH,
    "keyword": "采购",
}
saved = save_filter(alice_id, "技术部高优先级待办", filter_data)
check("保存过滤条件成功", saved["filter_name"] == "技术部高优先级待办")
check("过滤内容正确还原", saved["filter_data"] == filter_data)

filters = list_filters(alice_id)
check("列出 alice 的过滤条件数量为 1", len(filters) == 1)

fetched = get_filter(saved["id"], alice_id)
check("取回过滤条件内容一致", fetched["filter_data"] == filter_data)

print()
print("=" * 70)
print("【测试 6：任务列表过滤 + 当前处理人/最近操作显示】")
print("=" * 70)

all_tasks = list_tasks()
print(f"  系统总任务数: {len(all_tasks)}")

tech_tasks = list_tasks(department="技术部")
check("按部门过滤：技术部任务数量正确", len(tech_tasks) >= 3)

draft_tasks = list_tasks(status=TaskStatus.DRAFT)
check("按状态过滤：draft 状态任务数量正确", len(draft_tasks) >= 1)

print("  任务列表摘要（含当前处理人+最近操作）:")
for t in list_tasks():
    handler = t.get("current_handler_name") or "无"
    last_op = t.get("last_operation")
    op_text = f"{last_op['operator_name']} {last_op['action']}" if last_op else "无"
    print(f"    任务#{t['id']} [{t['status']}] {t['title'][:20]} | "
          f"当前处理人: {handler} | 最近操作: {op_text}")

sample_with_handler = next((t for t in all_tasks if t.get("current_handler_name")), None)
check("列表中包含显示当前处理人的任务", sample_with_handler is not None)
sample_with_lastop = next((t for t in all_tasks if t.get("last_operation")), None)
check("列表中包含显示最近操作的任务", sample_with_lastop is not None)

print()
print("=" * 70)
print("【测试 7：模拟重启后数据一致性验证】")
print("=" * 70)

print("  记录重启前关键数据快照...")
snapshot = {
    "task1_status": get_task(task1_id)["status"],
    "task1_history_count": len(get_task_history(task1_id)),
    "task2_status": get_task(task2_id)["status"],
    "task2_title": get_task(task2_id)["title"],
    "task3_status": get_task(task3_id)["status"],
    "task4_status": get_task(task4_id)["status"],
    "filters_count": len(list_filters(alice_id)),
    "total_tasks": len(list_tasks()),
}
print(f"  快照: {json.dumps(snapshot, ensure_ascii=False, indent=2)}")

print("  模拟系统重启（重新初始化连接）...")
import importlib
import app.database as db_mod
importlib.reload(db_mod)
from app.database import init_db as init_db2
init_db2()

print("  重启后重新查询验证...")
check("task1 状态一致（归档）", get_task(task1_id)["status"] == snapshot["task1_status"])
check("task1 历史记录数一致", len(get_task_history(task1_id)) == snapshot["task1_history_count"])
check("task2 驳回重提状态一致", get_task(task2_id)["status"] == snapshot["task2_status"])
check("task2 重提标题一致", get_task(task2_id)["title"] == snapshot["task2_title"])
check("task3 会签通过状态一致", get_task(task3_id)["status"] == snapshot["task3_status"])
check("task4 会签驳回状态一致", get_task(task4_id)["status"] == snapshot["task4_status"])
check("过滤条件数一致", len(list_filters(alice_id)) == snapshot["filters_count"])
check("总任务数一致", len(list_tasks()) == snapshot["total_tasks"])

print()
print("=" * 70)
print("【验收总结】")
print("=" * 70)
passed = sum(1 for _, s in results if s == PASS)
total = len(results)
print(f"共 {total} 项验收：{passed} 通过，{total - passed} 失败")
for name, status in results:
    print(f"  {status}  {name}")

if passed == total:
    print("\n🎉 全部验收通过！系统满足所有需求。")
else:
    print("\n⚠️  存在未通过项，请检查。")
    sys.exit(1)
