# -*- coding: utf-8 -*-
"""微信 <-> DSH 桥接（虚拟机版）

跟老 bridge.py 的区别：**主机不再碰微信**。

    主机：轮询适配器拿来信 → 问 DSH → 把回复发回适配器
    虚拟机：adapter.py 负责一切微信操作（读库、UIA 驱动、热激活）

所以这一版不需要空闲门控、不需要窗口还原、不需要抢鼠标的顾虑——
那些事现在都发生在虚拟机里，你的主机一根毛都不会被碰。

接口（虚拟机里的 adapter.py，经 NAT 端口转发到 127.0.0.1:8765）：
    GET  /recv  -> {"messages":[{chat,name,content,ts}, ...]}   取走并清空
    POST /send  -> {"chat":..., "text":...}
    GET  /status
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# 控制台编码兜底：中文 Windows 的控制台默认是 GBK，遇到 → ← ✓ 这类符号会直接抛
# UnicodeEncodeError 把脚本打死（实测：find_dsh.py 崩在打印对勾上）。
# 让不可编码的字符退化成 ?，别崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

BOT_DIR = Path(__file__).resolve().parent
LOG_DIR = BOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 人设：personas\<代号>.yml 是 dsh --patch 能吃的 overlay，由
# scripts/build_persona.py 从 persona\<代号>.md 编译出来。
# persona.txt 里写当前用哪个代号（一个词，比如 example）。
PERSONA_FILE = BOT_DIR / "persona.txt"
PERSONA_DIR = BOT_DIR / "personas"

# 代号别名，省得记全名。按你自己的文件改。
PERSONA_ALIAS: dict = {}

# 微信里发「/人设」列出、「/人设 <代号>」切换
PERSONA_CMD = re.compile(r"^[/／]\s*(?:人设|人設|persona)\s*(.*)$", re.IGNORECASE)


def list_personas() -> list:
    """扫 personas/ 目录，返回可用代号列表。"""
    if not PERSONA_DIR.is_dir():
        return []
    return sorted(p.stem for p in PERSONA_DIR.glob("*.yml"))


def resolve_persona(arg: str) -> str:
    """把用户输入的代号或别名归一成实际代号；认不出返回空。"""
    a = (arg or "").strip().lower()
    if a in list_personas():
        return a
    return PERSONA_ALIAS.get(a, "")


def current_persona() -> str:
    """当前人设代号；没写过或指向不存在的文件时，回落第一个可用的。"""
    avail = list_personas()
    try:
        cid = PERSONA_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        cid = ""
    if cid in avail:
        return cid
    return avail[0] if avail else ""


def current_patch(cfg: dict):
    """当前人设对应的 patch 路径；没有就退回 config 里的 dsh_patch。"""
    cid = current_persona()
    if cid:
        p = PERSONA_DIR / ("%s.yml" % cid)
        if p.is_file():
            return str(p)
    fallback = cfg.get("dsh_patch")
    return fallback if fallback and os.path.isfile(fallback) else None

DEFAULT_CONFIG = {
    # 允许机器人回复的微信联系人 wxid 白名单。留空的话谁也不回——这是默认安全的。
    # "filehelper" 是微信的文件传输助手，拿来自己跟自己测试最方便，不用第二个人。
    "allow": ["filehelper"],
    "adapter_url": "http://127.0.0.1:8765",
    "adapter_token": "",
    "poll_interval": 0.5,
    # ↓↓↓ 这三个按你机器上的实际情况填，或者用 scripts/find_dsh.py 探出来 ↓↓↓
    # Electron 版 DSH 的启动器（同时也是 node 宿主）。
    "dsh_exe": r"C:\Program Files\DeepSeek Harness\DeepSeek Harness.exe",
    # DSH 的 CLI 入口。Electron 版一般在 app.asar 里，见 README「前置条件」。
    "dsh_bin": r"C:\Program Files\DeepSeek Harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js",
    "dsh_profile": "headless",
    # 兜底人设 patch：personas\<当前人设>.yml 不存在时用这个。
    # 留空 = 用 Agent 自己的默认人设。
    "dsh_patch": "",
    "dsh_timeout": 240,
    "max_reply_chars": 1200,
    "split_replies": True,
    "prefix": "",
}

_LOG_LOCK = threading.Lock()
_LOG_FILE = LOG_DIR / ("bridge-%s.log" % datetime.now().strftime("%Y%m%d"))


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    with _LOG_LOCK:
        print(line, flush=True)
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    path = BOT_DIR / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log("config.json 读取失败，用默认值：%s" % e)
    return cfg


# ---------------------------------------------------------------- 适配器客户端
class Adapter:
    def __init__(self, base: str, token: str = ""):
        self.base = base.rstrip("/")
        self.token = (token or "").strip()

    def _headers(self, extra: dict | None = None) -> dict:
        h = dict(extra or {})
        if self.token:
            h["X-Adapter-Token"] = self.token
        return h

    def _get(self, path: str, timeout: float = 10.0):
        req = urllib.request.Request(self.base + path, method="GET",
                                     headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path: str, obj: dict, timeout: float = 120.0):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers=self._headers(
                                         {"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def status(self):
        return self._get("/status", timeout=8.0)

    def recv(self):
        return self._get("/recv", timeout=15.0).get("messages", [])

    def send(self, chat: str, text: str):
        return self._post("/send", {"chat": chat, "text": text})


# ---------------------------------------------------------------- DSH
class DshRunner:
    def __init__(self, cfg: dict, state: dict):
        self.cfg = cfg
        self.state = state

    def ask(self, chat: str, text: str, _retry: int = 0):
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        sid = self.state.get(chat, {}).get("session_id")
        cmd = [self.cfg["dsh_exe"], self.cfg["dsh_bin"], self.cfg["dsh_profile"]]
        patch = current_patch(self.cfg)
        if patch:
            cmd += ["--patch", patch]
        cmd.append("--json")
        if sid:
            cmd += ["--session-id", sid]
        cmd.append("-")

        t0 = time.time()
        # 用 Popen 而不是 subprocess.run：run 超时只杀父进程，而 dsh 会派生一个
        # node 子进程（dsh-subprocess-local），那孩子活得比爹久，会一直攥着会话的
        # 写句柄 —— 下一次请求就报 "already owned by an active write handle"。
        # 实测：16:14 卡过一次，之后到 16:48 所有请求全废。
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 errors="replace", env=env, cwd=str(BOT_DIR),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:  # noqa: BLE001
            return "", "启动失败 %s" % e

        try:
            stdout, stderr = p.communicate(input=text, timeout=self.cfg["dsh_timeout"])
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:  # noqa: BLE001
                pass
            try:
                p.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            log("dsh 超时，已连子进程一起清理（避免会话锁泄漏）")
            return "", "超时（已清理进程树）"

        p_stdout, p_stderr, p_returncode = stdout, stderr, p.returncode

        final_text, usage, new_sid = "", None, None
        for raw in (p_stdout or "").splitlines():
            raw = raw.strip()
            if not raw.startswith("{"):
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "session":
                new_sid = ev.get("sessionId")
            elif t == "final":
                final_text = ev.get("text") or ""
            elif t == "status" and ev.get("usage"):
                usage = ev["usage"]

        if new_sid and new_sid != sid:
            self.state.setdefault(chat, {})["session_id"] = new_sid
            self._flush()

        note = "%.1fs" % (time.time() - t0)
        if usage:
            note += " in=%s out=%s cache=%s" % (usage.get("inputTokens"),
                                                usage.get("outputTokens"),
                                                usage.get("cacheReadTokens"))
        if p_returncode != 0 and not final_text:
            err = [l for l in (p_stderr or "").splitlines() if l.strip()]
            last = (err[-1] if err else "exit=%s" % p_returncode)[:200]
            # 会话被别的进程攥着写句柄：退化成开一个新会话，别让整个机器人哑掉。
            # 只重试一次——不带 session-id 还报这个错就说明不是会话的问题，
            # 再递归下去就是无限套娃（每层都开一个新 dsh 进程）。
            if (sid and _retry < 1
                    and ("already owned" in last or "already owned" in (p_stdout or ""))):
                log("会话被占用，丢掉旧会话重试一次")
                self.state.pop(chat, None)
                self._flush()
                return self.ask(chat, text, _retry + 1)
            return "", "%s 失败: %s" % (note, last)
        return final_text.strip(), note

    def _flush(self):
        try:
            (BOT_DIR / "state.json").write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------- 主程序
class Bridge:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.adapter = Adapter(cfg["adapter_url"], cfg.get("adapter_token", ""))
        self.allow = {a.strip().lower() for a in cfg.get("allow", []) if a.strip()}
        try:
            self.state = json.loads((BOT_DIR / "state.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.state = {}
        self.runner = DshRunner(cfg, self.state)
        self.seen: set = set()
        self.done: "queue.Queue" = queue.Queue()

    def split_reply(self, text: str) -> list:
        if not self.cfg.get("split_replies", True):
            return [text]
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        merged = []
        for p in paras:
            one = " ".join(ln.strip() for ln in p.splitlines() if ln.strip())
            if one:
                merged.append(one)
        if not merged:
            return [text]
        if len(merged) > 3:
            merged = merged[:2] + [" ".join(merged[2:])]
        return merged

    def switch_persona(self, chat: str, arg: str) -> None:
        """处理微信里的「/人设」指令。不走 Agent，因为这是切换 Agent 本身的人设。"""
        cur = current_persona()
        code = resolve_persona(arg)
        avail = list_personas()
        if not arg or arg in ("?", "list", "列表", "help", "帮助"):
            lines = ["当前人设：%s" % (cur or "（无，用 Agent 默认）"), "", "可用："]
            lines += ["- %s" % k for k in avail] or ["（personas 目录是空的）"]
            lines.append("")
            lines.append("发「/人设 代号」切换，下一条消息生效。")
            text = "\n".join(lines)
        elif not code:
            text = "没这个人设。可用：%s" % ("、".join(avail) or "（无）")
        else:
            PERSONA_FILE.write_text(code, encoding="utf-8")
            text = "人设切到「%s」，下一条消息生效。" % code
            log("人设切换：%s -> %s" % (cur, code))
        try:
            r = self.adapter.send(chat, text)
            log("人设指令回复：%s" % ("成功" if r.get("ok") else "失败 %s" % r.get("message", "")))
        except Exception as e:  # noqa: BLE001
            log("人设指令回复异常：%s" % e)

    def worker(self):
        while True:
            msg = self.done.get()
            try:
                chat, content = msg["chat"], msg["content"]
                log("→ 问 Agent（%s）…" % content[:40])
                reply, note = self.runner.ask(chat, content)
                if not reply:
                    log("← 没给出回复（%s）" % note)
                    continue
                log("← Agent %s" % note)
                chunks = self.split_reply(reply)
                for i, c in enumerate(chunks):
                    r = self.adapter.send(chat, c)
                    log("发送%s -> %s%s %s" % ("成功" if r.get("ok") else "失败", msg.get("name", chat),
                                              ("（%d/%d 段）" % (i + 1, len(chunks))) if len(chunks) > 1 else "",
                                              r.get("message", "")))
                    if i + 1 < len(chunks):
                        time.sleep(0.6)
            except Exception:  # noqa: BLE001
                log("处理失败:\n%s" % traceback.format_exc())
            finally:
                self.done.task_done()

    def check_config(self) -> list:
        """开跑之前先把配置问题指出来。等收到第一条微信才报错太晚了。

        注意：**不要用 os.path.isfile 检查 dsh_bin**。Electron 版 DSH 的 CLI
        入口在 app.asar 里面，而 app.asar 对 Python 来说是个普通文件——只有
        Electron 打过补丁的 fs 才把它当目录。Python 的 isfile 一定返回 False，
        照它判断会把所有正常的安装全判成错误。（这个坑真踩过。）
        """
        problems = []
        exe, cli = self.cfg.get("dsh_exe", ""), self.cfg.get("dsh_bin", "")
        if not exe or not os.path.isfile(exe):
            problems.append("dsh_exe 不存在：%s\n"
                            "    → 跑一次  python scripts\\find_dsh.py  它会帮你找到并打印正确的值" % exe)
        if not cli:
            problems.append("dsh_bin 是空的\n"
                            "    → 跑一次  python scripts\\find_dsh.py")
        elif ".asar" in cli and not os.path.exists(cli):
            log("提示：Python 看不到 app.asar 内部的路径，这条本程序没法替你验证——"
                "只要 Agent 那边跑得起来就没问题。")
        if not self.cfg.get("adapter_url"):
            problems.append("adapter_url 是空的")
        return problems

    def check_warnings(self) -> list:
        """不拦启动，但要吼一声的问题。"""
        warns = []
        if not self.allow:
            warns.append("allow 白名单是空的——机器人不会回复任何人。\n"
                         "    → 想测：先用 filehelper（文件传输助手）自己跟自己发消息\n"
                         "    → 想知道对方 wxid：让他发一条，日志里会打印")
        return warns

    def run(self) -> int:
        log("=" * 56)
        log("微信 <-> Agent 桥接")
        log("白名单: %s" % (sorted(self.allow) or "（空——谁也不回）"))
        log("适配器: %s" % self.cfg["adapter_url"])
        _cp = current_persona()
        log("人设: %s -> %s" % (_cp or "（Agent 默认）", current_patch(self.cfg) or "（无 patch）"))
        log("=" * 56)

        problems = self.check_config()
        if problems:
            log("配置有问题，先修好再跑：")
            for p in problems:
                log("  · %s" % p)
            return 2
        for w in self.check_warnings():
            log("警告：%s" % w)

        try:
            st = self.adapter.status()
            log("适配器在线：账号 %s（wxid %s）微信就绪=%s"
                % (st.get("account"), st.get("wxid"), st.get("ready")))
            if st.get("ready") is False:
                log("注意：微信还没登录（停在扫码页）。你扫码后适配器会自己接管，不用重启本程序。")
        except Exception as e:  # noqa: BLE001
            log("适配器连不上：%s" % e)
            log("（虚拟机开着吗？适配器起了吗？端口转发配了吗？）")
            return 1

        threading.Thread(target=self.worker, daemon=True, name="dsh-worker").start()

        interval = float(self.cfg.get("poll_interval") or 2.0)
        log("开始轮询来信（每 %.1fs 一次）…" % interval)
        while True:
            try:
                for m in self.adapter.recv():
                    chat = (m.get("chat") or "").lower()
                    key = (m.get("chat"), m.get("ts"), (m.get("content") or "")[:60])
                    if key in self.seen:
                        continue
                    self.seen.add(key)
                    if len(self.seen) > 5000:
                        self.seen.clear()
                    if chat not in self.allow:
                        log("收到（不在白名单，忽略）[%s / %s] %s"
                            % (m.get("name"), m.get("chat"), (m.get("content") or "")[:60]))
                        continue

                    arg = PERSONA_CMD.match((m.get("content") or "").strip())
                    if arg:
                        self.switch_persona(chat, arg.group(1).strip().lower())
                        continue

                    log("收到 [%s] %s" % (m.get("name"), (m.get("content") or "")[:60]))
                    self.done.put(m)
            except Exception as e:  # noqa: BLE001
                log("轮询出错：%s: %s" % (type(e).__name__, e))
            time.sleep(interval)


LOCK_FILE = BOT_DIR / "bridge.lock"


def acquire_singleton() -> bool:
    """单实例锁：跑两个桥接会各回一遍消息，用户收到双份。

    锁文件里写自己的 pid；发现已有锁就看看那个 pid 还活着没
    （上次崩溃留下的死锁不该拦住这次启动）。
    """
    if LOCK_FILE.exists():
        try:
            other = int(LOCK_FILE.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            other = 0
        if other:
            try:
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, other)  # QUERY_LIMITED
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    log("已有桥接在跑（pid=%d），本实例退出" % other)
                    return False
            except Exception:  # noqa: BLE001
                pass
        log("发现残留锁（pid=%s 已不在），接管" % (other or "?"))
    try:
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as e:
        log("写锁文件失败（不影响运行）：%s" % e)
    return True


def release_singleton() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def main() -> int:
    cfg = load_config()
    if not acquire_singleton():
        return 3
    try:
        return Bridge(cfg).run()
    except KeyboardInterrupt:
        log("收到 Ctrl+C，退出")
        return 0
    except Exception:  # noqa: BLE001
        log("致命错误:\n%s" % traceback.format_exc())
        return 2
    finally:
        release_singleton()


if __name__ == "__main__":
    sys.exit(main())
