# -*- coding: utf-8 -*-
"""
make_summary_image.py — 구축 내용 / 개선 효과 한 장 요약 도해(SVG) 생성

AI 이미지 생성기는 한글을 정확히 그리지 못한다(자모가 깨지거나 없는 글자가
나온다). 보고자료는 문구가 틀리면 안 되므로, 좌표를 직접 계산해서 그린다.
표준 라이브러리만 사용하므로 폐쇄망에서도 그대로 돌아간다.

실행하면 두 개가 같이 나온다.
    summary.svg        문구까지 채워진 완성본
    summary_blank.svg  틀 + 아이콘만 있는 빈 버전 (문구는 PPT 에서 직접 입력)

사용법:
    python make_summary_image.py
    python make_summary_image.py --guides        # 빈 버전에 글자 자리 표시
    python make_summary_image.py --out 도해.svg
    python make_summary_image.py --png           # cairosvg 있으면 PNG 도

PPT 에 넣는 방법:
    삽입 > 그림 > svg 파일 선택
    → 그림 우클릭 > "그래픽을 도형으로 변환"
      (이러면 PPT 안에서 글자/색/위치를 직접 고칠 수 있다)

문구를 바꿀 곳:
    아래 TITLE / STAGES / CARDS / FOOTER 만 고치면 된다.
    리스트의 원소 하나 = 한 줄이다. 한 줄은 한글 18자 정도까지.
"""
import argparse
import html
import os
import sys

# ─────────────────────────────────────────────────────────────────────────
# 1. 문구 (여기만 고치면 된다)
# ─────────────────────────────────────────────────────────────────────────

TITLE = "ARS 운영 장애 예방 체계 구축"
SUBTITLE = "로그 통합 조회 · 시나리오 변경 비교 · 업무 구조 조회"
META = "ARS 7대 · AICC/VGW 3대 · 운영 시나리오 전체    |    운영 서버 변경 없음"

# 상단 : 시스템이 어떻게 구성되어 있는지 (왼쪽 → 오른쪽 흐름)
#   icon : servers / collect / database / monitor  중에서 고른다
STAGES = [
    {
        "title": "수집 대상",
        "icon": "servers",
        "items": [
            "ARS 7대 (Windows · OpenSSH)",
            "AICC · VGW 3대 (Linux)",
            "운영/과거 시나리오 XML",
        ],
    },
    {
        "title": "수집 방식",
        "icon": "collect",
        "items": [
            "원격 조회 전용 (설치 없음)",
            "SSH 키 인증 · 중단 시 자동 재개",
            "실시간 + 30일 소급 수집",
        ],
    },
    {
        "title": "처리",
        "icon": "database",
        "items": [
            "통화 단위 색인 (30일 보관)",
            "시나리오 구조 파싱 · 이력 비교",
            "블록 번호 → 업무 위치 환산",
        ],
    },
    {
        "title": "제공 화면",
        "icon": "monitor",
        "accent": True,                      # 강조색으로 표시
        "items": [
            "① 배포 전 변경 내용 비교",
            "② 통합 로그 검색 · 중단 지점",
            "③ 업무 구조 E2E 조회",
        ],
    },
]

# 하단 : 개선 효과 (현황 → 개선 → 효과)
#   icon : diff / breakpoint / tree  중에서 고른다
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
        "after": ["전체 흐름을 메뉴 경로 · STEP 으로", "펼쳐 업무 연관성 확인"],
        "effect": "업무 정보의 담당자 의존도 완화",
    },
]

FOOTER_L = "※ 수집은 조회 전용으로 동작하며 운영 서버에 설치·설정 변경 사항 없음"
FOOTER_R = "향후 : 채널 상태 기반 자동 알림 · 배포 승인 절차와 변경 검증 연계"

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
    "green":      "#047857",
    "green_soft": "#ECFDF5",
    "green_line": "#A7F3D0",
    "ghost":      "#EDF2F7",          # 빈 버전의 글자 자리 표시
}

