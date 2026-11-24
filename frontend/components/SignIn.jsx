"use client";
import { useState } from "react";

import Icon from "./Icon";

export const ROLES = ["Relationship Manager", "Back Office", "Human Resources", "Technology"];

/**
 * Sign-in in up to five steps:
 *   "email"   – email + role + "Do you have a reporting manager?" (their email goes in Cc on EOD reports)
 *   "verify"  – Continue with Google (Composio's hosted sign-in); proves it's your mailbox
 *   "connect" – connect any remaining apps (e.g. Google Drive)
 *   "role"    – only for people who signed up before roles existed
 *   "manager" – only for people who signed up before the reporting-manager question existed
 */
export default function SignIn({
  step, email, role, roles, manager, eodTo, steps, busy, error, onEmail, onConnect, onRole, onManager, onBack,
}) {
  return (
    <div className="signin">
      <div className="signin-inner">
        <div className="wordmark">EOD <em>End of Day</em></div>
        {step === "email" && (
          <EmailStep
            initial={email} initialRole={role} initialManager={manager} roles={roles} eodTo={eodTo}
            busy={busy} error={error} onSubmit={onEmail}
          />
        )}
        {step === "verify" && <VerifyStep email={email} busy={busy} error={error} onConnect={onConnect} onBack={onBack} />}
        {step === "connect" && (
          <ConnectStep email={email} steps={steps} busy={busy} error={error} onConnect={onConnect} onBack={onBack} />
        )}
        {step === "role" && <RoleStep email={email} roles={roles} busy={busy} error={error} onSubmit={onRole} onBack={onBack} />}
        {step === "manager" && (
          <ManagerStep email={email} eodTo={eodTo} busy={busy} error={error} onSubmit={onManager} onBack={onBack} />
        )}
        {step === "loading" && <p className="signin-lede signin-wait">Checking your sign-in…</p>}
      </div>
    </div>
  );
}

