# -*- coding: utf-8 -*-
"""虚拟机侧的「微信适配器」。

职责：只干跟微信打交道的事——读消息、发消息——然后对外开一个极简 HTTP 接口，
让主机上的 bridge.py 来调。DSH 和人设都留在主机，不进虚拟机。

接口：
    GET  /status          -> {"ok":true,"account":...,"wxid":...,"queue":N}
    GET  /recv            -> {"messages":[{"chat","name","content","ts"}, ...]}  取走并清空
    POST /send            -> body {"chat":"wxid_xxx","text":"..."}  -> {"ok":true}

为什么是 HTTP：虚拟机里 Python 标准库自带 http.server，不用装任何依赖，
主机侧用 urllib 就行。主机通过 VirtualBox 的 NAT 端口转发访问本机 127.0.0.1:8765。

启动：python adapter.py       （工作目录必须是本文件所在目录）
"""
from __future__ import annotations

import hmac
import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 控制台编码兜底：中文 Windows 的控制台默认是 GBK，遇到 → ← ✓ 这类符号会直接抛
# UnicodeEncodeError 把脚本打死（实测：find_dsh.py 崩在打印对勾上）。
# 让不可编码的字符退化成 ?，别崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

# wechatauto 会在【当前工作目录】建 wechatauto_logs，所以先钉死工作目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from wechatauto import WeChatDB                      # noqa: E402
    from wechatauto.db import Listener                   # noqa: E402
    from wechatauto.guia import WeChatGUI                # noqa: E402
    from wechatauto.uia_driver import WeChatUIA          # noqa: E402
except ImportError as _e:  # noqa: BLE001
    print("=" * 62)
    print("缺少依赖：%s" % _e)
    print()
    print("先在虚拟机里装依赖：")
    print("  python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple wechatauto-replica")
    print()
    print("如果已经装了还是报这个错，多半是装到了另一个 Python 下。")
    print("确认一下：  python -c \"import wechatauto, sys; print(wechatauto.__file__, sys.executable)\"")
    print("=" * 62)
    raise SystemExit(1)

PORT = int(os.environ.get("ADAPTER_PORT", "8765"))
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter.log")

# 可选令牌。本机通过 NAT 端口转发（绑定 127.0.0.1）访问时用不着，
# 但要是你把端口暴露到局域网，没有它等于把微信的发消息权限送人。
# 取值顺序：环境变量 ADAPTER_TOKEN > 同目录下 adapter-token.txt
TOKEN = (os.environ.get("ADAPTER_TOKEN") or "").strip()
if not TOKEN:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "adapter-token.txt"), encoding="utf-8") as _f:
            TOKEN = _f.read().strip()
    except OSError:
        TOKEN = ""

_lock = threading.Lock()
_queue: list = []            # 待取走的来信
_sent: list = []             # 刚发出去的（用于识别自己的回声）
_gui = None
_db = None


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    with _lock:
        print(line, flush=True)
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


# --------------------------------------------------------------------------
# 上游 bug 补丁：current_chat() 会带上"按住鼠标 语音输入文字"这个后缀
# （跟主机侧 bridge.py 里那条补丁同一个问题，虚拟机里一样会犯）
# --------------------------------------------------------------------------
_HINT = re.compile(
    r"(按住(鼠标)?\s*语音输入文字|按住\s*说话|按住\s*录音|按住鼠标左键说话|"
    r"Hold\s+to\s+(Talk|Speak))\s*$",
    re.IGNORECASE,
)


def patch_current_chat() -> None:
    if getattr(WeChatUIA.current_chat, "_fish_patched", False):
        return
    orig = WeChatUIA.current_chat

    def current_chat(self):  # noqa: ANN001
        name = orig(self)
        if not name:
            return name
        return _HINT.sub("", name).strip() or name

    current_chat._fish_patched = True
    WeChatUIA.current_chat = current_chat


_init_lock = threading.Lock()


def get_db():
    global _db
    with _init_lock:
        if _db is None:
            _db = WeChatDB()
        return _db