BLANK = False        # True 면 글자를 그리지 않는다
GUIDES = False       # True 면 빈 버전에 글자 자리를 연한 막대로 표시


# ─────────────────────────────────────────────────────────────────────────
# 3. 그리기 도구
# ─────────────────────────────────────────────────────────────────────────

def esc(s):
    return html.escape(str(s))


def est_w(s, size):
    """글자 폭 어림값. 한글·전각은 1.0em, 나머지는 0.52em 로 본다."""
    w = 0.0
    for ch in s:
        w += size * (1.0 if ord(ch) > 0x2000 else 0.52)
    return w


def T(x, y, s, size=15, fill=None, weight="400", anchor="start",
      spacing=None, keep=False):
    """텍스트 한 줄. 빈 버전에서는 생략(또는 자리 표시)한다.

    keep=True 는 문구가 아니라 틀의 일부인 것(①②③ 같은 번호)이라
    빈 버전에서도 그대로 남긴다.
    """
    if BLANK and not keep:
        if not GUIDES:
            return ""
        w, h = est_w(s, size), size * 0.74
        gx = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
        return R(gx, y - h, w, h, r=3, fill=C["ghost"])
    sp = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or C["ink"]}" '
            f'text-anchor="{anchor}"{sp}>{esc(s)}</text>')


def R(x, y, w, h, r=10, fill="none", stroke=None, sw=1):
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
            f'fill="{fill}"{st}/>')


def top_round(x, y, w, h, r, fill):
    """위쪽 두 각만 둥근 사각형 (박스 머리띠용)."""
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


# ─────────────────────────────────────────────────────────────────────────
# 4. 아이콘 (24x24 기준의 선 아이콘. 색·크기는 호출할 때 정한다)
# ─────────────────────────────────────────────────────────────────────────

