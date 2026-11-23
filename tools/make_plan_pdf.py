"""Build the 2-page A4 plan PDF (Korean) for the .EXE-THON submission.

Uses only reportlab. Fonts: a Korean-capable TTF/TTC is looked up in order (Windows 맑은 고딕,
Noto Sans CJK KR, WenQuanYi Zen Hei) so the command works on the submitter's PC and on Linux.

    python -m tools.make_plan_pdf [output.pdf]
"""
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
FONT_CANDIDATES = [
    ('C:/Windows/Fonts/malgun.ttf', 0),            # 맑은 고딕 (Windows)
    ('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', 0),
    ('/usr/share/fonts/pickle-hwp/NotoSansCJKkr-Regular.otf', 0),  # CFF: reportlab cannot embed it
    ('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 0),
]
INK = colors.HexColor('#12233B')
MUTED = colors.HexColor('#5B6B7C')
ACCENT = colors.HexColor('#1F5FBF')
RULE = colors.HexColor('#C9D6E6')
HEAD = colors.HexColor('#EAF1FA')
SOFT = colors.HexColor('#F7FAFD')


def register_font():
    for path, index in FONT_CANDIDATES:
        if not Path(path).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont('KR', path, subfontIndex=index))
            return path
        except Exception:
            continue
    raise SystemExit('한글 글꼴을 찾지 못했습니다. 맑은 고딕 또는 Noto Sans CJK KR을 설치하세요.')


def styles():
    base = ParagraphStyle('base', fontName='KR', fontSize=8.5, leading=12.7, textColor=INK, alignment=TA_LEFT)
    return {
        'title': ParagraphStyle('title', parent=base, fontSize=25, leading=29, textColor=ACCENT, spaceAfter=3),
        'tagline': ParagraphStyle('tagline', parent=base, fontSize=12.5, leading=17.5, textColor=INK, spaceAfter=2),
        'meta': ParagraphStyle('meta', parent=base, fontSize=8.2, leading=12, textColor=MUTED),
        'h1': ParagraphStyle('h1', parent=base, fontSize=12.4, leading=15.6, textColor=ACCENT, spaceBefore=7, spaceAfter=3),
        'body': ParagraphStyle('body', parent=base),
        'cell': ParagraphStyle('cell', parent=base, fontSize=8.1, leading=11.8),
        'cellhead': ParagraphStyle('cellhead', parent=base, fontSize=8.1, leading=11.8, textColor=colors.HexColor('#1B3A63')),
        'small': ParagraphStyle('small', parent=base, fontSize=7.6, leading=10.8, textColor=MUTED),
        'note': ParagraphStyle('note', parent=base, fontSize=8.1, leading=12.2, textColor=MUTED, backColor=SOFT,
                               borderPadding=(5, 6, 5, 6), spaceBefore=3, spaceAfter=2),
    }


S = styles()


def P(text, style='body'):
    return Paragraph(text, S[style])


def table(rows, widths, head=True, small=False):
    style = ParagraphStyle('tc', parent=S['cell'], fontSize=8.0 if small else 8.3, leading=11.6 if small else 12.2)
    style_h = ParagraphStyle('th', parent=style, textColor=colors.HexColor('#1B3A63'))
    data = [[Paragraph(str(cell), style_h if (head and r == 0) else style) for cell in row]
            for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    commands = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 3.6), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.6),
                ('LINEBELOW', (0, 0), (-1, -1), 0.35, RULE)]
    if head:
        commands += [('BACKGROUND', (0, 0), (-1, 0), HEAD), ('LINEABOVE', (0, 0), (-1, 0), 0.7, RULE)]
    t.setStyle(TableStyle(commands))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont('KR', 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9.5 * mm, 'AlphaDrex  |  .EXE-THON 2026  |  현장 업무 지원 AI 에이전트')
    canvas.drawRightString(A4[0] - 18 * mm, 9.5 * mm, f'{doc.page} / 2')
    canvas.restoreState()