function RolePicker({ roles, value, onChange }) {
  return (
    <fieldset className="roles">
      <legend>Your role</legend>
      <div className="role-grid">
        {(roles?.length ? roles : ROLES).map((r) => (
          <label key={r} className={`role-option${value === r ? " is-selected" : ""}`}>
            <input type="radio" name="role" value={r} checked={value === r} onChange={() => onChange(r)} required />
            <span className="role-check" aria-hidden="true"><Icon name="check" /></span>
            <span>{r}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export const EMPTY_MANAGER = { has_manager: null, manager_email: "" };
const looksLikeEmail = (v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.trim());

/** True once the manager question is fully answered (No, or Yes + a valid email). */
export function managerReady(m) {
  return m.has_manager === false || (m.has_manager === true && looksLikeEmail(m.manager_email || ""));
}

/** "Do you have a reporting manager?" Yes / No, then their email when Yes. */
export function ManagerPicker({ value, onChange, eodTo, idPrefix = "mgr" }) {
  const set = (patch) => onChange({ ...value, ...patch });
  return (
    <fieldset className="roles manager">
      <legend>Do you have a reporting manager?</legend>
      <div className="role-grid manager-grid">
        {[[true, "Yes"], [false, "No"]].map(([v, label]) => (
          <label key={label} className={`role-option${value.has_manager === v ? " is-selected" : ""}`}>
            <input
              type="radio" name={`${idPrefix}-has`} checked={value.has_manager === v} onChange={() => set({ has_manager: v })}
              required
            />
            <span className="role-check" aria-hidden="true"><Icon name="check" /></span>
            <span>{label}</span>
          </label>
        ))}
      </div>
      {value.has_manager === true && (
        <div className="manager-email">
          <label htmlFor={`${idPrefix}-email`}>Your reporting manager’s email</label>
          <input
            id={`${idPrefix}-email`} type="email" spellCheck={false} placeholder="manager@company.com" required
            value={value.manager_email || ""} onChange={(e) => set({ manager_email: e.target.value })}
          />
        </div>
      )}
      <p className="field-help">
        Your EOD reports go to {eodTo?.length ? eodTo.join(" and ") : "the EOD team"}
        {value.has_manager === true ? ", with your manager in Cc." : "."}
      </p>
    </fieldset>
  );
}

function EmailStep({ initial, initialRole, initialManager, roles, eodTo, busy, error, onSubmit }) {
  const [value, setValue] = useState(initial || "");
  const [role, setRole] = useState(initialRole || "");
  const [manager, setManager] = useState(initialManager || EMPTY_MANAGER);
  const ready = Boolean(role) && managerReady(manager);
  return (
    <>
      <h1>Your inbox, read and summarized.</h1>
      <p className="signin-lede">
        Pick a day and get a clear brief of what arrived, with room for your own notes and to-dos. Nothing is sent
        or changed without your approval.
      </p>
      <form
        className="signin-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim() && ready) onSubmit(value.trim(), role, manager);
        }}
      >
        <label htmlFor="userId">Work email</label>
        <input
          id="userId"
          type="email"
          autoComplete="email"
          spellCheck={false}
          placeholder="you@company.com"
          required
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <p className="field-help">The Gmail or Google Workspace address you want summarized.</p>
        <RolePicker roles={roles} value={role} onChange={setRole} />
        <ManagerPicker value={manager} onChange={setManager} eodTo={eodTo} />
        <button className="btn btn-primary btn-lg" type="submit" disabled={busy || !ready}>
          {busy ? "Checking…" : "Continue"}
        </button>
        <p className="form-error" role="alert">{error}</p>
      </form>
    </>
  );
}

function RoleStep({ email, roles, busy, error, onSubmit, onBack }) {
  const [role, setRole] = useState("");
  return (
    <>
      <h1>What’s your role?</h1>
      <p className="signin-lede">
        Signed in as <strong>{email}</strong>. Pick your role once; EOD remembers it.
      </p>
      <form className="signin-form" onSubmit={(e) => { e.preventDefault(); if (role) onSubmit(role); }}>
        <RolePicker roles={roles} value={role} onChange={setRole} />
        <button className="btn btn-primary btn-lg" type="submit" disabled={busy || !role}>
          {busy ? "Saving…" : "Continue"}
        </button>
        <p className="form-error" role="alert">{error}</p>
        <button className="link" type="button" onClick={onBack}>Sign out</button>
      </form>
    </>
  );
}

function ManagerStep({ email, eodTo, busy, error, onSubmit, onBack }) {
  const [manager, setManager] = useState(EMPTY_MANAGER);
  return (
    <>
      <h1>Who gets your EOD?</h1>
      <p className="signin-lede">
        Signed in as <strong>{email}</strong>. One quick question so your EOD reports reach the right people.
      </p>
      <form className="signin-form" onSubmit={(e) => { e.preventDefault(); if (managerReady(manager)) onSubmit(manager); }}>
        <ManagerPicker value={manager} onChange={setManager} eodTo={eodTo} idPrefix="mgr-step" />
        <button className="btn btn-primary btn-lg" type="submit" disabled={busy || !managerReady(manager)}>
          {busy ? "Saving…" : "Continue"}
        </button>
        <p className="form-error" role="alert">{error}</p>
        <button className="link" type="button" onClick={onBack}>Sign out</button>
      </form>
    </>
  );
}

function VerifyStep({ email, busy, error, onConnect, onBack }) {
  return (
    <>
      <h1>Confirm it’s you.</h1>
      <p className="signin-lede">
        Sign in with Google as <strong>{email}</strong>. This connects your mailbox through Composio, our secure
        connection service, and confirms the address is yours.
      </p>
      <div className="signin-form">
        <button className="btn btn-primary btn-lg" type="button" disabled={busy} onClick={() => onConnect("gmail")}>
          <Icon name="mail" />
          <span>{busy ? "Opening Google…" : "Continue with Google"}</span>
        </button>
        <ul className="signin-points">
          <li>You’ll come straight back here when you’re done.</li>
          <li>EOD can read your email to summarize it. It never sends anything without your OK.</li>
          <li>This device stays signed in for 90 days.</li>
        </ul>
        <p className="form-error" role="alert">{error}</p>
        <button className="link" type="button" onClick={onBack}>Use a different email</button>
      </div>
    </>
  );
}

function ConnectStep({ email, steps, busy, error, onConnect, onBack }) {
  const remaining = steps.filter((s) => !s.connected).length;
  return (
    <>
      <h1>{remaining ? "One more step." : "All set."}</h1>
      <p className="signin-lede">
        Signed in as <strong>{email}</strong>. Connect the apps EOD can work with.
      </p>
      <div className="signin-form">
        <ul className="connect-list">
          {steps.map((s) => (
            <li key={s.slug} className="connect-row">
              <span className="connect-name">{s.name}</span>
              {s.connected ? (
                <span className="connect-done"><Icon name="check" /><span>Connected</span></span>
              ) : (
                <button className="btn btn-primary btn-sm" type="button" disabled={busy} onClick={() => onConnect(s.slug)}>
                  Connect
                </button>
              )}
            </li>
          ))}
        </ul>
        <p className="form-error" role="alert">{error}</p>
        <button className="link" type="button" onClick={onBack}>Sign out</button>
      </div>
    </>
  );
}
