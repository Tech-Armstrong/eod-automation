// Small helpers: sanitized markdown/HTML, storage, dates, text.
import DOMPurify from "dompurify";
import { marked } from "marked";

function openLinksInNewTab(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  tpl.content.querySelectorAll("a").forEach((a) => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
  return tpl.innerHTML;
}

/** Markdown from the assistant -> safe HTML. Email content is untrusted, so always sanitize. */
export function markdownToHtml(md) {
  // breaks: keep single line breaks (e.g. "Thanks,\nAishwarya" in a draft) instead of joining the lines.
  return openLinksInNewTab(DOMPurify.sanitize(marked.parse(md || "", { async: false, breaks: true })));
}

/** An HTML email body -> safe HTML for the preview. */
export function sanitizeEmailHtml(html) {
  return openLinksInNewTab(DOMPurify.sanitize(html, { FORBID_TAGS: ["style", "img"], FORBID_ATTR: ["style"] }));
}

export const LOOKS_LIKE_HTML = /<\/?(p|br|div|ul|ol|li|strong|b|em|i|h[1-6]|table|span|a)\b[^>]*>/i;

/** Markdown -> readable plain text for the editable notes summary (keeps list numbers and bullets). */
export function toPlain(md) {
  return md
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/(^|[^*\w])[*_]([^*_\n]+)[*_](?=[^*\w]|$)/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "• ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export const storage = {
  get(k) {
    try { return localStorage.getItem(k); } catch { return null; }
  },
  set(k, v) {
    try { localStorage.setItem(k, v); } catch { /* private mode */ }
  },
  remove(k) {
    try { localStorage.removeItem(k); } catch { /* private mode */ }
  },
};

export function isoDate(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

export function fromIso(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function formatDate(d, opts) {
  return d.toLocaleDateString("en-GB", opts);
}

export function clockTime(d = new Date()) {
  return d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

export function autoGrow(t, max = 200) {
  if (!t) return;
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, max)}px`;
}

let nextId = 0;
export const uid = () => `m${++nextId}`;
