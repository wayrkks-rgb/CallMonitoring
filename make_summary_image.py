# -*- coding: utf-8 -*-
"""
make_summary_image.py — 구축 내용 / 개선 효과 한 장 요약 도해(SVG) 생성

AI 이미지 생성기는 한글을 정확히 그리지 못한다(자모가 깨지거나 없는 글자가
나온다). 보고자료는 문구가 틀리면 안 되므로, 좌표를 직접 계산해서 그린다.
표준 라이브러리만 사용하므로 폐쇄망에서도 그대로 돌아간다.

사용법:
    python make_summary_image.py                 # summary.svg 생성
    python make_summary_image.py --out 도해.svg
    python make_summary_image.py --png           # cairosvg 가 있으면 PNG 도 생성

PPT 에 넣는 방법:
    삽입 > 그림 > summary.svg
    → 그림 우클릭 > "그래픽을 도형으로 변환"
      (이러면 PPT 안에서 글자/색/위치를 직접 고칠 수 있다)

문구를 바꿀 곳:
    아래 TITLE / STAGES / CARDS / FOOTER 만 고치면 된다.
    줄바꿈은 리스트의 원소 하나 = 한 줄이다. 한 줄은 한글 18자 정도까지.
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
STAGES = [
    {
        "title": "수집 대상",
        "items": [
            "ARS 7대 (Windows · OpenSSH)",
            "AICC · VGW 3대 (Linux)",
            "운영/과거 시나리오 XML",
        ],
    },
    {
        "title": "수집 방식",
        "items": [
            "원격 조회 전용 (설치 없음)",
            "SSH 키 인증 · 중단 시 자동 재개",
            "실시간 + 30일 소급 수집",
        ],
    },
    {
        "title": "처리",
        "items": [
            "통화 단위 색인 (30일 보관)",
            "시나리오 구조 파싱 · 이력 비교",
            "블록 번호 → 업무 위치 환산",
        ],
    },
    {
        "title": "제공 화면",
        "accent": True,                      # 강조색으로 표시
        "items": [
            "① 배포 전 변경 내용 비교",
            "② 통합 로그 검색 · 중단 지점",
            "③ 업무 구조 E2E 조회",
        ],
    },
]

# 하단 : 개선 효과 (현황 → 개선 → 효과)
CARDS = [
    {
        "no": "①",
        "title": ["배포 전 시나리오", "변경 내용 자동 비교"],
        "before": ["변경 항목을 배포자 기억에 의존해", "확인, 영향 범위 사전 파악 불가"],
        "after": ["블록 · 변수 · 메뉴 버튼 단위로", "변경 내용과 전문을 배포 전 제시"],
        "effect": "잘못된 배포로 인한 장애 예방",
    },
    {
        "no": "②",
        "title": ["통화 중단 지점 제시", "및 로그 통합 조회"],
        "before": ["서버별 개별 접속 후 수동 검색,", "중단 지점은 로그를 직접 해석"],
        "after": ["10대 로그 동시 검색 + 통화별", "마지막 진행 지점·종료 사유 표시"],
        "effect": "장애 지점 확인 시간 단축",
    },
    {
        "no": "③",
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
}


def esc(s):
    return html.escape(str(s))


def T(x, y, s, size=15, fill=None, weight="400", anchor="start", spacing=None):
    """텍스트 한 줄."""
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
    """섹션 라벨 (작은 알약)."""
    w = 26 + int(len(label) * 15.5)
    out = [R(x, y, w, 30, r=15, fill=soft, stroke=color, sw=1),
           T(x + w / 2, y + 20, label, size=15, fill=color,
             weight="700", anchor="middle")]
    return "".join(out), w


def arrow_right(cx, cy, color):
    """단계 사이 화살표."""
    return (f'<path d="M {cx - 13},{cy} L {cx + 5},{cy}" stroke="{color}" '
            f'stroke-width="2.2" stroke-linecap="round"/>'
            f'<path d="M {cx + 3},{cy - 6} L {cx + 13},{cy} L {cx + 3},{cy + 6} Z" '
            f'fill="{color}"/>')


def chevron_down(cx, y, color):
    """현황 → 개선 사이 아래 화살표."""
    return (f'<path d="M {cx},{y} L {cx},{y + 10}" stroke="{color}" '
            f'stroke-width="2.2" stroke-linecap="round"/>'
            f'<path d="M {cx - 7},{y + 8} L {cx},{y + 17} L {cx + 7},{y + 8} Z" '
            f'fill="{color}"/>')


# ─────────────────────────────────────────────────────────────────────────
# 3. 그리기
# ─────────────────────────────────────────────────────────────────────────

def build_svg(font=None):
    global FONT
    if font:
        FONT = font
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
    chip, _ = pill(M + 28, p1y + 20, "구 성", C["slate"], C["slate_soft"])
    g.append(chip)
    g.append(T(M + 150, p1y + 40, "수집부터 화면 제공까지 하나의 시스템으로 구성",
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
        g.append(T(x + bw / 2, by + 29, st["title"], size=18,
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
    chip, _ = pill(M + 28, p2y + 20, "개선 효과", C["blue"], C["blue_soft"])
    g.append(chip)
    g.append(T(M + 178, p2y + 40, "현황 대비 달라진 점과 그 효과",
               size=14, fill=C["mute"]))

    cgap = 28
    cw = (W - 2 * M - 56 - cgap * 2) / 3
    cy, ch = p2y + 52, 350
    for i, cd in enumerate(CARDS):
        x = M + 28 + i * (cw + cgap)
        g.append(R(x, cy, cw, ch, r=12, fill=C["panel"], stroke=C["line"]))

        # 제목
        g.append(f'<circle cx="{x + 38}" cy="{cy + 40}" r="19" '
                 f'fill="{C["blue"]}"/>')
        g.append(T(x + 38, cy + 47, cd["no"], size=19, weight="700",
                   fill="#FFFFFF", anchor="middle"))
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


def main():
    ap = argparse.ArgumentParser(description="구축 요약 도해(SVG) 생성")
    ap.add_argument("--out", default="summary.svg", help="저장할 SVG 파일명")
    ap.add_argument("--png", action="store_true",
                    help="cairosvg 가 설치돼 있으면 PNG 도 함께 생성")
    ap.add_argument("--scale", type=float, default=2.0,
                    help="PNG 배율 (기본 2배 = 3200x1800)")
    ap.add_argument("--font", default=None,
                    help="글꼴 지정. PNG 로 뽑았을 때 글자가 □ 로 나오면 "
                         "설치된 한글 글꼴 이름을 직접 넣는다 (예: NanumGothic)")
    args = ap.parse_args()

    svg = build_svg(args.font)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"생성 완료 : {os.path.abspath(args.out)}  ({len(svg):,} bytes)")

    if args.png:
        png = os.path.splitext(args.out)[0] + ".png"
        try:
            import cairosvg
            cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=png,
                             output_width=int(W * args.scale),
                             output_height=int(H * args.scale))
            print(f"생성 완료 : {os.path.abspath(png)}")
        except ImportError:
            print("PNG 건너뜀 : cairosvg 가 없습니다.")
            print("  → PowerPoint 는 SVG 를 직접 넣을 수 있으므로 PNG 가 없어도 됩니다.")
            print("     삽입 > 그림 > SVG 선택 후, 우클릭 > '그래픽을 도형으로 변환'")
        except Exception as e:
            print(f"PNG 변환 실패 : {e}")
        else:
            print("  ※ PNG 글자가 □ 로 나오면 : --font NanumGothic 처럼 "
                  "설치된 글꼴을 지정하세요.")
            print("     (SVG 를 PPT 에 직접 넣으면 이 문제는 생기지 않습니다)")

    print()
    print("PPT 사용법")
    print("  1) 삽입 > 그림 > 이 SVG 파일 선택")
    print("  2) 그림 우클릭 > '그래픽을 도형으로 변환'  (글자/색 직접 수정 가능)")
    print("  3) 문구를 바꾸려면 이 스크립트 상단 TITLE/STAGES/CARDS 수정 후 재실행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
