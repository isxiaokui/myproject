import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
load_dotenv(BACKEND_DIR / ".env")

documents = TextLoader(str(DATA_DIR / "flowers.txt"), encoding="utf-8").load()
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1,
    chunk_overlap=0,
    separators=["\n"],
)
splits = text_splitter.split_documents(documents)
embedding = OpenAIEmbeddings(
    openai_api_key=os.getenv("ZAI_API_KEY"),
    model="embedding-3",
    openai_api_base="https://open.bigmodel.cn/api/paas/v4/",
)
Chroma.from_documents(
    splits,
    embedding,
    persist_directory=str(DATA_DIR / "chroma_db"),
)
