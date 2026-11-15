# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document


def test_pdf_extraction_respects_the_fonts_mapped_space_width() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(400, 300)
    writer.add_blank_page(400, 300)
    cmap = DecodedStreamObject()
    cmap.set_data(b"1 beginbfchar\n<25> <0020>\nendbfchar")
    # This subset encodes a space as glyph 37, whose width exceeds glyph 32's.
    widths = [500] * (90 - 32 + 1)
    widths[0] = 20
    widths[37 - 32] = 300
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/TrueType"),
        NameObject("/BaseFont"): NameObject("/CUSTOM+Letters"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(90),
        NameObject("/Widths"): ArrayObject(map(NumberObject, widths)),
        NameObject("/ToUnicode"): writer._add_object(cmap),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    expected = "QUARTERLY OPERATING CASH FLOWS"
    parts = ["BT /F1 16 Tf"]
    x = 20.0
    for character in expected:
        glyph = "%" if character == " " else character
        parts.append(f"1 0 0 1 {x:.2f} 110 Tm <{ord(glyph):02x}> Tj")
        x += 5.04 if character == " " else 8.4
    parts.append("ET")
    content = DecodedStreamObject()
    content.set_data("\n".join(parts).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)

    pages = parse_pdf_document(PDFDocumentInput(document_id="subset-font", content=output.getvalue()))

    assert [(page.page_number, page.text.strip()) for page in pages] == [(1, expected), (2, "")]
