"""
process_documents.py — Standalone document processing tool

Run this manually whenever you add, remove, or change files in the
documents/ folder. It's completely separate from the web app (app.py) and
the CLI app (main.py) — neither of those ever triggers OCR or ingestion
themselves; they only read the caches this script produces.

Each document is loaded (and OCR'd, if needed) exactly ONCE here — see
ingestion.py's process_all_documents() — rather than being reloaded
separately for the vectorstore, the chunk cache, and section detection.

Usage:
    python process_documents.py
"""

from ingestion import process_all_documents

DOCUMENTS_FOLDER = "documents"


def main():
    print("=== Processing documents in 'documents/' ===\n")
    print("Loading and processing every file exactly once (this is the only slow part)...\n")

    section_names = process_all_documents(DOCUMENTS_FOLDER, force_reload=True)

    print(f"\nDone. Detected {len(section_names)} section(s):")
    for name in section_names:
        print(f"  - {name}")

    print("\n=== Processing complete. The web app and CLI app will now load instantly. ===")


if __name__ == "__main__":
    main()