def build(output):
    register_font()
    doc = BaseDocTemplate(str(output), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=14 * mm, bottomMargin=18 * mm,
                          title='AlphaDrex .EXE-THON 2026 기획서 (보완)', author='팀 AlphaDrex',
                          subject='매뉴얼 근거 기반 현장 업무 지원 에이전트')
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='body',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame], onPage=footer)])
    W = doc.width
    story = []

    # ---------------------------------------------------------------- page 1
    story += [P('AlphaDrex', 'title'),
              P('매뉴얼의 근거를 보고서와 담당자 인계로 연결하는<br/>현장 업무 지원 AI 에이전트', 'tagline'),
              P('라이너 .EXE-THON 2026 · 팀 AlphaDrex · 시제품 v0.2.0 · 예선 보완판 2026-11-23', 'meta'),
              Spacer(1, 4)]
    story.append(P(
        '<font color="#1F5FBF">한 줄 요약</font> — 상황 한 줄이 <b>매뉴얼 근거가 붙은 판단·초안</b>이 되고, 그 답변이 문서 버전 → 담당자 검토 → '
        '에이전트 수신함 → 접수·회신까지 한 화면에서 이어집니다. 근거 없는 초안은 만들지 않고, 긴급은 AI보다 기관 절차가 먼저입니다.', 'note'))
    story.append(Spacer(1, 2))

    story.append(P('01  누가, 어떤 문제로, 얼마나 불편한가', 'h1'))
    story.append(P(
        '3교대 시설의 점검·당직 담당자는 현장에서 본 상황에 맞는 매뉴얼을 찾아 확인하고 그 결과를 다음 교대로 넘깁니다. '
        '매뉴얼은 부서 파일·PDF로 흩어져 있고 인계는 메신저·구두로 이뤄집니다. '
        '<font color="#5B6B7C">(규모는 가상 시나리오 가정이고, 실측값은 04에 구분해 적었습니다.)</font>'))
    story.append(Spacer(1, 3))
    story.append(table([
        ['불편한 지점', '지금 어떻게 되는가', '결과'],
        ['근거 확인', '상황에 맞는 매뉴얼 행을 사람이 찾고, 어느 문서가 최신인지 따로 확인한다',
         '찾는 데 걸리는 시간 + 최신판 오판 위험'],
        ['인계 내용', '발생 시각·확인한 사실·실제 조치·미확인 사항의 구분 없이 메신저·구두로 전달된다',
         '빠진 항목을 다음 교대가 처음부터 다시 확인'],
        ['보고 초안', '매번 처음부터 쓰고, 근거 없이 단정하거나 추정을 사실처럼 적는다',
         '검토에서 반려·수정 반복'],
    ], [22 * mm, 74 * mm, W - 96 * mm]))
    story.append(Spacer(1, 4))
    story.append(P(
        '<font color="#1F5FBF">규모(가정)</font> — 시설 1곳, 인계 담당 2명, 교대당 1건이면 월 60건 내외. 매뉴얼이 요구하는 6개 항목(발생 시각·장소·확인 사실·실시 조치·미확인 사항·후속 담당자) 확인과 '
        '초안·검토 요청·전달 확인에 10~15분, 한 항목이 빠져 되돌아오면 재확인 1~2회가 더 듭니다. 월 10~15시간이 여기에 쓰입니다.', 'note'))

    story.append(P('02  에이전트의 해결 방식', 'h1'))
    story.append(table([
        ['단계', '시제품의 동작', '보장하는 것'],
        ['상황 입력', '구역·식별자·눈으로 본 상태를 입력하면 키워드 미리보기를 즉시 보여 준다',
         'AI를 기다리는 동안에도 화면이 멈추지 않음'],
        ['판단', '등록 매뉴얼의 승인된 행만 후보로 보내 분류·정보 부족·검토 필요·검색 필요·긴급 여부와 <b>인용 행</b>을 받는다',
         '근거 없는 답을 만들지 않음. 근거가 없으면 "근거 부족"'],
        ['초안', '선택된 근거 행만으로 확인 목록과 보고 초안을 쓴다. "보고서에는 반드시…" 행은 자동 포함',
         '문서명·행 번호 인용 유지, 추정과 사실 분리'],
        ['문서화·검토', '답변을 확인한 사실 / 실제 수행한 조치 / 미확인 사항으로 나눠 문서 버전을 만들고 SHA-256으로 고정한다',
         '수정본이 이전 버전을 덮어쓰지 않음. 같은 지문에 대한 검토 없이는 전달 불가'],
        ['전달·회신', '수신 에이전트를 지정해 미리보기·확인 후 수신함에 등록하고, 접수(claim)와 회신(reply)을 기록한다',
         '전송 예약을 먼저 커밋해 중복 발송 차단, 회신은 "담당자 검토 전"으로 별도 보관'],
    ], [20 * mm, 88 * mm, W - 108 * mm], small=True))
    story.append(Spacer(1, 3))
    story.append(P(
        '<font color="#1F5FBF">설계 원칙</font> — 확인되지 않은 사실을 만들지 않는다 · 근거를 대지 못하는 초안을 만들지 않는다 · '
        '초안이 없는 이유(근거 부족·충돌·긴급·정보 부족·동의 전)를 기록에 남긴다 · 긴급은 AI보다 기관 절차가 먼저다.', 'note'))

    story.append(P('03  Liner API 활용 방식', 'h1'))
    story.append(P(
        '모든 호출은 <font color="#1F5FBF">providers.request_json()</font> 하나를 통과합니다: https·platform.liner.com 외 주소 거부, 내부망·전송 미허용 차단, 리디렉션 차단, '
        '2MB 상한, 오류에 URL·키·본문 미포함. 키는 env.local·환경 변수에서만 읽고 코드·ZIP·로그에 남기지 않습니다.'))
    story.append(table([
        ['기능', '호출', '보내는 것 / 조건'],
        ['판단', '<font color="#1F5FBF">POST /api/v1/chat/completions</font><br/>(모델 liner-gpt-router)',
         '상황 + 업무 분류 + 승인·외부승인 매뉴얼 행(최대 80행). 응답에서 분류·정보부족·검토·검색·긴급과 <b>근거 행 id</b>를 받는다. '
         'Liner가 주지 않은 행은 인용으로 승격하지 않는다.'],
        ['초안', '<font color="#1F5FBF">POST /api/v1/chat/completions</font>',
         '상황 + 선택된 근거 행. 근거가 있고 충돌·긴급·위험 단어·정보 부족이 없고, 사용자가 공개·가상 입력과 외부 전송에 동의한 경우에만 호출한다.'],
        ['검색', '<font color="#1F5FBF">POST /api/v1/tools/search/web</font>',
         '답변에 검색 확인이 뜨고 <b>사용자가 검색어를 확인한 뒤에만</b> 호출한다. 결과는 "원문 미검증"으로 표시한다.'],
    ], [16 * mm, 52 * mm, W - 68 * mm], small=True))

    # ---------------------------------------------------------------- page 2
    story.append(PageBreak())
    story.append(P('04  실제 가동 시연과 검증 결과', 'h1'))
    story.append(table([
        ['항목', '2026-11-18 단일 사례 실측', '2026-11-23 보완 검증'],
        ['Liner 호출', '판단 1회 · 초안 1회 실제 호출 성공 (검색 0회 — 사용자가 검색어를 확인하지 않은 흐름)', '모의 판단·초안으로 동일 경로 재현'],
        ['모델·근거', '요청 liner-gpt-router / 응답 gpt-6-sol / 인용 근거 1행', '충돌 상황에서 양쪽 문서 근거 동시 표시 확인'],
        ['처리 시간', '8.295초 (단일 사례의 측정값, 평균 성능 아님)', '키 없이 10건 검사 0.7초 (모델 호출 제외)'],
        ['키 없이 도는 검사', '전송 처리 검사 3건', 'HTTP 종단·모의 Liner 포함 <b>10건 전부 통과</b>'],
        ['차단 동작', '—', '개인정보가 든 상황은 전송 계층에 도달하지 않음(테스트로 고정), 충돌·긴급 시 초안 미생성'],
        ['영상', '39.28초 / 1440×900 / H.264 / 무음, 전체 디코딩 확인', '기존 영상 유지'],
    ], [24 * mm, 74 * mm, W - 98 * mm], small=True))
    story.append(Spacer(1, 3))
    story.append(P(
        '로컬 Drex(9B, CPU)는 이 PC에서 응답이 수십 초 걸려 시간 예산을 넘기는 경우가 있어 제출 영상은 실제 Liner 성공 기록에 연결했고, '
        'Drex·하이브리드 비교는 별도 검증 과제로 README에 한계로 적었습니다.'))

    story.append(P('05  다른 조직·직무로 확장 가능성', 'h1'))
    story.append(table([
        ['확장 단위', '내용', '예'],
        ['업무 팩(코드 수정 없음)', '분류 기준·키워드·추가 질문·보고 서식·매뉴얼을 JSON으로 교체', '교대 인계, 행사 준비, 물품 대여·반납, 당직 상황 보고'],
        ['문서 승인 체계', '문서별 승인·외부 전송 승인 플래그와 개정판(superseded) 관리', '부서 지침 개정 시 이전 판단 근거는 보존, 검색에서는 제외'],
        ['조직 간 적용', '같은 구조로 인계·근거·검토 기록을 남기는 현장 업무 전반', '병원 병동 인계, 호텔·컨벤션 시설 점검, 물류센터 안전 점검, 공공시설 현장 점검'],
        ['적용 전 필요한 검증', '전문가의 문장별 근거 검토, 개인정보·보안 검토, 다중 사용자 동시성, 효과 측정', '단일 PC SQLite → 기관 서버, SSO·전자결재 연동'],
    ], [30 * mm, 74 * mm, W - 104 * mm], small=True))

    story.append(P('06  기존에 만든 부분 / 이번에 새로 발전시킨 부분', 'h1'))
    story.append(table([
        ['구분', '범위'],
        ['기존 (11/12 이전)', '자체 코드 없음 — 개발 기록상 빈 폴더에서 시작했습니다. 기존 결과물을 가져다 발전시킨 경우는 없습니다.'],
        ['이번에 새로 만든 부분 (11/12~19)', '웹앱, Liner 연동(판단·초안·검색), 근거 처리와 문서 충돌 표시, 문서화·검토·수신함, 계정·권한·감사 기록·백업, 가상 매뉴얼과 시험'],
        ['이번 보완 (11/23)', 'API 키 없이 도는 HTTP 종단 검사 7건 추가(모의 Liner 경로 포함, 총 10건 통과), 초안이 없는 이유를 상황별로 정확히 표시, README를 실행·환경 변수·Liner 사용 지점·기존/신규 구분 중심으로 재작성'],
        ['외부 구성 요소', 'Liner Platform API, Python·SQLite·브라우저, 선택 pypdf. 로컬 판단을 켤 때만 Nace.AI Drex v1.5와 Nace llama.cpp fork 사용(모델 가중치는 저장소에 포함하지 않음)'],
    ], [34 * mm, W - 34 * mm], small=True))
    story.append(Spacer(1, 3))
    story.append(P(
        'AI 코딩 도구를 활용했습니다. 시연에는 가상 자료를 사용하고, 공개 재구성 자료의 출처는 매뉴얼에 기재했습니다. '
        'Nace.AI와 독립적으로 개발한 응용 프로그램이며, 긴급 상황은 언제나 기관의 긴급 절차와 현장 지휘 체계를 우선합니다.'))

    story.append(P('실행 방법', 'h1'))
    story.append(P(
        '소스 ZIP을 풀고 Python 3.11 이상에서 <font color="#1F5FBF">start.bat</font> 실행 → 브라우저에서 http://127.0.0.1:8765 → '
        'env.local에 LINER_API_KEY와 ALPHADREX_BRAIN=liner 설정 후 서버 재시작 → 설정에서 외부망·전송 허용, 질문마다 공개·가상 입력 확인. '
        '검사는 <font color="#1F5FBF">python -m unittest discover -s tests</font> (키 없이 10건 통과). 자세한 절차와 환경 변수 표는 README에 있습니다.'))
    story.append(KeepTogether([P('소스 코드는 제출 ZIP에 전체가 들어 있고, GitHub 저장소 링크는 제출 폼에 기재합니다.', 'small'),
        Spacer(1, 2), table([
        ['제출 파일', '내용'],
        ['AlphaDrex-source.zip', '전체 소스(키·업무 DB·모델 가중치 제외)와 README, 키 없이 도는 검사 10건'],
        ['AlphaDrex-Liner-live-demo.mp4', '39.28초 · 실제 Liner 판단 1회·초안 1회 호출과 근거 확인·문서 저장·수신함 회신 장면'],
        ['AlphaDrex-기획서 (이 문서)', 'A4 2쪽 · 문제 규모, 해결 방식, Liner 활용 지점, 확장성, 기존/신규 구분'],
    ], [52 * mm, W - 52 * mm], small=True)]))

    doc.build(story)
    return output


if __name__ == '__main__':
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'AlphaDrex-기획서-2페이지.pdf'
    print(build(target))
