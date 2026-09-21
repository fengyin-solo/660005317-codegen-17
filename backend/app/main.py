import asyncio, math, random, time, json, threading
from collections import defaultdict, deque
from itertools import count
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np

app = FastAPI(title="Digital Twin Factory Monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DEVICE_TYPES = ["CNC", "RobotArm", "Conveyor", "AGV", "InjectionMolding", "QCStation"]
STATUSES = ["RUNNING", "IDLE", "FAULT", "OFFLINE"]
ACTIVE_CLIENTS: list[WebSocket] = []
SIMULATOR_RUNNING = True

# ---------------------------------------------------------------------------
# 账号与告警处置权限
# ---------------------------------------------------------------------------
# can_handle 取值:
#   "any"      -> 管理员: 可指派处置人, 可处置任意告警
#   "assigned" -> 处置工程师: 仅可处置指派给自己的告警
#   "none"     -> 只读访客: 只能查看
# 角色若不在该配置中(或缺 can_handle 键), 视为"权限配置缺失", 不允许保存
ROLE_PERMISSIONS = {
    "admin":    {"label": "管理员",     "can_handle": "any"},
    "engineer": {"label": "处置工程师", "can_handle": "assigned"},
    "viewer":   {"label": "只读访客",   "can_handle": "none"},
}

USERS = {
    "admin": {"id": "admin", "name": "管理员",   "role": "admin"},
    "zhang": {"id": "zhang", "name": "张伟",     "role": "engineer"},
    "li":    {"id": "li",    "name": "李娜",     "role": "engineer"},
    "wang":  {"id": "wang",  "name": "王强",     "role": "viewer"},
    # auditor 角色未在 ROLE_PERMISSIONS 中配置, 用于演示"权限配置缺失"分支
    "zhao":  {"id": "zhao",  "name": "赵敏",     "role": "auditor"},
}

ALERT_STATUSES = ["PENDING", "PROCESSING", "RESOLVED"]
ANOMALY_LOCK = threading.Lock()
_anomaly_id_seq = count(1)
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

class DeviceState:
    def __init__(self, did: int, dtype: str, x: float, y: float, z: float):
        self.id = did
        self.type = dtype
        self.status = "RUNNING"
        self.position = [x, y, z]
        self.temperature = random.uniform(35, 45)
        self.vibration = random.uniform(0.1, 1.5)
        self.pressure = random.uniform(0.8, 1.2)
        self.production_count = 0
        self.fault_count = 0
        self.uptime = 0.0
        self.cycle_time = random.uniform(2, 8)
        self.quality_rate = random.uniform(0.95, 0.995)

    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "status": self.status,
            "position": self.position, "temperature": round(self.temperature, 2),
            "vibration": round(self.vibration, 3), "pressure": round(self.pressure, 2),
            "production_count": self.production_count, "fault_count": self.fault_count,
            "uptime": round(self.uptime, 2), "quality_rate": round(self.quality_rate, 3)
        }

devices = {i: DeviceState(i, random.choice(DEVICE_TYPES),
                          random.uniform(-5, 5), 0.5, random.uniform(-5, 5)) for i in range(1, 13)}

production_log = []
anomaly_log = []  # 告警主记录, 每条带稳定 id 及处置信息(处置人/状态/说明)


def make_anomaly_entry(triggers, device_type: str, timestamp: float,
                       assignee: Optional[str] = None,
                       status: str = "PENDING", note: str = ""):
    """构造一条带责任归属与处置信息的告警记录。"""
    return {
        "id": next(_anomaly_id_seq),
        "timestamp": timestamp,
        "triggers": triggers,
        "device_type": device_type,
        # 责任归属与处置信息
        "assignee": assignee,
        "assignee_name": USERS.get(assignee, {}).get("name") if assignee else None,
        "status": status,
        "note": note,
        "updated_by": None,
        "updated_at": None,
    }


