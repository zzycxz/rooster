import os
import time
import json
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = os.path.join(".rooster", "checkpoints")
_HEARTBEAT_TIMEOUT_S = 180

async def _heartbeat_loop():
    """
    [V12 B5.2] 守护进程轮询逻辑。
    监控 .rooster/checkpoints/ 下的所有 .heartbeat 文件。
    如果发现有文件超过 180 秒未更新，则发出警告。
    """
    logger.info("Guardian Monitor started. Watching for stalled tasks...")

    while True:
        try:
            if not os.path.exists(_CHECKPOINT_DIR):
                await asyncio.sleep(30)
                continue

            now = time.time()
            for filename in os.listdir(_CHECKPOINT_DIR):
                if not filename.endswith(".heartbeat"):
                    continue

                path = os.path.join(_CHECKPOINT_DIR, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        hb = json.load(f)

                    age = now - hb.get("timestamp", 0)
                    if age > _HEARTBEAT_TIMEOUT_S:
                        logger.warning(
                            f"🚨 [GUARDIAN ALERT] Task {hb.get('task_id')} "
                            f"(Subtask: {hb.get('running_subtask')}) "
                            f"has not reported heartbeat in {int(age)}s! "
                            f"It might be stuck or blocked indefinitely."
                        )
                except Exception as e:
                    logger.debug(f"Failed to read heartbeat {filename}: {e}")

        except Exception as e:
            logger.error(f"Guardian monitor error: {e}")

        await asyncio.sleep(30)

def start_guardian_task() -> Optional[asyncio.Task]:
    """
    启动守护监控任务（后台运行）。
    """
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(_heartbeat_loop())
    except RuntimeError:
        logger.warning("No running event loop. Guardian cannot be started.")
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_heartbeat_loop())
