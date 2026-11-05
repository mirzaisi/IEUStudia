"""Stand-in for the Claude Code CLI in stream-json mode (tests only).

Usage: fake_claude.py <system prompt file>. Each user message is answered with
"Answer <n> (images <k>, replay <bool>, lectures <m>): <question>", streamed in small text deltas.
A question containing SLOW streams slowly (so it can be interrupted), CRASH exits with an error,
LIMIT returns Claude's usage-limit error. With FAKE_CLAUDE_LOG set, each message's blocks (without their data) are
written to that file, one message per line.
"""

import collections
import json
import os
import queue
import sys
import threading
import time


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def control_ok(msg):
    out({"type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id"),
                                                  "response": {}}})


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        system = f.read()
    lines = queue.Queue()

    def reader():
        for line in sys.stdin:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    backlog = collections.deque()
    turns = 0
    while True:
        line = backlog.popleft() if backlog else lines.get()
        if line is None:
            return
        msg = json.loads(line)
        if msg.get("type") == "control_request":
            control_ok(msg)
            continue
        if msg.get("type") != "user":
            continue
        turns += 1
        content = msg["message"]["content"]
        if os.environ.get("FAKE_CLAUDE_LOG"):            # what Claude Code got, block by block (no picture data: its size)
            with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as f:
                f.write(json.dumps([{**{k: v for k, v in b.items() if k != "source"},
                                     **({"size": len((b.get("source") or {}).get("data") or "")} if "source" in b else {})}
                                    for b in content]) + "\n")
        texts = [b["text"] for b in content if b.get("type") == "text"]
        question = texts[-1] if texts else ""
        flaky = os.environ.get("FAKE_CLAUDE_FLAKY_MARKER", "")
        if "CRASH" in question or ("FLAKY" in question and flaky and not os.path.exists(flaky)):
            if flaky:
                open(flaky, "w").close()                  # FLAKY: fail only the first time
            sys.stderr.write("boom: simulated crash\n")
            sys.stderr.flush()
            sys.exit(3)
        if "LIMIT" in question:
            out({"type": "result", "subtype": "success", "is_error": True,
                 "result": "Claude AI usage limit reached|1792568400", "usage": {}})
            continue
        out({"type": "system", "subtype": "init"})
        out({"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.25, "resetsAt": 1792568400},
                                                    "seven_day": {"utilization": 0.5, "resetsAt": 1792965600}}}})
        images = sum(1 for b in content if b.get("type") == "image")
        replay = any("conversation so far" in t for t in texts)
        slow = "SLOW" in question
        answer = (" ".join(["word"] * 300) if slow else
                  f"Answer {turns} (images {images}, replay {replay}, lectures {system.count(chr(10) + '# ')}): "
                  f"{question}")
        out({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                               "content_block": {"type": "text", "text": ""}}})
        interrupted = False
        for i in range(0, len(answer), 8):
            try:
                pending = lines.get_nowait()
            except queue.Empty:
                pending = None
            if pending is not None:
                m = json.loads(pending)
                if m.get("type") == "control_request" and (m.get("request") or {}).get("subtype") == "interrupt":
                    control_ok(m)
                    interrupted = True
                    break
                backlog.append(pending)
            out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                   "delta": {"type": "text_delta", "text": answer[i:i + 8]}}})
            if slow:
                time.sleep(0.03)
        if interrupted:
            out({"type": "user", "message": {"role": "user", "content": [{"type": "text",
                                                                          "text": "[Request interrupted by user]"}]}})
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": None,
                 "usage": {"input_tokens": 0, "output_tokens": 0}})
            continue
        out({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
        out({"type": "result", "subtype": "success", "is_error": False, "result": answer,
             "usage": {"input_tokens": 12, "output_tokens": 7, "cache_read_input_tokens": 100 * (turns - 1),
                       "cache_creation_input_tokens": 50}})


if __name__ == "__main__":
    main()
