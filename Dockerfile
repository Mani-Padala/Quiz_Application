# Dockerfile for the Quiz Application
#
# Render's default Python deploy can't install system-level tools like
# Tesseract and Poppler (needed for OCR) — only pip packages. This
# Dockerfile installs them explicitly via apt, which is why Docker is the
# deploy method here instead of a plain Python service.
#
# IMPORTANT: run `python process_documents.py` locally BEFORE building/
# deploying this image. The image bakes in whatever's already in
# documents/, chunks_cache.pkl, sections_cache.pkl, and faiss_index/ at
# build time — so those need to already exist and be up to date before
# you deploy, otherwise the deployed app will show "documents not
# processed yet" with no way to run the processing step on Render itself
# (Render's free tier has no persistent disk to save the result to anyway).

FROM python:3.11-slim

# System dependencies for OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# On Linux, apt-get already puts these on PATH — empty POPPLER_PATH is
# treated as "use PATH" by ingestion.py (see: `POPPLER_PATH or None`)
ENV TESSERACT_CMD=/usr/bin/tesseract
ENV POPPLER_PATH=""

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]