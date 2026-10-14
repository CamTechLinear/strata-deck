"""Model catalog: what is installed (strata-*.json) and what setup.py offers."""
import os
import re
import json
import ast
from pathlib import Path

STRATA_DIR = Path(os.environ.get("STRATA_DIR", str(Path.home() / "Strata")))

# Fallback catalog parsed from setup.py's MODELS dict at import time.
_AVAILABLE_CACHE: list[dict] | None = None


def missing_paths(args: list[str]) -> list[dict]:
    """Every arg value that looks like an absolute path but doesn't exist.

    Catches configs left pointing at deleted/moved data dirs (e.g. after a
    --data-dir migration without re-running setup.sh per model).
    """
    missing = []
    for i, a in enumerate(args):
        # flag values only: the token after a --flag, absolute-path shaped
        if a.startswith("--") or i == 0:
            continue
        prev = args[i - 1]
        if not prev.startswith("--"):
            continue
        if a.startswith("/") and not Path(a).exists():
            missing.append({"flag": prev, "path": a})
    return missing


def installed_models() -> list[dict]:
    out = []
    for cfg in sorted(STRATA_DIR.glob("strata-*.json")):
        name = cfg.stem[len("strata-"):]
        try:
            c = json.loads(cfg.read_text())
        except Exception:
            continue
        log = STRATA_DIR / f"strata-{name}.log"
        args = c.get("args", [])
        out.append({
            "model": name,
            "model_name": c.get("model_name", name),
            "port": c.get("port"),
            "host": c.get("host"),
            "max_context": _arg_val(args, "--max-context"),
            "kv": _arg_val(args, "--kv"),
            "pack": _arg_val(args, "--pack"),
            "missing_paths": missing_paths(args),
            "stale": bool(missing_paths(args)),
            "log_exists": log.is_file(),
            "config_path": str(cfg),
        })
    return out


def find_config(model: str) -> str | None:
    cfg = STRATA_DIR / f"strata-{model}.json"
    return str(cfg) if cfg.is_file() else None


def read_config(model: str) -> dict:
    cfg = find_config(model)
    if not cfg:
        raise FileNotFoundError(model)
    c = json.loads(Path(cfg).read_text())
    c.pop("api_key", None)   # never ship the key to the browser
    return c


def write_config(model: str, updates: dict) -> dict:
    cfg = find_config(model)
    if not cfg:
        raise FileNotFoundError(model)
    p = Path(cfg)
    c = json.loads(p.read_text())

    # safety backup, mirroring setup.py's own .bak behaviour
    p.with_suffix(".json.bak").write_text(json.dumps(c, indent=4))

    if "port" in updates:
        c["port"] = int(updates["port"])
    if "host" in updates:
        c["host"] = updates["host"]
    args = c.get("args", [])
    if updates.get("args") is not None:
        # full args replace: validate every flag starts with --
        for a in updates["args"]:
            if not isinstance(a, str):
                raise ValueError("args must be strings")
        c["args"] = updates["args"]
        args = c["args"]
    # dropdown overrides applied LAST so they win over the raw textarea
    if updates.get("max_context"):
        _set_arg(args, "--max-context", str(int(updates["max_context"])))
    if updates.get("kv"):
        _set_arg(args, "--kv", updates["kv"])
    p.write_text(json.dumps(c, indent=4))
    out = json.loads(p.read_text())
    out.pop("api_key", None)
    return {"saved": True, "config": out}


def available_models() -> list[dict]:
    """Parse the MODELS dict out of setup.py (source of truth, no import)."""
    global _AVAILABLE_CACHE
    if _AVAILABLE_CACHE is not None:
        return _AVAILABLE_CACHE
    src = STRATA_DIR / "setup.py"
    if not src.is_file():
        return []
    try:
        tree = ast.parse(src.read_text())
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "MODELS":
                    try:
                        raw = ast.literal_eval(node.value)
                    except ValueError:
                        continue
                    out = []
                    for key, v in raw.items():
                        if not isinstance(v, dict):
                            continue
                        # real shape (setup.py 0.1.39): key = quant name,
                        # value = {about, download_gb, ram_gb, arena_gb,
                        #          families: (qwen,), ...optional flags}
                        families = list(v.get("families", ("qwen", "swift")))
                        out.append({
                            "key": key,
                            "model": key,
                            "family": families[0],
                            "families": families,
                            "about": v.get("about", ""),
                            "download_gb": v.get("download_gb"),
                            "ram_gb": v.get("ram_gb"),
                            "arena_gb": v.get("arena_gb"),
                            "experimental": bool(v.get("experimental")),
                            "vision": bool(v.get("vision")),
                        })
                    _AVAILABLE_CACHE = out
                    return out
    return []


def _arg_val(args: list[str], flag: str) -> str | None:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _set_arg(args: list[str], flag: str, value: str) -> None:
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]
