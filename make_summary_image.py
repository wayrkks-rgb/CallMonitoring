# -*- coding: utf-8 -*-
"""
make_summary_image.py — 구축 내용 / 개선 효과 한 장 요약 도해(SVG) 생성

AI 이미지 생성기는 한글을 정확히 그리지 못한다(자모가 깨지거나 없는 글자가
나온다). 보고자료는 문구가 틀리면 안 되므로, 좌표를 직접 계산해서 그린다.
표준 라이브러리만 사용하므로 폐쇄망에서도 그대로 돌아간다.

도해는 두 층으로 되어 있다.
    위  ─ ARS 운영 업무 흐름 위에 '어디를 어떻게 개선했는지' 를 얹은 그림
    아래 ─ 개선 과제별 현황 → 개선 → 효과

실행하면 두 개가 같이 나온다.
    summary.svg        문구까지 채워진 완성본
    summary_blank.svg  틀 + 아이콘만 있는 빈 버전 (문구는 PPT 에서 직접 입력)

사용법:
    python make_summary_image.py
    python make_summary_image.py --guides        # 빈 버전에 글자 자리 표시
    python make_summary_image.py --png           # cairosvg 있으면 PNG 도

PPT 에 넣는 방법:
    삽입 > 그림 > svg 파일 선택
    → 그림 우클릭 > "그래픽을 도형으로 변환"
      (이러면 PPT 안에서 글자/색/위치를 직접 고칠 수 있다)

문구를 바꿀 곳:
    아래 TITLE / FLOW / IMPROVEMENTS / BASE_BAND / CARDS / FOOTER 만 고치면 된다.
    리스트의 원소 하나 = 한 줄이다.
"""
import argparse
import html
import os
import sys

# ─────────────────────────────────────────────────────────────────────────
# 1. 문구 (여기만 고치면 된다)
# ─────────────────────────────────────────────────────────────────────────

TITLE = "ARS 운영 장애 예방 체계 구축"
SUBTITLE = "배포 전 변경 비교 · 통화 중단 지점 제시 · 업무 구조 E2E 조회"
META = "ARS 7대 · AICC/VGW 3대 · 운영 시나리오 전체    |    운영 서버 변경 없음"

PANEL1_LABEL = "개선 적용 지점"
PANEL1_CAPTION = "ARS 운영 업무 흐름에서 어느 단계를 어떻게 개선했는지"
PANEL2_LABEL = "개선 효과"
PANEL2_CAPTION = "과제별 현황 대비 달라진 점과 그 효과"

# 윗줄 : ARS 운영 업무 흐름 (왼쪽 → 오른쪽)
#   icon : edit / deploy / call / alert
FLOW = [
    {"label": "시나리오 변경", "icon": "edit"},
    {"label": "운영 배포", "icon": "deploy"},
    {"label": "고객 통화", "icon": "call"},
    {"label": "장애 · 문의 대응", "icon": "alert"},
]

# 아랫줄 : 그 단계에 붙은 개선 내용
#   span  : 위 FLOW 의 몇 번째부터 몇 번째 단계에 걸치는지 (0부터)
#   accent: True 면 강조(파랑). 번호 붙은 개선 과제
#   icon  : diff / collect / breakpoint / tree ...
IMPROVEMENTS = [
    {
        "span": (0, 1), "accent": True, "icon": "diff",
        "title": "① 배포 전 변경 내용 자동 비교",
        "desc": "블록 · 변수 · 메뉴 버튼 단위 비교 + 변경된 업무 위치까지 표시",
        "tag": "60분 → 10분",
    },
    {
        "span": (2, 2), "accent": False, "icon": "collect",
        "title": "로그 · 통화 이력 상시 수집",
        "desc": "ARS 7대 · AICC 3대 · 30일 보관",
        "tag": "",
    },
    {
        "span": (3, 3), "accent": True, "icon": "breakpoint",
        "title": "② 중단 지점 자동 제시",
        "desc": "마지막 진행 지점 · 종료 사유",
        "tag": "30분 → 5분",
    },
]

# 맨 아래 띠 : ①②에 공통으로 쓰이는 기반
BASE_BAND = {
    "icon": "tree",
    "title": "③ 업무 구조 E2E",
    "desc": "블록 번호를 업무 위치로 환산 · 전체 트리와 업무 요약을 PDF 일괄 제공"
            " — ① · ② 의 공통 기반",
    "note": "60분 → 5분",
}

# 아래 패널 : 개선 효과 (현황 → 개선 → 효과)
#   icon : diff / breakpoint / tree
CARDS = [
    {
        "no": "①",
        "icon": "diff",
        "title": ["배포 전 시나리오", "변경 내용 자동 비교"],
        "before": ["변경 항목을 배포자 기억에 의존해", "확인, 영향 범위 사전 파악 불가"],
        "after": ["블록 · 변수 · 메뉴 버튼 단위로", "변경 내용과 전문을 배포 전 제시"],
        "effect": "잘못된 배포로 인한 장애 예방",
    },
    {
        "no": "②",
        "icon": "breakpoint",
        "title": ["통화 중단 지점 제시", "및 로그 통합 조회"],
        "before": ["서버별 개별 접속 후 수동 검색,", "중단 지점은 로그를 직접 해석"],
        "after": ["10대 로그 동시 검색 + 통화별", "마지막 진행 지점·종료 사유 표시"],
        "effect": "장애 지점 확인 시간 단축",
    },
    {
        "no": "③",
        "icon": "tree",
        "title": ["업무 구조 E2E", "화면 제공"],
        "before": ["시나리오 디자이너로 블록 단위만", "열람, 업무 간 연관성 확인 불가"],
        "after": ["전체 트리 구조와 업무 요약을", "한 번에 조회 · PDF 일괄 제공"],
        "effect": "업무 정보의 담당자 의존도 완화",
    },
]

FOOTER_L = "※ 수집은 조회 전용으로 동작하며 운영 서버에 설치·설정 변경 사항 없음"
FOOTER_R = "향후 : 채널 상태 기반 자동 알림 · 배포 승인 절차와 변경 검증 연계"


