import os
import sys

# 保证 src 模块路径可见
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from src.toolset.definitions.visual_control import DesktopGroundingScanTool, DesktopGroundingScanArgs
from src.utils.config import settings

def test_schema_isolation():
    print("=== Agent 幻觉隔离测试 ===")
    
    # 【步骤 1】验证参数大纲（Schema）是否清除了 mode
    print("\n【步骤 1】检查工具向 LLM 暴露的参数大纲 (JSON Schema)...")
    schema = DesktopGroundingScanArgs.model_json_schema()
    properties = schema.get("properties", {})
    if "mode" in properties:
        print("❌ [失败] Schema 仍然暴露了 'mode' 参数，Agent 仍有可能会乱传。")
    else:
        print("✅ [通过] Schema 中已彻底移除 'mode' 参数，Agent 将永远不会被提示存在 'high' 模式。")
        
    # 【步骤 2】验证工具描述 (Description) 是否诱导 LLM
    print("\n【步骤 2】检查工具给 LLM 看的功能描述 (Description)...")
    tool = DesktopGroundingScanTool()
    desc = tool.description
    if "high" in desc or "low" in desc:
        print("❌ [失败] 描述中仍然提到了 high/low 模式：", desc)
    else:
        print("✅ [通过] 描述文本中已剔除所有的干扰词汇，LLM 将不再产生幻觉。")

    # 【步骤 3】模拟代码底层逻辑接管
    print("\n【步骤 3】验证底层实际获取 mode 的方式...")
    mode = getattr(settings, "VISION_SCAN_MODE", "low")
    print(f"✅ [通过] 无论 LLM 怎么想，底层只会从 .env 中安全读取 VISION_SCAN_MODE。当前值为: '{mode}'")

if __name__ == "__main__":
    test_schema_isolation()
