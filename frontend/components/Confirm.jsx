"use client";
import { useEffect, useMemo, useRef, useState } from "react";

import AddMore, { buildEodFeedback } from "./AddMore";
import Icon from "./Icon";
import { LOOKS_LIKE_HTML, markdownToHtml, sanitizeEmailHtml } from "@/lib/utils";

const APPS = {
  GMAIL: "Gmail", GOOGLEDRIVE: "Google Drive", GOOGLECALENDAR: "Google Calendar", GOOGLESHEETS: "Google Sheets",
  GOOGLEDOCS: "Google Docs", SLACK: "Slack", NOTION: "Notion", OUTLOOK: "Outlook",
};
const LABELS = {
  recipient_email: "To", to: "To", recipient: "To", cc: "Cc", bcc: "Bcc", extra_recipients: "Also to",
  subject: "Subject", file_name: "File", folder_id: "Folder", title: "Title", saved: "What", rows: "Which", days_included: "Days",
};
const BODY_KEYS = new Set(["body", "message_body", "message", "text", "content", "summary", "notes"]);
const HIDDEN_KEYS = new Set(["is_html", "user_id", "include_signature", "thread_id", "mime_type"]);

function actionTitle(slug) {
  const [app, ...rest] = slug.split("_");
  const words = rest.join(" ").toLowerCase();
  return {
    verb: words ? words.charAt(0).toUpperCase() + words.slice(1) : slug,
    app: APPS[app] ?? app.charAt(0) + app.slice(1).toLowerCase(),
  };
}

function show(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.map(show).join(", ");
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
}

/** Email body as the recipient will see it: HTML is rendered (sanitized), plain text keeps its line breaks. */
function Body({ body, isHtml, isMarkdown }) {
  const html = useMemo(
    () => (isMarkdown ? markdownToHtml(body) : isHtml || LOOKS_LIKE_HTML.test(body) ? sanitizeEmailHtml(body) : null),
    [body, isHtml, isMarkdown],
  );
  if (html !== null) return <div className="preview-body is-html" dangerouslySetInnerHTML={{ __html: html }} />;
  return <div className="preview-body">{body}</div>;
}

const TABLE_NAMES = {
  daily_summaries: "Daily summary",
  monthly_summaries: "Monthly summary",
  notes: "Note",
};

/** update_records -> a readable "what will change" card instead of raw JSON. */
function dbPreviewAction(action) {
  const { table, filters = {}, values = {} } = action.arguments || {};
  const which = Object.entries(filters)
    .map(([k, v]) => `${k.replace(/__(gte|gt)$/, " from").replace(/__(lte|lt)$/, " until").replace(/__contains$/, " contains").replace(/_/g, " ")}: ${show(v)}`)
    .join(", ");
  return {
    tool: "EOD_UPDATE_SAVED_DATA",
    arguments: { saved: TABLE_NAMES[table] || table, rows: which || "—", ...values },
  };
}

