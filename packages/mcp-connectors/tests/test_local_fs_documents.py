"""Tests for `mcp_connectors/local_fs/search.py`'s `"office"`/`"pdf"`
`file_categories` support: real `.docx`/`.xlsx`/`.pptx`/`.pdf` fixtures
built with the same libraries the connector uses to read them
(`python-docx`/`openpyxl`/`python-pptx`/`pypdf`), asserting real
extracted text comes back through `search_local_files`/`fetch_local_file`
-- not mocked file content -- plus the defensive "a corrupted file must
not crash a search" case for each format, one real per-format exception
observed directly (see `local_fs/search.py`'s module docstring's
"Per-format text extraction" section).

Skipped entirely (not failed) if the `documents` extra isn't installed --
see `pyproject.toml`'s `documents` extra, deliberately kept separate from
`all` since most `local_docs`/`onedrive` installs won't need it.
"""
from __future__ import annotations

import pytest

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")
pptx = pytest.importorskip("pptx")
pypdf = pytest.importorskip("pypdf")

from mcp_connectors.common import ConnectorAPIError  # noqa: E402
from mcp_connectors.local_fs.search import fetch_local_file, search_local_files  # noqa: E402


# -- real fixture builders -- each uses the same library the connector reads with --


def _write_docx(path, paragraph_text="", table_cell_text=None):
    document = docx.Document()
    if paragraph_text:
        document.add_paragraph(paragraph_text)
    if table_cell_text is not None:
        table = document.add_table(rows=1, cols=1)
        table.rows[0].cells[0].text = table_cell_text
    document.save(str(path))


def _write_xlsx(path, cell_value=""):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = cell_value
    workbook.save(str(path))


def _write_pptx(path, title_text=""):
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = title_text
    presentation.save(str(path))


def _write_pdf(path, text=""):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    content = f"BT /F1 18 Tf 10 150 Td ({text}) Tj ET".encode("latin-1")
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = writer._add_object(font)
    resources = DictionaryObject()
    resources[NameObject("/Font")] = font_dict
    page[NameObject("/Resources")] = resources

    with open(path, "wb") as fh:
        writer.write(fh)


# -- happy path: real extracted text comes back through search()/fetch() --------