# ─────────────────────────────────────────────────────────────────────────
#  기대효과 장표 (--page effect)
# ─────────────────────────────────────────────────────────────────────────

E_TITLE = "기대효과"
E_SUBTITLE = "ARS 운영 장애 예방 체계 구축"
E_META = "ARS 7대 · AICC/VGW 3대 · 운영 시나리오 전체"

QUANT_LABEL = "정량 효과"
QUANT_CAPTION = "동일 업무 기준 구축 전후 소요 시간 및 적용 실적"
QUAL_LABEL = "정성 효과"
QUAL_CAPTION = "수치로 드러나지 않는 운영 방식의 변화"

QUANT = [
    {
        "no": "①", "title": "장애 로그 분석 시간",
        "before": "30분", "after": "5분", "gain": "83% 단축",
        "note": "※ 서버 접속 · 로그 검색 · 중단 지점 확인 기준",
    },
    {
        "no": "②", "title": "배포 전 변경 비교 시간",
        "before": "60분", "after": "10분", "gain": "83% 단축",
        "note": "※ 변경 블록 40개 · 신규 시나리오 2개 추가 기준",
    },
    {
        "no": "③", "title": "배포 후 오류 재배포 건수",
        "before": "4건", "after": "0건", "gain": "100% 감소",
        "note": "※ 사전 검증 적용 전(7 · 8월 SR 6건) 대비 적용 후(9월 SR 5건)",
    },
]

QUAL = [
    {
        "icon": "shield", "title": "대응 방식 전환",
        "desc": ["담당자 경험에 의존한 판단에서",
                 "화면 기반의 동일한 확인 절차로 전환"],
        "chip": "사람 중심 → 시스템 중심",
    },
    {
        "icon": "pulse", "title": "장애 자동 탐지 기반 확보",
        "desc": ["통화별 중단 지점 · 종료 사유가",
                 "구조화 축적되어 탐지 규칙 적용 가능"],
        "chip": "중단 시점 요약 → 이상 징후 자동 탐지",
    },
    {
        "icon": "share", "title": "프로젝트 업무까지 활용 확장",
        "desc": ["신계약 프로젝트 인력이 로그 조회 · 시나리오",
                 "비교 · E2E 화면을 운영 소스 분석에 활용"],
        "note": "※ 신계약 모니터링 프로젝트 인력 2명 제공 ('26.09~'26.12)",
        "chip": "운영 전용 → 프로젝트 공동 활용",
    },
]

E_FOOTER_L = "※ 소요 시간은 동일 업무 기준의 구축 전후 비교값"
E_FOOTER_R = "향후 : 채널 상태 기반 자동 알림 · 배포 승인 절차와 변경 검증 연계"


# ─────────────────────────────────────────────────────────────────────────
#  향후 계획 장표 (--page roadmap)
# ─────────────────────────────────────────────────────────────────────────

R_TITLE = "향후 계획"
R_SUBTITLE = "ARS 운영 장애 예방 체계 단계별 고도화"
R_META = "담당자 직접 확인 → 시스템 화면 제공 → 시스템 자동 통보 → 시스템 사전 예측"

ROADMAP_LABEL = "단계별 고도화 계획"

#   state : done(지난 단계) / now(현재) / next(예정)
ROADMAP = [
    {
        "no": "1단계", "when": "과거", "state": "done", "icon": "manual",
        "title": "수동 확인",
        "desc": ["서버 개별 접속과 담당자", "경험에 의존한 확인"],
        "items": ["서버별 개별 접속 후 수동 검색",
                  "변경 내용 확인 절차 부재",
                  "업무 구조는 담당자 기억에 의존"],
        "key": "담당자 직접 확인",
    },
    {
        "no": "2단계", "when": "현재", "state": "now", "icon": "monitor",
        "title": "통합 조회",
        "desc": ["단일 화면에서 조회 · 비교 ·", "업무 구조 확인"],
        "items": ["배포 전 변경 내용 자동 비교",
                  "통화 중단 지점 자동 제시",
                  "업무 구조 E2E 조회 · PDF 제공"],
        "key": "시스템 화면 제공",
    },
    {
        "no": "3단계", "when": "중장기", "state": "next", "icon": "bell",
        "title": "자동 점검 및 알림",
        "desc": ["주기 점검 중 이상 발견 시", "담당자에게 자동 통보"],
        "items": ["채널 · 오류 상태 주기 자동 점검",
                  "평시 대비 이상 판단 규칙 적용",
                  "탐지 결과 자동 통보"],
        "key": "시스템 자동 통보",
    },
    {
        "no": "4단계", "when": "장기", "state": "next", "icon": "spark",
        "title": "예측 및 조치 연계",
        "desc": ["이상 징후 사전 감지 후", "조치까지 연결"],
        "items": ["누적 이력 기반 이상 패턴 예측",
                  "배포 위험도 사전 평가",
                  "승인 기반 조치 수행"],
        "key": "시스템 사전 예측",
    },
]

R_NOTE = ("3단계는 채널 상태 · 통화 종료 사유가 이미 수집 · 축적 중으로, "
          "판단 및 알림 계층 추가만으로 구현 가능")
R_FOOTER_L = "※ 자동 조치는 담당자 승인 후 수행 전제"
R_FOOTER_R = "2단계 완료 · 3단계 이후 단계적 적용"

# ─────────────────────────────────────────────────────────────────────────
# 2. 레이아웃 / 색
# ─────────────────────────────────────────────────────────────────────────

W, H = 1600, 900                      # 16:9 (PPT 기본 비율)
M = 48                                # 바깥 여백

FONT = ("'Malgun Gothic','맑은 고딕','Apple SD Gothic Neo',"
        "'Noto Sans KR','Noto Sans CJK KR','NanumGothic','Nanum Gothic',"
        "sans-serif")

