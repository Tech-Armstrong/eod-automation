"""Read ALL of a day's email for the EOD, in code, so nothing can be skipped.

`read_day_emails` (a tool for the agent) does this:
1. Searches Gmail for the day's RECEIVED mail and SENT mail (every page).
2. Collects the unique conversations (threads) from both.
3. Fetches every thread completely: every message (every page), every line of each body. If a message has no
   plain text, its HTML/MIME payload is decoded instead. Quoted replies and forwarded chains inside a message are
   part of its body, so they are read too.
4. Gives each whole thread to the model with a strict "extract every work detail" prompt (several threads in
   parallel). Each thread is processed on its own, so a long day can't push threads out of the model's context.
5. Returns the notes per thread and a coverage line (threads and messages read, anything that failed).
The agent then writes the EOD from those notes.
"""
import base64
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from datetime_tools import TZ, resolve_date_range

MAX_PAGES = 20            # 20 x 100 messages per search
MAX_THREAD_CHARS = 120_000  # per thread sent to the model; beyond this the OLDEST bodies are shortened
WORKERS = 6
MAX_DAYS = 7

NOTES_PROMPT = """You read ONE complete email conversation for the user ({user}) and write notes for their EOD
(end-of-day) report covering {day}.

Read every message and every line, including quoted replies and forwarded emails inside a message
("On ... wrote:", "Forwarded message", "From: ... Sent: ... Subject:" blocks): they often hold the actual task,
figures, decisions or approvals. Ignore only signatures, legal disclaimers and text that exactly repeats something
you already read.

Messages marked [ON THE DAY] are what the report covers. Messages marked [EARLIER] are background: use them only
to explain what the work is about.

If the conversation is a newsletter, promotion, product update, automated notification, or an EOD report itself,
reply with exactly: NOT WORK

Otherwise reply in this shape, with no other text:
TOPIC: <project or theme, a few words>
NOTES:
- <one specific fact per line: what was asked or received, what the user did, replied, sent, shared, decided or
  promised, figures and amounts, products/funds/clients/files as topics, approvals, problems, deadlines>
- <as many lines as needed: be complete rather than short; a detail you leave out is lost from the report>
NEXT STEPS:
- <follow-ups or pending items stated in the conversation, or "none">

Write the notes as facts about the work. Say "the user" for {user}. Never invent anything."""


# ---------- Gmail ----------
def _executor(user_id: str):
    from auth import verified_accounts
    from composio_setup import client

    c = client()
    account = verified_accounts(user_id).get("gmail")
    extra = {"connected_account_id": account} if account else {}

    def run(slug: str, args: dict) -> dict:
        res = c.tools.execute(slug, args, user_id=user_id, dangerously_skip_version_check=True, **extra)
        data = res if isinstance(res, dict) else getattr(res, "__dict__", {})
        if data.get("successful") is False:
            raise RuntimeError(str(data.get("error") or f"{slug} failed"))
        return data.get("data") or {}

    return run


def _search(run, query: str) -> list[dict]:
    out, token = [], None
    for _ in range(MAX_PAGES):
        args = {"query": query, "max_results": 100, "ids_only": True}
        if token:
            args["page_token"] = token
        data = run("GMAIL_FETCH_EMAILS", args)
        out += data.get("messages") or []
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def _thread(run, thread_id: str) -> list[dict]:
    msgs, token = [], None
    for _ in range(MAX_PAGES):
        args = {"thread_id": thread_id}
        if token:
            args["page_token"] = token
        data = run("GMAIL_FETCH_MESSAGE_BY_THREAD_ID", args)
        msgs += data.get("messages") or []
        token = data.get("nextPageToken")
        if not token:
            break
    return sorted(msgs, key=lambda m: m.get("messageTimestamp") or "")


# ---------- message text ----------
def _b64(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return ""


# Only real HTML tags are removed: plain-text mail is full of <name@company.com> and <https://...>, which must stay.
HTML_TAGS = ("a|abbr|b|blockquote|body|br|center|code|col|colgroup|dd|div|dl|dt|em|font|h[1-6]|head|hr|html|i|img|"
             "li|link|meta|o:p|ol|p|pre|s|small|span|strong|sub|sup|table|tbody|td|tfoot|th|thead|title|tr|u|ul|wbr")
_TAG = re.compile(rf"</?(?:{HTML_TAGS})(?:\s[^<>]*)?/?>", re.I)  # name then space, '/' or '>'


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|blockquote)>", "\n", raw)
    raw = _TAG.sub(" ", raw)
    return html.unescape(raw)


