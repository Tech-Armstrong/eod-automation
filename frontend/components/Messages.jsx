"use client";
import { useEffect, useMemo, useState } from "react";

import AddMore, { looksLikeDraft } from "./AddMore";
import Icon from "./Icon";
import { formatDate, markdownToHtml } from "@/lib/utils";

const STARTERS = [
  ["Today’s emails", "A brief of everything that arrived today", "Summarize the emails I received today."],
  ["Yesterday", "Catch up on what you missed", "Summarize the emails I received yesterday."],
  ["Needs a reply", "Emails from the last 3 days waiting on you",
    "Which emails from the last 3 days need a reply from me? Summarize each one, date-wise."],
  ["Attachments this week", "Files people have sent you",
    "Summarize the emails with attachments I received in the last 7 days, date-wise, and say what each file is."],
];

export function Welcome({ busy, onPick, onEod }) {
  return (
    <div className="welcome">
      <p className="welcome-date">{formatDate(new Date(), { weekday: "long", day: "numeric", month: "long" })}</p>
      <h2 className="welcome-title">What should we go through?</h2>
      <button type="button" className="eod-card" disabled={busy} onClick={onEod}>
        <span className="eod-card-icon"><Icon name="mail" /></span>
        <span className="starter-text">
          <span className="starter-label">Write today’s EOD</span>
          <span className="starter-hint">A detailed report from the emails you received and sent today. You review it, add points, then send.</span>
        </span>
        <Icon name="arrow" />
      </button>
      <div className="starters">
        {STARTERS.map(([label, hint, prompt]) => (
          <button key={label} type="button" className="starter" disabled={busy} onClick={() => onPick(prompt)}>
            <span className="starter-text">
              <span className="starter-label">{label}</span>
              <span className="starter-hint">{hint}</span>
            </span>
            <Icon name="arrow" />
          </button>
        ))}
      </div>
    </div>
  );
}

export function Ask({ text }) {
  return (
    <div className="ask">
      <div className="ask-text">{text}</div>
    </div>
  );
}

export function Reply({ reply, todos, time, busy, onAddToNotes, onCopy, onAddMore }) {
  const html = useMemo(() => markdownToHtml(reply), [reply]);
  const isDraft = useMemo(() => looksLikeDraft(reply), [reply]);
  return (
    <article className="reply">
      <div className="reply-meta">
        <span className="reply-who">Assistant</span>
        <span>{time}</span>
      </div>
      <div className="prose" dangerouslySetInnerHTML={{ __html: html }} />
      {todos?.length > 0 && (
        <details className="steps">
          <summary>{`${todos.length} step${todos.length > 1 ? "s" : ""} taken`}</summary>
          <ol>
            {todos.map((t, i) => (
              <li key={i} className={t.status === "completed" ? "is-done" : ""}>{t.content}</li>
            ))}
          </ol>
        </details>
      )}
      <div className="reply-tools">
        <button type="button" className="tool" onClick={onAddToNotes}>
          <Icon name="note" />
          <span>Add to notes</span>
        </button>
        <button type="button" className="tool" onClick={onCopy}>
          <Icon name="copy" />
          <span>Copy</span>
        </button>
      </div>
      {isDraft && onAddMore && <AddMore busy={busy} onSubmit={onAddMore} />}
    </article>
  );
}

export function Notice({ message }) {
  return (
    <div className="notice">
      <strong>That didn’t work. </strong>
      <span>{message}</span>
    </div>
  );
}

export function Working() {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => setSecs(Math.round((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="working">
      <span className="working-bar" />
      <span>Working</span>
      <span className="working-secs">{secs >= 3 ? `${secs}s` : ""}</span>
    </div>
  );
}