def get_gui():
    """复用同一个 WeChatGUI（每条消息重建一次要重新找窗口/加载校准）。"""
    global _gui
    with _init_lock:
        if _gui is None:
            _gui = WeChatGUI()
            return _gui
        try:
            if not _gui.main_hwnd or not _gui.ensure_visible():
                raise RuntimeError("窗口失效")
        except Exception:  # noqa: BLE001
            log("GUI 实例失效，重建")
            _gui = WeChatGUI()
        return _gui


def recent_echo(chat: str, content: str) -> bool:
    now = time.time()
    with _lock:
        for item in list(_sent):
            ts, c, text = item
            if now - ts > 90:
                _sent.remove(item)
                continue
            if c == chat and (text == content or content in text or text in content):
                return True
    return False


# --------------------------------------------------------------------------
# 消息回调
# --------------------------------------------------------------------------
def on_message(msg, _listener=None) -> None:
    try:
        chat = msg.get("chat") or msg.get("username") or ""
        if not chat:
            return
        content = (msg.get("content") or "").strip()
        if not content or content.startswith("<?xml") or content.startswith("<msg>"):
            return

        db = get_db()
        sender = (msg.get("sender_wxid") or msg.get("sender_username")
                  or msg.get("wxid") or "")
        if sender and db.wxid and sender == db.wxid:
            return
        if recent_echo(chat, content):
            return

        try:
            name = db.get_nickname(chat) or chat
        except Exception:  # noqa: BLE001
            name = chat

        with _lock:
            _queue.append({
                "chat": chat,
                "name": name,
                "content": content,
                "ts": msg.get("create_time"),
                "at": time.time(),
            })
        log("收到 [%s] %s" % (name, content[:60]))
    except Exception:  # noqa: BLE001
        log("回调异常:\n%s" % traceback.format_exc())


def do_send(chat: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}

    # 微信没登录时不立刻报错——你可能正在扫码，给它 30 秒
    if not _ready.is_set():
        _ready.wait(timeout=30)
    if not _ready.is_set():
        return {"ok": False, "error": "微信还没登录（适配器在等登录，扫完码自己就活了）"}

    db = get_db()
    try:
        name = db.get_nickname(chat) or chat
    except Exception:  # noqa: BLE001
        name = chat

    # 先记账再发：消息落库比 send_msg 返回早，监听会先捞到（同主机侧那个坑）
    rec = (time.time(), chat, text)
    with _lock:
        _sent.append(rec)
        if len(_sent) > 50:
            del _sent[:-50]

    try:
        gui = get_gui()
        r = gui.send_msg(text, name, verify=False)
        ok = bool(r)
        if not ok:
            with _lock:
                try:
                    _sent.remove(rec)
                except ValueError:
                    pass
        msg = ""
        try:
            msg = r.get("message") if hasattr(r, "get") else ""
        except Exception:  # noqa: BLE001
            pass
        log("发送%s -> %s %s" % ("成功" if ok else "失败", name, msg or ""))
        return {"ok": ok, "message": msg}
    except Exception as e:  # noqa: BLE001
        log("发送异常: %s: %s" % (type(e).__name__, e))
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):        # 别把访问日志刷进控制台
        return

    def _authed(self) -> bool:
        """配了令牌就必须带对，否则谁都能拿它发微信。没配则放行（本机端口转发场景）。"""
        if not TOKEN:
            return True
        got = (self.headers.get("X-Adapter-Token") or "").strip()
        return hmac.compare_digest(got, TOKEN)

    def do_GET(self):  # noqa: N802
        if not self._authed():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        if self.path.startswith("/status"):
            with _lock:
                n = len(_queue)
            account = wxid = ""
            try:
                _db_ = get_db()
                account, wxid = _db_.account, _db_.wxid
            except Exception:  # noqa: BLE001
                pass
            self._json({"ok": True, "ready": _ready.is_set(),
                        "account": account, "wxid": wxid, "queue": n})
        elif self.path.startswith("/recv"):
            with _lock:
                items = list(_queue)
                _queue.clear()
            self._json({"messages": items})
        elif self.path.startswith("/ping"):
            self._json({"ok": True, "t": time.time()})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self._authed():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": "bad json: %s" % e}, 400)
            return
        if self.path.startswith("/send"):
            self._json(do_send(data.get("chat") or "", data.get("text") or ""))
        else:
            self._json({"ok": False, "error": "not found"}, 404)


