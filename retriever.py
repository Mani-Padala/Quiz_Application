"""
retriever.py — Hybrid search (vector + keyword) for the Quiz Application

Combines FAISS (semantic/vector search) with BM25 (keyword search) via
LangChain's EnsembleRetriever, so retrieval catches both natural-language
matches and exact terms/abbreviations/product codes that pure vector
search can miss.
"""

from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever


def create_retriever(vectorstore, documents):
    """
    Builds a hybrid retriever combining BM25 (keyword) and FAISS (semantic)
    search with equal weighting.

    vectorstore: a FAISS vectorstore (from ingestion.py's create_vector_store
        or load_vector_store)
    documents: the raw chunked Document objects (needed by BM25, which
        indexes text directly rather than using embeddings)
    """
    # BM25 retriever — keyword matching
    bm25 = BM25Retriever.from_documents(documents)
    bm25.k = 3

    # FAISS retriever — semantic similarity
    faiss = vectorstore.as_retriever(search_kwargs={"k": 3})

    # Combine with equal weights
    ensemble = EnsembleRetriever(
        retrievers=[bm25, faiss],
        weights=[0.5, 0.5]
    )
    return ensemble


def retrieve_context(retriever, query):
    """
    Runs a query against the hybrid retriever and returns the matching
    Document objects (each with .page_content and .metadata).
    """
    return retriever.invoke(query)