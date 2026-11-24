"""Terminal chat with approval prompts.

    python chat.py <user_id>
"""
import sys
import uuid
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent import make_agent
from memory import save_memory


def ask(request: dict) -> dict:
    """One decision for one gated tool call."""
    print(f"\nApproval needed:\n{request.get('description') or request['name']}")
    if input("Approve? [y/N] ").strip().lower() == "y":
        return {"type": "approve"}
    feedback = input("What should change? (optional) ").strip()
    return {"type": "reject", "message": feedback or "The user declined this action. Do not retry it."}


OUTPUT_DIR = Path(__file__).parent / "outputs"


def new_transcript(user_id: str) -> Path:
    """One markdown file per chat session, e.g. outputs/alice-20261123-143005.md."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    started = datetime.now()
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in user_id)
    path = OUTPUT_DIR / f"{safe_id}-{started:%Y%m%d-%H%M%S}.md"
    path.write_text(f"# Agent session: {user_id}\n\nStarted {started:%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")
    return path


def append_turn(path: Path, question: str, answer: str, todos: list[dict]) -> None:
    parts = [f"\n---\n\n## {datetime.now():%H:%M:%S} · You\n\n{question}\n\n## Agent\n\n{answer}\n"]
    if todos:
        marks = {"completed": "x", "in_progress": "~", "pending": " "}
        parts.append("\n**Plan**\n\n" + "\n".join(f"- [{marks.get(t['status'], ' ')}] {t['content']}" for t in todos) + "\n")
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(parts))


def main(user_id: str):
    agent = make_agent(user_id)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    transcript = new_transcript(user_id)
    print(f"Connected-apps deep agent. Ctrl+C to quit.\nSaving replies to {transcript}")
    while True:
        text = input("\nyou> ").strip()
        if not text:
            continue
        result = agent.invoke({"messages": [HumanMessage(text)]}, config)
        # The main agent or a subagent may pause; parallel subagents can pause at the same time.
        while interrupts := result.get("__interrupt__"):
            resume = {i.id: {"decisions": [ask(r) for r in i.value["action_requests"]]} for i in interrupts}
            result = agent.invoke(Command(resume=resume), config)
        todos = result.get("todos") or []
        if todos:
            print("\nplan: " + " | ".join(f"[{t['status']}] {t['content']}" for t in todos))
        answer = result["messages"][-1].text
        print(f"\nagent> {answer}")
        append_turn(transcript, text, answer, todos)
        save_memory(user_id)  # keep what the assistant learned (stored in MySQL)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
