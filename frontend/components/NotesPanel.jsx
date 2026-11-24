"use client";
import { useEffect, useRef, useState } from "react";

import Icon from "./Icon";
import { api } from "@/lib/api";
import { autoGrow } from "@/lib/utils";

/**
 * Notes side panel. `notes` = { title, summary, todos: [{ text, done }], extra } and is owned by the parent,
 * so "Add to notes" under a reply can fill it in.
 */
export default function NotesPanel({ open, notes, setNotes, focusKey, sessionId, busy, onClose, onShare, toast }) {
  const [todoText, setTodoText] = useState("");
  const [status, setStatus] = useState("");
  const summaryRef = useRef(null);
  const extraRef = useRef(null);
  const todoRef = useRef(null);

  useEffect(() => autoGrow(summaryRef.current, 420), [notes.summary, open]);
  useEffect(() => autoGrow(extraRef.current, 260), [notes.extra, open]);
  useEffect(() => {
    if (focusKey) {
      setStatus("");
      todoRef.current?.focus();
    }
  }, [focusKey]);

  const update = (patch) => setNotes((n) => ({ ...n, ...patch }));
  const setTodos = (fn) => setNotes((n) => ({ ...n, todos: fn(n.todos) }));

  const payload = () => ({
    title: notes.title.trim() || "Email notes",
    summary: notes.summary.trim(),
    action_items: notes.todos.filter((t) => t.text.trim()),
    notes: notes.extra.trim(),
  });
  const isEmpty = (p) => !p.summary && !p.action_items.length && !p.notes;

  async function save() {
    const p = payload();
    if (isEmpty(p)) { setStatus("Nothing to save yet."); return; }
    if (!sessionId) return;
    try {
      const { path } = await api.saveNotes(sessionId, p);
      setStatus(`Saved to ${path}`);
      toast("Notes saved");
    } catch (err) {
      setStatus(err.message);
    }
  }

  function share() {
    const p = payload();
    if (isEmpty(p)) { setStatus("Write something first."); return; }
    let msg = "Here is my edited version with my own additions. Treat it as correct from now on.\n";
    if (p.summary) msg += `\nSummary:\n${p.summary}\n`;
    if (p.action_items.length) {
      msg += `\nTo-dos:\n${p.action_items.map((t) => `- [${t.done ? "x" : " "}] ${t.text}`).join("\n")}\n`;
    }
    if (p.notes) msg += `\nMy notes:\n${p.notes}\n`;
    msg += "\nReply briefly to confirm what you noted. Don't take any action yet.";
    if (window.matchMedia("(max-width: 1100px)").matches) onClose();
    onShare(msg);
  }

  return (
    <aside className="notes" aria-hidden={!open} aria-label="Notes">
      <header className="notes-head">
        <span className="notes-label">Notes</span>
        <button className="icon-btn" type="button" aria-label="Close notes" onClick={onClose}>
          <Icon name="x" />
        </button>
      </header>

      <div className="notes-body">
        <input
          className="notes-title"
          placeholder="Untitled"
          aria-label="Title"
          value={notes.title}
          onChange={(e) => update({ title: e.target.value })}
        />

        <section className="notes-sec">
          <h3>Summary</h3>
          <textarea
            ref={summaryRef}
            rows={5}
            placeholder="Click “Add to notes” under a reply, or write your own."
            value={notes.summary}
            onChange={(e) => update({ summary: e.target.value })}
          />
        </section>

        <section className="notes-sec">
          <h3>To-dos</h3>
          <ul className="todos">
            {notes.todos.map((t, i) => (
              <li key={i} className={`todo${t.done ? " is-done" : ""}`}>
                <input
                  type="checkbox"
                  checked={t.done}
                  aria-label={`Mark “${t.text}” done`}
                  onChange={(e) => setTodos((all) => all.map((x, j) => (j === i ? { ...x, done: e.target.checked } : x)))}
                />
                <span className="todo-text">{t.text}</span>
                <button type="button" className="todo-del" onClick={() => setTodos((all) => all.filter((_, j) => j !== i))}>
                  <Icon name="x" />
                  <span>Remove</span>
                </button>
              </li>
            ))}
          </ul>
          <form
            className="todo-add"
            onSubmit={(e) => {
              e.preventDefault();
              const text = todoText.trim();
              if (!text) return;
              setTodos((all) => [...all, { text, done: false }]);
              setTodoText("");
            }}
          >
            <input
              ref={todoRef}
              placeholder="Add a to-do and press Enter"
              aria-label="New to-do"
              value={todoText}
              onChange={(e) => setTodoText(e.target.value)}
            />
          </form>
        </section>

        <section className="notes-sec">
          <h3>Anything else</h3>
          <textarea
            ref={extraRef}
            rows={3}
            placeholder="Context, decisions, people to follow up with…"
            value={notes.extra}
            onChange={(e) => update({ extra: e.target.value })}
          />
        </section>
      </div>

      <footer className="notes-foot">
        <button className="btn btn-primary" type="button" onClick={save}>Save notes</button>
        <button className="btn btn-quiet" type="button" onClick={share} disabled={busy}>Share with assistant</button>
        <p className="notes-status" role="status">{status}</p>
      </footer>
    </aside>
  );
}
