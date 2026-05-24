import os
import logging
import json
from datetime import datetime
from filelock import FileLock

logger = logging.getLogger(__name__)


class UserWriter:
    """
    USER.md 字段级更新器。
    支持防抖机制（State Tracking）和白名单更新。
    """

    # USER.md field-level updater.
    # Supports debounce mechanism (State Tracking) and whitelist updates

    def __init__(self, user_path: str = ".rooster/USER.md", state_path: str = ".rooster/state/evolution_state.json"):
        self.user_path = os.path.abspath(user_path)
        self.state_path = os.path.abspath(state_path)
        self.lock_path = self.user_path + ".lock"

        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        self.whitelist_fields = ["## 当前重点项目", "## 偏好与习惯"]

    def _get_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self, state: dict):
        with open(self.state_path, "w") as f:
            json.dump(state, f)

    def update_field(self, field_name: str, content: str, turn_id: int) -> bool:
        """
        更新或追加用户画像字段。
        """
        # Update or append user profile field
        if field_name not in self.whitelist_fields:
            return False

        # 防抖检查：同一字段 5 轮内不重复更新
        # Debounce check: same field not updated within 5 turns
        state = self._get_state()
        last_turn = state.get(field_name, -10)
        if turn_id - last_turn < 5:
            logger.info(f"⏳ 字段 {field_name} 更新过于频繁，防抖拦截。")
            return False

        lock = FileLock(self.lock_path, timeout=5)
        try:
            with lock:
                with open(self.user_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                field_idx = -1
                for i, line in enumerate(lines):
                    if line.strip() == field_name:
                        field_idx = i
                        break

                if field_idx == -1:
                    return False

                timestamp = datetime.now().strftime("%Y-%m-%d")
                new_line = f"- [{timestamp} 进化引擎] {content}\n"

                # 寻找插入位置（章节末尾）
                # Find insertion position (end of section)
                insert_pos = field_idx + 1
                while insert_pos < len(lines) and not lines[insert_pos].startswith("##"):
                    insert_pos += 1

                lines.insert(insert_pos, new_line)

                with open(self.user_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)

                # 更新状态
                # Update state
                state[field_name] = turn_id
                self._save_state(state)

                logger.info(f"👤 已更新 USER.md -> {field_name}: {content[:30]}...")
                return True

        except Exception as e:
            logger.error(f"❌ USER 更新失败: {str(e)}")
            return False
