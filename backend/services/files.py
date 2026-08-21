"""File processing — PDF extraction, text normalization, Claude Vision OCR."""
import base64
import io
import re
from pathlib import Path

from backend.services import claude


def extract_text(filename: str, content: bytes) -> str:
    """Extract plain text from uploaded file bytes."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _pdf_to_text(content)
    elif ext in (".txt", ".md", ".csv"):
        return content.decode("utf-8", errors="replace")
    else:
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return ""


def _pdf_to_text(content: bytes) -> str:
    """
    Extract text from PDF. Tries pdfplumber first (fast, text-based PDFs).
    Falls back to Claude Vision for scanned / handwritten PDFs.
    """
    # ── Try pdfplumber (text-based PDFs) ─────────────────────────────────────
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [p.extract_text() for p in pdf.pages if p.extract_text()]
        if pages:
            return "\n\n".join(pages)
    except Exception:
        pass

    # ── Try pypdf (another text-based approach) ───────────────────────────────
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        texts = [page.extract_text() or "" for page in reader.pages]
        combined = "\n\n".join(t for t in texts if t.strip())
        if combined.strip():
            return combined
    except Exception:
        pass

    # ── Fallback: Claude Vision OCR (handles handwritten / scanned PDFs) ─────
    return _pdf_via_claude_vision(content)


def _pdf_via_claude_vision(content: bytes) -> str:
    """
    Send PDF to Claude as a base64 document and ask it to transcribe
    all text including handwritten notes.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()

        # Anthropic accepts PDFs directly as base64 documents
        pdf_b64 = base64.standard_b64encode(content).decode("utf-8")

        resp = client.messages.create(
            model=claude.VISION_MODEL,
            max_tokens=8000,
            output_config={"effort": "medium"},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a baseball scouting document. "
                            "Please transcribe ALL text you can see, including handwritten notes, "
                            "abbreviations, numbers, and any annotations. "
                            "Preserve the structure as best you can. "
                            "Output only the transcribed text, nothing else."
                        ),
                    },
                ],
            }],
        )
        return claude.first_text(resp).strip()

    except Exception as e:
        return f"[Vision OCR failed: {e}]"


# A horizontal rule on its own line separates players within one document.
SPLIT_RE = re.compile(r"^\s*-{3,}\s*$", re.M)
# "Player: Tarik Skubal - LHP" / "Name: ..." / a bare leading name line.
NAME_RE = re.compile(r"^\s*(?:player|name)\s*[:\-]\s*(.+)$", re.M | re.I)


def group_by_player(files: list[tuple[str, bytes]]) -> dict[str, str]:
    """
    Group files by player name (strips ' (page N)' suffixes from multi-page PDFs).
    Returns {player_label: combined_text}.
    """
    groups: dict[str, list[str]] = {}
    for filename, content in files:
        base = re.sub(
            r"\s*\(page \d+\)\s*$", "", Path(filename).stem, flags=re.IGNORECASE
        ).strip()
        text = extract_text(filename, content)
        groups.setdefault(base, []).append(text)

    merged = {label: "\n\n".join(texts) for label, texts in groups.items()}

    # A single document may hold several players separated by a `---` rule,
    # which is the convention the UI documents. Split those out so each player
    # is evaluated on his own evidence; without this every player in the file
    # is merged into one report and graded as a single arm.
    out: dict[str, str] = {}
    for label, text in merged.items():
        sections = [b.strip() for b in SPLIT_RE.split(text) if b.strip()]
        if len(sections) < 2:
            out[label] = text
            continue
        for i, section in enumerate(sections, start=1):
            out[_section_label(section, label, i)] = section
    return out


def _section_label(section: str, fallback: str, index: int) -> str:
    """Prefer the player's own name over a positional label."""
    m = NAME_RE.search(section)
    if m:
        name = m.group(1).strip(" -\u2014:")
        # Trim a trailing position tag, e.g. "Tarik Skubal - LHP".
        name = re.split(r"\s+[\u2014-]\s+", name)[0].strip()
        if name:
            return name
    return f"{fallback} ({index})"
