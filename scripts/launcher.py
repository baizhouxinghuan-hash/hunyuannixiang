"""一键启动 / 登录抓包 / 工具箱 的真实逻辑（Python 实现）。

为什么用 Python 而不是纯 bat？
--------------------------------------------------------------------------
cmd.exe 对 UTF-8 批处理有已知缺陷：
  * `!` 会被延迟展开吞掉（实测 `echo [!] x` 打印成 `[] x`）；
  * 多行 `( )` 块里的中文会被拆成命令执行（实测 `'退出' is not recognized ...`）；
  * 批处理含中文 **且使用 goto/call** 时，cmd 按字节偏移重读文件会切在汉字中间
    （实测把「括号块」切成「号块」并当成命令执行）。
因此三个入口 bat 一律是**纯 ASCII 壳**，只负责 `chcp 65001` + 调用本文件；
所有中文界面与逻辑都放在 Python 里（UTF-8 安全、可单元测试）。

用法::

    python scripts/launcher.py start     # 检查环境 + 起服务（= 一键启动.bat）
    python scripts/launcher.py capture   # 登录 + 抓包（= 一键登录抓包.bat）
    python scripts/launcher.py menu      # 交互式工具箱（= 一键菜单.bat）
    python scripts/launcher.py check     # 只做环境检查
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 1010
REQUIRED_MODULES = ("fastapi", "uvicorn", "httpx", "yaml", "pydantic")
REQUIREMENTS = ROOT / "requirements.txt"
STORAGE_STATE = ROOT / "secrets" / "storage_state.json"
CAPTURE_DIR = ROOT / "captures"

BANNER = "=" * 64


def line(text: str = "") -> None:
    print(text, flush=True)


def have_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_env(*, auto_install: bool = True) -> bool:
    """检查 Python / 依赖 / Playwright。返回是否可用。"""
    line(BANNER)
    line("  环境检查")
    line(BANNER)
    line(f"[1/4] Python {sys.version.split()[0]}  ({sys.executable})")

    missing = [name for name in REQUIRED_MODULES if not have_module(name)]
    if missing:
        line(f"[2/4] 缺少依赖：{', '.join(missing)}")
        if not auto_install:
            return False
        line("      正在安装 requirements.txt ...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)], cwd=str(ROOT)
        )
        if result.returncode != 0:
            line("[X] 依赖安装失败。请手动执行：")
            line(f"    {sys.executable} -m pip install -r requirements.txt")
            return False
        line("[2/4] 依赖安装完成")
    else:
        line("[2/4] 依赖检查通过")

    if not have_module("playwright"):
        line("[3/4] 未安装 playwright，正在安装 ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright==1.58.0"], cwd=str(ROOT))
    browsers_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not browsers_dir.exists():
        line("[3/4] 未检测到 Playwright 浏览器，正在下载 chromium ...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], cwd=str(ROOT))
    else:
        line("[3/4] playwright 就绪")

    if STORAGE_STATE.exists():
        line("[4/4] 已找到登录会话 secrets/storage_state.json")
    else:
        line("[4/4] 警告：还没有登录会话（secrets/storage_state.json 不存在）")
    line()
    return True


_LAST_WAS_EOF = False


def ask(prompt: str) -> str:
    """读取一行输入；遇到 EOF（管道/重定向场景）返回空串并置位 _LAST_WAS_EOF。"""
    global _LAST_WAS_EOF
    try:
        _LAST_WAS_EOF = False
        return input(prompt).strip()
    except EOFError:
        _LAST_WAS_EOF = True
        return ""


def port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def free_port(port: int) -> list[str]:
    """结束占用指定端口的进程（仅自检脚本用；普通启动不调用）。"""
    killed: list[str] = []
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, errors="replace"
        ).stdout
    except OSError:
        return killed
    for row in out.splitlines():
        if f":{port} " in row and "LISTENING" in row.upper():
            pid = row.strip().split()[-1]
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
            killed.append(pid)
    return killed


# ---------------------------------------------------------------------------
# start：一键启动
# ---------------------------------------------------------------------------
def cmd_start(args: argparse.Namespace) -> int:
    line(BANNER)
    line("   AI Studio  到  OpenAI 兼容代理      一键启动")
    line(BANNER)
    line()
    if not check_env(auto_install=not args.no_install):
        return 1

    port = int(args.port or DEFAULT_PORT)

    if not STORAGE_STATE.exists() and not args.no_prompt:
        line("还没有登录会话。现在去登录并抓包吗？")
        answer = ask("  Y = 现在登录 / 直接回车 = 跳过 : ")
        if answer.lower() in ("y", "yes", "是"):
            code = cmd_capture(argparse.Namespace(label=args.label or "baseline", no_har=False, keep_open=False))
            if code != 0 or not STORAGE_STATE.exists():
                line("[X] 仍未获得登录会话，先退出。可稍后单独运行「一键登录抓包.bat」")
                return 1

    if port_in_use(port):
        line()
        line(f"[提示] 端口 {port} 已被占用（可能服务已经在运行）。")
        line("        - 直接回车 = 打开已有服务的接口文档后退出")
        line("        - 输入数字 = 换一个端口启动（例如 1011）")
        answer = ask(" 新端口: ")
        if not answer:
            webbrowser.open(f"http://127.0.0.1:{port}/docs")
            return 0
        try:
            port = int(answer)
        except ValueError:
            line("[X] 端口必须是数字。")
            return 1

    line()
    line("-" * 64)
    line(f"   OpenAI Base URL : http://127.0.0.1:{port}/v1")
    line(f"   接口文档        : http://127.0.0.1:{port}/docs")
    line(f"   健康检查        : http://127.0.0.1:{port}/health")
    line("   按 Ctrl+C 停止服务")
    line("-" * 64)
    line()

    if not os.environ.get("NO_BROWSER"):
        threading.Timer(6.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}/docs")).start()

    try:
        return subprocess.call([sys.executable, str(ROOT / "run.py"), "--port", str(port)], cwd=str(ROOT))
    except KeyboardInterrupt:  # pragma: no cover - 用户按 Ctrl+C
        return 0


# ---------------------------------------------------------------------------
# capture：登录 + 抓包
# ---------------------------------------------------------------------------
CAPTURE_STEPS = """
==================== 请在弹出的浏览器里操作 ====================
  1) 自己完成登录（扫码 / 短信验证码；本程序不参与登录，也不保存你的密码）
  2) 新建会话，发送：你好，请简单介绍一下自己。
  3) 等回答开始输出后，点一次【停止生成】
  4) 再点一次【重新生成】；然后发送：今天天气怎么样
  5) 回到本窗口按 Enter 结束抓包
