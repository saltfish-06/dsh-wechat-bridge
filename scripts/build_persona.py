# -*- coding: utf-8 -*-
"""把人设正文编译成 Agent 能吃的 overlay。

    python scripts\\build_persona.py persona\\example.md personas\\example.yml

产物是 cordis patch 格式的 YAML，用 `dsh --profile <profile> --patch <文件>` 加载。

为什么要编译而不是手写 YAML：人设正文是几十上百行的多行文本，手工缩进进 YAML 块标量
几乎必错一处，而且改一次正文就要重缩进一次。

注意 cordis patch 的 **config 是整体替换语义**，所以 personaPrefix 和 personaSuffix
两个键必须都给全，只给一个会把另一个清空。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 控制台编码兜底：中文 Windows 的控制台默认是 GBK，遇到 → ← ✓ 这类符号会直接抛
# UnicodeEncodeError 把脚本打死（实测：find_dsh.py 崩在打印对勾上）。
# 让不可编码的字符退化成 ?，别崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

# 追加到人设末尾的通道规矩。想改就改，或者把这段挪进你自己的正文里。
DEFAULT_SUFFIX_RULES = """\
────────────────────────────────────────
即时通讯通道补充规矩（与上面冲突时以本节为准）：

- 这是聊天窗口，不是文档。**短问短答**。
- **默认不要调用任何工具。**对方多半只是想聊天。只有他明确要你查/改/跑某个东西时才动手。
- 不要输出 Markdown 表格、多级标题、长代码块。要列点就用短横线，最多三条。
- 口语化。不要写"首先/其次/综上"，不要写小标题，不要写"还有什么可以帮你的"。
"""


def main(argv: list) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2])
    if not src.is_file():
        print("找不到人设文件：%s" % src)
        return 1

    body = src.read_text(encoding="utf-8").rstrip("\n")
    if not body.strip():
        print("人设文件是空的：%s" % src)
        return 1

    # 正文里的每一行都往右缩 6 格（块标量在 config: 之下要缩 4，内容再缩 2）
    def indent(text: str, n: int = 6) -> str:
        pad = " " * n
        return "\n".join((pad + ln).rstrip() if ln.strip() else "" for ln in text.splitlines())

    out = []
    out.append("# 由 scripts/build_persona.py 从 %s 生成，别手改这个文件。" % src.name)
    out.append("# 改人设请改正文，然后重新编译：")
    out.append("#   python scripts\\build_persona.py %s %s" % (src, dst))
    out.append("")
    out.append("- id: system-prompt")
    out.append("  config:")
    out.append("    personaPrefix: |-")
    out.append(indent(body))
    out.append("")
    out.append(indent(DEFAULT_SUFFIX_RULES.rstrip("\n")))
    out.append("    personaSuffix: |-")
    out.append("      Your working directory is {{cwd}}.")
    out.append("")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")
    print("已写出 %s（正文 %d 行，共 %d 字符）"
          % (dst, len(body.splitlines()), len(dst.read_text(encoding='utf-8'))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
