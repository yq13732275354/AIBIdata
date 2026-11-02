"""FastAPI 入口：问数 API + 会话 + 报告 + 静态前端。"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import agent, overview, report, session
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("aibi.main")

app = FastAPI(title="智能问数 Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskIn(BaseModel):
    question: str
    conversation_id: str | None = None
    want_insight: bool = True


class NewChatIn(BaseModel):
    title: str = "新会话"


@app.on_event("startup")
def _startup() -> None:
    cfg = settings()
    session.init_db()
    (cfg["root"] / "reports").mkdir(parents=True, exist_ok=True)
    log.info("数据源 %s | 锚定期间 %s", cfg["duckdb_path"], cfg["anchor_period"])


@app.get("/api/health")
def health() -> dict:
    cfg = settings()
    import os
    return {
        "ok": True,
        "duckdb": cfg["duckdb_path"].exists(),
        "anchor_period": cfg["anchor_period"],
        "model": cfg["model"],
        "llm_key": bool(os.getenv("DEEPSEEK_API_KEY")),
    }


@app.get("/api/overview")
def api_overview() -> dict:
    try:
        return overview.overview()
    except Exception as e:  # noqa: BLE001
        log.exception("overview failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/dimensions")
def api_dimensions() -> dict:
    return overview.dimensions()


@app.post("/api/chat")
def api_chat(body: AskIn) -> dict:
    cid = body.conversation_id
    conv = session.get_conversation(cid) if cid else None
    slots = conv["slots"] if conv else None
    payload = agent.ask(body.question, slots, want_insight=body.want_insight)

    if conv is None:
        cid = session.create_conversation(body.question[:30] or "新会话")
        session.add_message(cid, "user", body.question)
    else:
        session.add_message(cid, "user", body.question)
    session.add_message(cid, "assistant", payload["headline"], payload)
    if payload["type"] == "answer":
        session.update_slots(cid, payload.get("slots"))
    payload["conversation_id"] = cid
    return payload


@app.get("/api/chat/stream")
async def api_chat_stream(q: str, cid: str | None = None) -> StreamingResponse:
    conv = await run_in_threadpool(session.get_conversation, cid) if cid else None
    slots = conv["slots"] if conv else None

    async def gen():
        def send(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield send("stage", {"stage": "解析问题", "detail": q})
        payload = await run_in_threadpool(agent.ask, q, slots, True)
        if payload.get("sql"):
            yield send("sql", {"sql": payload["sql"], "source": payload["source"]})
        yield send("stage", {"stage": "生成结论"})
        nonzero = await run_in_threadpool(_persist, q, cid, payload)
        payload["conversation_id"] = nonzero
        yield send("result", payload)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _persist(q: str, cid: str | None, payload: dict) -> str:
    if not cid:
        cid = session.create_conversation(q[:30] or "新会话")
    session.add_message(cid, "user", q)
    session.add_message(cid, "assistant", payload["headline"], payload)
    if payload["type"] == "answer":
        session.update_slots(cid, payload.get("slots"))
    return cid


@app.get("/api/conversations")
def api_conversations() -> list[dict]:
    return session.list_conversations()


@app.post("/api/conversations")
def api_new_conversation(body: NewChatIn) -> dict:
    return {"conversation_id": session.create_conversation(body.title)}


@app.get("/api/conversations/{cid}")
def api_conversation(cid: str) -> dict:
    conv = session.get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@app.post("/api/report")
async def api_report() -> dict:
    try:
        return await run_in_threadpool(report.build)
    except Exception as e:  # noqa: BLE001
        log.exception("report failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/suggestions")
def api_suggestions() -> dict:
    year = settings()["anchor_year"]
    return {
        "suggestions": [
            f"{year}年总销售额和同比",
            f"{year}年各月的销售额趋势",
            "Top10 品类销售额",
            "各平台销售额占比",
            f"{year}年各月的销售额以及同比，组合图展示",
            "Top10 品类销售额占比，环形图",
            f"{year}年各平台销售额，条形图",
            "哪个国家销售额最高",
            "销量下滑最多的5个品类",
            "近6个月销量",
        ]
    }


@app.get("/api/reports")
def api_reports() -> list[dict]:
    d = settings()["root"] / "reports"
    items = []
    for f in d.glob("report_*.html"):
        st = f.stat()
        items.append({
            "name": f.stem,
            "url": f"/reports/{f.name}",
            "created_at": st.st_mtime,
            "size": st.st_size,
        })
    return sorted(items, key=lambda x: x["created_at"], reverse=True)


# ---- 静态资源：报告目录 + 原型前端 ----
_cfg = settings()
(_cfg["root"] / "reports").mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(_cfg["root"] / "reports")), name="reports")
app.mount("/", StaticFiles(directory=str(_cfg["root"] / "smartbi-prototype"), html=True), name="ui")
