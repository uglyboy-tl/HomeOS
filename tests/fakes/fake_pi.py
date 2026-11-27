#!/usr/bin/env python3
"""测试用的假 pi：说 RPC 协议，行为由环境变量决定，够覆盖状态机又不联网。

- FAKE_PI_TEXT        本轮正文（按 2 字一帧发 text_delta）
- FAKE_PI_TOOL        本轮"调用"一个工具（tool_start + tool_end）
- FAKE_PI_ERROR       本轮以错误结束（agent_error）
- FAKE_PI_CRASH_AFTER 发这么多帧后进程自杀（模拟崩溃）
- FAKE_PI_STDERR      往 stderr 写这几行（验证我们不再把 stderr 丢掉）
- FAKE_PI_SLEEP       正文发完后停这么久再收尾（测“正在跑”与 abort）
- FAKE_PI_BIG         发一条 200KB 的单行事件（测 stdout 行上限）
- FAKE_PI_DIE_ON_STATE 收到 get_state 就退出（测“等命令时进程死了”）
- FAKE_PI_EXIT        --print 模式下用这个退出码失败
- FAKE_PI_PRINT_HANG  --print 模式下不退出（测 ASK_TIMEOUT）
- FAKE_PI_IGNORE_CLOSE   stdin 关了也不退出（测升级到 TERM）
- FAKE_PI_IGNORE_TERM    SIGTERM 也不理（测升级到 KILL）
- FAKE_PI_DELAY      每条命令回应前先等这么久（测命令超时）
- FAKE_PI_NOISE      回话前先发一帧非 JSON 与一帧未知类型
- FAKE_PI_UI_REQUEST 发一帧 extension_ui_request，并把我们的回应写进 FAKE_PI_UI_FILE
"""

import json
import os
import signal
import sys
import time

# 默认 id 带 pid：没收到 --session-id 时新进程必须是"另一个会话"，否则接续会话的用例测不出来
SESSION = {
    "id": os.environ.get("FAKE_PI_SESSION") or f"fake-session-{os.getpid()}",
    "messages": 0,
}


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def respond(req_id, command, data=None, success=True, error=None):
    payload = {"id": req_id, "type": "response", "command": command, "success": success}
    if data is not None:
        payload["data"] = data
    if error is not None:
        payload["error"] = error
    send(payload)


def state():
    return {
        "sessionId": SESSION["id"],
        "sessionFile": f"/tmp/sessions/{SESSION['id']}.jsonl",
        "messageCount": SESSION["messages"],
        "isStreaming": False,
    }


def write_stderr():
    text = os.environ.get("FAKE_PI_STDERR", "")
    if not text:
        return
    for line in text.splitlines():
        sys.stderr.write(line + "\n")
    sys.stderr.flush()


def run_turn():
    text = os.environ.get("FAKE_PI_TEXT", "")
    tool = os.environ.get("FAKE_PI_TOOL", "")
    crash_after = int(os.environ.get("FAKE_PI_CRASH_AFTER") or 0)
    error = os.environ.get("FAKE_PI_ERROR", "")
    write_stderr()
    if os.environ.get("FAKE_PI_BIG"):
        delta = {"type": "text_delta", "delta": "x" * 200_000}
        send({"type": "message_update", "assistantMessageEvent": delta})
        SESSION["messages"] += 2
        send({"type": "agent_settled", "aborted": False})
        return
    for sent, offset in enumerate(range(0, len(text), 2), start=1):
        delta = {"type": "text_delta", "delta": text[offset : offset + 2]}
        send({"type": "message_update", "assistantMessageEvent": delta})
        if crash_after and sent >= crash_after:
            sys.exit(1)
    if tool:
        send({"type": "tool_execution_start", "toolCallId": "t1", "toolName": tool, "args": {}})
        send(
            {
                "type": "tool_execution_end",
                "toolCallId": "t1",
                "toolName": tool,
                "result": {},
                "isError": False,
            }
        )
    if error:
        send({"type": "agent_error", "message": error})
    time.sleep(float(os.environ.get("FAKE_PI_SLEEP") or 0))
    SESSION["messages"] += 2
    send({"type": "agent_settled", "aborted": False})


def stay_forever():
    """把进程挂住：等 stdin EOF、SIGTERM 或 SIGKILL 来结束它。"""
    while True:
        time.sleep(0.05)


def main():
    if os.environ.get("FAKE_PI_IGNORE_TERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    argv = sys.argv[1:]
    if "--version" in argv:
        print("fake-pi 1.0")
        return
    # 真 pi 用 --session-id 续会话；假 pi 也要认，否则"接上旧会话"的用例测不到东西
    for index, arg in enumerate(argv):
        if arg == "--session-id" and index + 1 < len(argv):
            SESSION["id"] = argv[index + 1]
    if "--print" in argv:
        if os.environ.get("FAKE_PI_PRINT_HANG"):
            stay_forever()
        code = int(os.environ.get("FAKE_PI_EXIT") or 0)
        if code:
            sys.stderr.write("假 pi 按 FAKE_PI_EXIT 失败\n")
            raise SystemExit(code)
        print(f"答复:{argv[-1]}")
        return
    if os.environ.get("FAKE_PI_UI_REQUEST"):
        send({"type": "extension_ui_request", "id": "ui-1", "method": "confirm"})
    if os.environ.get("FAKE_PI_NOISE"):
        sys.stdout.write("这一行不是 JSON\n")
        sys.stdout.flush()
        send({"type": "队列变化", "detail": "状态机不关心这个"})
    while True:
        line = sys.stdin.readline()
        if not line:
            if os.environ.get("FAKE_PI_IGNORE_CLOSE"):
                stay_forever()
            return
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind, req_id = msg.get("type"), msg.get("id")
        if kind == "extension_ui_response":
            target = os.environ.get("FAKE_PI_UI_FILE")
            if target:
                with open(target, "w", encoding="utf-8") as handle:
                    json.dump(msg, handle)
            continue
        delay = float(os.environ.get("FAKE_PI_DELAY") or 0)
        if delay:
            time.sleep(delay)
        if kind == "get_state":
            if os.environ.get("FAKE_PI_DIE_ON_STATE"):
                sys.exit(1)  # 不回话直接死：验证 pending 命令会立刻失败，而不是等到超时
            respond(req_id, kind, state())
        elif kind == "get_session_stats":
            respond(
                req_id,
                kind,
                {
                    "tokens": {"input": 11, "output": 22},
                    "cost": 0.5,
                    "contextUsage": {"tokens": 3, "contextWindow": 100, "percent": 3},
                },
            )
        elif kind == "new_session":
            SESSION["id"] = f"{SESSION['id']}-new"
            SESSION["messages"] = 0
            respond(req_id, kind, {"cancelled": False})
        elif kind == "abort":
            respond(req_id, kind)
            send({"type": "agent_settled", "aborted": True})
        elif kind == "prompt":
            respond(req_id, kind, {"disposition": "started"})
            run_turn()
        else:
            respond(req_id, kind, success=False, error=f"假 pi 不认识 {kind}")


if __name__ == "__main__":
    main()