C = {
    "bg":         "#F8FAFC",
    "panel":      "#FFFFFF",
    "line":       "#E2E8F0",
    "ink":        "#0F172A",
    "sub":        "#475569",
    "mute":       "#7A8699",
    "slate":      "#475569",
    "slate_soft": "#F1F5F9",
    "blue":       "#1D4ED8",
    "blue_soft":  "#EFF6FF",
    "blue_line":  "#BFDBFE",
    "amber":      "#B45309",
    "amber_soft": "#FFFBEB",
    "amber_line": "#FDE68A",
    "green":      "#047857",
    "green_soft": "#ECFDF5",
    "green_line": "#A7F3D0",
    "ghost":      "#EDF2F7",          # 빈 버전의 글자 자리 표시
}

# 기대효과 · 향후계획 장표 전용 색.
# 구축 요약 장표와 같은 색을 쓰면 두 장이 한 장처럼 보여서, 짙은 남색 바탕에
# 청록 강조로 따로 잡는다.
N = {
    "page":       "#FFFFFF",
    "navy":       "#0F2D4A",
    "navy_dk":    "#0A2035",
    "navy_soft":  "#1D4269",          # 남색 위의 구분선
    "on_navy":    "#FFFFFF",
    "on_navy_sub": "#93AABF",
    "teal":       "#2DD4BF",          # 짙은 바탕 위 강조
    "teal_dk":    "#0D8B7B",          # 흰 바탕 위 강조
    "teal_soft":  "#ECFDF8",
    "teal_line":  "#9DE8DB",
    "ink":        "#0F2D4A",
    "sub":        "#51657A",
    "mute":       "#8496A8",
    "line":       "#E3E9EF",
    "soft":       "#F2F6F9",
    "ghost":      "#E8EEF3",
    "ghost_navy": "#1D4269",
}

BLANK = False        # True 면 글자를 그리지 않는다
GUIDES = False       # True 면 빈 버전에 글자 자리를 연한 막대로 표시


# ─────────────────────────────────────────────────────────────────────────
# 3. 그리기 도구
# ─────────────────────────────────────────────────────────────────────────

def esc(s):
    return html.escape(str(s))


def est_w(s, size):
    """글자 폭 어림값.

    숫자와 % 를 다른 글자와 같은 폭으로 잡으면 '100%' 같은 큰 수치에서
    폭이 모자라 화살표와 겹친다. 글자 종류별로 나눠서 본다.
    """
    w = 0.0
    for ch in s:
        if ord(ch) > 0x2000:            # 한글·전각 기호
            k = 1.0
        elif ch == "%":
            k = 0.90
        elif ch.isdigit():
            k = 0.58
        elif ch.isupper():
            k = 0.64
        else:
            k = 0.52
        w += size * k
    return w


def T(x, y, s, size=15, fill=None, weight="400", anchor="start",
      spacing=None, keep=False, ghost=None):
    """텍스트 한 줄. 빈 버전에서는 생략(또는 자리 표시)한다.

    keep=True 는 문구가 아니라 틀의 일부인 것(①②③ 같은 번호)이라
    빈 버전에서도 그대로 남긴다.
    """
    if not s:
        return ""
    if BLANK and not keep:
        if not GUIDES:
            return ""
        w, h = est_w(s, size), size * 0.74
        gx = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
        return R(gx, y - h, w, h, r=3, fill=ghost or C["ghost"])
    sp = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or C["ink"]}" '
            f'text-anchor="{anchor}"{sp}>{esc(s)}</text>')


def R(x, y, w, h, r=10, fill="none", stroke=None, sw=1):
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
            f'fill="{fill}"{st}/>')


def top_round(x, y, w, h, r, fill):
    """위쪽 두 각만 둥근 사각형."""
    return (f'<path d="M {x},{y + h} L {x},{y + r} '
            f'A {r},{r} 0 0 1 {x + r},{y} L {x + w - r},{y} '
            f'A {r},{r} 0 0 1 {x + w},{y + r} L {x + w},{y + h} Z" fill="{fill}"/>')


def pill(x, y, label, color, soft):
    """섹션 라벨 (작은 알약). 빈 버전에서는 테두리만 남는다."""
    w = 26 + int(len(label) * 15.5)
    out = [R(x, y, w, 30, r=15, fill=soft, stroke=color, sw=1),
           T(x + w / 2, y + 20, label, size=15, fill=color,
             weight="700", anchor="middle")]
    return "".join(out), w


def tag(x, y, label, color, soft, border):
    """작은 강조 꼬리표 (오른쪽 정렬로 쓴다). 오른쪽 끝 x 기준."""
    if not label:
        return ""
    w = 22 + est_w(label, 12.5)
    out = [R(x - w, y, w, 24, r=12, fill=soft, stroke=border, sw=1),
           T(x - w / 2, y + 16, label, size=12.5, fill=color,
             weight="700", anchor="middle")]
    return "".join(out)


def arrow_right(cx, cy, color):
    return (f'<path d="M {cx - 13},{cy} L {cx + 5},{cy}" stroke="{color}" '
            f'stroke-width="2.2" stroke-linecap="round"/>'
            f'<path d="M {cx + 3},{cy - 6} L {cx + 13},{cy} L {cx + 3},{cy + 6} Z" '
            f'fill="{color}"/>')


def chevron_down(cx, y, color):
    return (f'<path d="M {cx},{y} L {cx},{y + 10}" stroke="{color}" '
            f'stroke-width="2.2" stroke-linecap="round"/>'
            f'<path d="M {cx - 7},{y + 8} L {cx},{y + 17} L {cx + 7},{y + 8} Z" '
            f'fill="{color}"/>')


def big_arrow(cx, cy, color):
    """정량 카드의 현황 → 개선 화살표 (굵게)."""
    return (f'<path d="M {cx - 19},{cy} L {cx + 3},{cy}" stroke="{color}" '
            f'stroke-width="3" stroke-linecap="round"/>'
            f'<path d="M {cx},{cy - 9} L {cx + 16},{cy} L {cx},{cy + 9} Z" '
            f'fill="{color}"/>')


