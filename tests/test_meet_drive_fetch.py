from __future__ import annotations

import io
import zipfile

from scripts.notes.meet_drive_fetch import docx_text, note_name


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_word_transcript_becomes_a_plain_text_note():
    # Teams transcripts reach Drive as .docx, which the Docs export cannot convert.
    payload = _docx(["Meeting Transcript", "00:00:01 Anna: SAP ingestion is next.", "00:00:04 Philipp: Agreed."])
    document = {"name": "Meeting Transcript- Contoso ML Oct 26 2026.docx", "createdTime": "2026-10-26T17:44:00Z"}

    assert docx_text(payload) == (
        "Meeting Transcript\n00:00:01 Anna: SAP ingestion is next.\n00:00:04 Philipp: Agreed."
    )
    assert note_name(document) == "2026-10-26--meeting-transcript-contoso-ml-oct-26-2026"
