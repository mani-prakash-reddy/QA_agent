"""Requirement embeddings (Azure OpenAI ada-002 or an OpenAI-compatible endpoint; 1536-d). Failures never block ingestion."""

from app.qa.llm.gateway import get_embeddings

BATCH = 16
MAX_CHARS = 1500  # the default embedding model accepts about 512 tokens


def requirement_text(title: str, description: str) -> str:
    return f"{title}. {description}"[:MAX_CHARS]


async def embed_texts(texts: list[str], *, confidential: bool) -> list[list[float]]:
    emb = get_embeddings(confidential=confidential)
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        out.extend(await emb.aembed_documents(texts[i : i + BATCH]))
    return out