def chip_left(x, y, label, color, soft, border, size=13.5, minw=150):
    """왼쪽 기준의 작은 칩. 빈 버전에서는 테두리만 남는다."""
    w = max(minw, 28 + est_w(label, size))
    return "".join([R(x, y, w, 32, r=16, fill=soft, stroke=border, sw=1),
                    T(x + 14, y + 21, label, size=size, fill=color,
                      weight="700")])


def drop(cx, y1, y2, color):
    """업무 흐름 단계 → 개선 내용 을 잇는 점선."""
    return (f'<path d="M {cx},{y1} L {cx},{y2}" stroke="{color}" '
            f'stroke-width="1.6" stroke-dasharray="3 4"/>')


# ─────────────────────────────────────────────────────────────────────────
# 4. 아이콘 (24x24 기준의 선 아이콘. 색·크기는 호출할 때 정한다)
# ─────────────────────────────────────────────────────────────────────────

ICONS = {
    # ── 업무 흐름 ──────────────────────────────────────────────
    # 연필 — 시나리오 변경
    "edit": ('<path d="M3.5 20.5h17"/>'
             '<path d="M15.1 3.4l4.5 4.5L9.3 18.2l-5.2.7.7-5.2z"/>'
             '<path d="M13.3 5.2l4.5 4.5"/>'),
    # 위로 올림 — 운영 배포
    "deploy": ('<rect x="3" y="2.6" width="18" height="3.4" rx="1.4"/>'
               '<path d="M12 21V9"/><path d="M7.2 13.8L12 9l4.8 4.8"/>'),
    # 헤드셋 — 고객 통화
    "call": ('<path d="M4 14.5v-2.2a8 8 0 0 1 16 0v2.2"/>'
             '<rect x="2.2" y="13.6" width="4.6" height="7.2" rx="2.3"/>'
             '<rect x="17.2" y="13.6" width="4.6" height="7.2" rx="2.3"/>'),
    # 경고 — 장애 · 문의 대응
    "alert": ('<path d="M12 3.4L2.2 20.6h19.6z"/>'
              '<path d="M12 9.8v4.6"/><path d="M12 17.6h0.01" stroke-width="2.6"/>'),

    # ── 개선 내용 ──────────────────────────────────────────────
    # 두 문서 비교(+/-) — 배포 전 변경 비교
    "diff": ('<rect x="2.5" y="3.5" width="8.5" height="17" rx="1.6"/>'
             '<rect x="13" y="3.5" width="8.5" height="17" rx="1.6"/>'
             '<path d="M4.8 9h3.9"/><path d="M15.3 9h3.9M17.25 7.05v3.9"/>'
             '<path d="M4.8 13.5h3.9M4.8 16.5h2.4"/>'
             '<path d="M15.3 13.5h3.9M15.3 16.5h2.4"/>'),
    # 아래로 받아 담기 — 상시 수집
    "collect": ('<path d="M12 3v10"/><path d="M8 9.5l4 4 4-4"/>'
                '<path d="M4 17v2.5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V17"/>'),
    # 흐름 위의 지점 표시 — 통화 중단 지점
    "breakpoint": ('<path d="M12 21.5s6.3-5.7 6.3-10.4A6.3 6.3 0 0 0 5.7 11.1'
                   'c0 4.7 6.3 10.4 6.3 10.4z"/>'
                   '<circle cx="12" cy="10.9" r="2.4"/>'
                   '<path d="M2.5 6.5h2.2M2.5 10.5h1.4M19.3 6.5h2.2"/>'),
    # 전체 구조 — 업무 구조 E2E
    "tree": ('<rect x="8.5" y="2.5" width="7" height="5.5" rx="1.4"/>'
             '<rect x="1.5" y="16" width="7" height="5.5" rx="1.4"/>'
             '<rect x="15.5" y="16" width="7" height="5.5" rx="1.4"/>'
             '<path d="M12 8v3.8M5 16v-4.2h14V16"/>'),

    # ── 예비 ──────────────────────────────────────────────────
    "servers": ('<rect x="3" y="3.5" width="18" height="7" rx="1.8"/>'
                '<rect x="3" y="13.5" width="18" height="7" rx="1.8"/>'
                '<path d="M6.5 7h0.01M6.5 17h0.01" stroke-width="2.4"/>'),
    "database": ('<ellipse cx="12" cy="5.5" rx="8" ry="2.8"/>'
                 '<path d="M4 5.5v13c0 1.55 3.58 2.8 8 2.8s8-1.25 8-2.8v-13"/>'
                 '<path d="M4 12c0 1.55 3.58 2.8 8 2.8s8-1.25 8-2.8"/>'),
    "monitor": ('<rect x="2.5" y="3.5" width="19" height="13.5" rx="2"/>'
                '<path d="M9 20.5h6M12 17v3.5"/><path d="M2.5 7.5h19"/>'),
    "shield": ('<path d="M12 2.5l8 3v6.2c0 4.9-3.3 8.9-8 9.8-4.7-.9-8-4.9-8-9.8V5.5z"/>'
               '<path d="M8.4 11.8l2.6 2.6 4.6-4.6"/>'),
    # 관제 화면 — 이상 징후 탐지
    "pulse": ('<rect x="2.2" y="4" width="19.6" height="16" rx="2.2"/>'
              '<path d="M5.4 12.2h3l2.1-4.2 3 8.4 2.1-4.2h3"/>'),
    # 공유 — 업무 지식 공유
    "share": ('<circle cx="18" cy="5.6" r="2.9"/><circle cx="6" cy="12" r="2.9"/>'
              '<circle cx="18" cy="18.4" r="2.9"/>'
              '<path d="M8.6 10.6l6.8-3.6M8.6 13.4l6.8 3.6"/>'),
    # 돋보기 — 사람이 직접 찾는 단계
    "manual": ('<circle cx="10.4" cy="10.4" r="7"/><path d="M15.5 15.5L21 21"/>'),
    # 종 — 자동 알림
    "bell": ('<path d="M18.2 9a6.2 6.2 0 1 0-12.4 0c0 6-2.6 7.6-2.6 7.6h17.6'
             'S18.2 15 18.2 9z"/><path d="M13.9 20.2a2.1 2.1 0 0 1-3.8 0"/>'),
    # 반짝임 — 예측 · AI
    "spark": ('<path d="M10 2.6l2 5.2 5.2 2-5.2 2-2 5.2-2-5.2-5.2-2 5.2-2z"/>'
              '<path d="M18 14.4l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 '
              '2.4-.9z"/>'),
}


