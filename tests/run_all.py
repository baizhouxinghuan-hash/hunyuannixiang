"""无 pytest 时的兜底测试运行器：``python -m tests.run_all``。

按文件顺序 import 所有 ``test_*.py``，收集 ``test_*`` 函数并执行；
失败会打印异常并返回非零退出码。
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_FILES = (
    "tests.test_redaction",
    "tests.test_profile",
    "tests.test_stream",
    "tests.test_adapter",
    "tests.test_errors",
    "tests.test_conversation",
    "tests.test_auth",
    "tests.test_models",
    "tests.test_chat",
    "tests.test_tool_bridge",
    "tests.test_capture_cli_args",
    "tests.test_analyzer",
    "tests.test_server",
    "tests.test_tools_cli",
    "tests.test_live_upstream",
)


def main() -> int:
    passed = 0
    skipped = 0
    failures: list[tuple[str, BaseException]] = []
    for module_name in TEST_FILES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            print(f"[skip] {module_name}（文件不存在）")
            continue

        setup = getattr(module, "setup_module", None)
        if callable(setup):
            setup()

        for name in sorted(dir(module)):
            if not name.startswith("test_"):
                continue
            func: Callable[[], Any] | None = getattr(module, name)
            if not callable(func):
                continue
            try:
                _maybe_fixtures(func)
                func()
                passed += 1
            except _Skipped as exc:
                skipped += 1
                print(f"[skip] {module_name}.{name}：{exc}")
            except BaseException as exc:  # noqa: BLE001 - 兜底运行器要能报出一切失败
                failures.append((f"{module_name}.{name}", exc))
                print(f"[FAIL] {module_name}.{name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()

    print(f"\n共通过 {passed} 个，跳过 {skipped} 个（依赖 pytest fixture），失败 {len(failures)} 个。")
    for name, exc in failures:
        print(f"  - {name}: {type(exc).__name__}")
    return 1 if failures else 0


class _Skipped(Exception):
    """兜底运行器下的跳过标记。"""


def _maybe_fixtures(func: Callable[..., Any]) -> None:
    """需要 pytest fixture 的测试在兜底运行器下跳过（它们依赖 tmp_path / fixture 注入）。"""
    import inspect

    params = [p for p in inspect.signature(func).parameters]
    if params:
        raise _Skipped(f"依赖 pytest fixture {params}，请用 python -m pytest 运行")


if __name__ == "__main__":
    raise SystemExit(main())
