from guardian import load_env; import os; load_env()
import asyncio
from src.models.openai_adapter import OpenAILikeClient
from src.utils.config.providers import providers_config
async def test():
    client = OpenAILikeClient(base_url=providers_config.JIUTIAN_URL, api_key=providers_config.JIUTIAN_KEY, model='openai/gpt-oss-120b')
    try:
        resp = await client.generate('system', 'hello')
        print(resp)
    except Exception as e:
        print('Error:', e)
asyncio.run(test())
