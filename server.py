"""Web UI backend: serves frontend/ and exposes the agent over a small JSON API.

    python server.py            -> open http://127.0.0.1:8000

Sign-in (see auth.py): email -> Composio's Google sign-in -> we check the Gmail address matches -> device cookie.
    GET  /api/me                                           -> {user_id} or {user_id: null}
    POST /api/session   {user_id}                          -> {status: "sign_in" | "connect", steps} or
                                                              {status: "ready", session_id, user_id}
    POST /api/connect   {user_id, toolkit}                 -> {redirect_url}   (Composio's hosted sign-in)
    GET  /api/auth/callback?state=...                      <- Composio sends the browser back here
    POST /api/logout
    POST /api/manager   {has_manager, manager_email}       -> reporting manager (Cc on EOD reports)

Agent (need the device cookie of the session's user):
    POST /api/chat      {session_id, message}              -> Turn
    POST /api/approve   {session_id, decisions}            -> Turn
    POST /api/notes     {session_id, title, summary, action_items, notes} -> {path}

A Turn is {status: "done", reply, todos} or {status: "needs_approval", approvals: [...]}.
"""
import os
import socket
import threading
from contextlib import asynccontextmanager
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

import auth
import company_holidays
import composio_setup as cs
import db
import eod
import memory
import summaries
from agent import make_agent, pending_writes
from chat import OUTPUT_DIR, append_turn, new_transcript

ROOT = Path(__file__).parent
FRONTEND = ROOT / "frontend" / "out"  # static Next.js export, built by `npm run build` in frontend/
VERIFY_TOOLKIT = "gmail"  # signing in with Google through this toolkit proves who the user is
# The address people open the app at, e.g. https://eod.yourcompany.com. Used for the sign-in return link.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

@asynccontextmanager
async def _lifespan(_app):
    """Startup: check the database, load holidays, start the summary scheduler. Shutdown: stop the scheduler."""
    connect_database()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Connected-apps agent", lifespan=_lifespan)
_checkpointer = InMemorySaver()  # chat history for all sessions, lost when the server stops
_agents: dict[str, object] = {}  # one agent per user_id (creating one opens a Composio session)
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def agent_for(user_id: str):
    with _lock:
        if user_id not in _agents:
            _agents[user_id] = make_agent(user_id, _checkpointer)
        return _agents[user_id]


def current_user(request: Request) -> str | None:
    return auth.device_user(request.cookies.get(auth.COOKIE))


def get_session(session_id: str, request: Request) -> dict:
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session expired. Reload the page to start a new one.")
    if current_user(request) != s["user_id"]:
        raise HTTPException(401, "Please sign in again.")
    return s


def app_base_url(request: Request) -> str:
    """Where Composio should send the browser back to: PUBLIC_URL, or the page's own origin when it's safe."""
    if PUBLIC_URL:
        return PUBLIC_URL
    origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
    host = (urlparse(origin).hostname or "").lower()
    allowed = {"localhost", "127.0.0.1", (request.url.hostname or "").lower()}
    if host not in allowed:
        raise HTTPException(400, "Set PUBLIC_URL in .env to the address people use to open the app.")
    return origin.rstrip("/")


def save_turn(s: dict, question: str, reply: str, todos: list) -> None:
    """Store both sides of a finished turn in MySQL, title the conversation, and persist any memory the agent saved."""
    t = db.conversations
    with db.engine().begin() as c:
        now = db.utcnow()
        if question:
            c.execute(db.messages.insert().values(conversation_id=s["id"], role="user", content=question, created_at=now))
        c.execute(db.messages.insert().values(
            conversation_id=s["id"], role="assistant", content=reply, todos=todos or None, created_at=now))
        if question and not s.get("titled"):
            c.execute(t.update().where(t.c.id == s["id"]).values(title=question[:250]))
            s["titled"] = True
    memory.save_memory(s["user_id"])


