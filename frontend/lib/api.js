// Client for server.py.
//
// Turn:      { status: "done", reply, todos } | { status: "needs_approval", approvals }
// Approval:  { interrupt_id, index, tool, description, actions: [{ tool, arguments }] }
// Decision:  { interrupt_id, index, approve, feedback }
// Notes:     { title, summary, action_items: [{ text, done }], notes }

async function post(path, body) {
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Can't reach the server. Check that `python server.py` is running.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (${res.status}).`);
  }
  return data;
}

async function get(path) {
  const res = await fetch(path).catch(() => null);
  if (!res || !res.ok) throw new Error("Can't reach the server. Check that `python server.py` is running.");
  return res.json();
}

function managerBody(m) {
  if (!m || m.has_manager === null || m.has_manager === undefined) return {};
  return { has_manager: m.has_manager, manager_email: m.has_manager ? m.manager_email || "" : null };
}

export const api = {
  me: () => get("/api/me"),
  // -> { status: "sign_in" | "connect", user_id, steps } | { status: "choose_role", roles } | { status: "choose_manager" }
  //    | { status: "ready", session_id, user_id, role, has_manager, manager_email, eod_to }
  // manager: { has_manager, manager_email } from "Do you have a reporting manager?" (Cc on EOD reports)
  startSession: (userId, role, manager) => post("/api/session", { user_id: userId, role: role || null, ...managerBody(manager) }),
  connect: (userId, toolkit, role, manager) =>
    post("/api/connect", { user_id: userId, toolkit, role: role || null, ...managerBody(manager) }),
  setManager: (manager) => post("/api/manager", managerBody(manager)),
  logout: () => post("/api/logout", {}),
  // leave days (YYYY-MM-DD)
  leaveList: (start, end) => get(`/api/leave?start=${start}&end=${end}`),
  setLeave: (date, on, end) => post("/api/leave", { date, on, end: end || null }),
  holidays: (start, end) => get(`/api/holidays?start=${start}&end=${end}`),
  // HR only. -> { count, years, file, holidays: [{ date, name }] }
  uploadHolidays: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/holidays/upload", { method: "POST", body: form }).catch(() => null);
    if (!res) throw new Error("Can't reach the server. Check that `python server.py` is running.");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Upload failed (${res.status}).`);
    return data;
  },
  chat: (sessionId, message) => post("/api/chat", { session_id: sessionId, message }),
  approve: (sessionId, decisions) => post("/api/approve", { session_id: sessionId, decisions }),
  saveNotes: (sessionId, notes) => post("/api/notes", { session_id: sessionId, ...notes }),
};
