"""启动入口：python run.py

用法::

    python run.py                      # 读取 config.yaml
    python run.py --port 8080 --reload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.server import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Studio OpenAI-Compatible Proxy")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"), help="配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址（默认取 config.yaml）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取 config.yaml）")
    parser.add_argument("--log-level", default=None, help="日志级别")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()

    config = load_config(args.config)
    run(host=args.host, port=args.port, reload=args.reload, log_level=args.log_level, config=config)


if __name__ == "__main__":
    main()