def icon(name, cx, cy, size, color, sw=1.8, opacity=1.0):
    """아이콘을 (cx, cy) 를 중심으로 size 크기로 그린다."""
    body = ICONS.get(name)
    if not body:
        return ""
    s = size / 24.0
    x, y = cx - size / 2, cy - size / 2
    return (f'<g transform="translate({x:.2f},{y:.2f}) scale({s:.4f})" '
            f'fill="none" stroke="{color}" stroke-width="{sw / s:.2f}" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'opacity="{opacity}">{body}</g>')


# ─────────────────────────────────────────────────────────────────────────
# 5. 도해 조립
# ─────────────────────────────────────────────────────────────────────────

def _head(g, title, subtitle, meta):
    """장표 공통 머리글."""
    g.append(R(M, 40, 5, 44, r=2.5, fill=C["blue"]))
    g.append(T(M + 20, 68, title, size=32, weight="700", fill=C["ink"]))
    g.append(T(M + 20, 92, subtitle, size=16, fill=C["mute"]))
    g.append(T(W - M, 80, meta, size=14, fill=C["mute"], anchor="end"))


def build_overview():
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=C["bg"])]

    X0 = M + 28                       # 패널 안쪽 기준 x
    CW = W - 2 * M - 56               # 패널 안쪽 폭

    _head(g, TITLE, SUBTITLE, META)

    # ── 패널 1 : 업무 흐름 위의 개선 적용 지점 ───────────────────────
    p1y, p1h = 110, 304
    g.append(R(M, p1y, W - 2 * M, p1h, r=14, fill=C["panel"],
               stroke=C["line"], sw=1))
    chip, cw_ = pill(X0, p1y + 18, PANEL1_LABEL, C["slate"], C["slate_soft"])
    g.append(chip)
    g.append(T(X0 + cw_ + 22, p1y + 38, PANEL1_CAPTION, size=14, fill=C["mute"]))

    gap = 40
    n = len(FLOW)
    fw = (CW - gap * (n - 1)) / n
    fy, fh = 170, 52
    for i, st in enumerate(FLOW):
        x = X0 + i * (fw + gap)
        g.append(R(x, fy, fw, fh, r=10, fill=C["slate_soft"],
                   stroke=C["line"], sw=1))
        g.append(icon(st.get("icon", ""), x + 28, fy + fh / 2, 22,
                      C["slate"], sw=1.8))
        g.append(T(x + fw / 2 + 14, fy + 33, st["label"], size=17,
                   weight="700", fill=C["ink"], anchor="middle"))
        if i < n - 1:
            g.append(arrow_right(x + fw + gap / 2, fy + fh / 2, C["mute"]))

    iy, ih = 236, 104
    for im in IMPROVEMENTS:
        a, b = im["span"]
        x = X0 + a * (fw + gap)
        w = (b - a) * (fw + gap) + fw
        acc = im.get("accent")
        for k in range(a, b + 1):
            g.append(drop(X0 + k * (fw + gap) + fw / 2, fy + fh, iy,
                          C["blue_line"] if acc else C["line"]))
        g.append(R(x, iy, w, ih, r=12,
                   fill=C["blue_soft"] if acc else C["panel"],
                   stroke=C["blue_line"] if acc else C["line"],
                   sw=1.6 if acc else 1))
        g.append(R(x + 1, iy + 14, 4, ih - 28, r=2,
                   fill=C["blue"] if acc else C["mute"]))
        g.append(icon(im.get("icon", ""), x + 40, iy + 46, 28,
                      C["blue"] if acc else C["mute"], sw=1.7))
        g.append(T(x + 68, iy + 38, im["title"], size=17,
                   weight="700", fill=C["ink"] if acc else C["sub"]))
        g.append(T(x + 68, iy + 64, im["desc"], size=13.5, fill=C["sub"]))
        # 꼬리표는 제목과 겹치지 않도록 항상 아래쪽 오른편에 둔다
        if acc:
            g.append(tag(x + w - 16, iy + 72, im.get("tag", ""),
                         C["amber"], C["amber_soft"], C["amber_line"]))

    by, bh = 350, 46
    g.append(R(X0, by, CW, bh, r=10, fill=C["blue_soft"],
               stroke=C["blue_line"], sw=1.6))
    g.append(icon(BASE_BAND.get("icon", ""), X0 + 28, by + bh / 2, 22,
                  C["blue"], sw=1.7))
    g.append(T(X0 + 52, by + 29, BASE_BAND["title"], size=16.5,
               weight="700", fill=C["blue"]))
    g.append(T(X0 + 52 + est_w(BASE_BAND["title"], 16.5) + 16, by + 29,
               BASE_BAND["desc"], size=14, fill=C["sub"]))
    g.append(tag(X0 + CW - 16, by + 11, BASE_BAND.get("note", ""),
                 C["amber"], C["amber_soft"], C["amber_line"]))

    # ── 패널 2 : 개선 효과 ───────────────────────────────────────────
    p2y, p2h = 430, 426
    g.append(R(M, p2y, W - 2 * M, p2h, r=14, fill=C["panel"],
               stroke=C["line"], sw=1))
    chip, cw_ = pill(X0, p2y + 18, PANEL2_LABEL, C["blue"], C["blue_soft"])
    g.append(chip)
    g.append(T(X0 + cw_ + 22, p2y + 38, PANEL2_CAPTION, size=14, fill=C["mute"]))

    cgap = 28
    cw = (CW - cgap * 2) / 3
    cy, ch = p2y + 50, 350
    for i, cd in enumerate(CARDS):
        x = X0 + i * (cw + cgap)
        g.append(R(x, cy, cw, ch, r=12, fill=C["panel"], stroke=C["line"]))

        # 제목 + 아이콘
        g.append(f'<circle cx="{x + 38}" cy="{cy + 40}" r="19" '
                 f'fill="{C["blue"]}"/>')
        g.append(T(x + 38, cy + 47, cd["no"], size=19, weight="700",
                   fill="#FFFFFF", anchor="middle", keep=True))
        g.append(icon(cd.get("icon", ""), x + cw - 40, cy + 40, 30,
                      C["blue"], sw=1.7, opacity=0.9))
        for j, ln in enumerate(cd["title"]):
            g.append(T(x + 70, cy + 34 + j * 24, ln, size=18,
                       weight="700", fill=C["ink"]))

        # 현황
        ay, ah = cy + 84, 88
        g.append(R(x + 16, ay, cw - 32, ah, r=10, fill=C["slate_soft"],
                   stroke=C["line"]))
        g.append(T(x + 32, ay + 25, "현  황", size=12, weight="700",
                   fill=C["mute"], spacing="1"))
        for j, ln in enumerate(cd["before"]):
            g.append(T(x + 32, ay + 50 + j * 22, ln, size=14.5, fill=C["sub"]))

        g.append(chevron_down(x + cw / 2, cy + 182, C["blue"]))

        # 개선
        ty, th = cy + 206, 96
        g.append(R(x + 16, ty, cw - 32, th, r=10, fill=C["blue_soft"],
                   stroke=C["blue_line"]))
        g.append(T(x + 32, ty + 26, "개  선", size=12, weight="700",
                   fill=C["blue"], spacing="1"))
        for j, ln in enumerate(cd["after"]):
            g.append(T(x + 32, ty + 52 + j * 22, ln, size=14.5,
                       fill=C["ink"], weight="600"))

        # 효과
        ey = cy + 312
        g.append(R(x + 16, ey, cw - 32, 32, r=8, fill=C["green_soft"],
                   stroke=C["green_line"]))
        g.append(T(x + cw / 2, ey + 21, "▶  " + cd["effect"], size=14.5,
                   weight="700", fill=C["green"], anchor="middle"))

    # ── 꼬리말 ───────────────────────────────────────────────────────
    g.append(T(M, 880, FOOTER_L, size=13.5, fill=C["mute"]))
    g.append(T(W - M, 880, FOOTER_R, size=13.5, fill=C["mute"], anchor="end"))

    g.append('</svg>')
    return "".join(g)


