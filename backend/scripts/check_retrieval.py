import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")

embedding = OpenAIEmbeddings(
    openai_api_key=os.getenv("ZAI_API_KEY"),
    model="embedding-3",
    openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
)
vector_db = Chroma(
    persist_directory=str(BACKEND_DIR / "data" / "chroma_db"),
    embedding_function=embedding,
)

for document in vector_db.similarity_search("玫瑰", k=3):
    print(document.page_content)
