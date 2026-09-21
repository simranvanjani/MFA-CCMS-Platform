"""Case Intelligence app — FastAPI backend serving the case console UI + JSON API.

Uses on-behalf-of-user (OBO) auth for SQL + Genie so Unity Catalog masks/filters apply to
the real viewer; Vector Search + the LLM run on the app service principal.
"""
import json
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import agent
import tools

app = FastAPI(title="Consular Case Console")
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 20
CFG = Config()


def user_client(request: Request):
    """A WorkspaceClient scoped to the logged-in user (OBO), or None locally.

    Forces PAT auth so the ambient service-principal env credentials in the app
    runtime can't collide with the forwarded user token.
    """
    token = request.headers.get("x-forwarded-access-token")
    if not token:
        return None
    try:
        return WorkspaceClient(config=Config(host=CFG.host, token=token, auth_type="pat"))
    except Exception:
        return None


def user_email(request: Request):
    return (request.headers.get("X-Forwarded-Email")
            or request.headers.get("X-Forwarded-Preferred-Username") or "officer")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/chart.umd.min.js")
def chartjs():
    return FileResponse(os.path.join(HERE, "chart.umd.min.js"),
                        media_type="application/javascript")


@app.get("/api/me")
def me(request: Request):
    return {"email": user_email(request)}


@app.get("/api/filters")
def filters(request: Request):
    return tools.filter_options(user_client(request))


@app.get("/api/cases")
def cases(request: Request, page: int = 0, q: str = "", case_type: str = "",
          country: str = "", status: str = "", flag: str = "", sort: str = "newest"):
    w = user_client(request)
    f = {"q": q, "case_type": case_type, "country": country, "status": status, "flag": flag}
    f = {k: v for k, v in f.items() if v}
    cols, rows = tools.list_cases_page(page, PAGE_SIZE, w, f, sort)
    return {"total": tools.count_cases(w, f), "page": page, "page_size": PAGE_SIZE,
            "rows": [dict(zip(cols, r)) for r in rows]}


def _arr(v):
    try:
        x = json.loads(v)
        return x if isinstance(x, list) else []
    except Exception:
        return []


@app.get("/api/case")
def case(request: Request, ref: str):
    w = user_client(request)
    c = tools.get_case(ref, w)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    c["L1_Agencies"] = _arr(c.get("L1_Agencies"))
    c["L1_Situational_Flags"] = _arr(c.get("L1_Situational_Flags"))
    return {"case": c, "steps": tools.steps_with_status(c.get("Case_Type"), ref, w),
            "notes": tools.get_notes(ref, w)}


class StepIn(BaseModel):
    ref: str
    step_idx: int
    done: bool


@app.post("/api/step")
def step(s: StepIn, request: Request):
    tools.set_step(s.ref, s.step_idx, s.done, user_email(request), user_client(request))
    return {"ok": True}


class StatusIn(BaseModel):
    ref: str
    status: str


@app.post("/api/status")
def status(s: StatusIn, request: Request):
    tools.set_status(s.ref, s.status, user_email(request), user_client(request))
    return {"ok": True}


class NoteIn(BaseModel):
    ref: str
    note: str


@app.post("/api/note")
def add_note(n: NoteIn, request: Request):
    w = user_client(request)
    tools.add_note(n.ref, user_email(request), n.note, w)
    return {"ok": True, "notes": tools.get_notes(n.ref, w)}


class AskIn(BaseModel):
    question: str
    conv_id: str | None = None


@app.post("/api/ask")
def ask(a: AskIn, request: Request):
    import uuid
    w = user_client(request)
    email = user_email(request)
    conv = a.conv_id or uuid.uuid4().hex
    try:
        tools.add_chat(conv, email, "user", a.question, None, w)
    except Exception:
        pass
    try:
        route, answer = agent.answer(a.question, w, conv)
    except Exception as e:
        route, answer = "error", f"Error: {e}"
    try:
        tools.add_chat(conv, email, "assistant", answer, route, w)
    except Exception:
        pass
    return {"conv_id": conv, "route": route, "answer": answer}


@app.get("/api/chats")
def chats(request: Request):
    return {"chats": tools.chat_list(user_email(request), user_client(request))}


@app.get("/api/chat")
def chat(request: Request, conv_id: str):
    return {"messages": tools.chat_messages_for(conv_id, user_client(request))}


@app.get("/api/dashboard")
def dashboard(request: Request):
    return tools.dashboard_data(user_client(request))