def serialize_anomaly(a: dict):
    """返回给前端的告警视图, 实时补齐处置人姓名。"""
    assignee = a.get("assignee")
    out = dict(a)
    out["assignee_name"] = USERS.get(assignee, {}).get("name") if assignee else None
    return out


class AnomalyRules:
    def __init__(self):
        self.rules = [
            {"name": "高温告警", "field": "temperature", "threshold": 48, "op": "gt"},
            {"name": "振动超标", "field": "vibration", "threshold": 2.0, "op": "gt"},
            {"name": "压力异常", "field": "pressure", "threshold": 1.5, "op": "gt"},
        ]
        self.windows = defaultdict(lambda: deque(maxlen=10))

    def check(self, dev: DeviceState):
        triggers = []
        for rule in self.rules:
            val = getattr(dev, rule["field"])
            if (rule["op"] == "gt" and val > rule["threshold"]) or (rule["op"] == "lt" and val < rule["threshold"]):
                triggers.append({"device_id": dev.id, "rule": rule["name"],
                                 "value": round(val, 3), "threshold": rule["threshold"]})

        # sliding window trend
        key = f"{dev.id}_temp"
        self.windows[key].append(dev.temperature)
        if len(self.windows[key]) >= 8:
            vals = list(self.windows[key])
            if np.mean(vals[-4:]) - np.mean(vals[:4]) > 3:
                triggers.append({"device_id": dev.id, "rule": "温度趋势上升", "value": round(np.mean(vals[-4:]), 2), "threshold": ">3°C/周期"})

        if triggers:
            with ANOMALY_LOCK:
                anomaly_log.append(make_anomaly_entry(triggers, dev.type, time.time()))
        return triggers

rules_engine = AnomalyRules()

def simulate():
    while SIMULATOR_RUNNING:
        for dev in devices.values():
            drift = 0.1 * math.sin(time.time() * 0.5 + dev.id)
            noise = random.gauss(0, 0.3)
            dev.temperature = max(25, min(65, dev.temperature + drift + noise))

            v_drift = 0.02 * math.sin(time.time() * 0.3 + dev.id * 0.7)
            dev.vibration = max(0, min(3, dev.vibration + v_drift + random.gauss(0, 0.05)))

            dev.pressure = max(0.5, min(2, dev.pressure + random.gauss(0, 0.02)))

            if random.random() < 0.015:
                dev.status = "FAULT"
                dev.fault_count += 1
            elif random.random() < 0.03 and dev.status == "FAULT":
                dev.status = "RUNNING"

            if dev.status == "RUNNING":
                if random.random() < 0.4:
                    dev.production_count += 1
                dev.uptime += 1

            triggers = rules_engine.check(dev)
            if triggers and dev.status != "FAULT" and random.random() < 0.3:
                dev.status = "FAULT"

        production_log.append({"timestamp": time.time(), "count": sum(d.production_count for d in devices.values())})

        try:
            payload = {
                "devices": [d.to_dict() for d in devices.values()],
                "production": sum(d.production_count for d in devices.values()),
                "anomalies": [serialize_anomaly(a) for a in anomaly_log[-5:]] if anomaly_log else [],
                "oee": calculate_oee()
            }
            msg = json.dumps(payload)
        except:
            continue

        dead = []
        for ws in ACTIVE_CLIENTS:
            try:
                if MAIN_LOOP is not None:
                    asyncio.run_coroutine_threadsafe(ws.send_text(msg), MAIN_LOOP)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in ACTIVE_CLIENTS:
                ACTIVE_CLIENTS.remove(ws)

        time.sleep(1)


def calculate_oee():
    oee_list = []
    for dev in devices.values():
        if dev.uptime == 0:
            continue
        availability = min(1.0, dev.uptime / max(1, dev.uptime + dev.fault_count))
        performance = min(1.0, dev.production_count / max(1, dev.uptime / 2))
        quality = dev.quality_rate
        oee = round(availability * performance * quality * 100, 1)
        oee_list.append({"id": dev.id, "type": dev.type, "oee": oee,
                         "availability": round(availability * 100, 1),
                         "performance": round(performance * 100, 1),
                         "quality": round(quality * 100, 1)})
    return oee_list


