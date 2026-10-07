# dsh-wechat-bridge

> ⚠️ **纯 AI 编写，未人工审查，可能有致命 bug。** 上生产前请自行通读全部代码。作者不对任何后果负责。

在**微信**里跟你本机的 **DeepSeek Harness**（或任何能接 stdin/stdout 的 CLI Agent）聊天。

微信客户端跑在 **VirtualBox 虚拟机**里，所以自动化点鼠标的动作**全部发生在虚拟机内部**——
你的主机会话不会被抢焦点、不会被抢鼠标，锁屏也不影响。

---

## 为什么要在虚拟机里跑

微信 PC 端的自动化（读本地数据库 + UIA 驱动发消息）必须要有一个**已解锁的交互式桌面**。
直接在你的主机上跑，机器人每次发消息都会抢走你的鼠标和键盘焦点——你正在打字它就抢。

放进虚拟机之后：

| 场景 | 主机直跑 | 虚拟机方案 |
|---|---|---|
| 机器人发消息时你在打字 | ❌ 被抢焦点 | ✅ 无感 |
| 你锁屏（Win+L） | ❌ 自动化直接失效 | ✅ 照常工作 |
| 你最小化/关掉窗口 | — | ✅ 无窗口（headless）运行 |
| 你的微信被机器人操作 | ❌ 就是你的号 | ✅ 隔离在虚拟机里 |

代价：虚拟机要占约 6 GB 内存 + 4 个 CPU 核。

---

## 架构

```
   主机 (Windows)                                虚拟机 (Windows 11)
┌──────────────────────────────┐        ┌────────────────────────────────┐
│  bridge.py                   │        │  adapter.py                    │
│   · 轮询来信（0.5s）          │  HTTP  │   · 读微信数据库（SQLCipher）   │
│   · 拼人设 → 问 Agent        │◄──────►│   · UIA 驱动发消息              │
│   · 把回复切段发回            │  :8765 │   · 控件树热激活                │
│                              │  NAT   │   · 登录看门狗（等扫码）        │
│  dsh headless（Agent 本体）   │ 转发   │            ↓                   │
└──────────────────────────────┘        │        微信 PC 客户端           │
                                        └────────────────────────────────┘
```

**职责划分**：主机持有 Agent、人设、密钥；虚拟机只干"操作微信"这一件事。
适配器对外只有一个 HTTP 接口，你不装任何东西在主机上也能自己调它。

---

## 前置条件

| 需要 | 说明 |
|---|---|
| Windows 主机 | 有 16 GB 以上内存、能开 VirtualBox 的虚拟化 |
| VirtualBox 7.x | 装在主机上 |
| Windows 11 镜像 | 装进虚拟机（微软官网可下） |
| DeepSeek Harness | 装在主机上，`headless` profile 可用 |
| 一个微信小号 | **强烈建议用小号**，别拿主号冒险 |
| Python 3.10+ | 主机和虚拟机各一份 |

> 为什么必须用小号：这套东西要读你的微信本地数据库、要模拟点击。腾讯的账号风控对
> "非官方客户端行为"是不客气的。用小号，封了不心疼。

---

## 安装

分两边装。**先虚拟机，后主机。**

### 一、虚拟机

1. **建虚拟机**：Windows 11，建议 6 GB 内存 / 4 CPU / 80 GB 磁盘，网络用 NAT。
2. **装 Guest Additions**（必须，后面所有远程操作都靠它）。
3. **装微信 PC 端**。装完先**扫码登录一次**（后面重启要求重新登录是腾讯的行为，见「已知坑」）。
4. **装 Python**：`python-3.12.x-amd64.exe`，用 `/quiet InstallAllUsers=0 PrependPath=1` 装到当前用户下
   （`InstallAllUsers=1` 会要管理员权限，而远程执行工具给不了，会静默失败——别踩）。
5. **装依赖**：
   ```bat
   python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple wechatauto-replica
   ```
6. **装 VC++ 运行库**（`vc_redist.x64.exe`）。**不装微信会闪退**，日志里什么都没有，很难查。
7. 把 `vm/` 里的文件拷到 `C:\wxbridge\`，把 `vm/boot-at-logon.cmd` 拷一份到
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`。
8. **关掉 UAC 弹窗**：设置 → 搜索 `uac` → 滑块拉到最底。否则远程触发的安装器会卡在一个
   2 分钟后自动否决的弹窗上，表现为"什么都没发生"。

> `boot-at-logon.cmd` 是给**自己人**看的模板，里面的路径请按你的实际安装位置改。

### 二、主机

1. **配端口转发**（虚拟机运行时也能配）：
   ```powershell
   VBoxManage controlvm "你的虚拟机名" natpf1 "adapter,tcp,127.0.0.1,8765,,8765"
   ```
2. **把 `host/` 里的东西拷到任意目录**，复制 `config.example.json` 为 `config.json` 并填好：
   - `allow`：允许机器人回复的微信联系人 wxid 白名单（**留空 = 谁都不回**）
   - `dsh_exe` / `dsh_bin`：你机器上 DeepSeek Harness 的实际路径
3. **准备人设**（可选，不准备就用 Agent 默认人设）：
   ```bat
   python scripts\build_persona.py persona\example.md personas\example.yml
   ```
   然后 `persona.txt` 里写 `example`。
4. **启动**：`start.bat`

---

## 使用

### 日常

```
双击 host\start.bat          → 桥接跑起来，开始监听微信
```

