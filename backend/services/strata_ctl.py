"""Engine control: start/stop/restart via Strata's own scripts, status via its API."""
import os
import re
import json
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

from . import catalog

STRATA_DIR = Path(os.environ.get("STRATA_DIR", str(Path.home() / "Strata")))
SETTINGS = Path(os.environ.get("STRATA_SETTINGS",
               str(Path.home() / ".config" / "strata" / "settings.json")))


def _settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except Exception:
        return {}


def _api_alive() -> dict | None:
    """Ask the engine itself: if /v1/status answers, it is alive, whatever
    pgrep says. Returns {pid: None, model, pack: None} or None."""
    for port in (8080, 8081):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status",
                                        timeout=3) as r:
                d = json.loads(r.read().decode())
            name = d.get("model", "") or ""
            return {"pid": None, "model": name, "pack": None, "via_api": True}
        except Exception:
            continue
    return None


def running_engine() -> dict | None:
    """The live engine process, if any: {pid, model, pack}.
    pgrep first; if that misses (systemd service visibility quirks), fall
    back to the engine's own API. Never trust pgrep alone."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", "engine/.* --serve"],
            capture_output=True, text=True).stdout
    except Exception:
        return None
    for line in out.splitlines():
        pid, _, cmd = line.partition(" ")
        if "--pack" in cmd:
            pack = cmd.split("--pack")[1].split()[0]
            model = pack.rstrip("/").split("/")[-1]
            return {"pid": int(pid), "model": model, "pack": pack}
    return _api_alive()


def _same_model(current: dict, model: str) -> bool:
    """Match a running engine against a model key, tolerating the API's
    full name (qwen3.8-flash-next-iq3_s) vs the config key (iq3_s)."""
    if not current:
        return False
    cur = (current.get("model") or "").lower()
    return cur == model.lower() or cur.endswith("-" + model.lower()) or cur == model.lower().replace("-", "")


IN_USE_WINDOW_S = 120

_dns_cache: dict[str, str] = {}


def listening_ports() -> list[int]:
    """All TCP ports currently LISTENing on this host."""
    out = subprocess.run(["ss", "-tlnH"], capture_output=True, text=True).stdout
    ports = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            tail = parts[3]  # addr:port
            try:
                ports.add(int(tail.rsplit(":", 1)[1]))
            except (IndexError, ValueError):
                pass
    return sorted(ports)


def recent_consumers(ports: tuple[int, ...] = (8080, 8081)) -> list[dict]:
    """Who talked to the serving chain recently. TIME-WAIT sockets linger
    ~60s after each request, so ss shows the recent consumer set even when
    nothing is in flight. Local peers get process names via ss -tnp."""
    out = subprocess.run(
        ["ss", "-tanH", "( sport = :8080 or sport = :8081 )"],
        capture_output=True, text=True).stdout
    peers: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        state, peer = parts[0], parts[4]
        ip = peer.rsplit(":", 1)[0].strip("[]")
        if ip in ("127.0.0.1", "::1") or state == "LISTEN":
            continue
        peers[ip] = peers.get(ip, 0) + 1
    # local clients: map socket inodes to process names
    out2 = subprocess.run(
        ["ss", "-tnpH", "( sport = :8080 or sport = :8081 )"],
        capture_output=True, text=True).stdout
    local_procs = set()
    for m in re.finditer(r'users:\(\("([^"]+)",pid=(\d+)', out2):
        local_procs.add(m.group(1))
    result = []
    for ip, n in sorted(peers.items(), key=lambda x: -x[1]):
        if ip not in _dns_cache:
            try:
                import socket
                _dns_cache[ip] = socket.gethostbyaddr(ip)[0]
            except Exception:
                _dns_cache[ip] = ip
        result.append({"ip": ip, "host": _dns_cache[ip], "conns": n})
    return result

AUDIT = Path(os.environ.get("DECK_AUDIT",
              str(Path.home() / "strata-deck" / "deck-audit.log")))

# SMBIOS RAM info: cached, but re-read every 10 min so a RAM upgrade
# shows up without restarting the deck.
_RAM_HW_CACHE: dict = {}
_RAM_HW_TTL = 600


def audit(action: str, caller: str = "", decision: str = "", **kw):
    """Append-only trail: who did what, what we saw, what we decided."""
    try:
        rec = {"ts": int(time.time()), "action": action, "caller": caller,
               "decision": decision, "engine": running_engine(),
               "consumers": recent_consumers()}
        rec.update(kw)
        with open(AUDIT, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def in_use() -> dict | None:
    """Live-traffic detector. FAIL-CLOSED: if the engine is alive (pgrep OR
    API answers) but its activity data is unreachable, assume busy -
    unknown means do not stop."""
    alive = bool(running_engine()) or _api_alive() is not None
    if not alive:
        return None
    st = engine_status()
    api = st.get("api")
    if api is None:
        return {"in_flight": -1, "last_request_age_s": 0,
                "note": st.get("error") or "engine API unreachable"}
    act = api.get("activity") or {}
    inflight = act.get("in_flight", 0) or 0
    last = act.get("last_request_at") or 0
    age = (time.time() - last) if last else None
    if inflight > 0 or (age is not None and age < IN_USE_WINDOW_S):
        return {"in_flight": inflight,
                "last_request_age_s": round(age) if age is not None else 0}
    return None


def engine_status() -> dict:
    """Merge process state + the engine's own /v1/status + /health."""
    proc = running_engine()
    info = {"running": bool(proc), "process": proc, "api": None, "error": None}
    if not proc:
        return info
    cfg = catalog.find_config(proc["model"])
    port = 8080
    key = ""
    if cfg:
        try:
            c = json.loads(Path(cfg).read_text())
            port = c.get("port", 8080)
            key = c.get("api_key", "")
        except Exception:
            pass
    url = f"http://127.0.0.1:{port}/v1/status"
    info["port"] = port
    try:
        req = urllib.request.Request(url)
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=5) as r:
            info["api"] = json.loads(r.read().decode())
    except Exception as e:
        info["error"] = f"/v1/status: {e}"
    return info


