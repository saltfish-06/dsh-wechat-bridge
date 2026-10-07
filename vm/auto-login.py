# -*- coding: utf-8 -*-
"""微信重启后自动点「登录」。

WeChat 4.x 每次重启都要求重新登录：不用扫码（它记得账号，头像都在），
但那个绿色的「登录」按钮得有人点一下。VirtualBox 里没人，本脚本代劳。

判据（重要，第一版踩过坑）：
  **不要用 UIA 物化去判断登录状态** —— 微信停在登录页时，wechatauto 的
  ensure_window() 不是返回 False，而是**无限期挂住**（实测卡了 4 分钟没动静）。
  改用窗口尺寸：登录页实测 274x380，主窗口大得多。纯 pygetwindow，秒回。

用法：python auto-login.py     （开机自启脚本里会调它）
"""
from __future__ import annotations

import os
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import pyautogui  # noqa: E402

# 控制台编码兜底：中文 Windows 的控制台默认是 GBK，遇到 → ← ✓ 这类符号会直接抛
# UnicodeEncodeError 把脚本打死（实测：find_dsh.py 崩在打印对勾上）。
# 让不可编码的字符退化成 ?，别崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.2

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autologin.log")
WINDOW_TITLES = ("微信", "WeChat", "Weixin")
# 窗口宽度到这个数以上，就当它是主窗口（登录页只有 274 宽）
MAIN_WINDOW_MIN_WIDTH = 500
# 「登录」按钮在登录页里的相对位置（实测：水平居中，垂直约 77%）
BTN_X_RATIO = 0.50
BTN_Y_RATIO = 0.77
# 整个过程最多等多久
TOTAL_TIMEOUT = 240


def log(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def wechat_window():
    """返回微信窗口对象（登录页或主窗口），找不到返回 None。"""
    try:
        import pygetwindow as gw
    except Exception as e:  # noqa: BLE001
        log("pygetwindow 不可用：%s" % e)
        return None
    best = None
    for w in gw.getAllWindows():
        try:
            title = (w.title or "").strip()
            if title in WINDOW_TITLES and w.width > 150 and w.height > 150:
                # 有多个就挑最大的
                if best is None or w.width * w.height > best.width * best.height:
                    best = w
        except Exception:  # noqa: BLE001
            continue
    return best


def main() -> int:
    log("=" * 48)
    log("微信自动登录看门狗启动")

    deadline = time.time() + TOTAL_TIMEOUT
    tries = 0
    clicked = 0

    while time.time() < deadline:
        tries += 1
        w = wechat_window()
        if w is None:
            log("第 %d 次：没看到微信窗口，等 5 秒" % tries)
            time.sleep(5)
            continue

        if w.width >= MAIN_WINDOW_MIN_WIDTH:
            log("第 %d 次：窗口 %dx%d，已经是主窗口 → 已登录 ✓" % (tries, w.width, w.height))
            return 0

        x = int(w.left + w.width * BTN_X_RATIO)
        y = int(w.top + w.height * BTN_Y_RATIO)
        log("第 %d 次：登录页 %dx%d @(%d,%d) → 点「登录」(%d,%d)"
            % (tries, w.width, w.height, w.left, w.top, x, y))
        try:
            pyautogui.click(x, y)
            clicked += 1
        except Exception as e:  # noqa: BLE001
            log("点击失败：%s" % e)
        time.sleep(15)

    log("超时退出：点了 %d 次还没进主窗口，可能需要人工扫码" % clicked)
    return 1


if __name__ == "__main__":
    sys.exit(main())
