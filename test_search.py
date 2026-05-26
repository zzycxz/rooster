import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure src directory is in path before importing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

# Load environment before importing tools
load_dotenv(".env.local")

from src.toolset.definitions.exa_search import ExaSearchTool
from src.toolset.definitions.linkup_search import LinkupSearchTool
from src.toolset.definitions.glm_plan_search import GLMPlanSearchTool
from src.toolset.definitions.browser import WebSearchTool

async def test_search():
    print("Testing ExaSearchTool...")
    try:
        exa = ExaSearchTool()
        res = await exa.run(query="Huawei Pro 6000 GPU")
        print("Exa Result:", str(res)[:300])
    except Exception as e:
        print(f"Exa Failed: {e}")

    print("\nTesting LinkupSearchTool...")
    try:
        linkup = LinkupSearchTool()
        res2 = await linkup.run(query="Huawei Pro 6000 GPU")
        print("Linkup Result:", str(res2)[:300])
    except Exception as e:
        print(f"Linkup Failed: {e}")

    print("\nTesting GLMPlanSearchTool...")
    try:
        glm = GLMPlanSearchTool()
        res3 = await glm.run(query="Huawei Pro 6000 GPU")
        print("GLM Result:", str(res3)[:300])
    except Exception as e:
        print(f"GLM Failed: {e}")

    print("\nTesting WebSearchTool...")
    try:
        web = WebSearchTool()
        res4 = await web.run(query="Huawei Pro 6000 GPU")
        print("Web Result:", str(res4)[:300])
    except Exception as e:
        print(f"Web Failed: {e}")

if __name__ == "__main__":
    # Ensure src directory is in path
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
    asyncio.run(test_search())