def run_turn(s: dict, payload) -> dict:
    """Invoke the agent; return either the final reply or the approvals it is waiting for."""
    result = s["agent"].invoke(payload, s["config"])
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        s["pending"] = [i.id for i in interrupts]
        approvals = [
            {
                "interrupt_id": i.id,
                "index": n,
                "tool": r["name"],
                "description": r.get("description") or "",
                # The individual app actions (e.g. GMAIL_SEND_EMAIL + its arguments) for a readable card.
                "actions": eod.preview(s["user_id"], r.get("args") or {}) if r["name"] == eod.TOOL_NAME
                else pending_writes({"name": r["name"], "args": r.get("args") or {}}),
            }
            for i in interrupts
            for n, r in enumerate(i.value["action_requests"])
        ]
        return {"status": "needs_approval", "approvals": approvals}
    s["pending"] = []
    reply = result["messages"][-1].text
    todos = result.get("todos") or []
    question = s.pop("question", "")
    append_turn(s["transcript"], question, reply, todos)  # markdown copy in outputs/
    save_turn(s, question, reply, todos)
    return {"status": "done", "reply": reply, "todos": todos}


class NewSession(BaseModel):
    user_id: str
    role: str | None = None  # one of db.ROLES; saved once the device is verified as this user
    has_manager: bool | None = None  # "Do you have a reporting manager?" (Cc on EOD reports)
    manager_email: str | None = None


class ManagerIn(BaseModel):
    has_manager: bool
    manager_email: str | None = None


class ChatIn(BaseModel):
    session_id: str
    message: str


class Decision(BaseModel):
    interrupt_id: str
    index: int
    approve: bool
    feedback: str = ""


class ApproveIn(BaseModel):
    session_id: str
    decisions: list[Decision]


class ActionItem(BaseModel):
    text: str
    done: bool = False


class NotesIn(BaseModel):
    session_id: str
    title: str = ""
    summary: str = ""
    action_items: list[ActionItem | str] = []  # plain strings are accepted as open items
    notes: str = ""


class ConnectIn(BaseModel):
    user_id: str
    toolkit: str
    role: str | None = None
    has_manager: bool | None = None
    manager_email: str | None = None


def _manager_answer(user_id: str, has_manager: bool | None, manager_email: str | None) -> dict | None:
    """Validated sign-in answer about the reporting manager, or None if it wasn't given."""
    if has_manager is None:
        return None
    try:
        has, email = auth.clean_manager(user_id, has_manager, manager_email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"has_manager": has, "manager_email": email}


def _eod_info(user_id: str) -> dict:
    m = auth.get_manager(user_id)
    return {**m, "eod_to": eod.DEFAULT_RECIPIENTS}


@app.get("/api/me")
async def me(request: Request):
    user_id = current_user(request)
    role = await run_in_threadpool(auth.get_role, user_id) if user_id else None
    info = await run_in_threadpool(_eod_info, user_id) if user_id else {"eod_to": eod.DEFAULT_RECIPIENTS}
    return {"user_id": user_id, "role": role, "roles": db.ROLES, **info}


