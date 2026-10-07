# -*- coding: utf-8 -*-
"""帮你找到本机 DeepSeek Harness 的那两个路径，填进 config.json。

    python scripts\\find_dsh.py

Electron 版 DSH 的 CLI 入口藏在 app.asar 里，路径长且跟安装位置有关，
所以这里直接扫一遍常见位置，再把结果打印成能直接粘的 JSON。
"""
from __future__ import annotations

import glob
import json
import os
import sys

# 控制台编码兜底：中文 Windows 的控制台默认是 GBK，遇到 → ← ✓ 这类符号会直接抛
# UnicodeEncodeError 把脚本打死（实测：find_dsh.py 崩在打印对勾上）。
# 让不可编码的字符退化成 ?，别崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

CLI_REL = os.path.join("resources", "app.asar", "dsh", "node_modules",
                       "@deepseek-ai", "dsh", "lib", "bin.js")
EXE_NAME = "DeepSeek Harness.exe"


def candidates() -> list:
    roots = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            roots.append(base)
    for drive in ("C:", "D:", "E:"):
        roots.append(drive + os.sep)

    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        exe = os.path.join(root, "DeepSeek Harness", EXE_NAME)
        if os.path.isfile(exe):
            found.append(exe)
        # 装到更深一层的情况，扫两层
        for pattern in (os.path.join(root, "*", EXE_NAME),
                        os.path.join(root, "*", "*", EXE_NAME)):
            found += glob.glob(pattern)
    # 去重但保序
    seen, out = set(), []
    for f in found:
        k = os.path.normcase(f)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def main() -> int:
    print("在找 %s ...\n" % EXE_NAME)
    exes = candidates()
    if not exes:
        print("没找到。请手工把 config.json 的 dsh_exe / dsh_bin 填好。")
        print("（常见位置：C:\\Program Files\\DeepSeek Harness\\ 或你自定义的安装目录）")
        return 1

    # 注意：**不要用 os.path.isfile 判断 CLI 在不在**。Electron 版 DSH 的 CLI
    # 入口在 app.asar 里面，而 app.asar 对 Python 来说是个普通【文件】——只有
    # Electron 打过补丁的 fs 才把它当目录。Python 的 isfile 一定返回 False，
    # 照它判断会把所有正常的安装全判成坏的。（这个坑真踩过，别改回去。）
    out = []
    for exe in exes:
        root = os.path.dirname(exe)
        cli = os.path.join(root, CLI_REL)
        print("  [候选] %s" % exe)
        print("         CLI: %s" % cli)
        out.append({"dsh_exe": exe, "dsh_bin": cli})

    print()
    print("=" * 66)
    print("把下面这段填进 config.json（多个候选就挨个试，能跑起来的就是对的）：")
    print("=" * 66)
    print()
    print(json.dumps(out[0], indent=2, ensure_ascii=False))
    if len(out) > 1:
        print("\n（还有 %d 个别的候选，如果上面那个跑不通再试它们）" % (len(out) - 1))
    print()
    print("验证方法 —— 在 host 目录里跑这句，能回一句话就说明路径对了：")
    print('  "%s" "%s" headless "说一句话"' % (out[0]["dsh_exe"], out[0]["dsh_bin"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
