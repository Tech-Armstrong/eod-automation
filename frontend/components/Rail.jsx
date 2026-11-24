"use client";
import { useMemo, useRef, useState } from "react";

import Icon from "./Icon";
import { ManagerPicker, managerReady } from "./SignIn";
import { formatDate, fromIso, isoDate } from "@/lib/utils";

const HR_ROLE = "Human Resources";
const short = (iso) => formatDate(fromIso(iso), { day: "numeric", month: "short" });

/**
 * Left rail: new conversation; the last 7 days (each with a Leave toggle); another date (summarize or mark leave);
 * sender filter; upcoming company holidays (HR can upload the list); who gets the EOD (change manager); account.
 * Leave days and holidays are skipped: they can't be summarized from here.
 */
export default function Rail({
  userId, role, activeDay, busy, leaveDays, holidays, onNewChat, onSummarize, onToggleLeave, onUploadHolidays,
  manager, eodTo, onSetManager, onSwitchUser,
}) {
  const [from, setFrom] = useState("");
  const [other, setOther] = useState("");

  const today = useMemo(() => isoDate(new Date()), []);
  const days = useMemo(() => {
    const now = new Date();
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      return {
        iso: isoDate(d),
        name: i === 0 ? "Today" : i === 1 ? "Yesterday" : formatDate(d, { weekday: "long" }),
        short: formatDate(d, { day: "numeric", month: "short" }),
      };
    });
  }, []);

  const otherHoliday = other ? holidays.get(other) : null;
  const otherIsLeave = Boolean(other) && leaveDays.has(other);
  const otherIsFuture = Boolean(other) && other > today;
  const upcoming = useMemo(
    () => [...holidays.entries()].filter(([d]) => d >= today).sort(([a], [b]) => a.localeCompare(b)).slice(0, 4),
    [holidays, today],
  );

  return (
    <nav className="rail" aria-label="Days">
      <div className="rail-top">
        <div className="wordmark wordmark-sm">EOD <em>End of Day</em></div>
        <button className="rail-new" type="button" onClick={onNewChat} disabled={busy}>
          <Icon name="plus" />
          New conversation
        </button>
      </div>

      <div className="rail-section">
        <div className="rail-label">Summarize a day</div>
        <ul className="days">
          {days.map((d) => {
            const holiday = holidays.get(d.iso);
            const leave = !holiday && leaveDays.has(d.iso);
            const off = Boolean(holiday) || leave;
            return (
              <li key={d.iso} className={`day-row${leave ? " is-leave" : ""}${holiday ? " is-holiday" : ""}`}>
                <button
                  type="button"
                  className={`day${activeDay === d.iso ? " is-active" : ""}`}
                  disabled={busy || off}
                  title={holiday ? `Holiday: ${holiday} (skipped)` : leave ? "Marked as leave: skipped" : `Summarize ${d.short}`}
                  onClick={() => onSummarize(d.iso, from.trim())}
                >
                  <span className="day-name">{d.name}</span>
                  <span className="day-date">{holiday || (leave ? "On leave" : d.short)}</span>
                </button>
                {!holiday && (
                  <button
                    type="button"
                    className="leave-toggle"
                    aria-pressed={leave}
                    aria-label={leave ? `Remove leave on ${d.short}` : `Mark ${d.short} as leave`}
                    title={leave ? "Not on leave" : "Mark as leave"}
                    onClick={() => onToggleLeave(d.iso, !leave)}
                  >
                    {leave ? <Icon name="x" /> : "Leave"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>

        <div className="rail-field">
          <label htmlFor="otherDate">Another date</label>
          <input id="otherDate" type="date" value={other} onChange={(e) => setOther(e.target.value)} />
          {other && !otherHoliday && (
            <div className="other-actions">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={busy || otherIsLeave || otherIsFuture}
                title={otherIsLeave ? "Marked as leave: skipped" : otherIsFuture ? "This day hasn't happened yet" : ""}
                onClick={() => onSummarize(other, from.trim())}
              >
                Summarize
              </button>
              <button type="button" className="btn btn-quiet btn-sm" onClick={() => onToggleLeave(other, !otherIsLeave)}>
                {otherIsLeave ? "Not on leave" : "Mark as leave"}
              </button>
            </div>
          )}
          {otherHoliday && <span className="other-note is-holiday">Holiday on {short(other)}: {otherHoliday}. Skipped.</span>}
          {otherIsLeave && !otherHoliday && <span className="other-note">On leave {short(other)}: skipped</span>}
        </div>

        <label className="rail-field">
          <span>Only from <i>(optional)</i></span>
          <input placeholder="name or email" spellCheck={false} value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>

        <Holidays upcoming={upcoming} isHR={role === HR_ROLE} onUpload={onUploadHolidays} />
        <EodRecipients manager={manager} eodTo={eodTo} onSave={onSetManager} />
      </div>

      <div className="rail-foot">
        <span className="avatar" aria-hidden="true">{userId.charAt(0).toUpperCase()}</span>
        <span className="rail-who">
          <span className="rail-user">{userId}</span>
          {role && <span className="rail-role">{role}</span>}
        </span>
        <button className="link" type="button" onClick={onSwitchUser}>Switch</button>
      </div>
    </nav>
  );
}

function Holidays({ upcoming, isHR, onUpload }) {
  const fileRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState({ ok: true, text: "" });

  async function pick(e) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow picking the same file again
    if (!file) return;
    setUploading(true);
    setStatus({ ok: true, text: "" });
    try {
      const res = await onUpload(file);
      setStatus({ ok: true, text: `${res.count} holidays loaded for ${res.years.join(", ")}.` });
    } catch (err) {
      setStatus({ ok: false, text: err.message });
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="holidays" aria-label="Company holidays">
      <div className="rail-label">Upcoming holidays</div>
      {upcoming.length ? (
        <ul className="holiday-list">
          {upcoming.map(([d, name]) => (
            <li key={d}>
              <span className="holiday-name">{name}</span>
              <span className="holiday-date">{short(d)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="holiday-empty">No upcoming holidays on the list.</p>
      )}
      {isHR && (
        <div className="holiday-upload">
          <input ref={fileRef} type="file" accept=".pdf,.xlsx,.csv" hidden onChange={pick} />
          <button type="button" className="btn btn-quiet btn-sm" disabled={uploading} onClick={() => fileRef.current?.click()}>
            <Icon name="plus" />
            <span>{uploading ? "Reading the list…" : "Upload holiday list"}</span>
          </button>
          <p className="holiday-help">PDF, Excel or CSV. Replaces the holidays for the years in the file.</p>
          {status.text && <p className={`holiday-status${status.ok ? "" : " is-error"}`} role="status">{status.text}</p>}
        </div>
      )}
    </section>
  );
}

/** Who the EOD goes to: the fixed list in To, the reporting manager in Cc (changeable here). */
function EodRecipients({ manager, eodTo, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(manager);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save(e) {
    e.preventDefault();
    if (!managerReady(draft)) return;
    setSaving(true);
    setError("");
    try {
      await onSave(draft);
      setEditing(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="eod-to" aria-label="Who gets your EOD">
      <div className="rail-label">Your EOD goes to</div>
      <ul className="eod-to-list">
        {eodTo.map((e) => <li key={e}><span className="eod-to-kind">To</span><span>{e}</span></li>)}
        {manager.has_manager && manager.manager_email && (
          <li><span className="eod-to-kind">Cc</span><span>{manager.manager_email}</span></li>
        )}
      </ul>
      {editing ? (
        <form className="eod-to-form" onSubmit={save}>
          <ManagerPicker value={draft} onChange={setDraft} eodTo={eodTo} idPrefix="rail-mgr" />
          <div className="other-actions">
            <button type="submit" className="btn btn-primary btn-sm" disabled={saving || !managerReady(draft)}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn btn-quiet btn-sm" onClick={() => { setEditing(false); setError(""); }}>
              Cancel
            </button>
          </div>
          {error && <p className="holiday-status is-error" role="alert">{error}</p>}
        </form>
      ) : (
        <button type="button" className="link eod-to-change" onClick={() => { setDraft(manager); setEditing(true); }}>
          {manager.has_manager ? "Change reporting manager" : "Add a reporting manager"}
        </button>
      )}
    </section>
  );
}
