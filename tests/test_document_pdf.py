import io
from pathlib import Path
from PIL import Image, ImageDraw
from pypdf import PdfReader
from app.services.document_pdf_service import document_pdf_service


def _create_document_page_image(path: Path, *, include_text: bool = True) -> None:
    """Render a crisp synthetic document page with heading, body text, table rule, and diagram."""
    img = Image.new("RGB", (1200, 1550), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    if include_text:
        # Heading + divider line
        draw.text((90, 90), "ARDUINO MEGA PINOUT GUIDE", fill=(18, 18, 18))
        draw.line([(90, 145), (1100, 145)], fill=(40, 40, 40), width=4)
        draw.text((90, 200), "Operating Voltage: 5V DC", fill=(20, 20, 20))
        draw.text((90, 260), "Digital I/O Pins: 54 total", fill=(20, 20, 20))
        draw.text((90, 320), "Analog Input Pins: 16 channels", fill=(20, 20, 20))

    # Colored non-text schematic block
    draw.rectangle([(120, 520), (540, 820)], outline=(30, 80, 180), fill=(210, 232, 252), width=5)
    draw.ellipse([(240, 590), (420, 750)], outline=(180, 40, 40), width=5)

    img.save(path, format="PNG")


def test_build_enhanced_scan_pdf_multipage(tmp_path: Path):
    p1 = tmp_path / "page1.png"
    p2 = tmp_path / "page2.png"
    _create_document_page_image(p1, include_text=True)
    _create_document_page_image(p2, include_text=False)

    pdf_bytes = document_pdf_service.build_enhanced_scan_pdf([p1, p2], pdf_dpi=180)
    assert pdf_bytes.startswith(b"%PDF-")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 2


def test_build_typeset_pdf_layout_matched_and_clean_reflow(tmp_path: Path):
    p1 = tmp_path / "page1.png"
    _create_document_page_image(p1, include_text=True)

    # 1. Layout-matched vector PDF
    pdf_bytes, word_count, ocr_ok, warnings = document_pdf_service.build_typeset_pdf(
        [p1],
        mode="layout_matched",
        include_graphics=True,
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert ocr_ok is True
    assert word_count >= 4

    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    extracted = reader.pages[0].extract_text() or ""
    assert any(tok in extracted.upper() for tok in ("ARDUINO", "VOLTAGE", "DIGITAL", "ANALOG"))

    # 2. Clean reflow vector PDF
    reflow_bytes, r_words, r_ocr, _ = document_pdf_service.build_typeset_pdf(
        [p1],
        mode="clean_reflow",
        include_graphics=True,
    )
    assert reflow_bytes.startswith(b"%PDF-")
    assert r_ocr is True and r_words >= 4
    reflow_text = PdfReader(io.BytesIO(reflow_bytes)).pages[0].extract_text() or ""
    assert any(tok in reflow_text.upper() for tok in ("ARDUINO", "VOLTAGE", "DIGITAL"))


def test_build_typeset_pdf_handles_pure_diagram_page_gracefully(tmp_path: Path):
    p_diag = tmp_path / "diagram_only.png"
    _create_document_page_image(p_diag, include_text=False)

    pdf_bytes, word_count, ocr_ok, warnings = document_pdf_service.build_typeset_pdf(
        [p_diag],
        mode="layout_matched",
        include_graphics=True,
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert ocr_ok is True
    assert word_count == 0
    assert any("no printed text detected" in w.lower() for w in warnings)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