def build_effect():
    """기대효과 장표 — 머리글만 짙은 남색, 정량은 밝은 패널, 정성은 흰 바탕."""
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=N["page"])]

    # ── 머리글 (얇은 남색 띠) ────────────────────────────────────────
    g.append(R(0, 0, W, 118, r=0, fill=N["navy"]))
    g.append(T(M, 62, E_TITLE, size=30, weight="700", fill=N["on_navy"],
               ghost=N["ghost_navy"]))
    g.append(T(M, 90, E_SUBTITLE, size=14, fill=N["on_navy_sub"],
               ghost=N["ghost_navy"]))
    g.append(T(W - M, 76, E_META, size=13, fill=N["on_navy_sub"],
               anchor="end", ghost=N["ghost_navy"]))

    # ── 정량 (밝은 패널 위의 성적표) ─────────────────────────────────
    g.append(R(M, 148, 24, 3, r=1.5, fill=N["teal_dk"]))
    g.append(T(M + 36, 156, QUANT_LABEL, size=14, weight="700",
               fill=N["teal_dk"], spacing="2"))
    g.append(T(M + 36 + est_w(QUANT_LABEL, 14) + 30, 156, QUANT_CAPTION,
               size=13.5, fill=N["mute"]))

    py, rh = 182, 104
    g.append(R(M, py, W - 2 * M, 20 + rh * len(QUANT) + 20, r=14,
               fill=N["soft"], stroke=N["line"], sw=1))

    ix = M + 28                                   # 패널 안쪽 왼쪽
    for i, q in enumerate(QUANT):
        ry = py + 20 + i * rh
        if i:
            g.append(f'<path d="M {ix},{ry} L {W - M - 28},{ry}" '
                     f'stroke="{N["line"]}" stroke-width="1"/>')

        g.append(f'<circle cx="{ix + 18}" cy="{ry + 52}" r="18" '
                 f'fill="{N["teal_soft"]}" stroke="{N["teal_line"]}" '
                 f'stroke-width="1"/>')
        g.append(T(ix + 18, ry + 58, q["no"], size=16, weight="700",
                   fill=N["teal_dk"], anchor="middle", keep=True))
        g.append(T(ix + 52, ry + 46, q["title"], size=20, weight="700",
                   fill=N["ink"]))
        g.append(T(ix + 52, ry + 70, q["note"], size=12.5, fill=N["mute"]))

        # 배지 폭은 글자에 맞춰 늘린다 ('반영 오류 0건' 처럼 긴 문구 대비)
        gw = max(132, est_w(q["gain"], 14.5) + 30)
        gx = W - M - 28 - gw
        g.append(R(gx, ry + 35, gw, 34, r=17, fill=N["teal_soft"],
                   stroke=N["teal_line"], sw=1))
        g.append(T(gx + gw / 2, ry + 58, q["gain"], size=14.5, weight="700",
                   fill=N["teal_dk"], anchor="middle"))

        ax = gx - 34
        aw = est_w(q["after"], 44)
        g.append(T(ax, ry + 66, q["after"], size=44, weight="800",
                   fill=N["teal_dk"], anchor="end"))
        g.append(big_arrow(ax - aw - 26, ry + 52, N["mute"]))
        g.append(T(ax - aw - 52, ry + 64, q["before"], size=30, weight="700",
                   fill=N["mute"], anchor="end"))

    # ── 정성 (흰 바탕, 테두리 없는 3단) ──────────────────────────────
    g.append(R(M, 556, 24, 3, r=1.5, fill=N["teal_dk"]))
    g.append(T(M + 36, 564, QUAL_LABEL, size=14, weight="700",
               fill=N["teal_dk"], spacing="2"))
    g.append(T(M + 36 + est_w(QUAL_LABEL, 14) + 30, 564, QUAL_CAPTION,
               size=13.5, fill=N["mute"]))

    gap = 40
    cw = (W - 2 * M - gap * 2) / 3
    y0 = 592
    for i, q in enumerate(QUAL):
        x = M + i * (cw + gap)
        if i:
            dx = x - gap / 2
            g.append(f'<path d="M {dx},{y0 - 8} L {dx},{y0 + 236}" '
                     f'stroke="{N["line"]}" stroke-width="1"/>')
        g.append(f'<circle cx="{x + 28}" cy="{y0 + 28}" r="26" '
                 f'fill="{N["teal_soft"]}"/>')
        g.append(icon(q.get("icon", ""), x + 28, y0 + 28, 26,
                      N["teal_dk"], sw=1.7))
        g.append(T(x, y0 + 88, q["title"], size=20, weight="700",
                   fill=N["ink"]))
        g.append(R(x, y0 + 104, 40, 3, r=1.5, fill=N["teal_dk"]))
        for j, ln in enumerate(q["desc"]):
            g.append(T(x, y0 + 138 + j * 24, ln, size=15, fill=N["sub"]))
        g.append(T(x, y0 + 188, q.get("note", ""), size=12.5, fill=N["mute"]))
        g.append(T(x, y0 + 224, q.get("chip", ""), size=15, weight="700",
                   fill=N["teal_dk"]))

    g.append(T(M, 856, E_FOOTER_L, size=13, fill=N["mute"]))
    g.append(T(W - M, 856, E_FOOTER_R, size=13, fill=N["mute"], anchor="end"))

    g.append('</svg>')
    return "".join(g)