def _payload_text(payload: dict | None) -> str:
    """Plain text from a Gmail MIME payload: text/plain parts, else text/html parts converted to text."""
    plain, rich = [], []

    def walk(part):
        if not isinstance(part, dict):
            return
        mime = (part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            rich.append(_html_to_text(_b64(data)))
        for p in part.get("parts") or []:
            walk(p)

    walk(payload)
    return "\n".join(plain) if any(t.strip() for t in plain) else "\n".join(rich)


def message_text(m: dict) -> str:
    text = m.get("messageText") or ""
    if len(text.strip()) < 40:  # empty or near-empty: decode the raw payload instead
        decoded = _payload_text(m.get("payload"))
        if len(decoded.strip()) > len(text.strip()):
            text = decoded
    if LOOKS_HTML.search(text):
        text = _html_to_text(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


LOOKS_HTML = re.compile(r"<(html|body|div|p|br|table)\b", re.I)


def _when(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)
    except (ValueError, AttributeError):
        return None


def format_thread(msgs: list[dict], lo: datetime, hi: datetime) -> tuple[str, dict]:
    """The whole thread as text for the model, oldest first, each message marked [ON THE DAY] or [EARLIER]."""
    blocks, on_day, sent_on_day = [], 0, 0
    for m in msgs:
        when = _when(m.get("messageTimestamp") or "")
        in_range = bool(when and lo <= when < hi)
        sent = "SENT" in (m.get("labelIds") or [])
        if "DRAFT" in (m.get("labelIds") or []):
            continue  # unsent drafts are not work that happened
        on_day += in_range
        sent_on_day += in_range and sent
        files = ", ".join(a.get("filename", "") for a in m.get("attachmentList") or [] if a.get("filename"))
        head = (f"[{'ON THE DAY' if in_range else 'EARLIER'}] "
                f"{'SENT BY THE USER' if sent else 'RECEIVED'} | {when:%d %b %Y %H:%M} IST | " if when else
                f"[{'ON THE DAY' if in_range else 'EARLIER'}] {'SENT BY THE USER' if sent else 'RECEIVED'} | ")
        head += f"From: {m.get('sender', '')} | To: {m.get('to', '')} | Subject: {m.get('subject', '')}"
        if files:
            head += f" | Attachments: {files}"
        blocks.append([head, message_text(m)])

    # Too long for one model call: shorten the OLDEST bodies first; messages from the day stay complete.
    total = sum(len(h) + len(b) for h, b in blocks)
    trimmed = 0
    for blk in blocks:
        if total <= MAX_THREAD_CHARS:
            break
        if blk[0].startswith("[EARLIER]") and len(blk[1]) > 2000:
            total -= len(blk[1]) - 2000
            blk[1] = blk[1][:2000] + "\n[... older message shortened ...]"
            trimmed += 1
    text = "\n\n----------\n\n".join(f"{h}\n\n{b}" for h, b in blocks)
    meta = {"subject": next((m.get("subject") for m in msgs if m.get("subject")), "(no subject)"),
            "messages_read": len(blocks), "messages_on_day": on_day, "user_sent": sent_on_day > 0,
            "older_shortened": trimmed}
    return text, meta


# ---------- the whole day ----------
def read_day(user_id: str, llm, start: str = "today", end: str | None = None) -> dict:
    rng = resolve_date_range.invoke({"start": start, "end": end} if end else {"start": start})
    if len(rng["days"]) > MAX_DAYS:
        return {"error": f"Pick at most {MAX_DAYS} days at a time."}
    lo, hi = datetime.fromisoformat(rng["start_local"]), datetime.fromisoformat(rng["end_local_exclusive"])
    run = _executor(user_id)

    received = _search(run, f"{rng['gmail_query']} -in:sent -in:chats -in:drafts")
    sent = _search(run, f"{rng['gmail_query']} in:sent")
    thread_ids = list(dict.fromkeys(m.get("threadId") for m in received + sent if m.get("threadId")))

    def one(tid: str) -> dict:
        last_err = None
        for _ in range(2):  # one retry for a flaky fetch or model call
            try:
                text, meta = format_thread(_thread(run, tid), lo, hi)
                if not meta["messages_on_day"]:
                    return {**meta, "skip": "nothing on the day"}
                prompt = NOTES_PROMPT.format(user=user_id, day=rng["label"])
                notes = llm.invoke([SystemMessage(prompt), HumanMessage(text)]).text.strip()
                return {**meta, "notes": notes}
            except Exception as e:
                last_err = e
        return {"thread_id": tid, "error": str(last_err)}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, thread_ids))

    work, not_work, failed = [], [], []
    for r in results:
        if "error" in r:
            failed.append(r)
        elif r.get("skip"):
            continue
        elif r["notes"].upper().startswith("NOT WORK"):
            not_work.append(r["subject"])
        else:
            work.append({k: r[k] for k in ("subject", "user_sent", "messages_read", "messages_on_day", "notes")}
                        | ({"older_messages_shortened": r["older_shortened"]} if r["older_shortened"] else {}))
    messages = sum(r.get("messages_read", 0) for r in results)
    return {
        "period": rng["label"],
        "found": {"received_messages": len(received), "sent_messages": len(sent), "threads": len(thread_ids)},
        "coverage": f"Read {len(thread_ids) - len(failed)} of {len(thread_ids)} conversations in full "
                    f"({messages} messages, including quoted and forwarded parts).",
        "work_threads": work,
        "not_work": not_work,
        "could_not_read": [f["thread_id"] for f in failed],
    }


def day_mail_tools(user_id: str, llm) -> list:
    @tool
    def read_day_emails(start: str = "today", end: str | None = None) -> dict:
        """Read ALL the email of a day (received AND sent) in full and return detailed notes per conversation.
        Use this for every EOD report (and any report on a day's work) instead of searching Gmail yourself.

        It reads every conversation completely: every message, every line, including quoted replies and forwarded
        emails inside a message. `work_threads` has the notes for each work conversation; every one of them must
        be reflected in the report. `not_work` lists skipped newsletters/notifications. `could_not_read` lists
        conversations that failed (call again once if it is not empty).

        Args:
            start: The day, e.g. 'today', 'yesterday', '24 nov'.
            end: Optional last day for a short multi-day report (at most 7 days).
        """
        try:
            return read_day(user_id, llm, start, end)
        except Exception as e:
            return {"error": f"Couldn't read the email: {e}"}

    return [read_day_emails]
