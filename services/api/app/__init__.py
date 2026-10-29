"""在 Windows 上为 Claude Agent SDK 子进程设置 Proactor（uvicorn --reload 子进程也会 import 本包）。"""

import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
