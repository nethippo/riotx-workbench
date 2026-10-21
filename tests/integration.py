"""End-to-end tests for the dedicated compose demo only. Never points at registered external profiles."""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8088/api"
report = []


def request(path, body=None, method=None, expected=200):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data is not None else "GET"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status, result = response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        status, result = exc.code, json.load(exc)
    assert status == expected, (path, status, result)
    return result


def wait_for(fn, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.5)
    raise AssertionError("Condition timeout")


def check(name):
    report.append(name)
    print("PASS", name, flush=True)


def state():
    return request("/state")


def main():
    s = state()
    assert s["demo_enabled"]
    source = next(p for p in s["profiles"] if p["url"] == "redis://source:6379/0")
    target = next(p for p in s["profiles"] if p["url"] == "redis://target:6379/0")
    for r in s["runs"]:
        if r["status"] in {"QUEUED", "STARTING", "RUNNING", "CANCELLING"}:
            assert r["snapshot"]["workspace"] in {"데모 데이터 이전", "통합 검증"}
            request("/runs/" + r["id"] + "/cancel", {})
    wait_for(lambda: all(r["status"] not in {"QUEUED", "STARTING", "RUNNING", "CANCELLING"} for r in state()["runs"]))
    wait_for(lambda: request("/health")["capabilities_ready"])
    cap = request("/capabilities")
    assert cap["replicate"]["available"] and "--mode" in cap["replicate"]["options"]
    check("included runtime capabilities discovered")
    assert state()["telemetry"][source["id"]]["keys"] is not None
    check("source and target key counts and notification detection")
    for p in [source, target]:
        request("/profiles/"+p["id"]+"/notifications", {"enabled": True, "allow_server_change":False, "expected":{}}, expected=409)
    check("remote configuration requires explicit apply permission")

    def notification(p, enabled):
        t=request("/profiles/"+p["id"]+"/probe", {})
        return request("/profiles/"+p["id"]+"/notifications", {"enabled":enabled,"allow_server_change":True,"expected":{n["node"]:n["flags"] for n in t["nodes"]}})

    assert notification(source,True)["complete"]
    assert notification(target,False)["complete"]
    assert request("/profiles/"+target["id"]+"/probe", {})["notifications"] == "disabled"
    assert notification(target,True)["complete"]
    check("target notification OFF / ON / re-read verified")
    job = next(j for j in s["jobs"] if j["name"] == "실시간 복제")
    bad = dict(job, options={"remove-source-keys":True})
    request("/jobs", bad, expected=422)
    check("destructive and unsupported options rejected")
    assert notification(source,False)["complete"]
    request("/jobs/"+job["id"]+"/runs", {}, expected=409)
    assert notification(source,True)["complete"]
    check("LIVE blocked when source notifications disabled")
    live=request("/jobs/"+job["id"]+"/runs", {})
    wait_for(lambda: next(r for r in state()["runs"] if r["id"]==live["id"])["status"]=="RUNNING")
    request("/jobs/"+job["id"]+"/runs", {}, expected=409)
    t=request("/profiles/"+source["id"]+"/probe", {})
    request("/profiles/"+source["id"]+"/notifications", {"enabled":False,"allow_server_change":True,"expected":{n["node"]:n["flags"] for n in t["nodes"]}}, expected=409)
    check("same-target concurrent write and disabling active LIVE source blocked")
    request("/demo/activity", {})
    wait_for(lambda: state()["telemetry"][target["id"]]["keys"] >= 30)
    wait_for(lambda: all(any(a["profile_id"]==p["id"] for a in state()["activity"]) for p in [source,target]))
    assert all(a["key"].startswith("key:") for a in state()["activity"])
    check("LIVE writes observed on both endpoints; keys masked")
    import redis as redis_sync
    source_client = redis_sync.Redis.from_url("redis://source:6379/0")
    target_client = redis_sync.Redis.from_url("redis://target:6379/0")
    source_client.hset("workbench:demo:customer:0", mapping={"amount": "7654321"})
    source_client.expire("workbench:demo:customer:0", 120)
    wait_for(lambda: target_client.hget("workbench:demo:customer:0", "amount") == b"7654321")
    wait_for(lambda: 0 < target_client.ttl("workbench:demo:customer:0") <= 120)
    assert source_client.hgetall("workbench:demo:customer:0") == target_client.hgetall("workbench:demo:customer:0")
    source_client.close()
    target_client.close()
    check("LIVE value update, Hash contents and TTL propagated")
    wait_for(lambda: bool(state()["metrics"].get(live["id"],{}).get("samples")))
    check("real per-process Prometheus metrics collected")
    gen_job=request("/jobs", dict(name="동시 실행 검증",workspace="통합 검증",kind="gen",source_id="",target_id=source["id"],options={"count":100,"type":"hash","keyspace":"workbench:demo:load","batch":1,"rate":1}))
    slow_compare=request("/jobs", dict(name="속도 제한 비교",workspace="통합 검증",kind="compare",source_id=source["id"],target_id=target["id"],options={"key-pattern":"workbench:demo:customer:*","batch":1,"rate":1}))
    gr=request("/jobs/"+gen_job["id"]+"/runs", {})
    cr=request("/jobs/"+slow_compare["id"]+"/runs", {})
    three={live["id"],gr["id"],cr["id"]}
    wait_for(lambda: sum(r["status"]=="RUNNING" for r in state()["runs"] if r["id"] in three)==3)
    ports = [r["metrics_port"] for r in state()["runs"] if r["id"] in three]
    assert len(set(ports)) == 3
    queue_job=request("/jobs", dict(name="대기열 검증",workspace="통합 검증",kind="file-export",source_id=source["id"],target_id="",options={"key-pattern":"workbench:demo:customer:*"}))
    queued=request("/jobs/"+queue_job["id"]+"/runs", {})
    assert next(r for r in state()["runs"] if r["id"]==queued["id"])["status"]=="QUEUED"
    for other in [gr,cr,queued]:
        request("/runs/"+other["id"]+"/cancel", {})
    wait_for(lambda: all(r["status"]=="CANCELLED" for r in state()["runs"] if r["id"] in {gr["id"],cr["id"],queued["id"]}))
    check("three simultaneous RIOTX processes, fourth queued, active and queued cancellation")
    comp=next(j for j in s["jobs"] if j["kind"]=="compare")
    compare=request("/jobs/"+comp["id"]+"/runs", {})
    wait_for(lambda: next(r for r in state()["runs"] if r["id"]==compare["id"])["status"] not in {"QUEUED","STARTING","RUNNING"})
    check("independent comparison executes while LIVE runs")
    logs=request("/runs/"+live["id"]+"/logs")
    assert logs and all("password" not in l["message"].lower() for l in logs)
    check("persistent execution logs available")
    request("/runs/"+live["id"]+"/cancel", {})
    wait_for(lambda: next(r for r in state()["runs"] if r["id"]==live["id"])["status"]=="CANCELLED")
    check("graceful cancellation reaches terminal state")
    exported=request("/jobs", dict(name="검증 JSON 내보내기",workspace="통합 검증",kind="file-export",source_id=source["id"],target_id="",options={"key-pattern":"workbench:demo:customer:*"}))
    r=request("/jobs/"+exported["id"]+"/runs", {})
    wait_for(lambda: next(v for v in state()["runs"] if v["id"]==r["id"])["status"]=="SUCCEEDED")
    with urllib.request.urlopen(BASE+"/runs/"+r["id"]+"/artifact") as response:
        data=json.load(response)
    assert len(data)==30
    check("file export executes actual RIOTX and downloads 30 records")
    boundary="riotx-workbench-integration"
    csv="id,amount\n1001,12\n1002,34\n1000,-1\n"
    payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="orders.csv"\r\nContent-Type: text/csv\r\n\r\n{csv}\r\n--{boundary}--\r\n').encode()
    upload_req=urllib.request.Request(BASE+"/files",data=payload,headers={"Content-Type":"multipart/form-data; boundary="+boundary})
    with urllib.request.urlopen(upload_req) as response:
        uploaded=json.load(response)
    imported=request("/jobs",dict(name="CSV 변환 검증",workspace="통합 검증",kind="file-import",source_id="",target_id=target["id"],file_id=uploaded["id"],redis_command="hset",key_template="workbench:demo:import:#{id}",options={"type":"csv","header":True,"proc":["amount=T(java.lang.Integer).parseInt(amount)*100"],"filter":"T(java.lang.Integer).parseInt(id) > 1000"}))
    ir=request("/jobs/"+imported["id"]+"/runs", {})
    wait_for(lambda: next(v for v in state()["runs"] if v["id"]==ir["id"])["status"] not in {"QUEUED","STARTING","RUNNING"})
    assert next(v for v in state()["runs"] if v["id"]==ir["id"])["status"]=="SUCCEEDED",request("/runs/"+ir["id"]+"/logs")
    target_client=redis_sync.Redis.from_url("redis://target:6379/0")
    assert target_client.hget("workbench:demo:import:1001","amount")==b"1200"
    assert target_client.hget("workbench:demo:import:1002","amount")==b"3400"
    assert not target_client.exists("workbench:demo:import:1000")
    target_client.delete("workbench:demo:import:1001","workbench:demo:import:1002")
    target_client.close()
    check("CSV upload, SpEL numeric transformation, filtering and HSET output verified")
    about=request("/about")
    assert about["runtime"]["minimum_supported"]=="1.15.1" and about["dependencies"]["redis"]
    updates=request("/about/check-updates", {})
    assert updates["status"]=="checked" and updates["version"]
    check("About dependencies, minimum version and official update check")
    result={"passed":len(report),"checks":report,"runtime":about["runtime"]["version"],"time":state()["time"]}
    Path("/tmp/riotx-integration-results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
