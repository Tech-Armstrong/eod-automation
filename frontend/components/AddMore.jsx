"use client";
import { useEffect, useRef, useState } from "react";

import Icon from "./Icon";

/** Replies that read like an email or report draft get the "More to say?" option. */
export function looksLikeDraft(text) {
  const head = text.trim().split("\n").slice(0, 3).join("\n");
  const greeting = /^\s*(\*\*)?(hi|hello|hey|dear|good (morning|afternoon|evening))\b/im.test(head);
  const signoff = /\n\s*(thanks|thank you|regards|best|best regards|warm regards|kind regards|cheers|sincerely)\b[^\n]{0,40}(\n|$)/i
    .test(text);
  const subject = /^\s*(\*\*)?subject\s*:/im.test(head);
  return subject || (greeting && signoff);
}

/** Decline message for the EOD approval card: the assistant rewrites the report with these points and asks again. */
export function buildEodFeedback(points) {
  return (
    "Not yet: add these points to the EOD report before sending. Rewrite the whole report: put each point into " +
    "the numbered section it belongs to, or a new numbered section with a bold heading if it is a different topic " +
    "(never an \"Additional tasks\" section). Give every section 3-4 full lines of detail, keep the same subject, " +
    "greeting and sign-off, no names, emails or times in the body, and don't invent facts. Then call " +
    "send_eod_report again with the updated report.\n\nMy points:\n" +
    points.map((p) => `- ${p}`).join("\n")
  );
}

/** Builds the request that turns short pointers into a detailed, updated email. */
export function buildUpdateRequest(points) {
  return (
    "Rewrite the email you just drafted as one complete, updated EOD that includes the points below.\n" +
    "- Put each of my points into the numbered section it belongs to, or start a new numbered section with a " +
    "bold heading if it is a different topic. Do NOT collect them in a separate \"Additional tasks\" or " +
    "\"Other updates\" section.\n" +
    "- Every section needs at least 3-4 full lines of detail (one specific sentence per line), for the existing " +
    "sections and the new ones.\n" +
    "- No people's names, email addresses or times in the body.\n" +
    "- Use only facts from the draft and my points; don't invent numbers, names or outcomes.\n" +
    "- Keep the same subject, greeting and sign-off. Reply with only the full updated email.\n\nMy points:\n" +
    points.map((p) => `- ${p}`).join("\n")
  );
}

/**
 * "More to say?" under a draft: a short list of pointers (+ adds a row, Enter jumps to the next one),
 * then "Update email" asks the assistant to rewrite the draft with them in detail.
 */
export default function AddMore({
  busy, onSubmit, label = "More to say? Add tasks or points", submitLabel = "Update email", startOpen = false,
  onCancel,
}) {
  const [open, setOpen] = useState(startOpen);
  const [points, setPoints] = useState([""]);
  const [focusIndex, setFocusIndex] = useState(null);
  const rows = useRef([]);

  useEffect(() => {
    if (focusIndex !== null) {
      rows.current[focusIndex]?.focus();
      setFocusIndex(null);
    }
  }, [focusIndex, points]);

  const filled = points.map((p) => p.trim()).filter(Boolean);

  function addRow(after = points.length - 1) {
    setPoints((all) => [...all.slice(0, after + 1), "", ...all.slice(after + 1)]);
    setFocusIndex(after + 1);
  }

  function removeRow(i) {
    setPoints((all) => (all.length === 1 ? [""] : all.filter((_, j) => j !== i)));
    setFocusIndex(Math.max(0, i - 1));
  }

  function submit() {
    if (!filled.length || busy) return;
    onSubmit(filled);
    setOpen(false);
    setPoints([""]);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="more-trigger"
        disabled={busy}
        onClick={() => { setOpen(true); setFocusIndex(0); }}
      >
        <Icon name="plus" />
        <span>{label}</span>
      </button>
    );
  }

  return (
    <section className="more" aria-label="Add more to this email">
      <header className="more-head">
        <h3>More to say?</h3>
        <p>Jot down what else you did or want to mention, a few words each. I’ll write them up in detail and update the email.</p>
      </header>

      <ol className="more-list">
        {points.map((p, i) => (
          <li key={i} className="more-row">
            <span className="more-num" aria-hidden="true">{i + 1}</span>
            <input
              ref={(el) => (rows.current[i] = el)}
              value={p}
              placeholder={i === 0 ? "e.g. fixed the AMC downloader timeout" : "Another point"}
              aria-label={`Point ${i + 1}`}
              onChange={(e) => setPoints((all) => all.map((x, j) => (j === i ? e.target.value : x)))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  if (e.ctrlKey || e.metaKey) submit();
                  else if (p.trim()) addRow(i);
                } else if (e.key === "Backspace" && !p && points.length > 1) {
                  e.preventDefault();
                  removeRow(i);
                }
              }}
            />
            {points.length > 1 && (
              <button type="button" className="more-del" aria-label={`Remove point ${i + 1}`} onClick={() => removeRow(i)}>
                <Icon name="x" />
              </button>
            )}
          </li>
        ))}
      </ol>

      <button type="button" className="more-add" onClick={() => addRow()}>
        <span className="more-add-icon"><Icon name="plus" /></span>
        <span>Add a point</span>
      </button>

      <div className="more-actions">
        <button type="button" className="btn btn-primary" disabled={!filled.length || busy} onClick={submit}>
          {filled.length > 1 ? `${submitLabel} with ${filled.length} points` : submitLabel}
        </button>
        <button type="button" className="btn btn-quiet" onClick={() => { setOpen(false); onCancel?.(); }}>Cancel</button>
        <span className="more-hint"><kbd>Enter</kbd> next point · <kbd>Ctrl</kbd>+<kbd>Enter</kbd> update</span>
      </div>
    </section>
  );
}