function Preview({ action: raw }) {
  const action = raw.tool === "update_records" ? dbPreviewAction(raw) : raw;
  const { verb, app } = action.tool === "EOD_UPDATE_SAVED_DATA"
    ? { verb: "Update saved data", app: "EOD database" }
    : actionTitle(action.tool);
  const args = action.arguments || {};
  let body = "";
  const fields = [];
  for (const [k, v] of Object.entries(args)) {
    if (BODY_KEYS.has(k) && typeof v === "string") { body = v; continue; }
    if (HIDDEN_KEYS.has(k) || typeof v === "boolean") continue; // technical flags mean nothing to the reader
    fields.push([LABELS[k] ?? k.replace(/_/g, " "), show(v)]);
  }
  return (
    <div className="preview">
      <div className="preview-head">
        <Icon name={action.tool.startsWith("GMAIL") ? "mail" : "note"} />
        <span className="preview-verb">{verb}</span>
        <span className="preview-app">{app}</span>
      </div>
      {fields.length > 0 && (
        <dl className="preview-fields">
          {fields.map(([label, value]) => (
            <div key={label} style={{ display: "contents" }}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {body && <Body body={body} isHtml={args.is_html === true} isMarkdown={raw.tool === "update_records"} />}
    </div>
  );
}

function Item({ approval, decision, onDecide }) {
  const [declining, setDeclining] = useState(false);
  const [adding, setAdding] = useState(false);
  const isEod = approval.actions?.some((a) => a.kind === "eod");
  const [reason, setReason] = useState("");
  const reasonRef = useRef(null);

  useEffect(() => {
    if (declining) reasonRef.current?.focus();
  }, [declining]);

  return (
    <div className="confirm-item">
      {approval.actions?.length
        ? approval.actions.map((a, i) => <Preview key={i} action={a} />)
        : <pre className="preview-raw">{approval.description || approval.tool}</pre>}

      {decision ? (
        <p className={`confirm-outcome ${decision.approve ? "is-ok" : decision.points ? "is-more" : "is-no"}`}>
          <Icon name={decision.approve ? "check" : decision.points ? "plus" : "x"} />
          <span>
            {decision.approve
              ? isEod ? "Approved – sending" : "Approved"
              : decision.points ? `Sent back to add ${decision.points} point${decision.points > 1 ? "s" : ""}` : "Declined"}
          </span>
        </p>
      ) : adding ? (
        <AddMore
          startOpen
          submitLabel="Update EOD"
          onCancel={() => setAdding(false)}
          onSubmit={(points) => onDecide(false, buildEodFeedback(points), points.length)}
        />
      ) : declining ? (
        <div className="confirm-why">
          <textarea
            ref={reasonRef}
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="What should change? (optional) e.g. “make it shorter” or “cc Divyanshu”"
          />
          <div className="confirm-bar">
            <button type="button" className="btn btn-danger" onClick={() => onDecide(false, reason.trim())}>Decline</button>
            <button type="button" className="btn btn-quiet" onClick={() => setDeclining(false)}>Back</button>
          </div>
        </div>
      ) : (
        <div className="confirm-bar">
          <button type="button" className="btn btn-primary" onClick={() => onDecide(true, "")}>
            <Icon name="check" />
            <span>{isEod ? "Approve & send" : "Approve"}</span>
          </button>
          {isEod && (
            <button type="button" className="btn btn-quiet" onClick={() => setAdding(true)}>
              <Icon name="plus" />
              <span>Add points</span>
            </button>
          )}
          <button type="button" className="btn btn-quiet" onClick={() => setDeclining(true)}>Decline</button>
        </div>
      )}
    </div>
  );
}

/** "Needs your OK" block. Calls onSubmit once every action has a decision. */
export default function Confirm({ approvals, onSubmit }) {
  const [decisions, setDecisions] = useState({});
  const submitted = useRef(false);

  useEffect(() => {
    if (submitted.current || Object.keys(decisions).length !== approvals.length) return;
    submitted.current = true;
    onSubmit(approvals.map((_, i) => decisions[i]));
  }, [decisions, approvals, onSubmit]);

  return (
    <section className={`confirm${submitted.current ? " is-settled" : ""}`}>
      <header className="confirm-head">
        <Icon name="shield" />
        <span>{approvals.length > 1 ? "These need your OK" : "This needs your OK"}</span>
      </header>
      <p className="confirm-sub">
        {approvals.some((a) => a.actions?.some((x) => x.kind === "eod"))
          ? "Your EOD is ready but not sent. Check it, add anything that’s missing, then approve to send."
          : "Nothing has been sent or changed. Review it, then decide."}
      </p>
      {approvals.map((a, i) => (
        <Item
          key={`${a.interrupt_id}:${a.index}`}
          approval={a}
          decision={decisions[i]}
          onDecide={(approve, feedback, points = 0) =>
            setDecisions((d) => ({ ...d, [i]: { interrupt_id: a.interrupt_id, index: a.index, approve, feedback, points } }))
          }
        />
      ))}
    </section>
  );
}
