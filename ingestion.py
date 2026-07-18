"""
ingestion.py — Document ingestion pipeline for the Quiz Application

Key design point: every document is loaded (and OCR'd, if needed) exactly
ONCE, via _load_all_raw_documents(), which caches its result. The FAISS
vectorstore, the BM25 chunk cache, and section detection all derive from
that single raw pass instead of each independently re-loading/re-OCR'ing
every file — this used to happen 3 separate times per file in
process_documents.py, which is why processing felt much slower than it
needed to be.

Requires (system-level, not just pip installs):
- Tesseract OCR engine installed on the machine
- Poppler (for converting PDF pages into images before OCR)
"""

from pathlib import Path
import pickle
import os

from langchain_community.document_loaders import PyPDFLoader
from langchain_community.document_loaders import Docx2txtLoader
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

import pytesseract
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image
import re


# ---------------------------------------------------------------------------
# OCR configuration — reads from environment variables if set, otherwise
# falls back to this machine's known Windows paths. On Linux (e.g. Render's
# Docker deploy), these get overridden via environment variables instead —
# see Dockerfile for the actual Linux values.
# ---------------------------------------------------------------------------

TESSERACT_CMD = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
POPPLER_PATH = os.environ.get("POPPLER_PATH", r"C:\Program Files\poppler-26.02.0\Library\bin")

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

MIN_TEXT_LENGTH_THRESHOLD = 20

# Cache paths — all three are produced together by process_all_documents()
RAW_DOCS_CACHE_PATH = "raw_documents_cache.pkl"   # per-file raw pages (post load/OCR, pre-chunk)
CHUNKS_CACHE_PATH = "chunks_cache.pkl"             # flat chunked Documents, for BM25
SECTIONS_CACHE_PATH = "sections_cache.pkl"         # section_name -> list of context strings


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------

def detect_file_type(file_path):
    file_type = Path(file_path).suffix.lower()
    match file_type:
        case '.pdf': return 'pdf'
        case '.docx': return 'docx'
        case '.txt': return 'txt'
        case '.png' | '.jpg' | '.jpeg' | '.tiff': return 'image'
        case _: return 'unknown'


# ---------------------------------------------------------------------------
# OCR functions
# ---------------------------------------------------------------------------

def clean_ocr_text(text):
    """Normalizes Tesseract's often-noisy whitespace into cleaner text."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = '\n'.join(line.strip() for line in text.split('\n'))
    return text.strip()


def ocr_pdf_pages(file_path, batch_size=10, dpi=200):
    """
    Converts and OCRs a PDF's pages in small batches rather than converting
    every page to an image at once — keeps peak memory bounded regardless
    of total document length.
    """
    info = pdfinfo_from_path(str(file_path), poppler_path=POPPLER_PATH or None)
    total_pages = info["Pages"]
    print(f"  Total pages: {total_pages}. Running OCR in batches of {batch_size} pages...")

    documents = []
    for batch_start in range(1, total_pages + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, total_pages)

        page_images = convert_from_path(
            str(file_path),
            poppler_path=POPPLER_PATH or None,
            first_page=batch_start,
            last_page=batch_end,
            dpi=dpi,
        )

        for offset, image in enumerate(page_images):
            page_number = batch_start + offset
            text = clean_ocr_text(pytesseract.image_to_string(image))
            documents.append(
                Document(
                    page_content=text,
                    metadata={"source": str(file_path), "page": page_number, "ocr": True}
                )
            )
            print(f"  OCR progress: page {page_number}/{total_pages}")

        del page_images

    return documents


def ocr_image_file(file_path):
    image = Image.open(file_path)
    text = clean_ocr_text(pytesseract.image_to_string(image))
    return [Document(page_content=text, metadata={"source": str(file_path), "ocr": True})]


def _extracted_text_is_empty(documents, threshold=MIN_TEXT_LENGTH_THRESHOLD):
    total_length = sum(len(doc.page_content.strip()) for doc in documents)
    return total_length < threshold


# ---------------------------------------------------------------------------
# Document loading (per file — called exactly once per file, see below)
# ---------------------------------------------------------------------------

def load_document(file_path, file_type):
    if file_type == 'pdf':
        loader = PyPDFLoader(file_path)
        documents = loader.load()

        if _extracted_text_is_empty(documents):
            print(f"  '{Path(file_path).name}' appears to be a scanned/image-based PDF — running OCR (this can take a while for large files)...")
            documents = ocr_pdf_pages(file_path)

        return documents

    elif file_type == 'docx':
        loader = Docx2txtLoader(file_path)
        return loader.load()

    elif file_type == 'txt':
        loader = TextLoader(file_path)
        return loader.load()

    elif file_type == 'image':
        print(f"  Running OCR on image file '{Path(file_path).name}'...")
        return ocr_image_file(file_path)

    else:
        return None


# ---------------------------------------------------------------------------
# Single raw-load pass — THE only place load_document() gets called across
# the whole pipeline. Everything else (vectorstore, BM25 chunks, sections)
# derives from this cached result.
# ---------------------------------------------------------------------------

def _load_all_raw_documents(documents_folder, force_reload=False):
    """
    Loads (and OCRs, if needed) every file in documents_folder exactly
    once, caching the result. Returns a dict: filename -> list of raw
    page Documents (pre-chunking) — kept per-file so section detection
    can still correctly reset at each new document's boundary.
    """
    if not force_reload and os.path.exists(RAW_DOCS_CACHE_PATH):
        with open(RAW_DOCS_CACHE_PATH, "rb") as f:
            return pickle.load(f)

    folder = Path(documents_folder)
    raw_by_file = {}

    for file_path in folder.iterdir():
        if file_path.is_file():
            file_type = detect_file_type(file_path)
            documents = load_document(file_path, file_type)
            if documents is None:
                continue
            raw_by_file[file_path.name] = documents

    with open(RAW_DOCS_CACHE_PATH, "wb") as f:
        pickle.dump(raw_by_file, f)

    return raw_by_file


# ---------------------------------------------------------------------------
# Chunking and vector store
# ---------------------------------------------------------------------------

def chunk_documents(documents):
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    return splitter.split_documents(documents)


def create_vector_store(chunks):
    embeddings = HuggingFaceEmbeddings(model_name='all-MiniLM-L6-v2')
    vectorstore = FAISS.from_documents(chunks, embeddings)
    vectorstore.save_local('faiss_index')
    return vectorstore


def load_vector_store():
    """Pure cache reader — call process_all_documents() first if this doesn't exist yet."""
    embeddings = HuggingFaceEmbeddings(model_name='all-MiniLM-L6-v2')
    return FAISS.load_local('faiss_index', embeddings,
                           allow_dangerous_deserialization=True)


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

