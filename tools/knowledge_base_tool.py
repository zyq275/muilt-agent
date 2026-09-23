"""
本地知识库检索工具 — TDD §7.3

签名（照抄 TDD §7.3）：
    search_knowledge_base(query: str, k: int = 4) -> str

返回格式：
    每个片段：[来源: 文件名]\n片段文本
    片段间空行分隔
    无结果：知识库中未检索到相关内容

实现：对本地 Chroma 向量库做相似度检索，返回 top-k 个最相关分块。
依赖：向量库须已由 scripts/build_knowledge_base.py 构建。
"""

import os
import sys
import threading
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from utils.logger_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

VECTOR_STORE_DIR = os.getenv("VECTOR_STORE_DIR", "./vector_store")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")
EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "")

# 缓存 Embedding 实例（避免重复加载）
# 注：多个子智能体可能并发调用，必须加锁，否则会重复加载模型
#     并触发 Chroma 客户端竞争（Could not connect to tenant ...）
_embedding_fn = None
_vector_store = None
# 用 RLock：_get_vector_store 持锁期间会再调用 _get_embedding_fn（同锁嵌套）
_init_lock = threading.RLock()


def _get_embedding_fn() -> HuggingFaceEmbeddings:
    """获取或创建 Embedding 函数实例（单例）。"""
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn

    with _init_lock:
        # 双重检查：可能已被其他并发调用初始化
        if _embedding_fn is not None:
            return _embedding_fn

        model_name = EMBEDDING_MODEL_PATH if EMBEDDING_MODEL_PATH else EMBEDDING_MODEL
        logger.info(f"加载 Embedding 模型：{model_name}")

        encode_kwargs = {"normalize_embeddings": True}
        _embedding_fn = HuggingFaceEmbeddings(
            model_name=model_name,
            encode_kwargs=encode_kwargs,
        )
        return _embedding_fn


def _get_vector_store() -> Chroma | None:
    """获取 Chroma 向量库实例；若未构建则返回 None。"""
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    vs_path = Path(VECTOR_STORE_DIR).resolve()
    if not vs_path.exists() or not any(vs_path.iterdir()):
        return None

    with _init_lock:
        # 双重检查：可能已被其他并发调用初始化
        if _vector_store is not None:
            return _vector_store

        embedding = _get_embedding_fn()
        _vector_store = Chroma(
            persist_directory=str(vs_path),
            embedding_function=embedding,
        )
        return _vector_store


def search_knowledge_base(query: str, k: int = 4) -> str:
    """
    对本地知识库做语义检索，返回最相关的文档片段。

    Args:
        query: 检索问题/关键词。
        k: 返回最相关的 k 个片段，默认 4。

    Returns:
        格式化检索结果：
        [来源: 文件名]
        片段文本

        片段间空行分隔；无结果返回"知识库中未检索到相关内容"；
        索引未构建返回"知识库索引尚未构建，请先运行 scripts/build_knowledge_base.py"。
    """
    try:
        vs = _get_vector_store()
        if vs is None:
            return "知识库索引尚未构建，请先运行 scripts/build_knowledge_base.py"

        results = vs.similarity_search_with_score(query, k=k)
        if not results:
            return "知识库中未检索到相关内容"

        fragments = []
        for doc, score in results:
            source = doc.metadata.get("source", "未知来源")
            # 只取文件名，不取完整路径
            source_name = Path(source).name if source != "未知来源" else source
            fragment = f"[来源: {source_name}]\n{doc.page_content}"
            fragments.append(fragment)

        result_text = "\n\n".join(fragments)
        logger.info(f"search_knowledge_base('{query}') 返回 {len(results)} 个片段")
        return result_text

    except Exception as e:
        logger.error(f"search_knowledge_base 失败：{e}")
        return f"知识库检索失败：{e}"


# ── __main__ 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("=== search_knowledge_base 测试 ===")
    result = search_knowledge_base("阿莫西林的适应症", k=3)
    print(result)
