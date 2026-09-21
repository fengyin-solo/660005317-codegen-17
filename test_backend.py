import sys
# 优先使用环境中已安装的依赖；CI/容器无依赖时可通过 EXTRA_PYPATH 指向包目录
if "--pylibs" in sys.argv:
    sys.path.insert(0, "/tmp/pylibs")

from fastapi.testclient import TestClient
from backend.app.main import app

fails = []


def check(name, cond, info=""):
    print(("PASS" if cond else "FAIL"), "-", name, info)
    if not cond:
        fails.append(name)


with TestClient(app) as client:
    # 1. 未登录访问告警列表 -> 401 并说明原因
    r = client.get("/api/anomalies")
    check("未登录查看被拒绝(401)", r.status_code == 401 and "reason" in r.json()["detail"])

    # 2. 未登录直接保存处置 -> 401
    r = client.put("/api/anomalies/1/handling", json={"assignee": "bob", "status": "PENDING", "note": "x"})
    check("未登录保存被拒绝(401)", r.status_code == 401)

    # 3. 错误账号登录
    r = client.post("/api/auth/login", json={"username": "nobody"})
    check("未知账号登录被拒绝", r.status_code == 401)

    # 登录四个账号
    tokens = {}
    for u in ("alice", "bob", "carol", "dave"):
        r = client.post("/api/auth/login", json={"username": u})
        tokens[u] = r.json()["token"]
    H = {u: {"Authorization": f"Bearer {t}"} for u, t in tokens.items()}

    # dave 角色未配置权限
    r = client.get("/api/users", headers=H["alice"])
    dave = next(x for x in r.json()["users"] if x["username"] == "dave")
    check("dave 权限配置标记为缺失", dave["configured"] is False and dave["permissions"] == [])
    carol = next(x for x in r.json()["users"] if x["username"] == "carol")
    check("carol 只有只读权限", carol["permissions"] == ["anomaly:view"])

    # 4. 查看告警（任何登录账号都可以）
    r = client.get("/api/anomalies", headers=H["carol"])
    anoms = r.json()["anomalies"]
    seed = [a for a in anoms if a["id"] in (1, 2)]
    check("预置两条告警且初始处置人为空/PENDING",
          len(seed) == 2 and all(a["assignee"] is None and a["status"] == "PENDING" for a in seed))
    a1 = next(a for a in seed if a["id"] == 1)
    check("告警保留原有字段 triggers/device_type/timestamp",
          all(k in a1 for k in ("triggers", "device_type", "timestamp")) and a1["triggers"][0]["rule"] == "高温告警")

    # 5. bob 认领并处置 #1
    r = client.put("/api/anomalies/1/handling",
                   headers=H["bob"],
                   json={"assignee": "bob", "status": "IN_PROGRESS", "note": "已联系维修组，30分钟内到场"})
    check("处置人本人保存成功", r.status_code == 200, r.json().get("detail", ""))
    check("保存返回内容正确", r.json()["anomaly"]["note"] == "已联系维修组，30分钟内到场"
          and r.json()["anomaly"]["updated_by"] == "bob")

    # 6. carol(只读) 尝试修改 -> 403 并说明原因
    r = client.put("/api/anomalies/1/handling",
                   headers=H["carol"],
                   json={"assignee": "bob", "status": "RESOLVED", "note": "恶意修改"})
    check("只读账号越权改他人告警被拒(403)", r.status_code == 403)
    check("拒绝响应说明原因", "越权" in r.json()["detail"]["reason"] and "carol" in r.json()["detail"]["reason"])
    # 确认数据没被改动
    r = client.get("/api/anomalies", headers=H["carol"])
    a1now = next(a for a in r.json()["anomalies"] if a["id"] == 1)
    check("越权被拒后数据保持不变", a1now["note"] == "已联系维修组，30分钟内到场" and a1now["status"] == "IN_PROGRESS")

    # 6b. carol(只读) 即使提交空处置人等“不合格表单”，也应先按越权 403 拒绝
    r = client.put("/api/anomalies/1/handling",
                   headers=H["carol"],
                   json={"assignee": "", "status": "", "note": ""})
    check("只读账号不合格表单仍按越权拒绝(403)", r.status_code == 403 and "carol" in r.json()["detail"]["reason"])

    # 7. bob 想把尚未认领的 #2 直接指派给 alice -> 403（非管理员只能自行认领）
    r = client.put("/api/anomalies/2/handling",
                   headers=H["bob"],
                   json={"assignee": "alice", "status": "PENDING", "note": ""})
    check("非管理员指定他人为处置人被拒(403)", r.status_code == 403 and "认领" in r.json()["detail"]["reason"])

    # 8. 处置人为空 + 状态非法 -> 400 且列出不合格项
    r = client.put("/api/anomalies/2/handling",
                   headers=H["bob"],
                   json={"assignee": "  ", "status": "DONE", "note": ""})
    items = r.json()["detail"].get("invalid_items", [])
    check("不合格保存返回400", r.status_code == 400)
    check("指出处置人为空", any("处置人不能为空" in x for x in items))
    check("指出状态非法", any("处置状态" in x for x in items))

    # 9. 处置人指定为只读账号 carol -> 不合格项
    r = client.put("/api/anomalies/2/handling",
                   headers=H["alice"],
                   json={"assignee": "carol", "status": "PENDING", "note": ""})
    items = r.json()["detail"].get("invalid_items", [])
    check("只读账号不能被设为处置人", r.status_code == 400 and any("只读" in x for x in items))

    # 10. 不存在的处置人
    r = client.put("/api/anomalies/2/handling",
                   headers=H["alice"],
                   json={"assignee": "ghost", "status": "PENDING", "note": ""})
    check("不存在的处置人被拒绝", r.status_code == 400
          and any("不存在" in x for x in r.json()["detail"]["invalid_items"]))

    # 11. dave(权限配置缺失) 尝试认领 -> 400 不合格项中指出权限配置缺失
    r = client.put("/api/anomalies/2/handling",
                   headers=H["dave"],
                   json={"assignee": "dave", "status": "PENDING", "note": "我来处理"})
    check("权限配置缺失不允许保存(400)", r.status_code == 400)
    check("不合格项指出权限配置缺失",
          any("权限配置缺失" in x for x in r.json()["detail"]["invalid_items"]))

    # 12. 管理员 alice 可以把 #2 指派给 bob
    r = client.put("/api/anomalies/2/handling",
                   headers=H["alice"],
                   json={"assignee": "bob", "status": "PENDING", "note": "管理员代为派单"})
    check("管理员指派任意处置人成功", r.status_code == 200)

    # 13. bob 是 #2 的处置人但不能改派给 alice -> 403
    r = client.put("/api/anomalies/2/handling",
                   headers=H["bob"],
                   json={"assignee": "alice", "status": "PENDING", "note": "我搞不定转给alice"})
    check("处置人不能自行改派(403)", r.status_code == 403 and "改派" in r.json()["detail"]["reason"])

    # 14. 管理员可以改派，并可改说明/状态
    r = client.put("/api/anomalies/2/handling",
                   headers=H["alice"],
                   json={"assignee": "alice", "status": "RESOLVED", "note": "已更换冷却泵，告警恢复"})
    check("管理员改派并关闭告警成功", r.status_code == 200)

    # 15. 保存后回到列表，处置说明保持一致
    r = client.get("/api/anomalies", headers=H["bob"])
    by_id = {a["id"]: a for a in r.json()["anomalies"]}
    check("列表中#1说明一致", by_id[1]["note"] == "已联系维修组，30分钟内到场" and by_id[1]["assignee"] == "bob")
    check("列表中#2说明一致", by_id[2]["note"] == "已更换冷却泵，告警恢复" and by_id[2]["status"] == "RESOLVED")

    # 16. 不存在的告警
    r = client.put("/api/anomalies/99999/handling",
                   headers=H["alice"],
                   json={"assignee": "alice", "status": "RESOLVED", "note": "x"})
    check("不存在告警返回404", r.status_code == 404)

    # 17. 原有告警条数通道未被改坏：devices 接口仍返回切片，WS payload 结构不变
    r = client.get("/api/devices", headers=H["alice"])
    check("/api/devices 仍返回 anomalies 切片(<=10条)", isinstance(r.json()["anomalies"], list)
          and len(r.json()["anomalies"]) <= 10)
    check("设备条数仍为12台", len(r.json()["devices"]) == 12)

print()
if fails:
    print(f"{len(fails)} FAILURES:", fails)
    sys.exit(1)
print("ALL BACKEND CHECKS PASSED")