def system_stats() -> dict:
    """Disk + memory + gpu for the dashboard."""
    stats = {}
    st = shutil.disk_usage("/")
    stats["disk_root"] = {"used": st.used, "free": st.free, "total": st.total}
    data_dir = _settings().get("data_dir", "")
    if data_dir and os.path.isdir(data_dir):
        st2 = shutil.disk_usage(data_dir)
        stats["disk_data"] = {"path": data_dir, "used": st2.used,
                              "free": st2.free, "total": st2.total}
    stats["data_dir"] = data_dir
    # --- RAM hardware truth from SMBIOS (needs one narrow sudoers grant;
    # falls back silently to nothing when unavailable) ---
    if not _RAM_HW_CACHE.get("done") or (
            _RAM_HW_CACHE.get("at") and time.time() - _RAM_HW_CACHE["at"] > _RAM_HW_TTL):
        _RAM_HW_CACHE.clear()
        try:
            d = subprocess.run(["sudo", "-n", "dmidecode", "-t", "memory"],
                               capture_output=True, text=True, timeout=10)
            dimms = []
            for block in d.stdout.split("Memory Device")[1:]:
                fields: dict[str, list[str]] = {}
                for line in block.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        fields.setdefault(k.strip(), []).append(v.strip())
                # last Size = the Memory Device's own field (first may be
                # the Physical Memory Array header bleeding in)
                size = (fields.get("Size") or [""])[-1]
                if size and not size.startswith("No Module"):
                    loc = (fields.get("Locator") or ["?"])[-1]
                    bank = (fields.get("Bank Locator") or [""])[-1]
                    if bank:                       # board reports "DIMM 1" twice;
                        loc = f"{bank} {loc}"      # the channel lives in Bank Locator
                    typ = (fields.get("Type") or ["?"])[-1]
                    rated = (fields.get("Speed") or ["?"])[-1]
                    conf = (fields.get("Configured Memory Speed") or [rated])[-1]
                    dimms.append({
                        "locator": loc,
                        "size": size,
                        "speed": conf,
                        "rated_speed": rated,
                        "part": (fields.get("Part Number") or [""])[-1].strip(),
                        "vendor": (fields.get("Manufacturer") or [""])[-1],
                        "type": typ,
                    })
            if dimms:
                types = {m["type"] for m in dimms}
                speeds = {m["speed"] for m in dimms}
                rated = {m["rated_speed"] for m in dimms}
                summary = " ".join(types) + " " + " ".join(speeds) + f" ×{len(dimms)}"
                if rated != speeds:
                    summary += f" (rated {' '.join(rated)})"
                _RAM_HW_CACHE.update(done=True, at=time.time(), summary=summary.strip(), dimms=dimms)
            else:
                _RAM_HW_CACHE.update(done=True, at=time.time())
        except Exception:
            _RAM_HW_CACHE.update(done=True, at=time.time())
    if _RAM_HW_CACHE.get("summary"):
        stats["ram_hw"] = {"summary": _RAM_HW_CACHE["summary"],
                           "dimms": _RAM_HW_CACHE["dimms"]}
    try:
        free = subprocess.run(["free", "-b"], capture_output=True,
                              text=True).stdout.splitlines()[1].split()
        stats["ram"] = {"total": int(free[1]), "used": int(free[2])}
        # attribute RAM: engine + proxy RSS vs the rest of the system
        model_bytes = 0
        for pat in ("engine/strata --serve", "serve/server.py"):
            p = subprocess.run(["pgrep", "-f", pat], capture_output=True,
                               text=True).stdout.split()
            for pid in p[:1]:
                try:
                    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                        if line.startswith("VmRSS:"):
                            model_bytes += int(line.split()[1]) * 1024
                except Exception:
                    pass
        if model_bytes:
            stats["ram"]["model"] = model_bytes
            stats["ram"]["system"] = max(0, int(free[2]) - model_bytes)
    except Exception:
        pass
    try:
        g = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True)
        if g.returncode == 0:
            name, mt, mu, util = [x.strip() for x in g.stdout.strip().split(",")]
            stats["gpu"] = {"name": name, "mem_total_mib": int(mt),
                            "mem_used_mib": int(mu), "util_pct": int(util)}
    except Exception:
        pass
    return stats