===============================================================
"""


def cmd_capture(args: argparse.Namespace) -> int:
    line(BANNER)
    line("   AI Studio  登录 + 协议抓包")
    line(BANNER)
    line()
    line("说明：本步骤会打开一个 Chromium 窗口，登录请由你本人完成。")
    line("      抓包只记录你自己会话的网络通信；Cookie / Token 会自动脱敏为 ****。")
    line()
    if not check_env(auto_install=True):
        return 1

    label = args.label or "baseline"
    line(CAPTURE_STEPS)
    ask("按 Enter 开始抓包 ...")
    code = subprocess.call(
        [sys.executable, str(ROOT / "tools" / "capture.py"), "--label", label, "--interactive"],
        cwd=str(ROOT),
    )

    line()
    line(BANNER)
    if STORAGE_STATE.exists():
        line("[OK] 登录会话已保存：secrets/storage_state.json")
    else:
        line("[提示] 没有拿到 secrets/storage_state.json，说明登录可能没完成，可再跑一次。")
    line()
    line("下一步：把下面两个文件（已脱敏，可安全外发）交给助手回填协议：")
    line(f"    captures/{label}/summary.md")
    line(f"    captures/{label}/requests.json")
    line("然后就能用「一键启动.bat」启动服务、在客户端里真实对话了。")
    line(BANNER)

    if not args.keep_open:
        for target in (CAPTURE_DIR / label / "summary.md", CAPTURE_DIR / label):
            if target.exists():
                try:
                    os.startfile(target)  # type: ignore[attr-defined]  # Windows 专用
                except OSError:
                    pass
    return code


# ---------------------------------------------------------------------------
# menu：工具箱
# ---------------------------------------------------------------------------
MENU = """
================================================================
     AI Studio  到  OpenAI 兼容代理        工具箱
================================================================

     1) 安装 / 修复依赖
     2) 登录 + 抓包（打开浏览器，自己扫码登录）
     3) 启动 API 服务            http://127.0.0.1:{port}
     4) 检查登录会话（脱敏输出）
     5) 服务自检（起服务 + 真实 HTTP）
     6) 打开接口文档 Swagger
     7) 打开 captures 抓包产物目录
     8) 打开 logs 日志目录
     9) 运行离线测试（pytest，不需要账号）
     0) 退出
"""


def cmd_menu(args: argparse.Namespace) -> int:
    port = int(args.port or DEFAULT_PORT)
    while True:
        line(MENU.format(port=port))
        choice = ask(" 请选择: ")
        if _LAST_WAS_EOF:
            line("[提示] 输入已结束（EOF），退出菜单。")
            return 0
        if choice == "":
            continue
        if choice == "0":
            return 0
        if choice == "1":
            check_env(auto_install=True)
        elif choice == "2":
            cmd_capture(argparse.Namespace(label="baseline", no_har=False, keep_open=False))
        elif choice == "3":
            cmd_start(argparse.Namespace(port=port, label="baseline", no_install=False, no_prompt=False))
        elif choice == "4":
            subprocess.call([sys.executable, str(ROOT / "tools" / "import_session.py")], cwd=str(ROOT))
        elif choice == "5":
            subprocess.call([sys.executable, str(ROOT / "tools" / "selfcheck.py")], cwd=str(ROOT))
        elif choice == "6":
            webbrowser.open(f"http://127.0.0.1:{port}/docs")
            continue
        elif choice == "7":
            _open_dir(CAPTURE_DIR, "captures")
            continue
        elif choice == "8":
            _open_dir(ROOT / "logs", "logs")
            continue
        elif choice == "9":
            subprocess.call([sys.executable, "-m", "pytest", "-q"], cwd=str(ROOT))
        else:
            line("[提示] 无效选择，请重新输入。")
            continue
        ask("\n按 Enter 返回菜单 ...")


def _open_dir(path: Path, name: str) -> None:
    if not path.exists():
        line(f"[提示] 还没有 {name} 目录。")
        time.sleep(1.5)
        return
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except OSError:
        line(f"[提示] 无法打开 {path}，请手动访问。")


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一键启动 / 登录抓包 / 工具箱")
    parser.add_argument("command", choices=("start", "capture", "menu", "check"))
    parser.add_argument("--port", default=None, help="服务端口（默认 1010）")
    parser.add_argument("--label", default=None, help="抓包标签（默认 baseline）")
    parser.add_argument("--no-install", action="store_true", help="只检查依赖，不自动安装")
    parser.add_argument("--no-prompt", action="store_true", help="不询问登录，直接启动")
    parser.add_argument("--keep-open", action="store_true", help="抓包后不自动打开产物")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "capture":
        return cmd_capture(args)
    if args.command == "check":
        return 0 if check_env(auto_install=False) else 1
    return cmd_menu(args)


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())


