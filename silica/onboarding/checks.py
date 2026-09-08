# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""`silica doctor` — the state of this install and this root, as data.
No model, no endpoint: the core needs neither. Each check is (name, status,
detail, hint) with status ok | warn | fail."""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass
class CheckResult:
    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str
    hint: str = ""


def check_root(config: Any) -> CheckResult:
    root = Path(getattr(config, "vault_path", "") or "")
    if not root.is_dir():
        return CheckResult("root", "fail", f"{root or '(unset)'} is not a folder", "run inside the folder to serve, or pass --vault DIR")
    return CheckResult("root", "ok", str(root))


def check_index(config: Any) -> CheckResult:
    from silica.core import index_state
    try:
        st = index_state()
    except Exception as e:  # a broken index dir is a finding, not a crash
        return CheckResult("index", "fail", f"unreadable ({e})", "run `silica index --rebuild`")
    if st["state"] == "cold":
        return CheckResult("index", "warn", "never built", "run `silica index`; searches build it on first use")
    if st["state"] == "stale":
        return CheckResult("index", "warn", f"{st['docs']} docs, files changed since", "the next search refreshes it; `silica index` does it now")
    return CheckResult("index", "ok", f"{st['docs']} docs, current")


def check_extras(config: Any) -> CheckResult:
    missing = [name for name, mod in (("mcp", "mcp"), ("connect", "websockets")) if importlib.util.find_spec(mod) is None]
    if missing:
        return CheckResult("extras", "warn", "not installed: " + ", ".join(missing),
                           "pip install 'silica-harness[" + ",".join(missing) + "]'")
    return CheckResult("extras", "ok", "mcp, connect")


def check_converters(config: Any) -> CheckResult:
    lanes = ["pdf (pdfium)", "docx", "epub", "rtf", "xls", "odf"]
    if shutil.which("mineru"):
        lanes.append("mineru (OCR, images, pptx/xlsx)")
    if shutil.which("ffmpeg"):
        lanes.append("ffmpeg (audio/video)")
    return CheckResult("converters", "ok", ", ".join(lanes),
                       "" if shutil.which("mineru") else "optional: install mineru for scanned PDFs and images")


def check_quarantine(config: Any) -> CheckResult:
    from silica.kernel.recall.paths import index_dir
    q = index_dir() / "quarantine"
    n = sum(1 for _ in q.glob("*")) if q.is_dir() else 0
    if n:
        return CheckResult("quarantine", "warn", f"{n} unreadable index file(s) set aside", "run `silica index --rebuild`")
    return CheckResult("quarantine", "ok", "none")


def check_embeddings(config: Any) -> CheckResult:
    from silica import embeddings
    if not embeddings.enabled():
        return CheckResult("embeddings", "ok", "off (no SILICA_EMBEDDING_BASE_URL); search is lexical")
    n = len(embeddings.load_store().get("vectors", {}))
    return CheckResult("embeddings", "ok" if n else "warn", f"{config.embedding_model} @ {config.embedding_base_url}, {n} vectors",
                       "" if n else "run `silica index --embed`")


CHECKS = (check_root, check_index, check_embeddings, check_extras, check_converters, check_quarantine)


def run_checks(config: Any) -> list[CheckResult]:
    out = []
    for check in CHECKS:
        try:
            out.append(check(config))
        except Exception as e:
            out.append(CheckResult(check.__name__.removeprefix("check_"), "fail", f"check crashed: {e}"))
    return out


def verdict(results: list[CheckResult]) -> Literal["ok", "hold", "fail"]:
    if any(r.status == "fail" for r in results):
        return "fail"
    return "hold" if any(r.status == "warn" for r in results) else "ok"


def exit_code(results: list[CheckResult]) -> int:
    return 1 if verdict(results) == "fail" else 0


def report_payload(results: list[CheckResult]) -> dict:
    return {"results": [asdict(r) for r in results], "verdict": verdict(results)}


def render_report(results: list[CheckResult]) -> None:
    glyph = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    for r in results:
        line = f"  {glyph[r.status]}  {r.name:12s} {r.detail}"
        if r.hint:
            line += f"\n                      -> {r.hint}"
        print(line)
    print(f"\n  verdict: {verdict(results)}")
