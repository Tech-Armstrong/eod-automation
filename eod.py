"""Sending EOD reports: the recipients are fixed by the app, never chosen by the model.

    To: DEFAULT_RECIPIENTS (always; override with EOD_RECIPIENTS in .env, comma separated)
    Cc: the user's reporting manager, if they said at sign-in that they have one

The agent writes the report and calls `send_eod_report(subject, body_html)`. That call always stops for the user's
approval (see agent.py): the web app shows the email exactly as it will go out, with To and Cc, and the user can
Approve, Decline, or "Add points" (the agent rewrites the report with them and asks again).
"""
import os

from langchain_core.tools import tool

import auth

DEFAULT_RECIPIENTS = [
    e.strip().lower()
    for e in os.getenv("EOD_RECIPIENTS", "ranjitha@armstrong-cap.com,manju@armstrong-cap.com").split(",")
    if e.strip()
]
TOOL_NAME = "send_eod_report"


def recipients(user_id: str, extra_cc: list[str] | None = None) -> dict:
    """{to: [...], cc: [...]} for this user's EOD: the defaults, plus their manager (and any extra Cc) in Cc."""
    to = list(DEFAULT_RECIPIENTS)
    cc = []
    manager = auth.get_manager(user_id)
    for e in [manager["manager_email"], *(extra_cc or [])]:
        e = auth.normalize_email(e or "")
        if e and e != user_id and e not in to and e not in cc:
            cc.append(e)
    return {"to": to, "cc": cc}


def preview(user_id: str, args: dict) -> list[dict]:
    """The approval card for a send_eod_report call: the email as Gmail will send it."""
    r = recipients(user_id, args.get("extra_cc"))
    return [{
        "tool": "GMAIL_SEND_EMAIL",
        "kind": "eod",  # the web app offers "Add points" on this card
        "arguments": {"to": r["to"], **({"cc": r["cc"]} if r["cc"] else {}), "subject": args.get("subject", ""),
                      "body": args.get("body_html", ""), "is_html": True},
    }]


def send(user_id: str, subject: str, body_html: str, extra_cc: list[str] | None = None) -> dict:
    from composio_setup import client

    r = recipients(user_id, extra_cc)
    args = {"user_id": "me", "recipient_email": r["to"][0], "extra_recipients": r["to"][1:], "cc": r["cc"],
            "subject": subject, "body": body_html, "is_html": True}
    account = auth.verified_accounts(user_id).get("gmail")  # the mailbox confirmed at sign-in
    res = client().tools.execute(
        "GMAIL_SEND_EMAIL", args, user_id=user_id, dangerously_skip_version_check=True,
        **({"connected_account_id": account} if account else {}),
    )
    data = res if isinstance(res, dict) else getattr(res, "__dict__", {})
    if data.get("successful") is False or data.get("error"):
        return {"sent": False, "error": str(data.get("error") or "Gmail did not accept the email.")}
    return {"sent": True, "to": r["to"], "cc": r["cc"]}


def eod_tools(user_id: str) -> list:
    @tool(TOOL_NAME)
    def send_eod_report(subject: str, body_html: str, extra_cc: list[str] | None = None) -> dict:
        """Send the user's EOD report by email. ALWAYS use this for EOD reports, never GMAIL_SEND_EMAIL.

        The app adds the recipients itself (the fixed EOD list in To, the user's reporting manager in Cc), so never
        ask the user who it goes to. The user sees the email and approves it, declines it, or adds points first.

        Args:
            subject: e.g. "EOD Report – 24 Nov 2026".
            body_html: The full report in simple HTML (<p>, <strong>, <br>), in the EOD shape from your instructions.
            extra_cc: Only if the user explicitly asks to copy someone else in; otherwise leave empty.
        """
        try:
            return send(user_id, subject, body_html, extra_cc)
        except Exception as e:  # network, expired Google connection, ...
            return {"sent": False, "error": str(e)}

    return [send_eod_report]