class OEEAnalysis(BaseModel):
    availability: float
    performance: float
    quality: float


class AnomalyHandleRequest(BaseModel):
    note: Optional[str] = None
    status: Optional[str] = None
    assignee: Optional[str] = None  # 仅管理员可修改(指派处置人)


def current_user(x_user_id: Optional[str] = Header(default=None)):
    """从请求头 X-User-Id 解析当前登录账号。"""
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=401, detail={"error": "未识别到登录账号", "reasons": ["请求缺少 X-User-Id 头, 无法确定当前账号"]})
    uid = x_user_id.strip()
    user = USERS.get(uid)
    if not user:
        raise HTTPException(status_code=401, detail={"error": "账号不存在", "reasons": [f"账号 '{uid}' 不存在"]})
    return user


def find_anomaly(anomaly_id: int):
    for a in anomaly_log:
        if a["id"] == anomaly_id:
            return a
    return None


def recent_anomalies(limit: int):
    with ANOMALY_LOCK:
        return [serialize_anomaly(a) for a in anomaly_log[-limit:]]


@app.on_event("startup")
async def startup():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    # 预置几条带责任归属的演示告警, 便于直接看到处置状态(不影响模拟器持续产生新告警)
    demo = [
        ([{"device_id": 3, "rule": "高温告警", "value": 51.2, "threshold": 48}],
         "InjectionMolding", "zhang", "PROCESSING", "已安排停机检查冷却水路, 预计 14:00 完成。"),
        ([{"device_id": 7, "rule": "振动超标", "value": 2.4, "threshold": 2.0}],
         "RobotArm", "li", "PENDING", ""),
        ([{"device_id": 1, "rule": "压力异常", "value": 1.6, "threshold": 1.5}],
         "CNC", "zhang", "RESOLVED", "更换液压阀后压力恢复正常, 复测合格。"),
    ]
    base = time.time() - 30
    for idx, (triggers, dtype, assignee, status, note) in enumerate(demo):
        a = make_anomaly_entry(triggers, dtype, base + idx * 10,
                               assignee=assignee, status=status, note=note)
        a["updated_by"] = assignee if status != "PENDING" else None
        a["updated_at"] = base + idx * 10 + 5
        anomaly_log.append(a)

    t = threading.Thread(target=simulate, daemon=True)
    t.start()


@app.get("/api/auth/users")
def list_users(user: dict = Depends(current_user)):
    """返回可切换的账号列表及其权限配置(用于前端账号切换)。"""
    return {"users": list(USERS.values()), "role_permissions": ROLE_PERMISSIONS,
            "current": user["id"]}


@app.get("/api/auth/me")
def get_me(user: dict = Depends(current_user)):
    perm = ROLE_PERMISSIONS.get(user["role"])
    return {"user": user, "permission": perm}


@app.get("/api/devices")
def get_devices():
    return {"devices": [d.to_dict() for d in devices.values()], "anomalies": recent_anomalies(10)}


@app.get("/api/anomalies")
def list_anomalies(limit: int = 20):
    """告警列表(含处置人/状态/说明), 任何登录账号均可只读查看。"""
    return {"anomalies": recent_anomalies(max(1, min(limit, 100)))}


