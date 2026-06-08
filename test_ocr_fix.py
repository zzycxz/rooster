import asyncio
import os
from PIL import Image

async def run_test():
    print("=== OCR 参数兼容性测试 ===")
    
    # 【步骤 1】创建一张临时测试图片
    print("\n【步骤 1】创建用于测试的临时截图...")
    test_img_path = "test_ocr_dummy.png"
    img = Image.new('RGB', (100, 50), color="white")
    img.save(test_img_path)
    print(f"  -> 已生成：{test_img_path}")
    
    # 【步骤 2】实例化 OcrExtractTool 并运行
    print("\n【步骤 2】初始化 PaddleOCR 引擎并执行识别...")
    print("  -> 预期目标：不会抛出 'ValueError: Unknown argument: show_log' 崩溃。")
    from src.toolset.definitions.ocr import OcrExtractTool
    tool = OcrExtractTool()
    
    try:
        result = await tool.run(image_path=test_img_path)
        print(f"  -> OCR 引擎返回：\n{result}")
        if "Unknown argument: show_log" in result:
             print("\n❌ [测试失败] 仍然存在 show_log 参数错误！")
        else:
             print("\n✅ [测试通过] PaddleOCR 初始化成功，参数兼容性修复有效。")
    except Exception as e:
        print(f"\n❌ [测试异常] 发生了其他错误: {e}")
        
    # 【步骤 3】清理测试资源
    print("\n【步骤 3】清理临时文件...")
    if os.path.exists(test_img_path):
        os.remove(test_img_path)
        print("  -> 测试图片已删除。")

if __name__ == "__main__":
    # Ensure src is in pythonpath
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))
    asyncio.run(run_test())