def build_roadmap():
    """향후 계획 장표 — 4단계 타임라인."""
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=N["page"])]

    # ── 머리글 (얇은 남색 띠) ────────────────────────────────────────
    g.append(R(0, 0, W, 118, r=0, fill=N["navy"]))
    g.append(T(M, 62, R_TITLE, size=30, weight="700", fill=N["on_navy"],
               ghost=N["ghost_navy"]))
    g.append(T(M, 90, R_SUBTITLE, size=14, fill=N["on_navy_sub"],
               ghost=N["ghost_navy"]))
    g.append(T(W - M, 76, R_META, size=13, fill=N["teal"], anchor="end",
               ghost=N["ghost_navy"]))

    g.append(R(M, 156, 24, 3, r=1.5, fill=N["teal_dk"]))
    g.append(T(M + 36, 164, ROADMAP_LABEL, size=14, weight="700",
               fill=N["teal_dk"], spacing="2"))

    gap = 36
    cw = (W - 2 * M - gap * 3) / 4
    ccy, cr = 250, 36

    # 단계를 잇는 선 : 지나온 구간은 실선, 앞으로의 구간은 점선
    for i in range(len(ROADMAP) - 1):
        # '현재' 단계는 바깥 링이 있어 그만큼 띄운다
        o1 = cr + (10 if ROADMAP[i]["state"] == "now" else 0)
        o2 = cr + (10 if ROADMAP[i + 1]["state"] == "now" else 0)
        x1 = M + i * (cw + gap) + cw / 2 + o1
        x2 = M + (i + 1) * (cw + gap) + cw / 2 - o2
        future = ROADMAP[i + 1]["state"] == "next"
        dash = ' stroke-dasharray="5 6"' if future else ""
        g.append(f'<path d="M {x1},{ccy} L {x2},{ccy}" '
                 f'stroke="{N["teal_line"] if future else N["navy"]}" '
                 f'stroke-width="2"{dash}/>')

    cy, ch = 312, 400
    for i, st in enumerate(ROADMAP):
        x = M + i * (cw + gap)
        cx = x + cw / 2
        state = st["state"]
        now, future = state == "now", state == "next"

        # 단계 동그라미
        if future:
            g.append(f'<circle cx="{cx}" cy="{ccy}" r="{cr}" fill="{N["page"]}" '
                     f'stroke="{N["teal_line"]}" stroke-width="2" '
                     f'stroke-dasharray="5 6"/>')
            g.append(icon(st.get("icon", ""), cx, ccy, 30, N["teal_dk"], sw=1.7))
        else:
            g.append(f'<circle cx="{cx}" cy="{ccy}" r="{cr}" fill="{N["navy"]}"/>')
            g.append(icon(st.get("icon", ""), cx, ccy, 30, N["on_navy"], sw=1.7))
        if now:
            g.append(f'<circle cx="{cx}" cy="{ccy}" r="{cr + 7}" fill="none" '
                     f'stroke="{N["teal"]}" stroke-width="2.5"/>')

        # 단계 카드
        if now:
            g.append(R(x, cy, cw, ch, r=12, fill=N["page"],
                       stroke=N["teal_dk"], sw=1.8))
        elif future:
            g.append(f'<rect x="{x}" y="{cy}" width="{cw}" height="{ch}" '
                     f'rx="12" ry="12" fill="{N["page"]}" '
                     f'stroke="{N["teal_line"]}" stroke-width="1.4" '
                     f'stroke-dasharray="6 6"/>')
        else:
            g.append(R(x, cy, cw, ch, r=12, fill=N["soft"], stroke=N["line"]))

        # 단계 칩
        lbl = f'{st["no"]} · {st["when"]}'
        cwid = 24 + est_w(lbl, 13)
        if now:
            g.append(R(x + 20, cy + 20, cwid, 28, r=14, fill=N["teal_dk"]))
            g.append(T(x + 20 + cwid / 2, cy + 39, lbl, size=13, weight="700",
                       fill="#FFFFFF", anchor="middle"))
        else:
            g.append(R(x + 20, cy + 20, cwid, 28, r=14,
                       fill=N["page"] if future else "#E6ECF2",
                       stroke=N["teal_line"] if future else None))
            g.append(T(x + 20 + cwid / 2, cy + 39, lbl, size=13, weight="700",
                       fill=N["teal_dk"] if future else N["sub"],
                       anchor="middle"))

        g.append(T(x + 20, cy + 94, st["title"], size=22, weight="700",
                   fill=N["ink"] if not future else N["ink"]))
        for j, ln in enumerate(st["desc"]):
            g.append(T(x + 20, cy + 130 + j * 24, ln, size=14.5, fill=N["sub"]))
        g.append(f'<path d="M {x + 20},{cy + 182} L {x + cw - 20},{cy + 182}" '
                 f'stroke="{N["line"]}" stroke-width="1"/>')
        for j, it in enumerate(st["items"]):
            iy = cy + 220 + j * 34
            g.append(f'<circle cx="{x + 26}" cy="{iy - 5}" r="2.6" '
                     f'fill="{N["teal_dk"] if not st["state"] == "done" else N["mute"]}"/>')
            g.append(T(x + 40, iy, it, size=14.5, fill=N["sub"]))

        # 한 줄 요약 띠
        ky = cy + 318
        if now:
            g.append(R(x + 20, ky, cw - 40, 46, r=10, fill=N["teal_dk"]))
            g.append(T(cx, ky + 30, st["key"], size=17, weight="700",
                       fill="#FFFFFF", anchor="middle"))
        else:
            g.append(R(x + 20, ky, cw - 40, 46, r=10,
                       fill=N["teal_soft"] if future else "#E9EEF3",
                       stroke=N["teal_line"] if future else None))
            g.append(T(cx, ky + 30, st["key"], size=17, weight="700",
                       fill=N["teal_dk"] if future else N["mute"],
                       anchor="middle"))

    # ── 근거 한 줄 ───────────────────────────────────────────────────
    ny = 746
    g.append(R(M, ny, W - 2 * M, 58, r=10, fill=N["teal_soft"],
               stroke=N["teal_line"]))
    g.append(icon("spark", M + 32, ny + 29, 22, N["teal_dk"], sw=1.7))
    g.append(T(M + 58, ny + 35, R_NOTE, size=15, weight="700",
               fill=N["teal_dk"]))

    g.append(T(M, 856, R_FOOTER_L, size=13, fill=N["mute"]))
    g.append(T(W - M, 856, R_FOOTER_R, size=13, fill=N["mute"], anchor="end"))

    g.append('</svg>')
    return "".join(g)


