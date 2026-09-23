"""
离线构建向量索引 — TDD §9.2

流程：
1. 加载 knowledge_base/ 目录下的企业私有文档（支持 .txt/.md/.pdf/.docx）
2. 文档分块（chunk_size=500, overlap=50）
3. BGE 向量化（HuggingFaceEmbeddings）
4. 持久化到 vector_store/（Chroma）

使用：
    python scripts/build_knowledge_base.py
"""

import os
import shutil
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from utils.logger_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

# ── 配置 ──────────────────────────────────────────────
KNOWLEDGE_BASE_DIR = os.getenv("KNOWLEDGE_BASE_DIR", "./knowledge_base")
VECTOR_STORE_DIR = os.getenv("VECTOR_STORE_DIR", "./vector_store")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")
EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "")

# 分块参数（照抄 TDD §9.2）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _load_documents(kb_dir: Path) -> list[Document]:
    """
    从 knowledge_base/ 目录加载文档。

    支持：.txt / .md / .pdf / .docx
    """
    documents = []

    if not kb_dir.exists():
        logger.warning(f"知识库目录不存在：{kb_dir}")
        return documents

    for file_path in kb_dir.rglob("*"):
        if file_path.is_dir():
            continue

        suffix = file_path.suffix.lower()
        try:
            if suffix in (".txt", ".md"):
                text = file_path.read_text(encoding="utf-8")
                documents.append(Document(
                    page_content=text,
                    metadata={"source": str(file_path)},
                ))
            elif suffix == ".pdf":
                from pypdf import PdfReader
                reader = PdfReader(str(file_path))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                if text.strip():
                    documents.append(Document(
                        page_content=text,
                        metadata={"source": str(file_path)},
                    ))
            elif suffix == ".docx":
                from docx import Document as DocxDocument
                doc = DocxDocument(str(file_path))
                text = "\n".join(para.text for para in doc.paragraphs)
                if text.strip():
                    documents.append(Document(
                        page_content=text,
                        metadata={"source": str(file_path)},
                    ))
            else:
                logger.debug(f"跳过不支持的文件格式：{file_path}")
        except Exception as e:
            logger.error(f"加载文件失败 {file_path}：{e}")

    logger.info(f"共加载 {len(documents)} 个文档")
    return documents


def build_knowledge_base() -> None:
    """执行完整构建流程：加载 → 分块 → 向量化 → 持久化。"""
    kb_dir = Path(KNOWLEDGE_BASE_DIR).resolve()
    vs_dir = Path(VECTOR_STORE_DIR).resolve()

    logger.info(f"知识库目录：{kb_dir}")
    logger.info(f"向量存储目录：{vs_dir}")
    logger.info(f"分块参数：chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}")

    # 1. 加载文档
    documents = _load_documents(kb_dir)
    if not documents:
        logger.warning("知识库目录下没有可加载的文档，跳过构建")
        return

    # 2. 分块
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "，", " ", ""],
    )
    chunks = splitter.split_documents(documents)
    logger.info(f"分块完成：{len(documents)} 个文档 → {len(chunks)} 个分块")

    # 3. 初始化 Embedding
    model_name = EMBEDDING_MODEL_PATH if EMBEDDING_MODEL_PATH else EMBEDDING_MODEL
    logger.info(f"加载 Embedding 模型：{model_name}")

    embedding = HuggingFaceEmbeddings(
        model_name=model_name,
        encode_kwargs={"normalize_embeddings": True},
    )

    # 4. 清空旧索引
    #
    # Chroma.from_documents 是**追加**语义：它复用同名 collection 并往里加，
    # 不会覆盖。不清空的后果是每次重建都在累积——删掉或改名过的文档，
    # 它的旧分块会永远留在索引里继续被检索到，让 RAG 评估的命中率虚高，
    # 而且检索结果里会出现知识库里早已不存在的来源。
    #
    # 放在分块成功之后再清：万一前面加载/分块抛异常，旧索引还在，不至于两头落空。
    if vs_dir.exists():
        shutil.rmtree(vs_dir)
        logger.info(f"已清空旧索引：{vs_dir}")
    vs_dir.mkdir(parents=True, exist_ok=True)

    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embedding,
        persist_directory=str(vs_dir),
    )
    logger.info(f"向量索引已构建并持久化到 {vs_dir}")

    # 5. 校验：落盘向量数必须等于分块数。
    # 不等就说明还是追加了（或并发写入），此时索引不可信，宁可让构建失败。
    stored = vector_store._collection.count()
    logger.info(f"向量库中共 {stored} 个向量")
    if stored != len(chunks):
        raise RuntimeError(
            f"索引校验失败：分块 {len(chunks)} 个，但库中存有 {stored} 个向量。"
            "索引可能被追加写入污染，请手动删除 vector_store/ 后重建。"
        )


# ── __main__ ──────────────────────────────────────────
if __name__ == "__main__":
    build_knowledge_base()
