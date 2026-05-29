"""嵌入提供者抽象层：OpenAI 兼容 API + n-gram 哈希 fallback。"""

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import hashlib
import logging
import math
from abc import ABC, abstractmethod
from typing import List, Optional

logger = logging.getLogger(__name__)

DIMENSION_NGRAM = 256


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入。返回与 texts 等长的向量列表。"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回向量维度。"""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """提供者标识（用于缓存 key）。"""


class NgramHashEmbedding(EmbeddingProvider):
    """字符 n-gram 哈希嵌入，零外部依赖，作为 fallback。"""

    def __init__(self, dim: int = DIMENSION_NGRAM, n: int = 3):
        self._dim = dim
        self._n = n

    def _embed_one(self, text: str) -> List[float]:
        vec = [0.0] * self._dim
        text_lower = text.lower().strip()
        if len(text_lower) < self._n:
            text_lower = text_lower.ljust(self._n, " ")
        for i in range(len(text_lower) - self._n + 1):
            gram = text_lower[i : i + self._n]
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            idx = h % self._dim
            sign = 1 if (h // self._dim) % 2 == 0 else -1
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    async def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def provider_id(self) -> str:
        return "ngram"


class OpenAICompatibleEmbedding(EmbeddingProvider):
    """通过 OpenAI SDK 调用 /v1/embeddings 端点。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        dim: Optional[int] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dim = dim
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
            )
        return self._client

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        client = self._get_client()
        resp = await client.embeddings.create(
            model=self._model,
            input=texts,
        )
        # 按 index 排序，防止 API 返回顺序错乱
        sorted_data = sorted(resp.data, key=lambda x: x.index)
        vectors = [item.embedding for item in sorted_data]
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        return vectors

    @property
    def dimension(self) -> int:
        return self._dim if self._dim is not None else 1536

    @property
    def provider_id(self) -> str:
        return f"openai:{self._model}"


# [Phase4] 进程级模型缓存 — 相同模型名只加载一次
_LOCAL_MODEL_CACHE: dict = {}
_LOCAL_MODEL_CACHE_LOCK = None


class LocalSentenceTransformerEmbedding(EmbeddingProvider):
    """本地 sentence-transformers 嵌入，离线运行，零网络依赖。

    [Phase4] 进程内单例：相同 model_name 共享同一个 SentenceTransformer 实例，
    避免每个子任务（Executor 实例）重复加载几百兆权重文件。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self._model_name = model_name
        self._dim: Optional[int] = None

    @property
    def _model(self):
        """从全局缓存懒加载模型。"""
        return _LOCAL_MODEL_CACHE.get(self._model_name)

    def _load_model(self):
        global _LOCAL_MODEL_CACHE_LOCK
        if self._model_name in _LOCAL_MODEL_CACHE:
            # 已加载，更新本地 dim 缓存
            if self._dim is None:
                try:
                    m = _LOCAL_MODEL_CACHE[self._model_name]
                    self._dim = m.get_embedding_dimension()
                except AttributeError:
                    self._dim = _LOCAL_MODEL_CACHE[self._model_name].get_sentence_embedding_dimension()
            return

        import threading

        if _LOCAL_MODEL_CACHE_LOCK is None:
            _LOCAL_MODEL_CACHE_LOCK = threading.Lock()

        with _LOCAL_MODEL_CACHE_LOCK:
            # 双重检查，防止并发重复加载
            if self._model_name in _LOCAL_MODEL_CACHE:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                logger.debug("sentence_transformers 未安装，本地嵌入不可用（将自动降级到 n-gram）")
                raise
            logger.info(f"正在加载本地嵌入模型: {self._model_name} ...")
            model = SentenceTransformer(self._model_name)
            _LOCAL_MODEL_CACHE[self._model_name] = model
            try:
                self._dim = model.get_embedding_dimension()
            except AttributeError:
                self._dim = model.get_sentence_embedding_dimension()
            logger.info(f"本地嵌入模型加载完成: dim={self._dim}")

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        import asyncio

        loop = asyncio.get_event_loop()

        def _encode():
            self._load_model()
            return _LOCAL_MODEL_CACHE[self._model_name].encode(texts, normalize_embeddings=True)

        embeddings = await loop.run_in_executor(None, _encode)
        return [vec.tolist() for vec in embeddings]

    @property
    def dimension(self) -> int:
        if self._dim is None:
            try:
                self._load_model()
            except Exception:
                pass
        return self._dim or 512

    @property
    def provider_id(self) -> str:
        return f"local:{self._model_name}"


class CompositeEmbedding(EmbeddingProvider):
    """
    自动降级嵌入：API → 本地模型 → n-gram。
    首次 embed() 时探测，后续锁定到成功的方式。
    锁定后仍保留 fallback：API 暂时不可用时自动降级到本地。
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "text-embedding-3-small",
        local_model: str = "BAAI/bge-small-zh-v1.5",
    ):
        self._primary: Optional[EmbeddingProvider] = None
        self._local = LocalSentenceTransformerEmbedding(local_model)
        self._fallback = NgramHashEmbedding()
        self._locked_provider: Optional[EmbeddingProvider] = None

        if base_url and api_key:
            self._primary = OpenAICompatibleEmbedding(base_url, api_key, model)
        else:
            logger.info("未配置嵌入 API，将使用本地模型")

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        # 已锁定路径：带 fallback 保护
        if self._locked_provider:
            try:
                return await self._locked_provider.embed(texts)
            except Exception:
                if self._locked_provider is not self._fallback:
                    logger.warning(f"锁定的嵌入 {self._locked_provider.provider_id} 暂不可用，临时降级到 n-gram")
                    return await self._fallback.embed(texts)
                raise

        # 未锁定：探测 primary
        if self._primary:
            try:
                result = await self._primary.embed(texts[:1])
                if result and len(result[0]) > 0:
                    # probe 成功，锁定 primary
                    self._locked_provider = self._primary
                    logger.info(f"嵌入提供者锁定为: {self._primary.provider_id}")
                    if len(texts) > 1:
                        try:
                            rest = await self._primary.embed(texts[1:])
                            return result + rest
                        except Exception:
                            # rest 失败：用 fallback 补全剩余部分
                            logger.warning("批量嵌入部分失败，剩余降级到 n-gram")
                            rest = await self._fallback.embed(texts[1:])
                            return result + rest
                    return result
            except Exception:
                logger.info("OpenAI 嵌入不可用，尝试本地模型")

        # primary 不可用或未配置，尝试本地模型
        try:
            result = await self._local.embed(texts[:1])
            if result and len(result[0]) > 0:
                self._locked_provider = self._local
                logger.info(f"嵌入提供者锁定为: {self._local.provider_id}")
                if len(texts) > 1:
                    rest = await self._local.embed(texts[1:])
                    return result + rest
                return result
        except Exception:
            logger.info("本地嵌入不可用，降级到 n-gram")

        # 全部失败，锁定 n-gram fallback
        self._locked_provider = self._fallback
        logger.info(f"嵌入提供者锁定为: {self._fallback.provider_id}")
        return await self._fallback.embed(texts)

    @property
    def dimension(self) -> int:
        if self._locked_provider:
            return self._locked_provider.dimension
        if self._primary:
            return self._primary.dimension
        return self._local.dimension

    @property
    def provider_id(self) -> str:
        if self._locked_provider:
            return self._locked_provider.provider_id
        return "composite:unprobed"


def create_embedder(
    provider: str = "auto",
    base_url: str = "",
    api_key: str = "",
    model: str = "text-embedding-3-small",
    local_model: str = "BAAI/bge-small-zh-v1.5",
) -> EmbeddingProvider:
    """工厂函数：根据配置创建嵌入提供者。"""
    if provider == "ngram":
        return NgramHashEmbedding()
    if provider == "local":
        return LocalSentenceTransformerEmbedding(local_model)
    if provider == "openai":
        if not base_url or not api_key:
            logger.warning("EMBEDDING_PROVIDER=openai 但缺少 URL/key，降级到 n-gram")
            return NgramHashEmbedding()
        return OpenAICompatibleEmbedding(base_url, api_key, model)
    # auto: API → 本地 → n-gram
    return CompositeEmbedding(base_url, api_key, model, local_model=local_model)
