import asyncio, math, random, time, json, threading, secrets
from collections import defaultdict, deque
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import numpy as np

app = FastAPI(title="Digital Twin Factory Monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DEVICE_TYPES = ["CNC", "RobotArm", "Conveyor", "AGV", "InjectionMolding", "QCStation"]
STATUSES = ["RUNNING", "IDLE", "FAULT", "OFFLINE"]
ACTIVE_CLIENTS: list[WebSocket] = []
SIMULATOR_RUNNING = True
# 主事件循环：模拟线程不在事件循环线程内，run_coroutine_threadsafe 需要显式拿到它
EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None

# ---------------------------------------------------------------------------
# 账号与权限配置
# ---------------------------------------------------------------------------
# 演示账号（无密码演示环境）:
#   alice  管理员      —— 可处置/改派任意告警
#   bob    运维工程师  —— 可认领/处置自己负责的告警
#   carol  访客        —— 只读，只能查看
#   dave   外包人员    —— 角色 contractor 故意未配置权限，保存时按“权限配置缺失”拒绝
USERS = {
    "alice": {"display_name": "Alice · 管理员", "role": "admin"},
    "bob":   {"display_name": "Bob · 运维工程师", "role": "engineer"},
    "carol": {"display_name": "Carol · 访客", "role": "viewer"},
    "dave":  {"display_name": "Dave · 外包人员", "role": "contractor"},
}

# 角色 -> 权限点。未出现在此处的角色视为“权限配置缺失”。
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin":    {"anomaly:view", "anomaly:handle", "anomaly:assign_any"},
    "engineer": {"anomaly:view", "anomaly:handle"},
    "viewer":   {"anomaly:view"},
    # contractor 刻意留空：用于验证“权限配置缺失时不允许保存”
}

ANOMALY_STATUSES = ["PENDING", "IN_PROGRESS", "RESOLVED"]

# 内存态登录令牌: token -> username
TOKENS: dict[str, str] = {}

# 告警自增主键与读写锁（模拟线程 + 接口线程都会访问告警数据）
_anomaly_lock = threading.Lock()
_next_anomaly_id = 1


def role_permissions(role: str) -> Optional[set[str]]:
    """返回角色权限集合；角色未配置时返回 None。"""
    return ROLE_PERMISSIONS.get(role)


def user_public(username: str) -> dict:
    u = USERS[username]
    perms = role_permissions(u["role"])
    return {
        "username": username,
        "display_name": u["display_name"],
        "role": u["role"],
        "permissions": sorted(perms) if perms is not None else [],
        "configured": perms is not None,
    }


def error_response(status: int, reason: str, invalid_items: Optional[list[str]] = None):
    detail = {"reason": reason}
    if invalid_items is not None:
        detail["invalid_items"] = invalid_items
    return JSONResponse(status_code=status, content={"detail": detail})


def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"reason": "未登录或缺少令牌，请先登录后再操作"})
    token = authorization.removeprefix("Bearer ").strip()
    username = TOKENS.get(token)
    if not username or username not in USERS:
        raise HTTPException(status_code=401, detail={"reason": "登录已失效，请重新登录"})
    return {"username": username, **USERS[username]}


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
anomaly_log = []


def make_anomaly(triggers: list, device_type: str) -> dict:
    """构造一条带责任归属字段的告警。初始处置人为空、状态为待处理。"""
    global _next_anomaly_id
    with _anomaly_lock:
        aid = _next_anomaly_id
        _next_anomaly_id += 1
    return {
        "id": aid,
        "timestamp": time.time(),
        "triggers": triggers,
        "device_type": device_type,
        "assignee": None,
        "note": "",
        "status": "PENDING",
        "updated_by": None,
        "updated_at": None,
    }


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
            anomaly_log.append(make_anomaly(triggers, dev.type))
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
                "anomalies": anomaly_log[-5:] if anomaly_log else [],
                "oee": calculate_oee()
            }
            msg = json.dumps(payload)
        except:
            continue

        dead = []
        for ws in ACTIVE_CLIENTS:
            try:
                if EVENT_LOOP is None:
                    continue
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), EVENT_LOOP)
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


class LoginRequest(BaseModel):
    username: Optional[str] = None


class HandlingUpdate(BaseModel):
    # 全部给默认值，便于把“缺失/为空”统一收敛为不合格项，而不是框架层 422
    assignee: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None


@app.on_event("startup")
async def startup():
    global EVENT_LOOP
    EVENT_LOOP = asyncio.get_running_loop()
    # 预置两条告警，保证列表初始就有待认领数据（字段结构与运行期告警一致）
    anomaly_log.append(make_anomaly(
        [{"device_id": 3, "rule": "高温告警", "value": 52.4, "threshold": 48}], "CNC"))
    anomaly_log.append(make_anomaly(
        [{"device_id": 7, "rule": "振动超标", "value": 2.6, "threshold": 2.0}], "RobotArm"))
    t = threading.Thread(target=simulate, daemon=True)
    t.start()


@app.get("/api/devices")
def get_devices():
    return {"devices": [d.to_dict() for d in devices.values()], "anomalies": anomaly_log[-10:]}


@app.get("/api/oee")
def get_oee():
    return {"oee": calculate_oee()}