SECTION_HEADING_PATTERN = re.compile(
    r'^\s*(chapter|section|part)\s+[ivxlcdm\d]+[\s:.\-]*(.*)$',
    re.IGNORECASE
)


def detect_sections(documents):
    """
    Groups a single document's pages (in order) under detected section
    headings. Returns dict: section_name -> list of Document objects.
    Falls back to one "Full Document" bucket if no headings are found.
    """
    sections = {}
    current_section = None

    for doc in documents:
        stripped = doc.page_content.strip()
        first_line = stripped.split('\n', 1)[0] if stripped else ''
        match = SECTION_HEADING_PATTERN.match(first_line)

        if match:
            current_section = first_line.strip()[:80]
            if current_section not in sections:
                sections[current_section] = []

        if current_section is None:
            current_section = "Introduction"
            sections.setdefault(current_section, [])

        sections[current_section].append(doc)

    if len(sections) == 1:
        only_key = next(iter(sections))
        sections = {"Full Document": sections[only_key]}

    return sections


# ---------------------------------------------------------------------------
# The single entry point that builds everything — this is what
# process_documents.py calls. Loads each file once, derives all 3 caches.
# ---------------------------------------------------------------------------

def process_all_documents(documents_folder, force_reload=False):
    """
    The one place all processing happens. Loads/OCRs each file exactly
    once (via _load_all_raw_documents, itself cached), then builds:
      1. faiss_index/        — vector store, for potential future semantic search
      2. chunks_cache.pkl     — flat chunked Documents, for BM25 (retriever.py)
      3. sections_cache.pkl   — section_name -> context strings, for quiz generation

    Returns the detected section names (for a quick confirmation printout).
    """
    raw_by_file = _load_all_raw_documents(documents_folder, force_reload=force_reload)

    # --- 1 & 2: flatten for chunking, build vectorstore + chunk cache ---
    all_documents = []
    for pages in raw_by_file.values():
        all_documents.extend(pages)

    chunks = chunk_documents(all_documents)

    with open(CHUNKS_CACHE_PATH, "wb") as f:
        pickle.dump(chunks, f)

    create_vector_store(chunks)

    # --- 3: sections, detected per-file so boundaries don't bleed across files ---
    section_page_map = {}
    for pages in raw_by_file.values():
        file_sections = detect_sections(pages)
        for section_name, section_pages in file_sections.items():
            section_page_map.setdefault(section_name, []).extend(section_pages)

    sections_result = {}
    for section_name, pages in section_page_map.items():
        section_chunks = chunk_documents(pages)
        sections_result[section_name] = [c.page_content for c in section_chunks]

    with open(SECTIONS_CACHE_PATH, "wb") as f:
        pickle.dump(sections_result, f)

    return list(sections_result.keys())


# ---------------------------------------------------------------------------
# Pure cache readers — used by app.py and main.py. Neither ever triggers
# processing itself; if a cache is missing, that's process_documents.py's
# job to fix, not theirs.
# ---------------------------------------------------------------------------

def get_all_chunks(documents_folder=None, force_rebuild=False):
    with open(CHUNKS_CACHE_PATH, "rb") as f:
        return pickle.load(f)


def get_document_sections(documents_folder=None, force_rebuild=False):
    with open(SECTIONS_CACHE_PATH, "rb") as f:
        return pickle.load(f)