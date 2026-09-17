"""Case Intelligence app — FastAPI backend serving the case console UI + JSON API."""
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import agent
import tools

app = FastAPI(title="Consular Case Console")
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 20


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/api/me")
def me(request: Request):
    return {"email": (request.headers.get("X-Forwarded-Email")
                      or request.headers.get("X-Forwarded-Preferred-Username") or "officer")}


@app.get("/api/cases")
def cases(page: int = 0):
    cols, rows = tools.list_cases_page(page, PAGE_SIZE)
    return {"total": tools.count_cases(), "page": page, "page_size": PAGE_SIZE,
            "rows": [dict(zip(cols, r)) for r in rows]}


def _arr(v):
    try:
        x = json.loads(v)
        return x if isinstance(x, list) else []
    except Exception:
        return []


@app.get("/api/case")
def case(ref: str):
    c = tools.get_case(ref)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    c["L1_Agencies"] = _arr(c.get("L1_Agencies"))
    c["L1_Situational_Flags"] = _arr(c.get("L1_Situational_Flags"))
    return {"case": c, "steps": tools.recommended_steps_list(c.get("Case_Type")),
            "notes": tools.get_notes(ref)}


class NoteIn(BaseModel):
    ref: str
    note: str


@app.post("/api/note")
def add_note(n: NoteIn, request: Request):
    author = (request.headers.get("X-Forwarded-Email")
              or request.headers.get("X-Forwarded-Preferred-Username") or "officer")
    tools.add_note(n.ref, author, n.note)
    return {"ok": True, "notes": tools.get_notes(n.ref)}


class AskIn(BaseModel):
    question: str


@app.post("/api/ask")
def ask(a: AskIn):
    try:
        route, answer = agent.answer(a.question)
    except Exception as e:
        return {"route": "error", "answer": f"Error: {e}"}
    return {"route": route, "answer": answer}


@app.get("/api/dashboard")
def dashboard():
    return tools.dashboard_data()