def build_svg(page="overview"):
    if page == "effect":
        return build_effect()
    if page == "roadmap":
        return build_roadmap()
    return build_overview()



# ─────────────────────────────────────────────────────────────────────────
# 6. 실행
# ─────────────────────────────────────────────────────────────────────────

def write(path, svg, png, scale):
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"생성 완료 : {os.path.abspath(path)}")
    if not png:
        return
    out = os.path.splitext(path)[0] + ".png"
    try:
        import cairosvg
    except ImportError:
        print("  PNG 건너뜀 : cairosvg 가 없습니다. "
              "PowerPoint 는 SVG 를 직접 넣을 수 있으므로 없어도 됩니다.")
        return
    try:
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=out,
                         output_width=int(W * scale),
                         output_height=int(H * scale))
        print(f"생성 완료 : {os.path.abspath(out)}")
    except Exception as e:
        print(f"  PNG 변환 실패 : {e}")


def main():
    global BLANK, GUIDES, FONT
    ap = argparse.ArgumentParser(description="구축 요약 도해(SVG) 생성")
    ap.add_argument("--out", default="summary.svg", help="저장할 SVG 파일명")
    ap.add_argument("--guides", action="store_true",
                    help="빈 버전에 글자 들어갈 자리를 연한 막대로 표시")
    ap.add_argument("--only", choices=["full", "blank"], default=None,
                    help="문구 있는 것 / 빈 것 중 한 쪽만 생성 (기본은 둘 다)")
    ap.add_argument("--page", choices=["overview", "effect", "roadmap", "all"],
                    default="all",
                    help="overview=구축 요약, effect=기대효과, roadmap=향후 계획 "
                         "(기본은 전부)")
    ap.add_argument("--png", action="store_true",
                    help="cairosvg 가 설치돼 있으면 PNG 도 함께 생성")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="PNG 배율 (기본 2배 = 3200x1800)")
    ap.add_argument("--font", default=None,
                    help="글꼴 지정. PNG 로 뽑았을 때 글자가 □ 로 나오면 "
                         "설치된 한글 글꼴 이름을 넣는다 (예: NanumGothic)")
    args = ap.parse_args()

    if args.font:
        FONT = args.font

    base, ext = os.path.splitext(args.out)
    ext = ext or ".svg"

    pages = (["overview", "effect", "roadmap"] if args.page == "all"
             else [args.page])
    for page in pages:
        stem = base if page == "overview" else base + "_" + page
        if args.only != "blank":
            BLANK, GUIDES = False, False
            write(stem + ext, build_svg(page), args.png, args.scale)
        if args.only != "full":
            BLANK, GUIDES = True, args.guides
            write(stem + "_blank" + ext, build_svg(page), args.png, args.scale)

    print()
    print("PPT 사용법")
    print("  1) 삽입 > 그림 > svg 파일 선택")
    print("  2) 그림 우클릭 > '그래픽을 도형으로 변환'")
    print("  3) 빈 버전은 텍스트 상자를 올려서 직접 작성")
    print("  ※ 글자 자리를 눈으로 보고 싶으면 --guides 로 다시 뽑으세요")
    return 0


if __name__ == "__main__":
    sys.exit(main())
