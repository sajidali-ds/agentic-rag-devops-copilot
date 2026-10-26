import logfire
from pypdf import PdfReader


def parse_pdf(file_path: str) -> str:


    with logfire.span("PDF Parsing (local)", filename=file_path):
        try:
            reader = PdfReader(file_path)
            pages: list[str] = [(page.extract_text() or "") for page in reader.pages]
            blank = [i for i, t in enumerate(pages) if not t.strip()]
            logfire.info(f"PDF has {len(pages)} pages, {len(blank)} blank after pypdf.")

            if blank:
                try:
                    import pdfplumber

                    with pdfplumber.open(file_path) as pdf:
                        for i in blank:
                            pages[i] = pdf.pages[i].extract_text() or ""
                except Exception as e:  # noqa: BLE001
                    logfire.warning(f"pdfplumber fallback failed: {e}")

            full_text = "\n\n".join(t for t in pages if t.strip())
            if not full_text.strip():
                logfire.warning(f"No text extracted from {file_path}; it may be fully image-based (needs OCR).")
            return full_text
        except Exception as e:  # noqa: BLE001
            logfire.error(f"PDF parse failed for {file_path}: {e}")
            raise