ICONS = {
    # 서버 더미 — 수집 대상
    "servers": ('<rect x="3" y="3.5" width="18" height="7" rx="1.8"/>'
                '<rect x="3" y="13.5" width="18" height="7" rx="1.8"/>'
                '<path d="M6.5 7h0.01M6.5 17h0.01" stroke-width="2.4"/>'),
    # 아래로 받아 담기 — 수집 방식
    "collect": ('<path d="M12 3v10"/><path d="M8 9.5l4 4 4-4"/>'
                '<path d="M4 17v2.5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V17"/>'),
    # 저장/색인 — 처리
    "database": ('<ellipse cx="12" cy="5.5" rx="8" ry="2.8"/>'
                 '<path d="M4 5.5v13c0 1.55 3.58 2.8 8 2.8s8-1.25 8-2.8v-13"/>'
                 '<path d="M4 12c0 1.55 3.58 2.8 8 2.8s8-1.25 8-2.8"/>'),
    # 화면 — 제공 화면
    "monitor": ('<rect x="2.5" y="3.5" width="19" height="13.5" rx="2"/>'
                '<path d="M9 20.5h6M12 17v3.5"/><path d="M2.5 7.5h19"/>'),
    # 두 문서 비교(+/-) — 배포 전 변경 비교
    "diff": ('<rect x="2.5" y="3.5" width="8.5" height="17" rx="1.6"/>'
             '<rect x="13" y="3.5" width="8.5" height="17" rx="1.6"/>'
             '<path d="M4.8 9h3.9"/><path d="M15.3 9h3.9M17.25 7.05v3.9"/>'
             '<path d="M4.8 13.5h3.9M4.8 16.5h2.4"/>'
             '<path d="M15.3 13.5h3.9M15.3 16.5h2.4"/>'),
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

def build_svg():
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=C["bg"])]

    # ── 머리글 ────────────────────────────────────────────────────────
    g.append(R(M, 46, 5, 46, r=2.5, fill=C["blue"]))
    g.append(T(M + 20, 76, TITLE, size=32, weight="700", fill=C["ink"]))
    g.append(T(M + 20, 100, SUBTITLE, size=16, fill=C["mute"]))
    g.append(T(W - M, 88, META, size=14, fill=C["mute"], anchor="end"))

    # ── 패널 1 : 구성 ────────────────────────────────────────────────
    p1y, p1h = 126, 250
    g.append(R(M, p1y, W - 2 * M, p1h, r=14, fill=C["panel"],
               stroke=C["line"], sw=1))
    chip, cw_ = pill(M + 28, p1y + 20, "구 성", C["slate"], C["slate_soft"])
    g.append(chip)
    g.append(T(M + 28 + cw_ + 22, p1y + 40,
               "수집부터 화면 제공까지 하나의 시스템으로 구성",
               size=14, fill=C["mute"]))

    bx0, gap = M + 28, 40
    bw = (W - 2 * M - 56 - gap * 3) / 4
    by, bh = p1y + 66, 160
    for i, st in enumerate(STAGES):
        x = bx0 + i * (bw + gap)
        accent = st.get("accent")
        head = C["blue"] if accent else C["slate"]
        g.append(R(x, by, bw, bh, r=12, fill=C["panel"],
                   stroke=C["blue_line"] if accent else C["line"],
                   sw=1.6 if accent else 1))
        g.append(top_round(x, by, bw, 44, 12, head))
        g.append(icon(st.get("icon", ""), x + 32, by + 22, 22,
                      "#FFFFFF", sw=1.8, opacity=0.95))
        g.append(T(x + bw / 2 + 14, by + 29, st["title"], size=18,
                   weight="700", fill="#FFFFFF", anchor="middle"))
        for j, it in enumerate(st["items"]):
            ty = by + 78 + j * 30
            g.append(f'<circle cx="{x + 20}" cy="{ty - 5}" r="2.6" '
                     f'fill="{C["blue"] if accent else C["mute"]}"/>')
            g.append(T(x + 32, ty, it, size=15,
                       fill=C["ink"] if accent else C["sub"],
                       weight="600" if accent else "400"))
        if i < len(STAGES) - 1:
            g.append(arrow_right(x + bw + gap / 2, by + bh / 2, C["mute"]))

    # ── 패널 2 : 개선 효과 ───────────────────────────────────────────
    p2y, p2h = 390, 428
    g.append(R(M, p2y, W - 2 * M, p2h, r=14, fill=C["panel"],
               stroke=C["line"], sw=1))
    chip, cw_ = pill(M + 28, p2y + 20, "개선 효과", C["blue"], C["blue_soft"])
    g.append(chip)
    g.append(T(M + 28 + cw_ + 22, p2y + 40, "현황 대비 달라진 점과 그 효과",
               size=14, fill=C["mute"]))

    cgap = 28
    cw = (W - 2 * M - 56 - cgap * 2) / 3
    cy, ch = p2y + 52, 350
    for i, cd in enumerate(CARDS):
        x = M + 28 + i * (cw + cgap)
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
    g.append(T(M, 852, FOOTER_L, size=13.5, fill=C["mute"]))
    g.append(T(W - M, 852, FOOTER_R, size=13.5, fill=C["mute"], anchor="end"))

    g.append('</svg>')
    return "".join(g)


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
                    help="한 쪽만 생성 (기본은 둘 다)")
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

    if args.only != "blank":
        BLANK, GUIDES = False, False
        write(base + ext, build_svg(), args.png, args.scale)

    if args.only != "full":
        BLANK, GUIDES = True, args.guides
        write(base + "_blank" + ext, build_svg(), args.png, args.scale)

    print()
    print("PPT 사용법")
    print("  1) 삽입 > 그림 > svg 파일 선택")
    print("  2) 그림 우클릭 > '그래픽을 도형으로 변환'")
    print("  3) 빈 버전은 텍스트 상자를 올려서 직접 작성")
    print("  ※ 글자 자리를 눈으로 보고 싶으면 --guides 로 다시 뽑으세요")
    return 0


if __name__ == "__main__":
    sys.exit(main())
