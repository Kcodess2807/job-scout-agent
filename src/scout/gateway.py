"""The model and embedding clients, both through GRAVITY_LLM_URL with the run token (the gateway swaps it for the real
key; the agent never holds one). Functions only: environment variables are read per run, never at import."""
import os

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config import CHAT_MODEL, EMBED_MODEL


def make_model(max_tokens: int = 2500) -> ChatOpenAI:
    return ChatOpenAI(model=CHAT_MODEL, base_url=os.environ["GRAVITY_LLM_URL"], api_key=os.environ["GRAVITY_RUN_TOKEN"],
                      max_tokens=max_tokens, timeout=90, max_retries=1, temperature=0)


def make_embedder():
    """An async texts -> vectors function, or None when the run has no model endpoint."""
    if not os.environ.get("GRAVITY_LLM_URL"):
        return None
    client = OpenAIEmbeddings(model=EMBED_MODEL, base_url=os.environ["GRAVITY_LLM_URL"],
                              api_key=os.environ["GRAVITY_RUN_TOKEN"], check_embedding_ctx_length=False,
                              max_retries=1, request_timeout=60)

    async def embed(texts: list[str]) -> list[list[float]]:
        return await client.aembed_documents([t[:2000] for t in texts])

    return embed
