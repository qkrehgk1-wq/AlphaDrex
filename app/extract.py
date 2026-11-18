"""Text from manual files people actually have (review follow-up B-7, 2026-11-17).

- TXT/MD: as before.
- HWPX (한글 2014+ default 'open' format): a ZIP of XML; paragraphs are read with the standard library.
- PDF: page text via pypdf when installed; scanned (image-only) PDFs are refused, there is no OCR.
- HWP (binary 5.0): not parsed. The person is told to save it as HWPX or TXT in 한글 first.
Nothing here guesses: an empty or unreadable result is an error, never a silent empty manual.
"""
import base64
import io
import re
import zipfile
from xml.etree import ElementTree

MAX_BINARY = 8_000_000
MAX_EXPANDED = 16_000_000
MAX_TEXT = 300_000
MAX_SECTIONS = 256
TEXT_SUFFIXES = ('.txt', '.md')
BINARY_SUFFIXES = ('.hwpx', '.pdf')


def _paragraphs_hwpx(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError('HWPX 파일을 열 수 없습니다. 한글에서 다시 저장해 주세요') from None
    with archive:
        sections = sorted((i for i in archive.infolist() if re.fullmatch(r'Contents/section\d+\.xml', i.filename)),
                          key=lambda i: int(re.search(r'\d+', i.filename.rsplit('/', 1)[1]).group()))
        if not sections:
            raise ValueError('HWPX 본문을 찾지 못했습니다')
        if len(sections) > MAX_SECTIONS or sum(i.file_size for i in sections) > MAX_EXPANDED:
            raise ValueError('HWPX 압축 해제 본문이 너무 큽니다. 문서를 나눠 등록하세요')
        if len({i.filename for i in sections}) != len(sections):
            raise ValueError('HWPX 본문 구성이 중복되었습니다. 한글에서 다시 저장해 주세요')
        lines, characters = [], 0
        for info in sections:
            try:
                xml = archive.read(info)
                # HWPX needs no DTD. Refuse entity expansion before the XML parser sees it.
                declarations = xml.replace(b'\0', b'').upper()
                if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
                    raise ValueError('HWPX 본문에 지원하지 않는 XML 선언이 있습니다')
                root = ElementTree.fromstring(xml)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, ElementTree.ParseError):
                raise ValueError('HWPX 본문을 읽지 못했습니다. 한글에서 다시 저장해 주세요') from None
            for paragraph in root.iter():
                if paragraph.tag.rsplit('}', 1)[-1] != 'p':
                    continue
                # Tables nest their own paragraphs and are visited separately.
                text = ''.join(t.text or '' for run in paragraph if run.tag.rsplit('}', 1)[-1] == 'run'
                               for t in run if t.tag.rsplit('}', 1)[-1] == 't').strip()
                if text:
                    characters += len(text) + bool(lines)
                    if characters > MAX_TEXT:
                        raise ValueError('문서 본문은 300,000자 이하만 등록할 수 있습니다')
                    lines.append(text)
    return lines


def _pages_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError('이 PC에는 PDF 읽기 도구(pypdf)가 없습니다. PDF를 TXT로 저장해 등록하거나 관리자에게 설치를 요청하세요') from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError('암호가 걸린 PDF는 읽을 수 없습니다')
        pages = [(page.extract_text() or '').strip() for page in reader.pages]
    except ValueError:
        raise
    except Exception:
        raise ValueError('PDF를 읽지 못했습니다. 파일이 손상되었는지 확인하세요') from None
    return pages


def manual_text(filename, text=None, data_base64=None):
    """Return (text, note). Raises ValueError with an instruction the person can act on."""
    lower = filename.lower()
    if lower.endswith('.hwp'):
        raise ValueError('HWP(한글 기본 저장 형식)는 직접 읽지 못합니다. 한글에서 [다른 이름으로 저장] → HWPX 또는 TXT로 저장한 뒤 등록하세요')
    if lower.endswith(TEXT_SUFFIXES):
        if not isinstance(text, str):
            raise ValueError('텍스트 파일 내용이 없습니다')
        return text, '텍스트 그대로 등록'
    if not lower.endswith(BINARY_SUFFIXES):
        raise ValueError('지원 형식: TXT·MD·HWPX·PDF (HWP는 HWPX로 저장 후 등록)')
    try:
        data = base64.b64decode(data_base64 or '', validate=True)
    except (ValueError, TypeError):
        raise ValueError('파일 전송 형식 오류') from None
    if not data or len(data) > MAX_BINARY:
        raise ValueError('HWPX·PDF는 8MB 이하만 등록할 수 있습니다')
    if lower.endswith('.hwpx'):
        lines = _paragraphs_hwpx(data)
        note = f'HWPX 문단 {len(lines)}개 추출'
        result = '\n'.join(lines)
    else:
        pages = _pages_pdf(data)
        readable = [p for p in pages if p]
        if not readable:
            raise ValueError('글자를 읽을 수 없는 PDF입니다(스캔 이미지일 가능성). OCR은 지원하지 않습니다')
        note = f'PDF {len(pages)}쪽 중 {len(readable)}쪽에서 글자 추출 · 표·도형은 원문과 대조하세요'
        result = '\n'.join(f'[{i}쪽] ' + line for i, page in enumerate(pages, 1) for line in page.splitlines() if line.strip())
    if not result.strip():
        raise ValueError('문서에서 글자를 찾지 못했습니다')
    return result, note