@app.post("/api/manager")
async def set_manager(body: ManagerIn, request: Request):
    """Change the signed-in user's reporting manager (Cc on their EOD reports)."""
    user_id = current_user(request)
    if not user_id:
        raise HTTPException(401, "Please sign in again.")
    try:
        await run_in_threadpool(auth.set_manager, user_id, body.has_manager, body.manager_email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await run_in_threadpool(_eod_info, user_id)


@app.post("/api/session")
async def create_session(body: NewSession, request: Request):
    user_id = auth.normalize_email(body.user_id)
    if not user_id:
        raise HTTPException(400, "Enter a valid email address.")
    try:
        steps = await run_in_threadpool(cs.toolkit_status, user_id)
    except Exception as e:  # bad API key, network, etc.
        raise HTTPException(500, f"Could not reach Composio: {e}") from e

    if body.role and not auth.valid_role(body.role):
        raise HTTPException(400, f"Choose a role: {', '.join(db.ROLES)}.")
    manager = _manager_answer(user_id, body.has_manager, body.manager_email)
    if current_user(request) != user_id:  # this device hasn't proven it's this person yet
        return {"status": "sign_in", "user_id": user_id, "steps": steps}
    if body.role:  # verified device: the role they picked is theirs to set
        await run_in_threadpool(auth.set_role, user_id, body.role)
    if manager:
        await run_in_threadpool(auth.set_manager, user_id, manager["has_manager"], manager["manager_email"])
    role = await run_in_threadpool(auth.get_role, user_id)
    if not role:  # signed up before roles existed
        return {"status": "choose_role", "user_id": user_id, "roles": db.ROLES}
    info = await run_in_threadpool(_eod_info, user_id)
    if info["has_manager"] is None:  # signed up before the reporting-manager question existed
        return {"status": "choose_manager", "user_id": user_id, "eod_to": info["eod_to"]}
    if not all(s["connected"] for s in steps):
        return {"status": "connect", "user_id": user_id, "steps": steps}

    try:
        agent = await run_in_threadpool(agent_for, user_id)
    except Exception as e:
        raise HTTPException(500, f"Could not start the agent: {e}") from e
    session_id = uuid.uuid4().hex
    def start_conversation():
        with db.engine().begin() as c:
            db.ensure_user(c, user_id)
            c.execute(db.conversations.insert().values(id=session_id, user_id=user_id, started_at=db.utcnow()))

    await run_in_threadpool(start_conversation)
    _sessions[session_id] = {
        "id": session_id,
        "user_id": user_id,
        "agent": agent,
        "config": {"configurable": {"thread_id": session_id}},
        "transcript": new_transcript(user_id),
        "pending": [],
    }
    return {"status": "ready", "session_id": session_id, "user_id": user_id, "role": role, **info}


@app.post("/api/connect")
async def connect(body: ConnectIn, request: Request):
    """Start Composio's hosted sign-in. Gmail doubles as identity check; other apps need a signed-in device."""
    user_id = auth.normalize_email(body.user_id)
    toolkit = body.toolkit.strip().lower()
    if not user_id:
        raise HTTPException(400, "Enter a valid email address.")
    if toolkit not in [t.lower() for t in cs.TOOLKITS]:
        raise HTTPException(400, "Unknown app.")
    if toolkit != VERIFY_TOOLKIT and current_user(request) != user_id:
        raise HTTPException(401, "Sign in with Google first.")
    state = auth.new_pending(user_id, toolkit, body.role, _manager_answer(user_id, body.has_manager, body.manager_email))
    callback = f"{app_base_url(request)}/api/auth/callback?state={state}"
    try:
        redirect_url, account_id = await run_in_threadpool(cs.start_link, user_id, toolkit, callback)
    except Exception as e:
        raise HTTPException(500, f"Could not start sign-in: {e}") from e
    auth.set_pending_account(state, account_id)
    return {"redirect_url": redirect_url}


def _finish_sign_in(p: dict, device_user: str | None) -> tuple[str, str | None]:
    """Runs after Composio redirects back. Returns (where to send the browser, new device token or None)."""
    user_id, toolkit, account_id = p["user_id"], p["toolkit"], p["account_id"]
    if not account_id:
        return "/?auth_error=expired", None
    status = ""
    for _ in range(10):  # the connection can take a moment to turn ACTIVE
        status = cs.account_status(account_id)
        if status == "ACTIVE":
            break
        time.sleep(1)
    if status != "ACTIVE":
        return "/?auth_error=not_connected", None

    token = None
    if toolkit == VERIFY_TOOLKIT:
        address = (cs.gmail_address(user_id, account_id) or "").lower()
        if address != user_id:
            cs.delete_account(account_id)  # never keep someone else's mailbox under this id
            return f"/?auth_error=mismatch&got={quote(address or 'unknown')}", None
        token = auth.issue_device(user_id)
        if p.get("role"):  # chosen on the first screen; saved now that we know the email is theirs
            auth.set_role(user_id, p["role"])
        if p.get("manager"):  # reporting manager answer from the first screen, same rule
            auth.set_manager(user_id, p["manager"]["has_manager"], p["manager"]["manager_email"])
    elif device_user != user_id:
        cs.delete_account(account_id)
        return "/?auth_error=expired", None

    auth.set_verified_account(user_id, toolkit, account_id)
    cs.remove_older_accounts(user_id, toolkit, keep_id=account_id)
    with _lock:
        _agents.pop(user_id, None)  # rebuild with the fresh connection on next use
    return f"/?connected={quote(toolkit)}", token


@app.get("/api/auth/callback")
async def auth_callback(request: Request, state: str = ""):
    p = auth.take_pending(state)
    if not p:
        return RedirectResponse("/?auth_error=expired", status_code=303)
    try:
        location, token = await run_in_threadpool(_finish_sign_in, p, current_user(request))
    except Exception:
        return RedirectResponse("/?auth_error=failed", status_code=303)
    resp = RedirectResponse(location, status_code=303)
    if token:
        secure = request.url.scheme == "https" or PUBLIC_URL.startswith("https://")
        resp.set_cookie(auth.COOKIE, token, max_age=auth.COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=secure)
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    auth.revoke_device(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


class LeaveIn(BaseModel):
    date: str  # YYYY-MM-DD
    end: str | None = None  # optional last day of a range (YYYY-MM-DD)
    on: bool = True


def _iso(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(400, "Dates must be YYYY-MM-DD.") from e


@app.get("/api/leave")
async def list_leave(request: Request, start: str, end: str):
    """Leave days of the signed-in user between start and end (YYYY-MM-DD), as YYYY-MM-DD."""
    user_id = current_user(request)
    if not user_id:
        raise HTTPException(401, "Please sign in again.")
    days = await run_in_threadpool(summaries.get_leave, user_id, _iso(start), _iso(end))
    return {"leave": [d.isoformat() for d in days]}


@app.post("/api/leave")
async def toggle_leave(body: LeaveIn, request: Request):
    """Mark or unmark leave for one day or a range (only for the signed-in user)."""
    user_id = current_user(request)
    if not user_id:
        raise HTTPException(401, "Please sign in again.")
    try:
        changed = await run_in_threadpool(
            summaries.set_leave, user_id, _iso(body.date), _iso(body.end) if body.end else None, body.on)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"changed": [d.isoformat() for d in changed], "on": body.on}


HR_ROLE = "Human Resources"


@app.get("/api/holidays")
async def list_holidays(request: Request, start: str, end: str):
    """Company holidays between start and end (YYYY-MM-DD). Any signed-in user."""
    if not current_user(request):
        raise HTTPException(401, "Please sign in again.")
    items = await run_in_threadpool(company_holidays.get, _iso(start), _iso(end))
    return {"holidays": [{"date": h["date"].isoformat(), "name": h["name"]} for h in items]}


@app.post("/api/holidays/upload")
async def upload_holidays(request: Request, file: UploadFile = File(...)):
    """HR only: read a holiday list (PDF, Excel or CSV), replace those years' holidays, and keep the file."""
    user_id = current_user(request)
    if not user_id:
        raise HTTPException(401, "Please sign in again.")
    if await run_in_threadpool(auth.get_role, user_id) != HR_ROLE:
        raise HTTPException(403, "Only Human Resources can upload the holiday list.")
    name = company_holidays.safe_filename(file.filename or "holidays")
    if Path(name).suffix not in company_holidays.ALLOWED:
        raise HTTPException(400, "Use a PDF, Excel (.xlsx) or CSV file.")
    data = await file.read(company_holidays.MAX_BYTES + 1)
    try:
        items = await run_in_threadpool(company_holidays.parse_file, name, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # unreadable/corrupt file
        raise HTTPException(400, f"Couldn't read that file: {e}") from e

    company_holidays.FOLDER.mkdir(exist_ok=True)
    stored = company_holidays.FOLDER / f"{datetime.now():%Y%m%d-%H%M%S} {name}"
    stored.write_bytes(data)  # newest file wins for its years when the folder is loaded again
    result = await run_in_threadpool(company_holidays.save, items, stored.name, user_id)
    return {
        "count": result["count"],
        "years": result["years"],
        "file": f"holidays/{stored.name}",
        "holidays": [{"date": d.isoformat(), "name": n} for d, n in items],
    }


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    s = get_session(body.session_id, request)
    if s["pending"]:
        raise HTTPException(409, "Answer the approval request first.")
    if not body.message.strip():
        raise HTTPException(400, "Message is empty.")
    s["question"] = body.message.strip()
    try:
        return await run_in_threadpool(run_turn, s, {"messages": [HumanMessage(s["question"])]})
    except Exception as e:
        raise HTTPException(500, f"Agent error: {e}") from e


@app.post("/api/approve")
async def approve(body: ApproveIn, request: Request):
    s = get_session(body.session_id, request)
    if not s["pending"]:
        raise HTTPException(409, "Nothing is waiting for approval.")
    resume: dict[str, dict] = {i: {"decisions": []} for i in s["pending"]}
    for d in sorted(body.decisions, key=lambda d: d.index):
        if d.interrupt_id not in resume:
            raise HTTPException(400, "Unknown approval request.")
        decision = (
            {"type": "approve"}
            if d.approve
            else {"type": "reject", "message": d.feedback.strip() or "The user declined this action. Do not retry it."}
        )
        resume[d.interrupt_id]["decisions"].append(decision)
    try:
        return await run_in_threadpool(run_turn, s, Command(resume=resume))
    except Exception as e:
        raise HTTPException(500, f"Agent error: {e}") from e


@app.post("/api/notes")
async def save_notes(body: NotesIn, request: Request):
    s = get_session(body.session_id, request)
    now = datetime.now()
    title = body.title.strip() or "Email summary"
    parts = [f"# {title}\n\n_Saved {now:%Y-%m-%d %H:%M} by {s['user_id']}_\n"]
    if body.summary.strip():
        parts.append(f"\n## Summary\n\n{body.summary.strip()}\n")
    items = [i if isinstance(i, ActionItem) else ActionItem(text=i) for i in body.action_items]
    items = [i for i in items if i.text.strip()]
    if items:
        lines = "\n".join(f"- [{'x' if i.done else ' '}] {i.text.strip()}" for i in items)
        parts.append(f"\n## To-dos\n\n{lines}\n")
    if body.notes.strip():
        parts.append(f"\n## My notes\n\n{body.notes.strip()}\n")
    OUTPUT_DIR.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())[:40].strip("-") or "notes"
    path = OUTPUT_DIR / f"notes-{now:%Y%m%d-%H%M%S}-{safe}.md"
    path.write_text("".join(parts), encoding="utf-8")

    def store():
        with db.engine().begin() as c:
            c.execute(db.notes.insert().values(
                user_id=s["user_id"],
                conversation_id=s["id"],
                title=title[:255],
                summary=body.summary.strip() or None,
                action_items=[{"text": i.text.strip(), "done": i.done} for i in items] or None,
                notes=body.notes.strip() or None,
                created_at=db.utcnow(),
            ))

    await run_in_threadpool(store)
    return {"path": f"outputs/{path.name}"}


@app.get("/")
async def index():
    page = FRONTEND / "index.html"
    if not page.exists():
        return HTMLResponse(
            "<h2>The web app isn't built yet.</h2><p>Run <code>cd frontend && npm install && npm run build</code>, "
            "then reload this page.</p>",
            status_code=503,
        )
    return FileResponse(page)


if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")


def connect_database():
    """Fail fast with a clear message if the database isn't reachable; import pre-database JSON files once."""
    print(f"Database: {db.check()}")
    if moved := auth.import_legacy_files(ROOT / "data"):
        print(f"Imported {moved} sign-in records from data/*.json into the database.")
    for line in company_holidays.import_folder():
        print(f"Holidays: {line}")


def _summary_scheduler(stop: threading.Event) -> None:
    """Every 30 minutes: nightly daily summaries and month-end consolidation (see summaries.py)."""
    stop.wait(60)  # let the server finish starting
    while not stop.is_set():
        try:
            done = summaries.run_due_jobs(agent_for)
            if done.get("daily") or done.get("monthly") or done.get("errors"):
                print(f"Summaries: {done}")
        except Exception as e:  # never let the scheduler die
            print(f"Summaries job failed: {e}")
        stop.wait(30 * 60)


_stop_scheduler = threading.Event()


def start_scheduler():
    if os.getenv("SUMMARY_SCHEDULER", "on").lower() not in {"0", "false", "no", "off"}:
        threading.Thread(target=_summary_scheduler, args=(_stop_scheduler,), daemon=True, name="summaries").start()


def stop_scheduler():
    _stop_scheduler.set()


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    # HOST=0.0.0.0 lets other devices on your network open the app (set PUBLIC_URL to the address they use).
    host = os.getenv("HOST", "127.0.0.1")
    if _port_in_use(port):
        print(f"Port {port} is already in use, so this copy won't start.\n"
              f"If you ran `npm run dev` in frontend/, it already started the server: just open "
              f"http://localhost:3000.\nOtherwise another program is using the port: stop it, or run with "
              f"a different PORT.")
        raise SystemExit(0)
    print(f"Open {PUBLIC_URL or f'http://127.0.0.1:{port}'} in your browser. Ctrl+C to stop.")
    uvicorn.run(app, host=host, port=port)