@app.put("/api/anomalies/{anomaly_id}/handle")
def handle_anomaly(anomaly_id: int, req: AnomalyHandleRequest, user: dict = Depends(current_user)):
    """
    保存告警处置结果(处置人指派 / 处置状态 / 处置说明)。

    权限规则:
      - 管理员(can_handle=any):      可处置任意告警, 可指派/变更处置人
      - 处置工程师(can_handle=assigned): 仅处置指派给自己的告警, 不得改处置人
      - 只读访客(can_handle=none):   一律拒绝, 仅可查看
      - 角色未配置权限: 拒绝保存并指出"权限配置缺失"
    校验规则:
      - 处置人必须存在且为有效账号
      - 状态必须在允许范围内
      - 处置人为空时不允许保存, 返回全部不合格项
    """
    uid = user["id"]
    role = user["role"]
    perm = ROLE_PERMISSIONS.get(role)

    # 1) 权限配置缺失: 不允许保存
    if not perm or "can_handle" not in perm:
        raise HTTPException(
            status_code=400,
            detail={"error": "权限配置缺失",
                    "reasons": [f"账号 '{user['name']}' 的角色 '{role}' 未配置告警处置权限(can_handle 缺失), 请联系管理员补充角色权限配置后再保存"]})

    can_handle = perm["can_handle"]

    # 2) 只读账号 / 无处置权限: 越权请求直接拒绝并说明原因
    if can_handle == "none":
        raise HTTPException(
            status_code=403,
            detail={"error": "无权修改告警处置信息",
                    "reasons": [f"账号 '{user['name']}' 的角色为'{perm.get('label', role)}', 只有只读权限, 只能查看告警, 不能修改处置说明与状态"]})

    with ANOMALY_LOCK:
        anomaly = find_anomaly(anomaly_id)
        if anomaly is None:
            raise HTTPException(status_code=404,
                                detail={"error": "告警不存在", "reasons": [f"未找到 id={anomaly_id} 的告警"]})

        is_admin = can_handle == "any"
        assignee_changed = req.assignee is not None and req.assignee != anomaly.get("assignee")

        # 3) 非管理员不得指派/变更处置人
        if assignee_changed and not is_admin:
            raise HTTPException(
                status_code=403,
                detail={"error": "无权指派处置人",
                        "reasons": [f"账号 '{user['name']}' 不是管理员, 不能指派或变更处置人(仅管理员可操作); 该告警当前处置人为 '{anomaly.get('assignee_name') or '未指派'}'"]})

        # 4) 非管理员只能处置指派给自己的告警
        if not is_admin and anomaly.get("assignee") != uid:
            owner_name = anomaly.get("assignee_name") or "尚未指派"
            raise HTTPException(
                status_code=403,
                detail={"error": "只能处置本人负责的告警",
                        "reasons": [f"该告警的处置人是 '{owner_name}', 当前账号 '{user['name']}' 不是处置人也不是管理员, 仅可只读查看"]})

        # 5) 字段校验: 汇总所有不合格项, 一次性返回
        invalid = []
        new_assignee = req.assignee if req.assignee is not None else anomaly.get("assignee")
        if not new_assignee or not str(new_assignee).strip():
            invalid.append("处置人为空: 每条告警必须有明确的处置人, 请先指派处置人后再保存")
        elif new_assignee not in USERS:
            invalid.append(f"处置人账号 '{new_assignee}' 不存在, 请选择有效账号")

        new_status = req.status if req.status is not None else anomaly.get("status")
        if req.status is not None and new_status not in ALERT_STATUSES:
            invalid.append(f"处置状态 '{req.status}' 不合法, 允许值为 {ALERT_STATUSES}")

        new_note = req.note if req.note is not None else anomaly.get("note", "")

        if invalid:
            raise HTTPException(status_code=400, detail={"error": "存在不合格项, 未允许保存", "reasons": invalid})

        anomaly["assignee"] = new_assignee
        anomaly["status"] = new_status
        anomaly["note"] = new_note
        anomaly["updated_by"] = uid
        anomaly["updated_at"] = time.time()

    return {"ok": True, "anomaly": serialize_anomaly(anomaly)}


@app.get("/api/oee")
def get_oee():
    return {"oee": calculate_oee()}


@app.get("/api/production")
def get_production():
    return {"log": production_log[-60:]}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    ACTIVE_CLIENTS.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in ACTIVE_CLIENTS:
            ACTIVE_CLIENTS.remove(websocket)


@app.on_event("shutdown")
async def shutdown():
    global SIMULATOR_RUNNING
    SIMULATOR_RUNNING = False