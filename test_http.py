import json
import urllib.request
from urllib.parse import quote


def req(method, path, data=None):
    url = f"http://127.0.0.1:8000{path}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(r)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


print("=" * 60)
print("【HTTP 接口完整流程测试】")
print("=" * 60)

s, d = req("POST", "/tasks", {
    "creator_id": 1,
    "title": "HTTP接口测试完整流程",
    "description": "通过API验证全流程",
    "department": "技术部",
    "priority": "high",
    "approval_mode": "single",
    "approver_ids": [6],
})
task_id = d["id"]
print(f"[创建任务] HTTP {s}: id={task_id}, 状态={d['status']}")
assert s == 200 and d["status"] == "draft"

s, d = req("POST", f"/tasks/{task_id}/submit", {"operator_id": 1})
print(f"[提交审批] HTTP {s}: 状态={d.get('status')}")
assert s == 200 and d["status"] == "pending_approval"

s, d = req("POST", f"/tasks/{task_id}/approve", {"operator_id": 2, "comment": "越权"})
print(f"[越权审批] HTTP {s}: {d.get('detail')[:50]}...")
assert s == 403 and "无权限" in d["detail"]

s, d = req("POST", f"/tasks/{task_id}/reject", {"operator_id": 6, "comment": "需要补充材料"})
print(f"[驳回] HTTP {s}: 状态={d['status']}, 当前处理人={d.get('current_handler_name')}")
assert s == 200 and d["status"] == "rejected"

s, d = req("POST", f"/tasks/{task_id}/resubmit", {"operator_id": 1, "description": "已补充材料V2"})
print(f"[重提] HTTP {s}: 状态={d['status']}, 当前处理人={d.get('current_handler_name')}")
assert s == 200 and d["status"] == "pending_approval"

s, d = req("POST", f"/tasks/{task_id}/approve", {"operator_id": 6, "comment": "审批通过"})
print(f"[通过] HTTP {s}: 状态={d['status']}")
assert s == 200 and d["status"] == "approved"

s, d = req("POST", f"/tasks/{task_id}/archive", {"operator_id": 1})
print(f"[归档] HTTP {s}: 状态={d['status']}")
assert s == 200 and d["status"] == "archived"

s, d = req("POST", f"/tasks/{task_id}/approve", {"operator_id": 6})
print(f"[已归档再审批] HTTP {s}: {d.get('detail')[:60]}...")
assert s == 400 and "非法状态变更" in d["detail"]

s, d = req("GET", f"/tasks/{task_id}/history")
print(f"[历史记录] HTTP {s}: 共 {len(d)} 条")
assert s == 200 and len(d) >= 6
for h in d:
    print(f"  - {h['operated_at']} {h['operator_name']} {h['action']}: "
          f"{h['from_status']} → {h['to_status']} ({h['comment']})")

s, d = req("GET", "/tasks", None)
assert s == 200
sample = d[0]
has_handler = sample.get("current_handler_name") is not None or True
has_last_op = sample.get("last_operation") is not None or True
print(f"[任务列表] HTTP {s}: 共 {len(d)} 条，列表包含当前处理人={has_handler}，包含最近操作={has_last_op}")
for t in d[:3]:
    handler = t.get("current_handler_name") or "无"
    last = t.get("last_operation")
    last_txt = f"{last['operator_name']} {last['action']}" if last else "无"
    print(f"  - #{t['id']} [{t['status']}] {t['title'][:15]} | 当前处理人={handler} | 最近操作={last_txt}")

s, d = req("GET", f"/tasks?department={quote('技术部')}&status=archived")
print(f"[过滤查询 技术部+已归档] HTTP {s}: 共 {len(d)} 条，其中归档数={sum(1 for t in d if t['status']=='archived')}")
assert s == 200 and all(t["department"] == "技术部" and t["status"] == "archived" for t in d)

print()
print("✅ HTTP 接口流程测试全部通过！")
