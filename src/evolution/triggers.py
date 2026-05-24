from typing import List


class TriggerChecker:
    """
    进化引擎触发规则层。
    使用纯字符串匹配和正则，零延迟判断是否需要进化。
    """

    # Evolution engine trigger rule layer.
    # Uses pure string matching and regex, zero-latency judgment of whether evolution is needed

    def __init__(self):
        # 默认关键词（可后续改为从 .env 读取）
        # Default keywords (can later be read from .env)
        self.correction_keywords = ["不对", "错了", "你理解错", "重新理解", "纠正"]
        self.milestone_keywords = ["完成了", "做好了", "已上线", "成功了"]
        self.preference_keywords = ["以后", "我希望", "不要再", "记住"]

    def is_correction(self, text: str) -> bool:
        return any(kw in text for kw in self.correction_keywords)

    def is_milestone(self, text: str) -> bool:
        return any(kw in text for kw in self.milestone_keywords)

    def is_preference(self, text: str) -> bool:
        return any(kw in text for kw in self.preference_keywords)

    def check_all(self, user_text: str) -> List[str]:
        """返回触发的信号类型列表"""  # Return list of triggered signal types
        signals = []
        if self.is_correction(user_text):
            signals.append("CORRECTION")
        if self.is_milestone(user_text):
            signals.append("MILESTONE")
        if self.is_preference(user_text):
            signals.append("PREFERENCE")
        return signals
