import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import re
import secrets
import signal
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
import redis.asyncio as redis
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client.parser import text_string_to_metric_families
from pydantic import BaseModel, Field, field_validator

from .inheritance import comparison_parent, effective_job
from .deletion import deletion_targets, active_deletion_runs
from .source_files import metadata, fingerprint, parser_key, inspect_file

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
RUNTIME = json.loads((ROOT / "runtime.json").read_text())
ACTIVE = {"QUEUED", "STARTING", "RUNNING", "CANCELLING"}
KINDS = RUNTIME["verified_commands"]
MAX_RUNS = max(1, min(8, int(os.getenv("MAX_CONCURRENT_RUNS", "3"))))
telemetry, clients, subscriber_tasks, run_tasks, processes, metrics = {}, {}, {}, {}, {}, {}
reserved_ports = set()
activity = deque(maxlen=800)
series = defaultdict(lambda: deque(maxlen=60))
log_cache = defaultdict(lambda: deque(maxlen=500))
event_totals = defaultdict(int)
capabilities, latest_release = {}, {"status": "unchecked"}
semaphore = asyncio.Semaphore(MAX_RUNS)
db = None
cipher = None
file_locks = defaultdict(asyncio.Lock)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:12]


def setup_store():
    global db, cipher
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    (DATA / "files").mkdir(exist_ok=True)
    keyfile = DATA / "secret.key"
    if not keyfile.exists():
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    cipher = Fernet(keyfile.read_bytes())
    db = sqlite3.connect(DATA / "workbench.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS objects (kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id));
      CREATE TABLE IF NOT EXISTS logs (seq INTEGER PRIMARY KEY AUTOINCREMENT,run TEXT,body TEXT);
      CREATE INDEX IF NOT EXISTS logs_run ON logs(run,seq);
    """)
    db.commit()


def objects(kind, include_deleted=False):
    rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM objects WHERE kind=? ORDER BY rowid", (kind,))]
    return [r for r in rows if include_deleted or kind != "job" or not r.get("deleted_at")]



def get(kind, ident, include_deleted=False):
    row = db.execute("SELECT body FROM objects WHERE kind=? AND id=?", (kind, ident)).fetchone()
    if not row:
        raise HTTPException(404, "항목을 찾을 수 없습니다")
    obj = json.loads(row[0])
    if kind == "job" and obj.get("deleted_at") and not include_deleted:
        raise HTTPException(404, "삭제된 작업입니다. 삭제한 항목에서 복원하세요")
    return obj


def put(kind, obj):
    db.execute("INSERT INTO objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body", (kind, obj["id"], json.dumps(obj, ensure_ascii=False)))
    db.commit()
    return obj


def secret_profile(ident):
    row = get("profile", ident)
    return json.loads(cipher.decrypt(row["encrypted"].encode())) | {"id": ident}


def public_profile(ident):
    p = secret_profile(ident)
    return {k: v for k, v in p.items() if k not in {"password"}} | {"has_password": bool(p.get("password"))}


def save_profile(p):
    ident = p.pop("id", None) or uid()
    put("profile", {"id": ident, "encrypted": cipher.encrypt(json.dumps(p).encode()).decode()})
    return public_profile(ident)


def safe_error(exc):
    # Connection exceptions can embed URIs, so return only the class and a stable explanation.
    return f"{type(exc).__name__}: 연결·인증·권한 또는 서버 상태를 확인하세요"


def redact(text):
    text = re.sub(r"(rediss?://)[^\s/@]+:[^\s/@]*@", r"\1***@", text)
    for item in objects("profile"):
        p = secret_profile(item["id"])
        if p.get("password"):
            text = text.replace(p["password"], "***")
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)[:4000]


def add_log(run, message, level="INFO"):
    row = {"time": now(), "level": level, "message": redact(message)}
    db.execute("INSERT INTO logs(run,body) VALUES (?,?)", (run, json.dumps(row, ensure_ascii=False)))
    seq = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    row["seq"] = seq
    log_cache[run].append(row)
    db.execute("DELETE FROM logs WHERE seq IN (SELECT seq FROM logs WHERE run=? ORDER BY seq DESC LIMIT -1 OFFSET 2000)", (run,))
    db.commit()


class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str
    username: str = ""
    password: str | None = None
    cluster: bool = False
    observe: bool = True
    mask_keys: bool = True

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        p = urlparse(value)
        if p.scheme not in {"redis", "rediss"} or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError("redis(s)://host:port/db 형식을 사용하고 인증 정보는 별도 입력하세요")
        if p.path and not re.fullmatch(r"/\d+", p.path):
            raise ValueError("DB 번호는 정수여야 합니다")
        return value


class Job(BaseModel):
    parent_job_id: str = ""
    name: str = Field(min_length=1, max_length=100)
    workspace: str = Field(default="데이터 이전", min_length=1, max_length=80)
    kind: str = "replicate"
    source_id: str = ""
    target_id: str = ""
    options: dict = Field(default_factory=dict)
    arguments: list[str] = Field(default_factory=list)
    redis_command: str = ""
    key_template: str = ""
    file_id: str = ""
    watch_file: bool = True


def resolve_job(job):
    return effective_job(job, objects("job"), capabilities.get("compare", {}).get("options", []))


def bind_comparisons():
    """Persist unique legacy relationships before a parent can change endpoints."""
    jobs = objects("job")
    for job in jobs:
        parent = comparison_parent(job, jobs)
        if parent and not job.get("parent_job_id"):
            put("job", job | {"parent_job_id": parent["id"]})


def redis_client(profile):
    return redis.Redis.from_url(profile["url"], username=profile.get("username") or None,
        password=profile.get("password") or None, decode_responses=True, socket_timeout=3,
        socket_connect_timeout=3, health_check_interval=15)


async def node_clients(p):
    if not p.get("cluster"):
        return [("standalone", redis_client(p))]
    u = urlparse(p["url"])
    cluster = redis.RedisCluster(host=u.hostname, port=u.port or 6379, username=p.get("username") or None,
        password=p.get("password") or None, ssl=u.scheme == "rediss", decode_responses=True,
        socket_timeout=3, socket_connect_timeout=3, require_full_coverage=True)
    try:
        await cluster.initialize()
        return [(f"{n.host}:{n.port}", redis.Redis(host=n.host, port=n.port,
            username=p.get("username") or None, password=p.get("password") or None,
            ssl=u.scheme == "rediss", decode_responses=True, socket_timeout=3,
            socket_connect_timeout=3)) for n in cluster.get_primaries()]
    finally:
        await cluster.aclose()


def notification_status(flags):
    if flags is None:
        return "unknown"
    if not flags:
        return "disabled"
    expanded = set(flags)
    if "A" in expanded:
        expanded.update("g$lshztdxe")
    # RIOT's source and this observer both subscribe to keyspace (K) channels.
    # RIOTX 1.15.1 validates both K and E (even though this observer uses K channels).
    return "enabled" if set("KEg$lshzxe").issubset(expanded) else "partial"


async def probe(p):
    ident = p["id"]
    nodes = []
    try:
        for name, c in await node_clients(p):
            try:
                count = await c.dbsize()
                flags, config_error = None, None
                try:
                    cfg = await c.config_get("notify-keyspace-events")
                    flags = cfg.get("notify-keyspace-events", "")
                except Exception as exc:
                    config_error = safe_error(exc)
                nodes.append({"node": name, "keys": count, "flags": flags,
                    "notifications": notification_status(flags), "config_error": config_error, "ok": True})
            except Exception as exc:
                nodes.append({"node": name, "ok": False, "error": safe_error(exc)})
            finally:
                await c.aclose()
        complete = bool(nodes) and all(n["ok"] for n in nodes)
        old = telemetry.get(ident, {})
        total = sum(n.get("keys", 0) for n in nodes) if complete else old.get("keys")
        flags_status = [n.get("notifications", "unknown") for n in nodes]
        notifications = flags_status[0] if len(set(flags_status)) == 1 else "partial"
        t = {"id": ident, "keys": total, "complete": complete, "scope": "primary DB total" if p["cluster"] else f"DB {urlparse(p['url']).path.lstrip('/') or '0'} 전체",
            "updated_at": now() if complete else old.get("updated_at"), "sampled_at": now(),
            "nodes": nodes, "notifications": notifications, "observe": p["observe"],
            "subscriber": old.get("subscriber", "connecting" if p["observe"] else "off"),
            "observed_events": event_totals[ident]}
        telemetry[ident] = t
        series[ident].append({"time": now(), "keys": total, "events": event_totals[ident]})
    except Exception as exc:
        old = telemetry.get(ident, {})
        telemetry[ident] = old | {"id": ident, "complete": False, "error": safe_error(exc), "notifications": "unknown", "sampled_at": now()}


async def subscribe_node(p, name, c):
    ident = p["id"]
    # Pub/Sub must wait indefinitely when idle; command socket timeouts are inappropriate here.
    c.connection_pool.connection_kwargs["socket_timeout"] = None
    pub = c.pubsub()
    try:
        database = urlparse(p["url"]).path.lstrip("/") or "0"
        await pub.psubscribe(f"__keyspace@{database}__:*")
        telemetry.setdefault(ident, {})["subscriber"] = "connected"
        async for message in pub.listen():
            if message["type"] != "pmessage":
                continue
            key = message["channel"].split("__:", 1)[-1]
            event_totals[ident] += 1
            masked = hashlib.sha256(key.encode()).hexdigest()[:10]
            activity.appendleft({"id": uid(), "time": now(), "profile_id": ident, "node": name,
                "key": f"key:{masked}" if p["mask_keys"] else key[:200], "event": str(message["data"])[:80]})
    finally:
        await pub.aclose()
        await c.aclose()


async def observe(p):
    ident = p["id"]
    while True:
        children = []
        try:
            nodes = await node_clients(p)
            children = [asyncio.create_task(subscribe_node(p, name, c)) for name, c in nodes]
            # Periodic reconnect discovers changed cluster primary topology. Gaps are marked explicitly.
            done, _ = await asyncio.wait(children, timeout=60 if p["cluster"] else None, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            telemetry.setdefault(ident, {})["subscriber_error"] = safe_error(exc)
        finally:
            for task in children:
                task.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            telemetry.setdefault(ident, {})["subscriber"] = "reconnecting; gap possible"
        await asyncio.sleep(2)


def get_watch(job):
    return next((w for w in objects("file-watch") if w["id"] == job["id"]), {"id": job["id"]})


def file_status(job):
    if job["kind"] != "file-import":
        return None
    w = get_watch(job)
    if w.get("file_id") != job.get("file_id"):
        w = {}
    return {k: v for k, v in w.items() if k != "inspection"} | {"records": w.get("inspection", {}).get("records"),
        "count_note": w.get("inspection", {}).get("count_note"), "watch_enabled": job.get("watch_file", True)}


async def refresh_file(job, force=False):
    async with file_locks[job["id"]]:
        return await inspect_job_file(job, force)


async def inspect_job_file(job, force=False):
    w = get_watch(job)
    try:
        f = get("file", job.get("file_id", ""))
        path = DATA / "files" / f["id"]
        info = metadata(path)
        token = fingerprint(f["id"], info)
        previous = w.get("inspection", {})
        w.update(checked_at=now(), metadata=info, fingerprint=token, file_id=f["id"], name=f["name"], error=None)
        if w.get("candidate") != token and not force:
            w.update(candidate=token, stable=False)
            # A changed file must not display the previous version's record count.
            w["inspection"] = {}
            put("file-watch", w)
            return w
        if previous.get("fingerprint") != token or previous.get("parser_key") != parser_key(job.get("options", {})):
            result = await asyncio.to_thread(inspect_file, path, info, job.get("options", {}), f["name"])
            if metadata(path) != info:
                raise ValueError("파일 변경 중: 다음 조회에서 다시 확인합니다")
            previous = result | {"fingerprint": token, "metadata": info, "parser_key": parser_key(job.get("options", {}))}
        w.update(inspection=previous, candidate=token, stable=True)
        if "attempted" not in w:
            # Upgrade baseline: never replay an old import merely because the server restarted.
            w["attempted"] = token
            w["armed"] = any(r["job_id"] == job["id"] and r["snapshot"].get("file_id") == job.get("file_id") for r in objects("run"))
        put("file-watch", w)
        return previous
    except (OSError, ValueError, HTTPException) as exc:
        w.update(checked_at=now(), error=str(exc) if isinstance(exc, ValueError) else "입력 파일이 없거나 접근할 수 없습니다", stable=False, inspection={})
        put("file-watch", w)
        return w


async def check_files_once():
    for job in objects("job"):
        if job["kind"] != "file-import":
            continue
        await refresh_file(job)
        w = get_watch(job)
        if not w.get("stable") or w.get("error") or not w.get("armed") or not job.get("watch_file", True) or w.get("attempted") == w.get("fingerprint"):
            continue
        if any(r["job_id"] == job["id"] and r["status"] in ACTIVE for r in objects("run")):
            continue
        try:
            if not capabilities.get("file-import", {}).get("available"):
                continue
            await queue_run(job["id"], automatic=True)
        except HTTPException as exc:
            if exc.status_code != 404:
                put("file-watch", get_watch(job) | {"error": str(exc.detail)})
        except (OSError, ValueError):
            put("file-watch", get_watch(job) | {"error": "파일 확인 중입니다. 다음 조회에서 다시 시도합니다"})


async def poll_files():
    while True:
        await check_files_once()
        await asyncio.sleep(2)

async def poll():
    while True:
        profiles = [secret_profile(p["id"]) for p in objects("profile")]
        await asyncio.gather(*(probe(p) for p in profiles), return_exceptions=True)
        current_profiles = {p["id"] for p in objects("profile")}
        for p in profiles:
            ident = p["id"]
            if ident not in current_profiles:
                telemetry.pop(ident, None)
                series.pop(ident, None)
                continue
            # A profile may have been edited while probing Redis.
            p = secret_profile(ident)
            if p["observe"] and (ident not in subscriber_tasks or subscriber_tasks[ident].done()):
                subscriber_tasks[ident] = asyncio.create_task(observe(p))
            elif not p["observe"]:
                task = subscriber_tasks.pop(ident, None)
                if task:
                    task.cancel()
                telemetry.setdefault(ident, {})["subscriber"] = "off"
        await asyncio.sleep(2)


async def discover():
    for kind in KINDS:
        p = await asyncio.create_subprocess_exec("riotx", kind, "--help", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await p.communicate()
        help_text = output.decode(errors="replace")
        options = sorted(set(re.findall(r"--[a-z][a-z0-9-]*", help_text)))
        capabilities[kind] = {"available": p.returncode == 0, "options": options, "help": help_text}


BLOCKED_OPTIONS = {"help", "version", "log-file", "termination-log", "metrics-port", "metrics-name", "metrics-prop", "var", "flushdb", "remove-source-keys", "insecure", "source-insecure", "target-insecure", "verify", "source-verify", "target-verify"}
CONNECTION_OPTIONS = re.compile(r"^(source-|target-)?(pass|user|host|port|uri|socket|db|key|key-pass|cert|cacert|entra.*|tls|sni.*|cluster)$")


def build_command(job, run_id):
    kind = job["kind"]
    cap = capabilities.get(kind, {})
    if kind not in KINDS or not cap.get("available"):
        raise HTTPException(422, "포함 RIOTX에서 지원하지 않는 명령입니다")
    args, env = ["riotx", kind], os.environ.copy()
    # Do not inherit host RIOT_* defaults into a reproducible saved job.
    env = {k: v for k, v in env.items() if not k.startswith("RIOT_")}

    def connection(ident, prefix=""):
        p = secret_profile(ident)
        if prefix:
            args.append(p["url"])
        else:
            args.extend(["--uri", p["url"]])
        for field in ["password", "username"]:
            if p.get(field):
                key = (prefix.upper() + "_" if prefix else "REDIS_") + ("PASS" if field == "password" else "USER")
                env["RIOT_" + key] = p[field]
        if p.get("cluster"):
            args.append("--" + (prefix + "-" if prefix else "") + "cluster")
        return p

    if kind in {"replicate", "compare", "stream-export"}:
        connection(job["source_id"], "source")
        connection(job["target_id"], "target")
    elif kind == "memcached-replicate":
        raise HTTPException(422, "Memcached는 별도 인증·연결 어댑터가 필요합니다. 현재 UI 실행은 지원하지 않습니다")
    else:
        ident = job["target_id"] if kind in {"file-import", "db-import", "stream-import", "gen", "faker", "elastic-import", "snowflake-import", "redis-import"} else job["source_id"]
        connection(ident)
    options = job.get("options", {})
    for name, value in options.items():
        if name in BLOCKED_OPTIONS or re.search(r"pass|secret|token", name) or CONNECTION_OPTIONS.fullmatch(name) or name.startswith("metrics") or f"--{name}" not in cap["options"]:
            raise HTTPException(422, f"지원하지 않거나 관리되는 옵션: {name}")
        if value is False or value is None or value == "":
            continue
        if value is True:
            args.append("--" + name)
        else:
            for val in value if isinstance(value, list) else [value]:
                args.append(f"--{name}={val}")
    positional = job.get("arguments", [])
    if len(positional) > 12 or any(not isinstance(v, str) or v.startswith("-") or len(v) > 10000 for v in positional):
        raise HTTPException(422, "위치 인자를 확인하세요")
    if kind == "file-import":
        f = get("file", job.get("file_id", ""))
        args.append(str(DATA / "files" / job.get("file_source", {}).get("snapshot_file", f["id"])))
    elif kind == "file-export":
        args.append(str(DATA / "files" / (run_id + ".json")))
    else:
        args.extend(positional)
    if kind in {"db-import", "db-export"} and not positional:
        raise HTTPException(422, "SQL 인자가 필요합니다")
    if "--metrics" in cap["options"]:
        args.extend(["--metrics", f"--metrics-name={run_id}"])
    if "--progress" in cap["options"]:
        args.append("--progress=LOG")
    command = job.get("redis_command")
    if command:
        if command not in {"hset", "json.set", "set", "xadd", "sadd", "lpush", "rpush", "zadd", "log"} or kind not in {"file-import", "db-import", "stream-import", "faker", "elastic-import"}:
            raise HTTPException(422, "지원하는 가져오기 하위 명령을 선택하세요")
        args.append(command)
        key = job.get("key_template", "")
        if key.startswith("-"):
            raise HTTPException(422, "키 템플릿이 옵션으로 시작할 수 없습니다")
        if key:
            args.append(key)
    return args, env


async def preflight(job):
    if job.get("inheritance_error"):
        raise HTTPException(422, job["inheritance_error"])
    if job.get("comparison_disabled"):
        raise HTTPException(422, "상위 복제 설정에서 비교가 비활성화되어 있습니다. 비교 방식을 QUICK 또는 FULL로 변경하세요")
    build_command(job, "validate")
    def endpoint(ident):
        if not ident:
            return None
        u = urlparse(secret_profile(ident)["url"])
        return (u.scheme, u.hostname.lower(), u.port or 6379, int(u.path.lstrip("/") or "0"))
    if job["kind"] in {"replicate", "stream-export"} and endpoint(job["source_id"]) == endpoint(job["target_id"]):
        raise HTTPException(409, "소스와 타깃이 같은 Redis DB입니다. 다른 DB를 선택하세요")
    if job["kind"] in {"replicate", "stream-export"} and str(job.get("options", {}).get("mode", "SCAN")).upper() in {"LIVE", "LIVEONLY"}:
        p = secret_profile(job["source_id"])
        await probe(p)
        if telemetry[p["id"]].get("notifications") != "enabled":
            raise HTTPException(409, "LIVE 소스의 keyspace 알림이 꺼져 있거나 불충분/확인 불가입니다. 설정에서 확인·활성화하세요")
    for r in objects("run"):
        if r["status"] in ACTIVE:
            old = r["snapshot"]
            readonly = {"compare", "stats", "file-export", "db-export"}
            if old["kind"] not in readonly and job["kind"] not in readonly and endpoint(old.get("target_id")) and endpoint(old.get("target_id")) == endpoint(job.get("target_id")):
                # Conservative: disjoint pattern proofs are outside this first release.
                raise HTTPException(409, "같은 타깃을 사용하는 실행이 있습니다. 종료 후 실행하거나 별도 타깃 프로필을 사용하세요")


async def scrape_metrics(run_id, port):
    async with httpx.AsyncClient(timeout=2) as client:
        while True:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/metrics")
                response.raise_for_status()
                samples = []
                for fam in text_string_to_metric_families(response.text):
                    for s in fam.samples:
                        if s.name.startswith(("riot", "spring_batch", "redis_batch")):
                            samples.append({"name": s.name, "labels": s.labels, "value": s.value if math.isfinite(s.value) else None})
                metrics[run_id] = {"captured_at": now(), "samples": samples, "available": True}
            except Exception:
                metrics[run_id] = metrics.get(run_id, {}) | {"available": False}
            await asyncio.sleep(2)


async def execute_run(run):
    ident = run["id"]
    collector = None
    port = None
    try:
        async with semaphore:
            run = get("run", ident)
            if run["status"] != "QUEUED":
                return
            run.update(status="STARTING", started_at=now())
            put("run", run)
            args, env = build_command(run["snapshot"], ident)
            port = next(n for n in range(9200, 9300) if n not in reserved_ports)
            reserved_ports.add(port)
            if "--metrics" in args:
                args.insert(2, f"--metrics-port={port}")
            add_log(ident, "RIOTX 프로세스 시작: " + " ".join(args))
            proc = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            processes[ident] = (proc, port)
            run.update(status="RUNNING", pid=proc.pid, metrics_port=port)
            put("run", run)
            collector = asyncio.create_task(scrape_metrics(ident, port))
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                msg = line.decode(errors="replace").strip()
                if msg:
                    add_log(ident, msg, "ERROR" if "ERROR" in msg else "WARN" if "WARN" in msg else "INFO")
            code = await proc.wait()
            run = get("run", ident)
            cancelled = run["status"] == "CANCELLING"
            run.update(status="CANCELLED" if cancelled else "SUCCEEDED" if code == 0 else "FAILED", exit_code=code,
                ended_at=now(), validation="NOT_RUN")
            if run["snapshot"]["kind"] == "file-export" and code == 0:
                run["artifact"] = ident + ".json"
            put("run", run)
            note = "비교 범위와 상세 결과는 위 RIOTX 출력을 확인하세요" if run["snapshot"]["kind"] == "compare" else "데이터 검증은 결과 비교 메뉴에서 실행하세요"
            add_log(ident, f"프로세스 종료: {run['status']} / exit {code}. {note}")
    except asyncio.CancelledError:
        proc = processes.get(ident, (None,))[0]
        if proc and proc.returncode is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 8)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        r = get("run", ident)
        if r["status"] in ACTIVE:
            put("run", r | {"status": "UNKNOWN", "ended_at": now()})
        raise
    except Exception as exc:
        proc = processes.get(ident, (None,))[0]
        if proc and proc.returncode is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        put("run", get("run", ident) | {"status": "FAILED", "ended_at": now()})
        add_log(ident, safe_error(exc), "ERROR")
    finally:
        if collector:
            collector.cancel()
        processes.pop(ident, None)
        if port is not None:
            reserved_ports.discard(port)


async def check_release():
    global latest_release
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
            response = await c.get(RUNTIME["release_api"], headers={"Accept": "application/vnd.github+json", "User-Agent": "RIOTX-Workbench"})
            response.raise_for_status()
            r = response.json()
            v = r["tag_name"].lstrip("v")
            if not re.fullmatch(r"\d+\.\d+\.\d+", v) or r.get("prerelease") or r.get("draft"):
                raise ValueError("unstable release")
            latest_release = {"status": "checked", "version": v, "checked_at": now(),
                "update_available": tuple(map(int, v.split("."))) > tuple(map(int, RUNTIME["version"].split("."))),
                "url": f"https://github.com/redis/riotx-dist/releases/tag/v{v}"}
            put("setting", {"id": "release", **latest_release})
    except Exception as exc:
        latest_release = latest_release | {"status": "unavailable", "attempted_at": now(), "error": type(exc).__name__}
    return latest_release


async def release_watch():
    while True:
        await check_release()
        await asyncio.sleep(21600)


def seed():
    if os.getenv("DEMO_ENABLED") != "true" or objects("job", include_deleted=True):
        return
    src = save_profile({"name": "데모 소스", "url": os.getenv("DEMO_SOURCE", "redis://source:6379/0"), "username": "", "password": "", "cluster": False, "observe": True, "mask_keys": True})
    dst = save_profile({"name": "데모 타깃", "url": os.getenv("DEMO_TARGET", "redis://target:6379/0"), "username": "", "password": "", "cluster": False, "observe": True, "mask_keys": True})
    j = Job(name="실시간 복제", workspace="데모 데이터 이전", source_id=src["id"], target_id=dst["id"], options={"mode": "LIVE", "key-pattern": "workbench:demo:*", "struct": True, "batch": 50, "threads": 1}).model_dump()
    put("job", j | {"id": uid(), "revision": 1, "created_at": now()})
    put("job", Job(name="결과 비교", workspace="데모 데이터 이전", kind="compare", source_id=src["id"], target_id=dst["id"], options={"key-pattern": "workbench:demo:*"}).model_dump() | {"id": uid(), "revision": 1, "created_at": now()})


@asynccontextmanager
async def lifespan(app):
    setup_store()
    version_process = await asyncio.create_subprocess_exec("riotx", "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    version_output, _ = await version_process.communicate()
    actual = re.search(r"riotx (\d+\.\d+\.\d+)", version_output.decode(errors="replace"))
    if version_process.returncode or not actual or actual[1] != RUNTIME["version"]:
        raise RuntimeError("Bundled RIOTX does not match the verified runtime manifest")
    for r in objects("run"):
        if r["status"] in ACTIVE:
            put("run", r | {"status": "UNKNOWN", "ended_at": now()})
    seed()
    bind_comparisons()
    discovery = asyncio.create_task(discover())
    polling = asyncio.create_task(poll())
    file_polling = asyncio.create_task(poll_files())
    updates = asyncio.create_task(release_watch())
    yield
    tasks = [discovery, polling, file_polling, updates, *subscriber_tasks.values(), *run_tasks.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    db.close()


app = FastAPI(title="RIOTX Workbench", lifespan=lifespan)


@app.middleware("http")
async def protection(request, call_next):
    if request.url.path.startswith("/api"):
        token = os.getenv("WORKBENCH_TOKEN", "")
        if token and request.url.path != "/api/health":
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "접근 토큰이 필요합니다"}, status_code=401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "교차 사이트 변경 요청 거부"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/api/health")
async def health():
    return {"status": "ok", "riotx": RUNTIME["version"], "capabilities_ready": len(capabilities) == len(KINDS)}


@app.get("/api/state")
async def state():
    return {"jobs": [resolve_job(j) | {"source_status": file_status(j)} for j in objects("job")], "runs": objects("run")[-200:], "profiles": [public_profile(p["id"]) for p in objects("profile")],
        "telemetry": telemetry, "activity": list(activity)[:150], "series": dict(series), "metrics": metrics,
        "deletions": [d for d in objects("deletion") if not d.get("restored_at")],
        "files": objects("file"), "runtime": RUNTIME, "release": latest_release, "max_runs": MAX_RUNS,
        "demo_enabled": os.getenv("DEMO_ENABLED") == "true", "time": now()}


@app.get("/api/capabilities")
async def caps():
    return capabilities


@app.get("/api/about")
async def about():
    return {"runtime": RUNTIME, "release": latest_release,
        "dependencies": {name: importlib.metadata.version(name) for name in ["fastapi", "uvicorn", "redis", "httpx", "cryptography", "prometheus-client"]},
        "python": os.sys.version.split()[0], "java": "Eclipse Temurin JRE 21", "frontend": "React 19 · Vite 6 · Phosphor Icons · Recharts",
        "storage": "SQLite WAL / encrypted profiles / persistent Docker volume", "build": "1.0.0",
        "limitations": ["Pub/Sub 알림은 유실될 수 있으며 감사 로그가 아닙니다", "키 수 일치는 데이터 일치를 보장하지 않습니다", "Cluster 관측은 primary 노드에 직접 접근할 수 있어야 합니다", "Memcached 실행은 연결 어댑터 미지원", "SQL·SpEL은 신뢰할 수 있는 운영자만 사용하세요"]}


@app.post("/api/about/check-updates")
async def updates():
    return await check_release()


@app.post("/api/profiles")
async def create_profile(body: Profile):
    return save_profile(body.model_dump() | {"password": body.password or ""})


@app.put("/api/profiles/{ident}")
async def update_profile(ident: str, body: Profile):
    old = secret_profile(ident)
    p = body.model_dump() | {"id": ident}
    if body.password is None:
        p["password"] = old.get("password", "")
    task = subscriber_tasks.pop(ident, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return save_profile(p)


@app.delete("/api/profiles/{ident}")
async def delete_profile(ident: str):
    get("profile", ident)
    # Check execution snapshots, including queued runs and jobs edited since launch.
    if any(r["status"] in ACTIVE and ident in
           (r["snapshot"].get("source_id"), r["snapshot"].get("target_id"))
           for r in objects("run")):
        raise HTTPException(409, "이 연결을 사용하는 실행이 진행 중이거나 대기 중입니다. 실행을 종료한 뒤 삭제하세요")
    changed = []
    # No await before commit: a concurrent submission cannot retain this profile.
    with db:
        for job in objects("job", include_deleted=True):
            fields = [key for key in ("source_id", "target_id") if job.get(key) == ident]
            if not fields:
                continue
            job.update({key: "" for key in fields})
            job["revision"] = job.get("revision", 0) + 1
            db.execute("UPDATE objects SET body=? WHERE kind='job' AND id=?",
                       (json.dumps(job, ensure_ascii=False), job["id"]))
            changed.append(job["id"])
        db.execute("DELETE FROM objects WHERE kind='profile' AND id=?", (ident,))
    task = subscriber_tasks.pop(ident, None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    telemetry.pop(ident, None)
    series.pop(ident, None)
    event_totals.pop(ident, None)
    return {"deleted_id": ident, "updated_jobs": changed}


@app.post("/api/profiles/{ident}/probe")
async def probe_endpoint(ident: str):
    await probe(secret_profile(ident))
    return telemetry[ident]


class NotificationChange(BaseModel):
    enabled: bool
    allow_server_change: bool = False
    expected: dict[str, str | None]


@app.post("/api/profiles/{ident}/notifications")
async def change_notifications(ident: str, body: NotificationChange):
    if not body.allow_server_change:
        raise HTTPException(409, "서버 설정 변경 허용을 먼저 선택하세요")
    if not body.enabled:
        for r in objects("run"):
            j = r["snapshot"]
            if r["status"] in ACTIVE and j["source_id"] == ident and str(j["options"].get("mode", "")).upper() in {"LIVE", "LIVEONLY"}:
                raise HTTPException(409, "실행 중인 LIVE 소스 알림을 끌 수 없습니다")
    p = secret_profile(ident)
    results = []
    for name, c in await node_clients(p):
        try:
            current = (await c.config_get("notify-keyspace-events")).get("notify-keyspace-events", "")
            if name not in body.expected or body.expected[name] is None or set(current) != set(body.expected[name]):
                raise HTTPException(409, "알림 설정 또는 topology가 변경됐습니다. 다시 탐지한 뒤 적용하세요")
            # Preserve all existing classes. RIOTX's own preflight requires KEA coverage.
            desired = "".join(dict.fromkeys(current + "KEA")) if body.enabled else ""
            await c.config_set("notify-keyspace-events", desired)
            verified = (await c.config_get("notify-keyspace-events")).get("notify-keyspace-events", "")
            results.append({"node": name, "before": current, "after": verified, "ok": notification_status(verified) == ("enabled" if body.enabled else "disabled")})
        except HTTPException:
            raise
        except Exception as exc:
            results.append({"node": name, "ok": False, "error": safe_error(exc)})
        finally:
            await c.aclose()
    put("audit", {"id": uid(), "time": now(), "profile_id": ident, "action": "notify-keyspace-events", "results": results})
    await probe(p)
    return {"results": results, "complete": all(r["ok"] for r in results), "telemetry": telemetry[ident], "persistent": False}


class DeletionRequest(BaseModel):
    scope: str
    job_id: str = ""
    workspace: str = ""
    expected_ids: list[str] | None = None


def deletion_preview(body):
    if body.scope not in {"job", "workspace"}:
        raise HTTPException(422, "삭제 대상을 확인하세요")
    if body.scope == "job":
        get("job", body.job_id)
    elif not body.workspace:
        raise HTTPException(422, "작업공간을 선택하세요")
    jobs = [resolve_job(j) for j in objects("job")]
    targets = deletion_targets(jobs, job_id=body.job_id if body.scope == "job" else "",
                               workspace=body.workspace if body.scope == "workspace" else None)
    if not targets:
        raise HTTPException(404, "삭제할 항목이 없습니다")
    active = active_deletion_runs(targets, objects("run"), ACTIVE)
    return {"jobs": [{k: j[k] for k in ("id", "name", "kind", "workspace")} for j in targets],
            "blocked": bool(active), "active_runs": [r["id"] for r in active],
            "label": body.workspace if body.scope == "workspace" else next(j["name"] for j in targets if j["id"] == body.job_id)}


@app.post("/api/deletions/preview")
async def preview_deletion(body: DeletionRequest):
    return deletion_preview(body)


@app.post("/api/deletions")
async def delete_entries(body: DeletionRequest):
    preview = deletion_preview(body)
    ids = {j["id"] for j in preview["jobs"]}
    if body.expected_ids is None or set(body.expected_ids) != ids:
        raise HTTPException(409, "삭제 대상이 변경됐습니다. 옵션 메뉴에서 다시 확인하세요")
    if preview["blocked"]:
        raise HTTPException(409, "실행 중이거나 대기 중인 작업이 있습니다. 실행을 종료한 뒤 삭제하세요")
    # No await between active-run checks and the transaction: submissions cannot
    # slip into this deletion. Jobs, logs, profiles and Redis data are retained.
    batch = {"id": uid(), "label": preview["label"], "scope": body.scope,
             "created_at": now(), "jobs": preview["jobs"]}
    with db:
        for ident in ids:
            job = get("job", ident)
            job.update(deleted_at=batch["created_at"], deletion_id=batch["id"])
            db.execute("UPDATE objects SET body=? WHERE kind='job' AND id=?", (json.dumps(job, ensure_ascii=False), ident))
        db.execute("INSERT INTO objects VALUES ('deletion',?,?)", (batch["id"], json.dumps(batch, ensure_ascii=False)))
    return {"deleted_ids": sorted(ids), "deletion": batch}


@app.post("/api/deletions/{ident}/restore")
async def restore_entries(ident: str):
    batch = get("deletion", ident)
    if batch.get("restored_at"):
        raise HTTPException(409, "이미 복원한 항목입니다")
    jobs = [get("job", j["id"], include_deleted=True) for j in batch["jobs"]]
    if any(j.get("deletion_id") != ident or not j.get("deleted_at") for j in jobs):
        raise HTTPException(409, "삭제 상태가 변경됐습니다. 목록을 새로고침하세요")
    available = {j["id"]: j for j in [*objects("job"), *jobs]}
    for job in jobs:
        if job.get("parent_job_id"):
            parent = available.get(job["parent_job_id"])
            if not parent or parent["kind"] != "replicate":
                raise HTTPException(409, "상위 복제 작업을 먼저 복원하세요")
    with db:
        for job in jobs:
            job.pop("deleted_at", None)
            job.pop("deletion_id", None)
            db.execute("UPDATE objects SET body=? WHERE kind='job' AND id=?", (json.dumps(job, ensure_ascii=False), job["id"]))
        batch["restored_at"] = now()
        db.execute("UPDATE objects SET body=? WHERE kind='deletion' AND id=?", (json.dumps(batch, ensure_ascii=False), ident))
    return {"restored_ids": [j["id"] for j in jobs]}


@app.post("/api/jobs")
async def create_job(body: Job):
    j = body.model_dump()
    parent = comparison_parent(j, objects("job"))
    if parent:
        j["parent_job_id"] = parent["id"]
    resolved = resolve_job(j)
    if resolved.get("inheritance_error"):
        raise HTTPException(422, resolved["inheritance_error"])
    build_command(resolved, "validate")
    return put("job", j | {"id": uid(), "revision": 1, "created_at": now()})


@app.put("/api/jobs/{ident}")
async def edit_job(ident: str, body: Job):
    old = get("job", ident)
    bind_comparisons()
    j = body.model_dump()
    if old.get("parent_job_id"):
        j["parent_job_id"] = old["parent_job_id"]
    resolved = resolve_job(j)
    if resolved.get("inheritance_error"):
        raise HTTPException(422, resolved["inheritance_error"])
    build_command(resolved, "validate")
    return put("job", j | {"id": ident, "revision": old["revision"] + 1, "created_at": old["created_at"]})


@app.post("/api/jobs/{ident}/validate")
async def validate_job(ident: str):
    j = resolve_job(get("job", ident))
    await preflight(j)
    args, _ = build_command(j, "preview")
    return {"valid": True, "command": redact(" ".join(args)), "note": "도움말·설정 검증입니다. SQL·SpEL 및 실제 전송 결과는 실행 시 검증됩니다"}


@app.post("/api/jobs/{ident}/runs")
async def start(ident: str):
    return await queue_run(ident)


async def queue_run(ident: str, automatic: bool = False):
    j = resolve_job(get("job", ident))
    if sum(r["status"] in ACTIVE for r in objects("run")) >= 50:
        raise HTTPException(429, "대기열 한도 50개에 도달했습니다")
    await preflight(j)
    # Preflight does Redis I/O. Recheck visibility/settings after that await so
    # deleting a job or its parent cannot leave a newly queued orphan run.
    if resolve_job(get("job", ident)) != j:
        raise HTTPException(409, "작업 설정이 변경됐습니다. 새로고침 후 다시 실행하세요")
    if j["kind"] == "file-import":
        source = await refresh_file(j, force=True)
        if not source.get("snapshot_file") or source.get("error"):
            raise HTTPException(409, source.get("error") or "입력 파일 확인 중입니다")
        if resolve_job(get("job", ident)) != j:
            raise HTTPException(409, "파일 확인 중 작업 설정이 변경됐습니다")
        # No await between the final conflict check and queue insertion.
        await preflight(j)
        if resolve_job(get("job", ident)) != j or metadata(DATA / "files" / j["file_id"]) != source["metadata"]:
            raise HTTPException(409, "작업 또는 입력 파일이 변경됐습니다. 다시 실행하세요")
        if automatic and not j.get("watch_file", True):
            raise HTTPException(409, "자동 가져오기가 비활성화되었습니다")
        j = j | {"file_source": source}
    run = {"id": uid(), "job_id": ident, "snapshot": j, "status": "QUEUED", "created_at": now(), "validation": "NOT_RUN", "trigger": "file-change" if automatic else "manual"}
    if j["kind"] == "file-import":
        watch = get_watch(j)
        put("file-watch", watch | {"attempted": source["fingerprint"], "armed": True, "last_run_id": run["id"], "error": None})
    put("run", run)
    run_tasks[run["id"]] = asyncio.create_task(execute_run(run))
    return run


@app.post("/api/runs/{ident}/cancel")
async def cancel(ident: str):
    r = get("run", ident)
    if r["status"] not in ACTIVE:
        raise HTTPException(409, "실행 중인 잡이 아닙니다")
    if ident not in processes:
        task = run_tasks.get(ident)
        if task:
            task.cancel()
        return put("run", r | {"status": "CANCELLED", "ended_at": now()})
    put("run", r | {"status": "CANCELLING"})
    proc = processes[ident][0]
    os.killpg(proc.pid, signal.SIGTERM)
    async def force():
        await asyncio.sleep(8)
        if proc.returncode is None:
            os.killpg(proc.pid, signal.SIGKILL)
    asyncio.create_task(force())
    return {"status": "CANCELLING"}


@app.get("/api/runs/{ident}/logs")
async def logs(ident: str):
    get("run", ident)
    return [json.loads(r["body"]) | {"seq": r["seq"]} for r in db.execute("SELECT seq,body FROM logs WHERE run=? ORDER BY seq", (ident,))]


@app.get("/api/runs/{ident}/events")
async def events(ident: str, request: Request):
    get("run", ident)
    async def stream():
        header = request.headers.get("last-event-id", "0")
        seq = int(header) if header.isdigit() else 0
        while not await request.is_disconnected():
            rows = db.execute("SELECT seq,body FROM logs WHERE run=? AND seq>? ORDER BY seq LIMIT 100", (ident, seq)).fetchall()
            for row in rows:
                seq = row["seq"]
                yield f"id: {seq}\nevent: log\ndata: {row['body']}\n\n"
            yield f"event: state\ndata: {json.dumps(get('run', ident))}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def save_upload(file, ident, old=None):
    size = 0
    path = DATA / "files" / ident
    temporary = path.with_name(uid() + ".upload")
    try:
        with temporary.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise HTTPException(413, "파일 한도는 100MB입니다")
                f.write(chunk)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return put("file", {"id": ident, "name": Path(file.filename or "upload").name, "size": size,
        "created_at": old["created_at"] if old else now(), "updated_at": now()})


@app.post("/api/files")
async def upload(file: UploadFile):
    return await save_upload(file, uid())


@app.put("/api/files/{ident}")
async def replace_file(ident: str, file: UploadFile):
    old = get("file", ident)
    return await save_upload(file, ident, old)


@app.get("/api/runs/{ident}/artifact")
async def artifact(ident: str):
    r = get("run", ident)
    if not r.get("artifact"):
        raise HTTPException(404, "결과 파일이 없습니다")
    return FileResponse(DATA / "files" / r["artifact"], filename=r["artifact"], media_type="application/json")


@app.post("/api/demo/activity")
async def demo_activity():
    if os.getenv("DEMO_ENABLED") != "true":
        raise HTTPException(404)
    # Fixed env-owned endpoint and prefix: never follows an arbitrary user profile.
    c = redis.Redis.from_url(os.environ["DEMO_SOURCE"], decode_responses=True)
    try:
        for i in range(30):
            await c.hset(f"workbench:demo:customer:{i}", mapping={"id": i, "updated": now(), "amount": i * 100})
        await c.set("workbench:demo:session", "demo", ex=10)
    finally:
        await c.aclose()
    return {"written": 31, "prefix": "workbench:demo:", "time": now()}


STATIC = ROOT / "static"
if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
