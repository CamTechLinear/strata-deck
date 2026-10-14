"""Strata Deck — a point-and-click cockpit for the Strata engine.

Runs as a systemd user service on the Strata host, as the same user
that runs Strata. Controls the engine via its own scripts: run-<model>.sh,
setup.py, and the OpenAI-compatible /v1/status API.
"""
import os
import re
import json
import glob
import signal
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .services import strata_ctl as ctl
from .services import catalog

app = FastAPI(title="Strata Deck")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


# ------------------------------------------------------------------ status
@app.get("/api/status")
def status():
    st = ctl.engine_status()
    st["in_use"] = ctl.in_use()
    st["consumers"] = ctl.recent_consumers()
    return st


@app.get("/api/system")
def system():
    return ctl.system_stats()


@app.get("/api/ports")
def ports():
    return {"listening": ctl.listening_ports()}


# ------------------------------------------------------------------ models
@app.get("/api/models")
def models():
    return {"installed": catalog.installed_models(),
            "available": catalog.available_models()}


class StartReq(BaseModel):
    model: str
    force: bool = False


@app.post("/api/start")
def start(req: StartReq):
    try:
        return ctl.start_model(req.model, force=req.force)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/switch")
def switch(req: StartReq, http: Request):
    try:
        return ctl.switch_model(req.model, force=req.force,
                                caller=http.client.host)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/stop")
def stop(req: StartReq = StartReq(model=""), http: Request = None):
    try:
        return ctl.stop_engine(force=req.force,
                               caller=http.client.host if http else "")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/restart")
def restart(req: StartReq = StartReq(model="")):
    try:
        return ctl.restart(force=req.force)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ install
class InstallReq(BaseModel):
    model: str
    family: str = "qwen"
    context: int | None = None
    vision: str = "no"


_installs: dict[str, dict] = {}   # job_id -> {proc, log, model, done, rc}


@app.post("/api/install")
def install(req: InstallReq):
    try:
        return ctl.start_install(req.model, req.family, req.context,
                                 req.vision, _installs)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/install/{job_id}")
def install_status(job_id: str):
    job = _installs.get(job_id)
    if not job:
        raise HTTPException(404, "no such install job")
    return ctl.install_status(job)


# ------------------------------------------------------------------ config
@app.get("/api/config/{model}")
def get_config(model: str):
    try:
        return catalog.read_config(model)
    except FileNotFoundError:
        raise HTTPException(404, f"no config for model {model!r}")


class ConfigReq(BaseModel):
    args: list[str] | None = None
    port: int | None = None
    host: str | None = None
    max_context: int | None = None
    kv: str | None = None


@app.put("/api/config/{model}")
def put_config(model: str, req: ConfigReq):
    return catalog.write_config(model, req.model_dump(exclude_none=True))


# ------------------------------------------------------------------ logs
@app.get("/api/logs/{model}")
def logs(model: str, lines: int = 200):
    return {"lines": ctl.tail_log(model, min(lines, 2000))}


@app.get("/health")
def health():
    return {"ok": True, "app": "strata-deck"}


# ------------------------------------------------------------------ spa
app.mount("/assets", StaticFiles(directory=str(FRONTEND / "css")), name="assets")
app.mount("/js", StaticFiles(directory=str(FRONTEND / "js")), name="js")


@app.get("/{path:path}")
def serve_spa(path: str):
    f = FRONTEND / path
    if path and f.is_file():
        return FileResponse(f)
    return FileResponse(FRONTEND / "index.html")
