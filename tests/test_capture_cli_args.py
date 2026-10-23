"""CLI 参数回归测试（防止"文档里的命令跑不通"）。

背景：曾出现 README/REPORT 里写 `--interactive` / `--har`，
但 `tools/capture.py` 实际只有 `--non-interactive` / `--no-har`，
用户照抄必然报 `unrecognized arguments`。
本文件用 `sys.argv` 打桩，断言**文档中出现的每条命令**都能被解析。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from tests.conftest import ROOT
from tools import capture as capture_cli
from tools import healthcheck as healthcheck_cli
from tools import import_session as import_session_cli
from tools import replay as replay_cli
from tools import smoke_chat as smoke_chat_cli


def parse(module, argv: list[str]):
    """用指定 argv 调用 argparse，返回 Namespace。"""
    original = sys.argv
    try:
        sys.argv = ["tool"] + argv
        return module.parse_args()
    finally:
        sys.argv = original


# ---------------------------------------------------------------------------
# tools/capture.py —— README / REPORT / docs 里出现过的命令
# ---------------------------------------------------------------------------
def test_capture_default_label_is_baseline():
    args = parse(capture_cli, [])
    assert args.label == "baseline"
    assert args.no_har is False
    assert args.non_interactive is False


@pytest.mark.parametrize(
    "argv",
    [
        ["--label", "baseline"],
        ["--interactive", "--label", "baseline"],
        ["--interactive", "--har", "--label", "baseline"],
        ["--no-har", "--label", "after-change"],
        ["--interactive", "--har", "--label", "baseline", "--keep-open"],
        ["--non-interactive", "--duration", "180"],
        ["--non-interactive", "--duration", "120", "--chat", "你好"],
        ["--all", "--poll-interval", "1.5"],
    ],
)
def test_capture_documented_commands_parse(argv):
    args = parse(capture_cli, argv)
    assert args.label


def test_capture_interactive_and_non_interactive_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse(capture_cli, ["--interactive", "--non-interactive"])


def test_capture_har_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse(capture_cli, ["--har", "--no-har"])


def test_capture_interactive_flag_implies_interactive_mode():
    args = parse(capture_cli, ["--interactive"])
    # main() 里的推导逻辑：interactive = not non_interactive
    assert (not args.non_interactive) is True
    # har = not no_har
    args_har = parse(capture_cli, ["--har"])
    assert (not args_har.no_har) is True
    args_no_har = parse(capture_cli, ["--no-har"])
    assert (not args_no_har.no_har) is False


# ---------------------------------------------------------------------------
# 其它文档里出现的命令
# ---------------------------------------------------------------------------
def test_replay_documented_commands_parse():
    assert parse(replay_cli, ["captures/baseline/requests.json", "--seq", "12"]).send is False
    assert parse(replay_cli, ["captures/baseline/requests.json", "--fingerprint", "chat"]).fingerprint == "chat"
    assert parse(replay_cli, ["captures/baseline/requests.json", "--label", "send_message", "--send"]).send is True


def test_import_session_documented_commands_parse():
    assert parse(import_session_cli, []).probe is False
    args = parse(import_session_cli, ["--probe"])
    assert args.probe is True
    assert parse(import_session_cli, ["--cookie-header", "a=1", "--save"]).save is True


def test_smoke_chat_documented_commands_parse():
    args = parse(smoke_chat_cli, ["--prompt", "你好", "--conversation", "demo"])
    assert args.prompt == "你好"
    assert args.stream is True
    assert parse(smoke_chat_cli, ["--prompt", "x", "--no-stream"]).stream is False


def test_healthcheck_documented_commands_parse():
    assert parse(healthcheck_cli, []).direct is False
    args = parse(healthcheck_cli, ["--direct", "--write-snapshot"])
    assert args.direct is True and args.write_snapshot is True


# ---------------------------------------------------------------------------
# 一键启动 bat（纯 ASCII 壳 + Python 逻辑）
# ---------------------------------------------------------------------------
BAT_FILES = ("一键启动.bat", "一键登录抓包.bat", "一键菜单.bat")


@pytest.mark.parametrize("name", [*BAT_FILES, "scripts/launcher.py"])
def test_one_click_files_exist(name):
    assert (Path(ROOT) / name).exists(), f"缺少 {name}"


@pytest.mark.parametrize("name", BAT_FILES)
def test_bat_files_are_pure_ascii(name):
    """bat 必须是纯 ASCII 壳。

    原因（都是实测踩到的 cmd 缺陷）：
      * `!` 会被延迟展开吞掉（`echo [!] x` 打印成 `[] x`）；
      * 多行 ( ) 块里的中文会被拆成命令执行；
      * 批处理含非 ASCII + 使用 goto/call 时，cmd 按字节偏移重读文件会切在
        汉字中间（实测把「括号块」切成「号块」并当成命令执行）。
    因此所有中文界面都放在 scripts/launcher.py（UTF-8 安全、可测试）。
    """
    raw = (Path(ROOT) / name).read_bytes()
    non_ascii = [b for b in raw if b > 127]
    assert not non_ascii, f"{name} 含非 ASCII 字节（{len(non_ascii)} 个）；请把中文移到 scripts/launcher.py"


@pytest.mark.parametrize("name", BAT_FILES)
def test_bat_files_set_utf8_and_cd(name):
    text = (Path(ROOT) / name).read_text(encoding="ascii")
    assert "chcp 65001" in text.lower(), f"{name} 缺少 chcp 65001"
    assert "PYTHONIOENCODING=utf-8" in text, f"{name} 缺少 PYTHONIOENCODING"
    assert 'cd /d "%~dp0"' in text, f"{name} 未切换到脚本目录"
    assert "launcher.py" in text, f"{name} 未调用 scripts/launcher.py"


@pytest.mark.parametrize(
    ("name", "command"),
    [
        ("一键启动.bat", "start"),
        ("一键登录抓包.bat", "capture"),
        ("一键菜单.bat", "menu"),
    ],
)
def test_bat_delegates_to_expected_launcher_command(name, command):
    text = (Path(ROOT) / name).read_text(encoding="ascii")
    assert f"launcher.py\" {command}" in text


# ---------------------------------------------------------------------------
# scripts/launcher.py
# ---------------------------------------------------------------------------
def test_launcher_parser_accepts_documented_commands():
    from scripts import launcher

    assert launcher.parse_args(["start"]).command == "start"
    assert launcher.parse_args(["capture", "--label", "demo"]).label == "demo"
    assert launcher.parse_args(["menu", "--port", "1011"]).port == "1011"
    check = launcher.parse_args(["check"])
    assert check.command == "check" and check.no_install is False
    with pytest.raises(SystemExit):
        launcher.parse_args([])  # command 是必填


def test_launcher_defaults_and_paths():
    from scripts import launcher

    assert launcher.DEFAULT_PORT == 1010
    assert launcher.STORAGE_STATE.name == "storage_state.json"
    # 不能断言仓库目录名（公开克隆时可任意命名）：只断言指向项目根
    assert Path(launcher.ROOT).resolve() == Path(ROOT).resolve()
    assert (launcher.ROOT / "run.py").exists()


def test_launcher_check_env_without_install_returns_true_here():
    """本机依赖齐全，--no-install 检查应当通过。"""
    from scripts import launcher

    assert launcher.check_env(auto_install=False) is True


def test_launcher_port_in_use_detects_free_port():
    from scripts import launcher

    assert launcher.port_in_use(1) is False  # 1 号端口不会有人监听


def test_launcher_menu_string_has_all_options():
    from scripts import launcher

    text = launcher.MENU.format(port=1010)
    for option in ("1)", "2)", "3)", "4)", "5)", "6)", "7)", "8)", "9)", "0)"):
        assert option in text
    assert "1010" in text


def test_launcher_open_dir_missing_is_safe(tmp_path):
    from scripts import launcher

    launcher._open_dir(tmp_path / "nope", "nope")  # 不应抛异常


def test_launcher_menu_returns_on_eof(monkeypatch):
    """管道/EOF 输入时菜单必须退出，不能空转（曾导致 --bat 测试里死循环）。"""
    from scripts import launcher

    def boom(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert launcher.cmd_menu(argparse.Namespace(port=1010)) == 0


def test_launcher_menu_exits_on_zero(monkeypatch):
    from scripts import launcher

    monkeypatch.setattr("builtins.input", lambda prompt="": "0")
    assert launcher.cmd_menu(argparse.Namespace(port=1010)) == 0