虚拟机那头靠开机自启（启动文件夹里的 `boot-at-logon.cmd`）：微信起来 → 等 30 秒 → 适配器起来。

### 换人设

在微信里给机器人发：

```
/人设                   列出当前人设和可选项
/人设 <代号>             切换，下一条消息生效
```

代号取 `persona.txt` 同级 `personas\` 目录里的文件名。切换不需要重启任何东西。

### 健康检查

```powershell
# 适配器活着吗、微信登录了吗
Invoke-RestMethod http://127.0.0.1:8765/status
# => {"ok":true,"ready":true,"account":"...","wxid":"...","queue":0}
```

`ready:false` 表示微信停在登录页——**扫码之后适配器会自己在 20 秒内接管**，不用重启它。

日志：主机 `logs\bridge-YYYYMMDD.log`，虚拟机 `C:\wxbridge\adapter.log`。

---

## 已知坑（都是踩过的，不是猜的）

### 远程执行

| 现象 | 真正的原因 |
|---|---|
| 通过 Guest Control 跑安装器，报 `VERR_PROC_ELEVATION_REQUIRED` | Guest Control **拒绝**启动需要提权的进程。得转手：`Start-Process -Verb RunAs` |
| `Start-Process` 起来的窗口点不动 | **UIPI**：低权限进程不能往高权限窗口注入输入。让 pyautogui 也提权跑 |
| 提权跑 pyautogui 后它点了自己 | 提权会带一个控制台窗口抢焦点。用 `pythonw.exe`（不带控制台） |
| 多层引号糊成一团 | PowerShell → VBoxManage → 客体 cmd 三层引号。**能不用引号就别用**，路径别带空格 |
| 批量 `taskkill /IM cmd.exe` 之后客体卡在 "starting" | 把 Guest Control 自己的辅助进程杀了。别这么干 |

### 微信本身

| 现象 | 真正的原因 |
|---|---|
| 微信启动后立刻退出，退出码 20 | **工作目录不对**。必须先 `cd` 到微信安装目录再启动 |
| 微信闪退，什么日志都没有 | 缺 **VC++ 运行库** |
| 每次重启都要求重新登录 | 腾讯的行为。点一下「登录」+ 手机确认，或者扫码。**硬重置/断电会强制它要求登录** |
| `wechatauto` 报 `PermissionError: [WinError 5]` | 它在**当前工作目录**建日志目录。远程执行时 cwd 是只读的 `System32`，必须 `cd` 到可写目录 |
| 判断"微信登录了没"时卡死 | **微信停在登录页时，UIA 的 `ensure_window()` 不是返回 False，而是无限期挂住**（实测卡了 4 分钟）。改用窗口宽度判断：登录页 274×380，主窗口 ≥500 |
| 版本升级后热激活失效 | UIA 物化靠的是 `Weixin.dll` 里的固定偏移。微信更新大概率会变 |

### Agent 侧

| 现象 | 真正的原因 |
|---|---|
| 机器人突然全哑，报 `already owned by an active write handle` | 上一次调用超时了。`subprocess.run` 超时**只杀父进程**，Agent 派生的 node 子进程活下来，一直攥着会话写句柄 |
| 修好之后又哑 | 需要**连子进程一起杀**（`taskkill /F /T`），并且把旧会话丢掉重试 |

### 写这个项目时踩到的其他坑

| 现象 | 真正的原因 |
|---|---|
| 用 `os.path.isfile()` 检查 Electron 版 DSH 的 CLI 路径，永远返回 False | **`app.asar` 对 Python 来说是个普通文件**，不是目录。只有 Electron 打过补丁的 fs 才把它当目录。照 `isfile` 判断会把所有正常安装判成坏的 |
| 脚本在中文 Windows 上崩于 `UnicodeEncodeError: 'gbk' codec can't encode` | 控制台默认代码页是 GBK，打印 `→` `✓` 这类符号会直接抛异常把脚本打死。给 `sys.stdout.reconfigure(errors="replace")` 兜底 |
| PowerShell 里 `gc`、`r` 之类的函数名不好使 | 那些是 PowerShell 内置别名（`gc`=Get-Content，`r`=Invoke-History）。函数名别撞别名 |

---

## 目录

```
host/
  bridge.py              主机侧桥接：轮询 + 问 Agent + 回复 + 人设切换
  config.example.json    配置模板（复制成 config.json 再改）
  start.bat              启动脚本
vm/
  adapter.py             虚拟机侧适配器：读库 + 发消息 + HTTP 接口
  boot-at-logon.cmd      虚拟机开机自启模板（拷进启动文件夹）
  run-adapter.cmd        单独拉起适配器
  auto-login.py          可选：自动点「登录」（实验性，见文件头注释）
persona/
  example.md             示例人设正文（改成你要的就行）
scripts/
  build_persona.py       把人设正文编译成 Agent 能吃的 patch
  find_dsh.py            帮你找本机 DSH 的两个路径
docs/
  架构.md                各模块职责与数据流
  排错.md                照症状查
```

---

## 安全提醒

- **适配器默认对 `0.0.0.0` 监听**。端口转发绑在 `127.0.0.1` 上时外部访问不到；
  如果你要暴露到局域网，**务必**在 `C:\wxbridge\adapter-token.txt` 里写一个令牌，并在主机
  `config.json` 的 `adapter_token` 填一样的值。
- 虚拟机里存着你的微信登录态。虚拟机磁盘文件（`.vdi`）**不要**传到任何地方。
- 这套东西会读你的微信本地数据库。用**小号**。

## 许可

MIT，见 `LICENSE`。
