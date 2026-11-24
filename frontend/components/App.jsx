"use client";
import { useCallback, useEffect, useRef, useState } from "react";

import { buildUpdateRequest } from "./AddMore";
import Confirm from "./Confirm";
import Icon from "./Icon";
import { Ask, Notice, Reply, Welcome, Working } from "./Messages";
import NotesPanel from "./NotesPanel";
import Rail from "./Rail";
import SignIn, { EMPTY_MANAGER, ROLES } from "./SignIn";
import { api } from "@/lib/api";
import { autoGrow, clockTime, formatDate, fromIso, isoDate, storage, toPlain, uid } from "@/lib/utils";

const DEFAULT_SUB = "India time · dates without a year mean this year";
const EMPTY_NOTES = { title: "", summary: "", todos: [], extra: "" };
// The "Write today's EOD" button: what the chat shows, and what the assistant receives.
const EOD_DISPLAY = "Write a detailed EOD report from the emails I received and sent today.";
const EOD_PROMPT =
  "Write an in-detail EOD report for today from both the emails I received and the emails I sent today. Read every " +
  "thread in full, including every earlier message quoted or forwarded inside an email, so no important detail is " +
  "left out. Use my usual EOD format and send it to me for review with send_eod_report.";

function readStoredManager() {
  try { return { ...EMPTY_MANAGER, ...JSON.parse(storage.get("manager") || "{}") }; } catch { return EMPTY_MANAGER; }
}