@app.get("/api/production")
def get_production():
    return {"log": production_log[-60:]}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    username = (req.username or "").strip()
    if not username or username not in USERS:
        return error_response(401, f"登录失败：账号 '{username or '(空)'}' 不存在")
    token = secrets.token_hex(16)
    TOKENS[token] = username
    return {"token": token, "user": user_public(username)}


@app.post("/api/auth/logout")
def logout(user: dict = Depends(current_user)):
    for tok, name in list(TOKENS.items()):
        if name == user["username"]:
            TOKENS.pop(tok, None)
    return {"ok": True}


@app.get("/api/users")
def list_users(user: dict = Depends(current_user)):
    return {"users": [user_public(name) for name in USERS]}


@app.get("/api/anomalies")
def list_anomalies(user: dict = Depends(current_user)):
    # 任何登录账号都可以查看；处置/改派在保存接口里鉴权
    return {"anomalies": list(anomaly_log)}


@app.put("/api/anomalies/{anomaly_id}/handling")
def update_handling(anomaly_id: int, body: HandlingUpdate, user: dict = Depends(current_user)):
    """保存告警处置（处置人 / 处置说明 / 状态）。

    校验顺序:
      1. 登录态 (401)
      2. 当前账号角色权限配置缺失 -> 400 invalid_items（配置缺陷，明确指出）
      3. 无处置权限的只读账号越权操作 -> 403 并说明原因
      4. 表单不合格项（处置人为空、状态非法、处置人不存在等）-> 400 invalid_items
      5. 越权改他人告警 / 越权改派 -> 403 并说明原因
    """
    actor = user["username"]
    actor_role = user["role"]
    actor_perms = role_permissions(actor_role)

    # ---- 2. 当前账号权限配置缺失：属于必须指出的不合格项，不允许保存 ----
    if actor_perms is None:
        return error_response(
            400, "保存失败，存在不合格项",
            [f"当前账号角色 '{actor_role}' 权限配置缺失，不允许保存处置信息，请联系管理员补充权限"])

    # ---- 3. 只读账号（如 viewer）任何保存都按越权拒绝，并说明原因 ----
    if "anomaly:handle" not in actor_perms:
        return error_response(
            403,
            f"越权操作已拒绝：账号 {actor}（角色 {actor_role}）只有查看权限，"
            "仅处置人本人或管理员可以修改处置说明与状态")

    # ---- 4. 表单层不合格项 ----
    invalid_items: list[str] = []
    assignee = (body.assignee or "").strip() if isinstance(body.assignee, str) else ""
    status = (body.status or "").strip().upper() if isinstance(body.status, str) else ""
    note = body.note if isinstance(body.note, str) else None

    if not assignee:
        invalid_items.append("处置人不能为空：每条告警都必须指定明确的处置人")
    elif assignee not in USERS:
        invalid_items.append(f"处置人账号 '{assignee}' 不存在")
    else:
        target_role = USERS[assignee]["role"]
        target_perms = role_permissions(target_role)
        if target_perms is None:
            invalid_items.append(
                f"处置人 {assignee} 的角色 '{target_role}' 权限配置缺失，无法承担告警处置")
        elif "anomaly:handle" not in target_perms:
            invalid_items.append(f"处置人 {assignee}（角色 {target_role}）仅有只读权限，不能作为处置人")

    if not status:
        invalid_items.append("处置状态不能为空")
    elif status not in ANOMALY_STATUSES:
        invalid_items.append(f"处置状态 '{status}' 非法，仅允许：{ '/'.join(ANOMALY_STATUSES) }")

    if note is None:
        invalid_items.append("处置说明格式不合法（必须为文本，可为空字符串）")

    if invalid_items:
        return error_response(400, "保存失败，存在不合格项", invalid_items)

    with _anomaly_lock:
        anomaly = next((a for a in anomaly_log if a["id"] == anomaly_id), None)
        if anomaly is None:
            return error_response(404, f"告警 #{anomaly_id} 不存在")

        current_assignee = anomaly["assignee"]
        is_admin = "anomaly:assign_any" in actor_perms

        if current_assignee is None:
            # 尚未认领：非管理员只能把处置人指定为自己
            if assignee != actor and not is_admin:
                return error_response(
                    403,
                    f"越权操作已拒绝：账号 {actor} 不能把处置人指定为 {assignee}，"
                    "仅可自行认领；指定他人需要管理员权限")
        else:
            if assignee != current_assignee and not is_admin:
                return error_response(
                    403,
                    f"越权操作已拒绝：告警 #{anomaly_id} 当前处置人为 {current_assignee}，"
                    "只有管理员可以改派处置人")
            if assignee == current_assignee and current_assignee != actor and not is_admin:
                return error_response(
                    403,
                    f"越权操作已拒绝：告警 #{anomaly_id} 的处置人是 {current_assignee}，"
                    f"账号 {actor} 仅可查看，不能修改其处置说明与状态")

        # ---- 校验通过，落库 ----
        anomaly["assignee"] = assignee
        anomaly["note"] = note.strip()
        anomaly["status"] = status
        anomaly["updated_by"] = actor
        anomaly["updated_at"] = time.time()
        saved = dict(anomaly)

    return {"ok": True, "anomaly": saved}


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