def test_docx_paragraph_and_table_text_is_searchable(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_docx(allowed / "report.docx", paragraph_text="unique-docx-paragraph-marker", table_cell_text="unique-docx-table-marker")

    results = search_local_files(
        "unique-docx-paragraph-marker", [allowed], source="local_docs", limit=10, file_categories=["office"]
    )
    assert [doc.title for doc in results] == ["report.docx"]

    table_results = search_local_files(
        "unique-docx-table-marker", [allowed], source="local_docs", limit=10, file_categories=["office"]
    )
    assert [doc.title for doc in table_results] == ["report.docx"]


def test_docx_fetch_returns_real_extracted_text(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    docx_path = allowed / "report.docx"
    _write_docx(docx_path, paragraph_text="the real docx body text")

    doc = fetch_local_file(str(docx_path), [allowed], source="local_docs", file_categories=["office"])
    assert "the real docx body text" in doc.snippet


def test_xlsx_cell_value_is_searchable(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_xlsx(allowed / "sheet.xlsx", cell_value="unique-xlsx-cell-marker")

    results = search_local_files(
        "unique-xlsx-cell-marker", [allowed], source="local_docs", limit=10, file_categories=["office"]
    )
    assert [doc.title for doc in results] == ["sheet.xlsx"]


def test_xlsx_fetch_returns_real_cell_value(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    xlsx_path = allowed / "sheet.xlsx"
    _write_xlsx(xlsx_path, cell_value="the real xlsx cell value")

    doc = fetch_local_file(str(xlsx_path), [allowed], source="local_docs", file_categories=["office"])
    assert "the real xlsx cell value" in doc.snippet


def test_pptx_slide_title_text_is_searchable(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_pptx(allowed / "deck.pptx", title_text="unique-pptx-slide-marker")

    results = search_local_files(
        "unique-pptx-slide-marker", [allowed], source="local_docs", limit=10, file_categories=["office"]
    )
    assert [doc.title for doc in results] == ["deck.pptx"]


def test_pptx_fetch_returns_real_slide_text(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    pptx_path = allowed / "deck.pptx"
    _write_pptx(pptx_path, title_text="the real pptx slide text")

    doc = fetch_local_file(str(pptx_path), [allowed], source="local_docs", file_categories=["office"])
    assert "the real pptx slide text" in doc.snippet


def test_pdf_page_text_is_searchable(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_pdf(allowed / "doc.pdf", text="unique-pdf-marker-99")

    results = search_local_files(
        "unique-pdf-marker-99", [allowed], source="local_docs", limit=10, file_categories=["pdf"]
    )
    assert [doc.title for doc in results] == ["doc.pdf"]


def test_pdf_fetch_returns_real_page_text(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    pdf_path = allowed / "doc.pdf"
    _write_pdf(pdf_path, text="realpdftext")

    doc = fetch_local_file(str(pdf_path), [allowed], source="local_docs", file_categories=["pdf"])
    assert "realpdftext" in doc.snippet


def test_pdf_encrypted_document_is_treated_as_unreadable_not_crashed(tmp_path):
    """A password-protected PDF is skipped (no password-guessing
    attempted -- this connector has no credential of any kind, on
    principle) rather than raising or returning garbage."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    # The unencrypted source lives *outside* the allowed directory -- only
    # the encrypted copy is placed where search() can reach it, so a
    # correct "encrypted files don't match" result can't be confused with
    # this source file itself legitimately matching.
    plain_path = tmp_path / "plain-source.pdf"
    _write_pdf(plain_path, text="secret-encrypted-marker")

    writer = pypdf.PdfWriter()
    writer.append(str(plain_path))
    writer.encrypt(user_password="hunter2")
    encrypted_path = allowed / "encrypted.pdf"
    with open(encrypted_path, "wb") as fh:
        writer.write(fh)

    # search: the encrypted file never shows up as a match
    results = search_local_files(
        "secret-encrypted-marker", [allowed], source="local_docs", limit=10, file_categories=["pdf"]
    )
    assert results == []

    # fetch: a clear error, not a raised library exception or empty/garbage content
    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(encrypted_path), [allowed], source="local_docs", file_categories=["pdf"])


# -- corrupted files: skip this one file, never crash the whole search ----------


def _write_garbage(path):
    path.write_bytes(b"this is not a real office/pdf file, just plain garbage bytes" * 20)


def test_corrupted_pdf_is_skipped_not_crashed_in_search(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_garbage(allowed / "corrupt.pdf")
    _write_pdf(allowed / "real.pdf", text="findable-marker")

    results = search_local_files("findable-marker", [allowed], source="local_docs", limit=10, file_categories=["pdf"])
    assert [doc.title for doc in results] == ["real.pdf"]


def test_corrupted_pdf_fetch_raises_clear_connector_error_not_library_exception(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    corrupt = allowed / "corrupt.pdf"
    _write_garbage(corrupt)

    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(corrupt), [allowed], source="local_docs", file_categories=["pdf"])


def test_corrupted_docx_is_skipped_not_crashed(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    corrupt = allowed / "corrupt.docx"
    _write_garbage(corrupt)

    results = search_local_files("garbage", [allowed], source="local_docs", limit=10, file_categories=["office"])
    assert results == []
    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(corrupt), [allowed], source="local_docs", file_categories=["office"])


def test_corrupted_xlsx_is_skipped_not_crashed(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    corrupt = allowed / "corrupt.xlsx"
    _write_garbage(corrupt)

    results = search_local_files("garbage", [allowed], source="local_docs", limit=10, file_categories=["office"])
    assert results == []
    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(corrupt), [allowed], source="local_docs", file_categories=["office"])


def test_corrupted_pptx_is_skipped_not_crashed(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    corrupt = allowed / "corrupt.pptx"
    _write_garbage(corrupt)

    results = search_local_files("garbage", [allowed], source="local_docs", limit=10, file_categories=["office"])
    assert results == []
    with pytest.raises(ConnectorAPIError):
        fetch_local_file(str(corrupt), [allowed], source="local_docs", file_categories=["office"])


def test_corrupted_office_file_does_not_abort_a_multi_file_walk(tmp_path):
    """The real point of the defensive per-format catch: one bad file
    among several must not prevent the good ones from being found."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_garbage(allowed / "bad1.docx")
    _write_garbage(allowed / "bad2.xlsx")
    _write_garbage(allowed / "bad3.pptx")
    _write_garbage(allowed / "bad4.pdf")
    _write_docx(allowed / "good.docx", paragraph_text="survives-the-walk-marker")

    results = search_local_files(
        "survives-the-walk-marker",
        [allowed],
        source="local_docs",
        limit=10,
        file_categories=["office", "pdf"],
    )
    assert [doc.title for doc in results] == ["good.docx"]


# -- extension/category coverage --------------------------------------------------


def test_office_and_pdf_files_invisible_without_the_right_category_enabled(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _write_docx(allowed / "report.docx", paragraph_text="hidden-marker")
    _write_pdf(allowed / "doc.pdf", text="hidden-marker")

    results = search_local_files("hidden-marker", [allowed], source="local_docs", limit=10, file_categories=["text"])
    assert results == []