export default function App() {
  // session
  const [userId, setUserId] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [signingIn, setSigningIn] = useState(false);
  const [signinError, setSigninError] = useState("");
  const [signinStep, setSigninStep] = useState("loading"); // loading | email | verify | connect | role
  const [steps, setSteps] = useState([]);
  const [role, setRole] = useState(() => storage.get("role") || "");
  const [roles, setRoles] = useState(ROLES);
  const [manager, setManager] = useState(readStoredManager); // { has_manager, manager_email }; Cc on EOD reports
  const [eodTo, setEodTo] = useState([]); // fixed EOD recipients (from the server)

  // conversation
  const [items, setItems] = useState([]); // { id, kind: "ask" | "reply" | "confirm" | "error", ... }
  const [busy, setBusy] = useState(false);
  const [topic, setTopic] = useState({ title: "New conversation", sub: DEFAULT_SUB });
  const [activeDay, setActiveDay] = useState(null);
  const [text, setText] = useState("");

  // panels
  const [railOpen, setRailOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [notes, setNotes] = useState(EMPTY_NOTES);
  const [notesFocus, setNotesFocus] = useState(0);
  const [toastMsg, setToastMsg] = useState("");
  const [leaveDays, setLeaveDays] = useState(() => new Set()); // YYYY-MM-DD strings
  const [holidays, setHolidays] = useState(() => new Map()); // YYYY-MM-DD -> name (company-wide)

  const sessionRef = useRef(null);
  const busyRef = useRef(false);
  const threadRef = useRef(null);
  const inputRef = useRef(null);
  const toastTimer = useRef(0);

  const toast = useCallback((m) => {
    setToastMsg(m);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastMsg(""), 2400);
  }, []);

  const push = (item) => setItems((all) => [...all, { id: uid(), ...item }]);

  // ---------- sign in ----------
  /** Ask the server where this email stands and move to the matching step. Returns true when the app is ready. */
  const signIn = useCallback(async (email, { keepError = false, role: chosen, manager: mgr } = {}) => {
    setSigningIn(true);
    if (!keepError) setSigninError("");
    try {
      const res = await api.startSession(email, chosen, mgr);
      setUserId(res.user_id);
      storage.set("userId", res.user_id);
      if (res.eod_to) setEodTo(res.eod_to);
      if (res.status === "ready") {
        sessionRef.current = res.session_id;
        setSessionId(res.session_id);
        if (res.role) setRole(res.role);
        setManager({ has_manager: res.has_manager, manager_email: res.manager_email || "" });
        storage.remove("role"); // saved on the server now
        storage.remove("manager");
        return true;
      }
      if (res.status === "choose_role") {
        if (res.roles?.length) setRoles(res.roles);
        setSigninStep("role");
        return false;
      }
      if (res.status === "choose_manager") {
        setSigninStep("manager");
        return false;
      }
      setSteps(res.steps || []);
      setSigninStep(res.status === "sign_in" ? "verify" : "connect");
    } catch (e) {
      setSigninError(e.message);
      setSigninStep("email");
    } finally {
      setSigningIn(false);
    }
    return false;
  }, []);

  async function connectApp(toolkit) {
    setSigningIn(true);
    setSigninError("");
    try {
      // the role rides along and is saved once Google sign-in proves the email is theirs
      const { redirect_url } = await api.connect(userId, toolkit, role, manager);
      window.location.assign(redirect_url); // Composio's hosted sign-in; it sends the browser back to us
    } catch (e) {
      setSigninError(e.message);
      setSigningIn(false);
    }
  }

  // On load: read the result of a sign-in redirect, then resume with this device's account.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get("auth_error");
    if (params.toString()) window.history.replaceState(null, "", window.location.pathname);
    const typed = storage.get("userId") || "";
    if (err) {
      setSigninError(
        {
          mismatch: `You signed in to Google as ${params.get("got") || "a different account"}, which doesn’t match ${typed || "the email you entered"}. Sign in with the Google account for that address.`,
          expired: "That sign-in link expired. Please try again.",
          not_connected: "Google sign-in wasn’t completed. Please try again.",
          failed: "Something went wrong while signing you in. Please try again.",
        }[err] || "Sign-in didn’t work. Please try again.",
      );
    }
    (async () => {
      let me = null;
      try {
        const info = await api.me();
        me = info.user_id;
        if (info.roles?.length) setRoles(info.roles);
        if (info.role) setRole(info.role);
        if (info.eod_to) setEodTo(info.eod_to);
      } catch { /* server down: show the email step */ }
      const email = me || typed;
      setUserId(email);
      if (email && (me || err)) await signIn(email, { keepError: Boolean(err) });
      else setSigninStep("email");
    })();
  }, [signIn]);

  async function switchUser() {
    try { await api.logout(); } catch { /* signed out locally anyway */ }
    storage.remove("userId");
    storage.remove("role");
    storage.remove("manager");
    window.location.assign("/");
  }

  async function newChat() {
    if (busyRef.current) return;
    try {
      const res = await api.startSession(userId);
      if (res.status !== "ready") { window.location.assign("/"); return; }
      sessionRef.current = res.session_id;
      setSessionId(res.session_id);
    } catch (e) {
      toast(e.message);
      return;
    }
    setItems([]);
    setActiveDay(null);
    setTopic({ title: "New conversation", sub: DEFAULT_SUB });
    setRailOpen(false);
  }

  // ---------- turns ----------
  const handleTurn = (turn) => {
    if (turn.status === "needs_approval") push({ kind: "confirm", approvals: turn.approvals });
    else push({ kind: "reply", reply: turn.reply, todos: turn.todos || [], time: clockTime() });
  };

  async function run(call) {
    busyRef.current = true;
    setBusy(true);
    try {
      handleTurn(await call());
    } catch (e) {
      push({ kind: "error", message: e.message });
    } finally {
      busyRef.current = false;
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  /** `display` is what appears in the chat; `message` is what the assistant receives (they differ for shortcuts). */
  function send(message, display = message) {
    const m = message.trim();
    if (!m || busyRef.current || !sessionRef.current) return;
    push({ kind: "ask", text: display.trim() });
    run(() => api.chat(sessionRef.current, m));
  }

  function addMoreToDraft(points) {
    send(buildUpdateRequest(points), `Add to the email:\n${points.map((p) => `• ${p}`).join("\n")}`);
  }

  function writeEod() {
    setTopic({ title: "Today’s EOD", sub: formatDate(new Date(), { day: "numeric", month: "long", year: "numeric" }) });
    setActiveDay(null);
    send(EOD_PROMPT, EOD_DISPLAY);
  }

  async function saveManager(next) {
    const res = await api.setManager(next); // throws with a readable message
    setManager({ has_manager: res.has_manager, manager_email: res.manager_email || "" });
    toast(res.has_manager ? `${res.manager_email} will be in Cc on your EODs` : "No manager in Cc");
  }

  const approve = useCallback((decisions) => {
    run(() => api.approve(sessionRef.current, decisions));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function summarizeDay(iso, from) {
    const d = fromIso(iso);
    const long = formatDate(d, { day: "numeric", month: "long", year: "numeric" });
    setTopic({
      title: `Emails · ${formatDate(d, { weekday: "short", day: "numeric", month: "short" })}`,
      sub: from ? `From ${from}` : long,
    });
    setActiveDay(iso);
    setRailOpen(false);
    send(`Summarize the emails I received${from ? ` from ${from}` : ""} on ${long}.`);
  }

  // ---------- notes ----------
  function addToNotes(markdown) {
    const reply = toPlain(markdown);
    setNotes((n) => ({
      ...n,
      title: n.title.trim() ? n.title : topic.title === "New conversation" ? "Email notes" : topic.title,
      summary: n.summary.trim() && n.summary.trim() !== reply ? `${n.summary.trim()}\n\n---\n\n${reply}` : reply,
    }));
    setNotesOpen(true);
    setNotesFocus((k) => k + 1);
  }

  function copy(reply) {
    navigator.clipboard.writeText(reply).then(() => toast("Copied"), () => toast("Couldn't copy"));
  }

  // ---------- leave ----------
  // Load leave days around today once signed in (past 6 months, next 6 months).
  useEffect(() => {
    if (!sessionId) return;
    const now = new Date();
    const start = isoDate(new Date(now.getFullYear(), now.getMonth() - 6, 1));
    const end = isoDate(new Date(now.getFullYear(), now.getMonth() + 7, 0));
    api.leaveList(start, end).then((r) => setLeaveDays(new Set(r.leave))).catch(() => {});
    loadHolidays();
  }, [sessionId]);

  function loadHolidays() {
    const now = new Date();
    const start = isoDate(new Date(now.getFullYear() - 1, 0, 1));
    const end = isoDate(new Date(now.getFullYear() + 1, 11, 31));
    api.holidays(start, end)
      .then((r) => setHolidays(new Map(r.holidays.map((h) => [h.date, h.name]))))
      .catch(() => {});
  }

  async function uploadHolidays(file) {
    const res = await api.uploadHolidays(file); // throws with a readable message
    loadHolidays();
    toast(`Loaded ${res.count} holidays for ${res.years.join(", ")}`);
    return res;
  }

  async function toggleLeave(iso, on) {
    setLeaveDays((prev) => {
      const next = new Set(prev);
      if (on) next.add(iso); else next.delete(iso);
      return next;
    });
    try {
      await api.setLeave(iso, on);
      const label = formatDate(fromIso(iso), { day: "numeric", month: "short" });
      toast(on ? `${label} marked as leave` : `${label} is no longer leave`);
    } catch (e) {
      setLeaveDays((prev) => {  // undo on failure
        const next = new Set(prev);
        if (on) next.delete(iso); else next.add(iso);
        return next;
      });
      toast(e.message);
    }
  }

  // ---------- effects ----------
  useEffect(() => {
    document.body.classList.toggle("notes-open", notesOpen);
    document.body.classList.toggle("rail-open", railOpen);
  }, [notesOpen, railOpen]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setNotesOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const t = threadRef.current;
    if (t) t.scrollTo({ top: t.scrollHeight, behavior: "smooth" });
  }, [items, busy]);

  useEffect(() => autoGrow(inputRef.current), [text]);

  useEffect(() => {
    if (sessionId) inputRef.current?.focus();
  }, [sessionId]);

  // ---------- render ----------
  if (!sessionId) {
    return (
      <>
        <SignIn
          step={signinStep}
          email={userId}
          steps={steps}
          busy={signingIn}
          error={signinError}
          role={role}
          roles={roles}
          manager={manager}
          eodTo={eodTo}
          onEmail={(email, chosen, mgr) => {
            setRole(chosen);
            setManager(mgr);
            storage.set("role", chosen); // survives the trip to Google and back
            storage.set("manager", JSON.stringify(mgr));
            signIn(email, { role: chosen, manager: mgr });
          }}
          onManager={(mgr) => {
            setManager(mgr);
            signIn(userId, { manager: mgr });
          }}
          onRole={(chosen) => {
            setRole(chosen);
            signIn(userId, { role: chosen });
          }}
          onConnect={connectApp}
          onBack={switchUser}
        />
        {toastMsg && <div className="toast" role="status">{toastMsg}</div>}
      </>
    );
  }

  const openTodos = notes.todos.filter((t) => !t.done).length;
  // "More to say?" only on the latest reply, so the update always applies to the draft just written.
  const lastReplyId = [...items].reverse().find((it) => it.kind === "reply")?.id;

  return (
    <>
      <div className="shell">
        <Rail
          userId={userId}
          role={role}
          activeDay={activeDay}
          leaveDays={leaveDays}
          onToggleLeave={toggleLeave}
          holidays={holidays}
          onUploadHolidays={uploadHolidays}
          manager={manager}
          eodTo={eodTo}
          onSetManager={saveManager}
          busy={busy}
          onNewChat={newChat}
          onSummarize={summarizeDay}
          onSwitchUser={switchUser}
        />
        <div className="scrim" onClick={() => setRailOpen(false)} />

        <main className="stage">
          <header className="stage-head">
            <button className="icon-btn only-narrow" type="button" aria-label="Open days" onClick={() => setRailOpen(true)}>
              <Icon name="menu" />
            </button>
            <div className="stage-title">
              <span id="stageTitle">{topic.title}</span>
              <span className="stage-sub">{topic.sub}</span>
            </div>
            <button className="btn btn-quiet btn-sm" type="button" onClick={() => setNotesOpen((o) => !o)}>
              <span>Notes</span>
              {openTodos > 0 && <span className="count">{openTodos}</span>}
            </button>
          </header>

          <div className="thread" ref={threadRef}>
            <div className="thread-inner">
              {items.length === 0 && <Welcome busy={busy} onPick={send} onEod={writeEod} />}
              {items.map((it) => {
                if (it.kind === "ask") return <Ask key={it.id} text={it.text} />;
                if (it.kind === "error") return <Notice key={it.id} message={it.message} />;
                if (it.kind === "confirm") return <Confirm key={it.id} approvals={it.approvals} onSubmit={approve} />;
                return (
                  <Reply
                    key={it.id}
                    reply={it.reply}
                    todos={it.todos}
                    time={it.time}
                    busy={busy}
                    onAddToNotes={() => addToNotes(it.reply)}
                    onCopy={() => copy(it.reply)}
                    onAddMore={it.id === lastReplyId ? addMoreToDraft : undefined}
                  />
                );
              })}
              {busy && <Working />}
            </div>
          </div>

          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(text);
              setText("");
            }}
          >
            {items.length > 0 && ( // the welcome screen already has the big "Write today's EOD" card
              <div className="composer-actions">
                <button className="eod-btn" type="button" disabled={busy} onClick={writeEod}>
                  <Icon name="mail" />
                  <span>Write today’s EOD</span>
                </button>
              </div>
            )}
            <div className="composer-box">
              <textarea
                ref={inputRef}
                rows={1}
                placeholder="Ask about your email…"
                aria-label="Message"
                value={text}
                disabled={busy}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    e.currentTarget.form.requestSubmit();
                  }
                }}
              />
              <button className="send" type="submit" aria-label="Send" disabled={busy}>
                <Icon name="send" />
              </button>
            </div>
            <p className="composer-hint">
              <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line
            </p>
          </form>
        </main>

        <NotesPanel
          open={notesOpen}
          notes={notes}
          setNotes={setNotes}
          focusKey={notesFocus}
          sessionId={sessionId}
          busy={busy}
          onClose={() => setNotesOpen(false)}
          onShare={(m) => send(m)}
          toast={toast}
        />
      </div>
      {toastMsg && <div className="toast" role="status">{toastMsg}</div>}
    </>
  );
}