def switch_model(model: str, force: bool = False, caller: str = "") -> dict:
    """Safely swap engines: validate the target config BEFORE stopping the
    running engine, so a doomed switch can never leave us with nothing."""
    script = STRATA_DIR / f"run-{model}.sh"
    if not script.is_file():
        raise ValueError(f"no run script for model {model!r}")
    # already running this model? no-op. stopping+starting the engine that
    # serves live agent conversations for no reason is the carpet-pull bug.
    current = running_engine()
    if current and _same_model(current, model):
        audit("switch", caller=caller, decision="noop-already-running",
              target=model)
        return {"switched": None, "note": f"{model} is already running",
                "pid": current["pid"]}
    audit("switch", caller=caller, decision="proceed", target=model,
          was_running=current.get("model") if current else None)
    if not force:
        try:
            cfg = catalog.read_config(model)
        except FileNotFoundError:
            cfg = {}
        missing = catalog.missing_paths(cfg.get("args", []))
        if missing:
            detail = "; ".join(f"{m['flag']} -> {m['path']}" for m in missing)
            raise ValueError(
                f"config for {model!r} points at missing files: {detail}. "
                "Fix with: ./setup.sh --data-dir <path>, or use force=true")
    stopped = stop_engine(force=force)
    return {"switched": model, "stopped": stopped.get("stopped")}


def start_model(model: str, force: bool = False) -> dict:
    current = running_engine()
    if current and _same_model(current, model):
        return {"started": None, "note": f"{model} is already running",
                "pid": current["pid"]}
    if current:
        raise ValueError("an engine is already running - stop it first")
    script = STRATA_DIR / f"run-{model}.sh"
    if not script.is_file():
        raise ValueError(f"no run script for model {model!r}")
    # validate config paths BEFORE launching: a doomed start must not be
    # reachable via a switch that already stopped the healthy engine
    if not force:
        try:
            cfg = catalog.read_config(model)
        except FileNotFoundError:
            cfg = {}
        missing = catalog.missing_paths(cfg.get("args", []))
        if missing:
            detail = "; ".join(f"{m['flag']} -> {m['path']}" for m in missing)
            raise ValueError(
                f"config for {model!r} points at missing files: {detail}. "
                "Fix with: ./setup.sh --data-dir <path>, or use force=true")
    return _launch(model)