def wechat_window_width() -> int:
    """微信窗口宽度；没有窗口返回 0，取不到返回 -1。

    为什么看尺寸：**登录页上不能碰 UIA**。实测微信停在登录页时，
    WeChatUIA().ensure_window() / ensure_materialized() 不是返回 False，
    而是无限期挂住（卡了 4 分钟没动静）。窗口尺寸是纯 pygetwindow，秒回：
      登录页 274x380   主窗口 >= 500
    """
    try:
        import pygetwindow as gw
    except Exception:  # noqa: BLE001
        return -1
    best = 0
    try:
        for w in gw.getAllWindows():
            try:
                if (w.title or "").strip() in ("微信", "WeChat", "Weixin") and w.width > 150:
                    best = max(best, w.width)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return -1
    return best


MAIN_WINDOW_MIN_WIDTH = 500
_ready = threading.Event()


def warmup(verbose: bool = True) -> bool:
    """把读库、控件树、发送通道都准备好；成功返回 True。"""
    width = wechat_window_width()
    if width == 0:
        if verbose:
            log("没看到微信窗口")
        return False
    if 0 < width < MAIN_WINDOW_MIN_WIDTH:
        if verbose:
            log("微信停在登录页（窗口宽 %d）——去虚拟机里点「登录」或扫码" % width)
        return False

    try:
        db = get_db()
        if not db.wxid:
            if verbose:
                log("读不到 wxid，可能还没登录")
            return False
        if verbose:
            log("账号: %s (wxid %s)" % (db.account, db.wxid))
    except Exception as e:  # noqa: BLE001
        if verbose:
            log("读库失败: %s: %s" % (type(e).__name__, e))
        return False

    try:
        u = WeChatUIA()
        if not u.ensure_materialized(timeout=10.0):
            if verbose:
                log("控件树还没物化")
            return False
        if verbose:
            log("控件树物化: True")
    except Exception as e:  # noqa: BLE001
        if verbose:
            log("控件树预热失败: %s: %s" % (type(e).__name__, e))
        return False

    # 预热发送通道：WeChatGUI() 头一次构造要十几秒，放在这里做掉，
    # 用户的第一条消息就不用干等它
    try:
        get_gui()
        if verbose:
            log("发送通道已预热")
    except Exception as e:  # noqa: BLE001
        if verbose:
            log("发送通道预热失败: %s: %s" % (type(e).__name__, e))
        return False
    return True


def warmup_watchdog() -> None:
    """没登录就一直等；登录了自动接管——不用重启适配器。

    这样你什么时候扫码都行：扫完 20 秒内适配器自己就活了。
    """
    while not _ready.is_set():
        if warmup():
            _ready.set()
            log("★ 微信已就绪，开始工作")
            return
        time.sleep(20)


def main() -> int:
    log("=" * 56)
    log("微信适配器启动")
    log("鉴权: %s" % ("已启用（令牌 %d 字符）" % len(TOKEN) if TOKEN else
                    "未启用 —— 只应通过本机端口转发访问，别把端口暴露到局域网"))
    patch_current_chat()
    log("current_chat 后缀清理补丁已应用")

    if warmup():
        _ready.set()
        log("★ 微信已就绪，开始工作")
    else:
        log("微信现在不可用（多半停在登录页）——转入等待模式，你登录后自动接管")

    # 监听：登录前读库会失败，所以放在就绪之后起
    def start_listener() -> None:
        while not _ready.wait(timeout=5):
            pass
        try:
            lst = Listener(get_db(), interval=0.8)
            lst.add_all(on_message)
            lst.start()
            log("监听已启动")
        except Exception as e:  # noqa: BLE001
            log("监听启动失败: %s: %s" % (type(e).__name__, e))

    threading.Thread(target=warmup_watchdog, daemon=True, name="warmup").start()
    threading.Thread(target=start_listener, daemon=True, name="listener").start()

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log("HTTP 接口监听 0.0.0.0:%d" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