def stop_engine(force: bool = False, caller: str = "") -> dict:
    proc = running_engine()
    if not proc:
        audit("stop", caller=caller, decision="noop-no-engine")
        return {"stopped": None}
    busy = in_use()
    if busy and not force:
        audit("stop", caller=caller, decision="BLOCKED-in-use", busy=busy)
        raise ValueError(
            f"engine is IN USE (in_flight={busy['in_flight']}, "
            f"last request {busy['last_request_age_s']}s ago"
            + (f", {busy['note']}" if busy.get("note") else "") + "). "
            "Stopping will interrupt live sessions (agent turns, API calls). "
            "Use force=true to proceed anyway.")
    audit("stop", caller=caller, decision="forced" if force else "idle-ok",
          busy=busy)
    # kill the server.py parent too so it does not respawn the engine
    subprocess.run(["pkill", "-f", "serve/server.py --engine strata"])
    if proc.get("pid"):
        subprocess.run(["kill", str(proc["pid"])])
    # wait until BOTH pgrep and the API agree the engine is gone
    for _ in range(20):
        if not running_engine():
            break
        time.sleep(0.5)
    else:
        if proc.get("pid"):
            subprocess.run(["kill", "-9", str(proc["pid"])])
        subprocess.run(["pkill", "-9", "-f", "engine/.* --serve"])
    return {"stopped": proc["model"]}


def _launch(model: str) -> dict:
    """Raw launch, no running-engine guard (callers validated already).
    Launches in its OWN transient systemd unit so the engine/proxy do NOT
    live in the deck's cgroup - restarting the deck must never kill the
    serving stack (that was the real carpet-pull)."""
    script = STRATA_DIR / f"run-{model}.sh"
    unit = f"strata-engine-{model}"
    subprocess.run(["systemctl", "--user", "reset-failed", unit],
                   capture_output=True)
    r = subprocess.run(
        ["systemd-run", "--user", f"--unit={unit}", "--collect",
         str(script)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"launch failed: {r.stderr.strip()[:200]}")
    return {"started": model, "unit": unit}


def restart(force: bool = False) -> dict:
    proc = running_engine()
    if not proc:
        raise ValueError("nothing is running")
    model = proc["model"]
    # map pack-derived model name back to a config key (iq3_s)
    key = next((m["model"] for m in catalog.installed_models()
                if _same_model({"model": model}, m["model"])), model)
    stop_engine(force=force)
    # stop_engine's wait loop uses running_engine() which falls back to the
    # API; by the time we get here the API is truly down
    return _launch(key)


def start_install(model: str, family: str, context: int | None,
                  vision: str, jobs: dict) -> dict:
    if model in [j["model"] for j in jobs.values() if not j.get("done")]:
        raise ValueError(f"install of {model} already in progress")
    cmd = [str(STRATA_DIR / ".venv" / "bin" / "python"), "setup.py",
           "--setup", "--model", model, "--family", family,
           "--vision", vision, "--yes", "--no-start"]
    if context:
        cmd += ["--context", str(context)]
    job_id = f"{model}-{int(time.time())}"
    log = STRATA_DIR / f"deck-install-{job_id}.log"
    proc = subprocess.Popen(cmd, stdout=open(log, "a"),
                            stderr=subprocess.STDOUT, cwd=str(STRATA_DIR),
                            start_new_session=True)
    jobs[job_id] = {"proc": proc, "log": str(log), "model": model,
                    "done": False, "rc": None}
    return {"job_id": job_id, "model": model}


def install_status(job: dict) -> dict:
    proc = job["proc"]
    rc = proc.poll()
    if rc is not None:
        job["done"], job["rc"] = True, rc
    tail = []
    try:
        tail = Path(job["log"]).read_text(errors="replace").splitlines()[-25:]
    except Exception:
        pass
    return {"done": job["done"], "rc": job["rc"], "model": job["model"],
            "log_tail": tail}


def tail_log(model: str, lines: int) -> list[str]:
    log = STRATA_DIR / f"strata-{model}.log"
    if not log.is_file():
        return [f"(no log at {log})"]
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 128 * 1024))
            data = f.read().decode(errors="replace")
        return data.splitlines()[-lines:]
    except Exception as e:
        return [f"(cannot read log: {e})"]
