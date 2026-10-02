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
QUANT_CAPTION = "배포 전에 막는 효과와, 장애가 난 뒤 빨리 잡는 효과"
QUAL_LABEL = "정성 효과"
QUAL_CAPTION = "수치로 드러나지 않는 운영 방식의 변화"

#  group : 'prevent' = 장애 예방(배포 전) / 'contain' = 장애 확산 방지(발생 후)
QUANT_GROUPS = {
    "prevent": ("장애 예방", "배포 전"),
    "contain": ("확산 방지", "장애 발생 후"),
}

QUANT = [
    {
        "group": "prevent", "no": "①", "title": "업무 FLOW 누락 지점 검출",
        "before": "0개", "after": "3개 업무", "gain": "장애 요인 사전 차단",
        "note": "※ 운영 시나리오 전수 점검 수행 및 시나리오 누락에 의한 "
                "오류 지점 사전 검출",
    },
    {
        "group": "prevent", "no": "②", "title": "오류 소스 운영 반영 비율",
        "before": "25%", "after": "0%", "gain": "재배포 5건 예방",
        "note": "※ '26년 SR 25건 · 적용 전(~'26.08) 20건 중 5건 재배포 · "
                "적용 후('26.09) 5건 중 0건",
    },
    {
        "group": "contain", "no": "③", "title": "장애 원인 특정 시간",
        "before": "30분", "after": "5분", "gain": "83% 단축",
        "note": "※ 통화 흐름 요약 · 중단 지점 · 종료 사유 자동 제시",
    },
]

QUAL = [
    {
        "icon": "shield", "title": "대응 방식 전환",
        "desc": ["담당자 경험에 의존한 판단에서",
                 "**시스템 화면 기반의 동일한 확인 절차**로 전환"],
        "note": "※ 통화 흐름 요약 · 중단 지점 · 화면 진행을 한 화면에서 확인",
        "chip": "사람 중심 → 시스템 중심",
    },
    {
        "icon": "diff", "title": "콜 인프라 전용 형상관리 구축",
        "desc": ["형상관리 솔루션 연동이 불가한 환경에서",
                 "**콜 인프라 전용 형상관리 체계**를 자체 구축"],
        "note": "※ 시나리오 XML 약 740개 · 버전 비교 · 변경 블록 · FLOW 추적",
        "chip": "형상관리 부재 → 배포 전 사전 검증 체계",
    },
    {
        "icon": "share", "title": "프로젝트 업무까지 활용 확장",
        "desc": ["신계약 프로젝트 인력이 로그 조회 · 시나리오",
                 "비교 · E2E 화면을 **운영 소스 분석에 활용**"],
        "note": "※ 신계약 모니터링 프로젝트 인력 2명 제공 ('26.09~'26.12)",
        "chip": "운영 전용 → 프로젝트 공동 활용",
    },
]

E_SUMMARY = ("배포 전 업무 FLOW 누락 · 오류 소스 차단 — "
             "장애 발생 시 원인 5분 내 특정")
E_FOOTER_L = "※ 소요 시간은 동일 업무 기준 구축 전후 비교값"
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
#  개선 전 · 후 업무 흐름 장표 (--page flow)
# ─────────────────────────────────────────────────────────────────────────

F_TITLE = "개선 전 · 후 업무 흐름"
F_SUBTITLE = "ARS 시나리오 변경부터 장애 분석까지"
F_META = "시나리오 디자이너 등 기존 개발 · 배포 절차 변경 없음"

#  실제 작업 절차를 단계로 세우고, 같은 단계를 위아래로 비교한다.
#  body  = 그 단계에서 실제로 하는 일
#  impact= 개선 전은 '그래서 생기는 문제', 개선 후는 '달라진 점'
PHASES = ["변경 대상 확인", "시나리오 수정", "배포 전 검증",
          "운영 반영", "장애 · 문의 대응"]

ASIS_LABEL, ASIS_SUB = "개선 전", "AS-IS"
TOBE_LABEL, TOBE_SUB = "개선 후", "TO-BE"

#  단계별 개선 전 · 후 비교표
#    before : 개선 전에 실제로 하는 일 / issue : 그래서 생기는 문제
#    after  : 개선 후 방식 / gain : 그 결과
ROWS = [
    {"no": "1", "step": "변경 대상 확인",
     "before": ["SR 내용으로 수정 대상 선정",
                "연관 범위는 담당자 경험으로 짐작"],
     "issue": "영향 범위를 확인할 수단 없음",
     "after": ["업무 구조 E2E 화면에서",
               "대상과 연관 시나리오를 함께 확인"],
     "gain": "누락 위험 감소"},
    {"no": "2", "step": "시나리오 수정",
     "before": ["시나리오 XML 블록 · 함수 수정",
                "여러 SR 이 같은 파일에 몰림"],
     "issue": "수정 이력이 파일에 남지 않음",
     "after": ["수정 방식은 기존과 동일",
               "저장 시 비교 대상으로 자동 등록"],
     "gain": "도구 교체 없음"},
    {"no": "3", "step": "배포 전 검증",
     "before": ["운영본과 수정본을 각각 열어 비교",
                "블록 내 함수 · 변수를 하나씩 대조"],
     "issue": "확인 범위가 사람마다 달라짐",
     "after": ["전체 자동 비교로 변경된 블록 · 함수 · 변수만 추출",
               "연관 시나리오까지 한 화면에 표시"],
     "gain": "60분 → 10분"},
    {"no": "4", "step": "운영 반영",
     "before": ["수정본 적용 후 실제 통화로 확인",
                "*오류 발견 시 수정 후 재배포"],
     "issue": "누락 · 혼재를 반영 후에 발견",
     "after": ["변경 내용과 영향 범위 확인 후 반영",
               "SR 간 중복 변경 · 누락분을 배포 전 식별"],
     "gain": "재배포 25% → 0%"},
    {"no": "5", "step": "장애 · 문의 대응",
     "before": ["고객 정보로 전체 로그 조회",
                "처음부터 읽어 중단 지점 판단"],
     "issue": "원인 지점까지 도달에 장시간 소요",
     "after": ["통화 흐름 요약과 중단 지점 · 사유를 먼저 제시",
               "해당 구간 로그만 확인"],
     "gain": "30분 → 5분"},
]

ASIS_LABEL, ASIS_SUB = "개선 전", "AS-IS"
TOBE_LABEL, TOBE_SUB = "개선 후", "TO-BE"

# ─────────────────────────────────────────────────────────────────────────
#  개선 전 · 후 비교 장표 (--page compare)
#  개선 전에 '실제로 보던 원본' 과 개선 후에 '시스템이 보여 주는 화면' 을
#  나란히 둔다. 오른쪽 화면은 실제 기능을 기준으로 한 예시.
# ─────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────
#  현황 및 문제점 장표 (--page issues)
#  지금 ARS 운영 구조에서 무엇이 어렵고 위험한지. 개선 전후 비교 대신 쓴다.
# ─────────────────────────────────────────────────────────────────────────

I_TITLE = "현황 및 문제점"
I_SUBTITLE = "솔루션 기반 ARS 시나리오 운영의 구조적 한계"
I_META = "시나리오 XML 약 740개 · ARS 7대 · AICC/VGW 3대"

#  chain : 문제가 이어지는 경로. 마지막 칸이 결과(강조)
ISSUES = [
    {
        "no": "1", "title": "형상관리 도구 부재",
        "fig": "740개", "fig_label": "시나리오 XML",
        "lines": ["솔루션 기반 제품(시나리오 디자이너) 운영으로 고객사 형상관리 연동 불가",
                  "소스 비교 · 이력 관리 도구 없이 XML 파일을 한 개씩 대조"],
        "chain": ["XML 약 740개", "파일 단위 개별 대조", "비교 · 검증 장시간"],
        "impact": "배포 전 검증 품질이 담당자의 시간과 경험에 좌우",
    },
    {
        "no": "2", "title": "잘못된 배포 = 즉시 대고객 장애",
        "fig": "Critical", "fig_label": "대고객 업무 중단",
        "lines": ["소스 형상관리가 담당자 경험과 기억에 의존",
                  "잘못된 소스가 운영에 나가면 고객 업무에 바로 영향"],
        "chain": ["휴대폰 인증 오류", "인증 불가", "업무 미완료", "Critical 장애"],
        "impact": "인증 · 청구 등 핵심 단계 오류 시 고객이 업무를 끝낼 수 없음",
    },
    {
        "no": "3", "title": "요건 중첩 시 소스 혼재",
        "fig": "혼재 위험", "fig_label": "요건이 겹칠수록 증가",
        "lines": ["프로젝트 · 신규 업무 요건이 겹치면 같은 시나리오를 동시에 수정",
                  "이번 배포 대상이 아닌 수정분 포함 · 필요한 수정분 누락 가능"],
        "chain": ["프로젝트 요건 + 신규 요건", "같은 시나리오 동시 수정",
                  "혼재 · 누락 배포"],
        "impact": "운영 반영 후에야 발견 → 재배포 · 대고객 영향",
    },
    {
        "no": "4", "title": "로그 분석 부담",
        "fig": "1,000줄+", "fig_label": "고객 1건 통화 로그",
        "lines": ["고객 한 명의 통화 로그가 1,000줄 이상",
                  "문제 지점을 처음부터 한 줄씩 짚어가며 확인"],
        "chain": ["1,000줄 이상 로그", "처음부터 순서대로 추적", "원인 특정 지연"],
        "impact": "장애 원인을 찾는 동안 대응이 늦어짐",
    },
]

I_NOTE = ("형상관리 · 배포 검증 · 로그 분석을 모두 사람에 의존 — "
          "작은 실수가 그대로 대고객 장애로 연결")
I_FOOTER_L = "※ 시나리오 디자이너 : 인티큐브 ARS 시나리오 개발 솔루션"

C_TITLE = "개선 전 · 후 비교"
C_SUBTITLE = "같은 업무를 할 때 보게 되는 화면"
C_META = "※ 개선 후 화면은 실제 기능 기준 예시"

#  기대효과의 세 지표에 맞춰, 서로 다른 결(효율 / 범위 / 안정성)로 나눈다
CMP = [
    {
        "no": "1", "kind": "장애 예방", "title": ["배포 전", "사전 검증"],
        "desc": ["SR 반영 전", "무엇이 바뀌었는지"],
        "raw": ['<Node seq="00001234" type="Script">',
                '  <Script>app.nAuthType = 1;</Script>',
                '<Node seq="00001235" type="Branch">',
                '  <Cond>app.nRetCode == 0</Cond>',
                '<Node seq="00001236" type="Menu"> …'],
        "pain": "운영본 · 수정본을 번갈아 열어 한 블록씩 대조",
        "mock": "diff",
        "big": "3개 업무", "big_label": "업무 FLOW 누락 지점 검출",
        "sub": "배포 전 수정 필요 경고",
    },
    {
        "no": "2", "kind": "확산 방지", "title": ["장애 원인", "확인"],
        "desc": ["통화가 어디서", "끊겼는지"],
        "raw": ["10:02:11 [W_Main.dxml][00000010] End Event[ok]",
                "10:02:13 [W_고객조회.dxml][00000342] MCI_SEND …",
                "10:02:14 CTIInterface … Inputdigit=1",
                "10:02:19 [W_보험금.dxml][00001234] End Event[ok]",
                "10:02:31 TERM REASON ==> TM_USRSTOP"],
        "pain": "서버별로 접속해 수백 줄을 처음부터 읽음",
        "mock": "precheck",
        "big": "30분 → 5분", "big_label": "장애 원인 특정 시간",
        "sub": "중단 지점 · 사유 자동 제시",
    },
    {
        "no": "3", "kind": "장애 예방", "title": ["오류 소스", "운영 반영"],
        "desc": ["잘못된 소스가", "운영에 나가는지"],
        "raw": ["W_보험금청구.xml   SR-A 수정분",
                "W_보험금청구.xml   SR-B 수정분 (다음 배포)",
                "W_인증공통.xml     SR-A 연관 수정 누락",
                "",
                "→ 한 파일에 두 SR 이 섞인 채 배포"],
        "pain": "혼재 · 누락을 운영 반영 후에야 발견",
        "mock": "redeploy",
        "big": "25% → 0%", "big_label": "오류 소스 운영 반영 비율",
        "sub": "재배포 5건 예방",
    },
]

C_NOTE = "원본을 직접 뒤지는 확인에서, 시스템이 정리한 결과를 먼저 보는 확인으로 전환"
C_FOOTER_L = ("※ 소요 시간은 동일 업무 기준 구축 전후 비교값 · 운영 반영 비율은 "
              "'26년 SR 25건 (적용 전 ~'26.08 20건 / 적용 후 '26.09 5건) 기준")

ASIS_RESULT = "변경 범위를 사람의 기억과 육안 대조로 확인 — 놓친 부분은 운영 반영 후에야 드러남"
TOBE_RESULT = "영향 범위 · 변경분 · 혼재 여부를 배포 전에 화면으로 확인 — 장애는 지점을 먼저 제시"

F_NOTE = ("소스 수정과 운영 반영 절차는 그대로 두고 검증 · 분석 방식만 변경 "
          "— 기존 개발 · 배포 도구 교체 없음")
F_FOOTER_L = "※ 개선 후에도 시나리오 디자이너와 기존 배포 절차는 동일하게 사용"
F_FOOTER_R = "변경 검증 · 장애 분석 단계만 시스템으로 대체"

# ─────────────────────────────────────────────────────────────────────────
# 2. 레이아웃 / 색
# ─────────────────────────────────────────────────────────────────────────

W, H = 1600, 900                      # 16:9 (PPT 기본 비율)
M = 48                                # 바깥 여백

# 글꼴은 나눔고딕 하나로 고정한다.
# 여러 개를 나열하면('NanumGothic','맑은 고딕',…) PowerPoint 가 SVG 를 도형으로
# 변환할 때 목록 전체를 글꼴 이름 하나로 읽어 기본 글꼴로 바꿔 버린다.
FONT = "NanumGothic"

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
    "amber":      "#B45309",          # 개선 전(불편·위험) 표시
    "amber_soft": "#FFF8EC",
    "amber_line": "#F6D8A8",
    "ghost":      "#E8EEF3",
    "ghost_navy": "#1D4269",
}

# 원본(XML · 로그)을 보여 줄 때 쓰는 고정폭 글꼴
# 원본(XML · 로그)을 보여 줄 때 쓰는 고정폭 글꼴 (나눔고딕코딩)
MONO = "NanumGothicCoding"

BLANK = False        # True 면 글자를 그리지 않는다
GUIDES = False       # True 면 빈 버전에 글자 자리를 연한 막대로 표시


# ─────────────────────────────────────────────────────────────────────────
# 3. 그리기 도구
# ─────────────────────────────────────────────────────────────────────────

def esc(s):
    return html.escape(str(s))


def est_w(s, size):
    """글자 폭 어림값 (나눔고딕 실측 기준).

    종류별로 실제 폭을 재서 계수를 잡았다. 계수가 크면 칩·배지가 글자보다
    헐렁해 보이고, 작으면 글자가 도형 밖으로 나간다.
    """
    k = {" ": 0.28, "·": 0.17, "→": 0.90, "%": 0.87}
    w = 0.0
    for ch in s:
        if ch in k:
            f = k[ch]
        elif ord(ch) > 0x2000:          # 한글 · 전각 기호
            f = 0.94
        elif ch.isdigit():
            f = 0.60
        elif ch.isupper():
            f = 0.59
        elif ch.isalpha():
            f = 0.48
        else:
            f = 0.45
        w += size * f
    return w


def T(x, y, s, size=15, fill=None, weight="400", anchor="start",
      spacing=None, keep=False, ghost=None):
    """텍스트 한 줄 (y 는 기준선). 빈 버전에서는 생략(또는 자리 표시)한다.

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


def TC(x, cy, s, size=15, fill=None, weight="400", anchor="middle",
       spacing=None, keep=False, ghost=None):
    """글자의 세로 가운데가 cy 에 오도록 그린다.

    기준선(baseline)으로 위치를 잡으면 글꼴마다 몇 px 씩 어긋난다.
    나눔고딕에서 글자가 실제로 차지하는 영역의 중심은 기준선보다
    0.315em 위에 있어(실측), 그만큼 내려서 기준선을 잡는다.
    """
    return T(x, cy + size * 0.315, s, size=size, fill=fill, weight=weight,
             anchor=anchor, spacing=spacing, keep=keep, ghost=ghost)


def TRC(x, cy, s, size=15, fill=None, hl=None, weight="400", hl_weight="700"):
    """세로 가운데 · 왼쪽 정렬 텍스트. **굵게** 로 감싼 부분만 강조색으로 그린다."""
    parts = s.split("**")
    plain = "".join(parts)
    if BLANK or len(parts) == 1:
        return TC(x, cy, plain, size=size, fill=fill, weight=weight, anchor="start")
    spans = []
    for i, p in enumerate(parts):
        if not p:
            continue
        if i % 2:
            spans.append(f'<tspan font-weight="{hl_weight}" fill="{hl}">{esc(p)}</tspan>')
        else:
            spans.append(esc(p))
    return (f'<text x="{x}" y="{cy + size * 0.315}" font-family="{FONT}" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill or C["ink"]}">'
            + "".join(spans) + '</text>')


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
    w = 22 + est_w(label, 15)
    out = [R(x, y, w, 27, r=13.5, fill=soft, stroke=color, sw=1),
           TC(x + w / 2, y + 13.5, label, size=15, fill=color, weight="700")]
    return "".join(out), w


def tag(x, y, label, color, soft, border):
    """작은 강조 꼬리표 (오른쪽 정렬로 쓴다). 오른쪽 끝 x 기준."""
    if not label:
        return ""
    w = 17 + est_w(label, 12.5)
    out = [R(x - w, y, w, 22, r=11, fill=soft, stroke=border, sw=1),
           TC(x - w / 2, y + 11, label, size=12.5, fill=color, weight="700")]
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
    w = max(minw, 22 + est_w(label, size))
    return "".join([R(x, y, w, 28, r=14, fill=soft, stroke=border, sw=1),
                    TC(x + 11, y + 14, label, size=size, fill=color,
                       weight="700", anchor="start")])


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
        g.append(TC(x + 38, cy + 40, cd["no"], size=19, weight="700",
                    fill="#FFFFFF", keep=True))
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
        g.append(TC(x + cw / 2, ey + 16, "▶  " + cd["effect"], size=14.5,
                    weight="700", fill=C["green"]))

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

    # 표 머리 (적용 전 · 적용 후 · 효과) — 행마다 같은 세로 줄에 놓여
    # 지표 설명과 수치 사이가 비지 않는다
    X_BEF, X_ARR0, X_ARR1, X_AFT = 900, 960, 1080, 1188
    X_GAIN = 1428
    py, ggap, hh = 172, 12, 34
    rh = 84 if len(QUANT) <= 3 else 70
    n_q = len(QUANT)
    n_gap = sum(1 for i in range(1, n_q)
                if QUANT[i]["group"] != QUANT[i - 1]["group"])
    ph_ = hh + rh * n_q + ggap * n_gap + 10
    g.append(R(M, py, W - 2 * M, ph_, r=13, fill=N["soft"],
               stroke=N["line"], sw=1))
    GX, GW = M + 10, 112
    ix = GX + GW + 20                             # 행 내용 시작
    hy = py + hh / 2 + 2
    for hx, lab in ((ix + 40, "지표"), (X_BEF, "적용 전"),
                    (X_AFT, "적용 후"), (X_GAIN, "효과")):
        g.append(TC(hx, hy, lab, size=13, weight="700", fill=N["mute"],
                    anchor="start" if lab == "지표" else "middle"))
    g.append(f'<path d="M {ix},{py + hh} L {W - M - 20},{py + hh}" '
             f'stroke="{N["line"]}" stroke-width="1"/>')

    # 행 위치 (범주가 바뀌는 곳은 조금 띄운다)
    rys, y = [], py + hh
    for i, q in enumerate(QUANT):
        if i and q["group"] != QUANT[i - 1]["group"]:
            y += ggap
        rys.append(y)
        y += rh

    # 범주 띠 — 표 왼쪽에서 묶는다
    for gk, (gname, gwhen) in QUANT_GROUPS.items():
        idx = [i for i, q in enumerate(QUANT) if q["group"] == gk]
        if not idx:
            continue
        y0g = (py + 10) if idx[0] == 0 else rys[idx[0]] + 4
        y1g = rys[idx[-1]] + rh - 4
        col = N["teal_dk"] if gk == "prevent" else N["navy"]
        g.append(R(GX, y0g, GW, y1g - y0g, r=9, fill=col))
        cy_ = (y0g + y1g) / 2
        g.append(TC(GX + GW / 2, cy_ - 10, gname, size=16, weight="700",
                    fill="#FFFFFF"))
        g.append(TC(GX + GW / 2, cy_ + 12, gwhen, size=12.5,
                    fill="#CFEFEA" if gk == "prevent" else N["on_navy_sub"]))

    gw_max = max(est_w(q["gain"], 14.5) for q in QUANT) + 30
    for i, q in enumerate(QUANT):
        ry = rys[i]
        mid = ry + rh / 2
        if i and q["group"] == QUANT[i - 1]["group"]:
            g.append(f'<path d="M {ix},{ry} L {W - M - 20},{ry}" '
                     f'stroke="{N["line"]}" stroke-width="1"/>')

        g.append(f'<circle cx="{ix + 15}" cy="{mid}" r="15" '
                 f'fill="{N["teal_soft"]}" stroke="{N["teal_line"]}" '
                 f'stroke-width="1"/>')
        g.append(TC(ix + 15, mid, q["no"], size=14, weight="700",
                    fill=N["teal_dk"], keep=True))
        g.append(TC(ix + 40, mid - 12, q["title"], size=21, weight="700",
                    fill=N["ink"], anchor="start"))
        g.append(TC(ix + 40, mid + 15, q["note"], size=13, fill=N["mute"],
                    anchor="start"))

        # 적용 전 → 적용 후 (긴 화살표로 가운데를 잇는다)
        g.append(TC(X_BEF, mid, q["before"], size=30, weight="700",
                    fill=N["mute"]))
        g.append(f'<path d="M {X_ARR0},{mid} L {X_ARR1 - 14},{mid}" '
                 f'stroke="{N["teal"]}" stroke-width="4" stroke-linecap="round"/>')
        g.append(f'<path d="M {X_ARR1 - 18},{mid - 11} L {X_ARR1 + 2},{mid} '
                 f'L {X_ARR1 - 18},{mid + 11} Z" fill="{N["teal"]}"/>')
        g.append(TC(X_AFT, mid, q["after"], size=42, weight="700",
                    fill=N["teal_dk"]))

        g.append(R(X_GAIN - gw_max / 2, mid - 17, gw_max, 34, r=17,
                   fill=N["teal_dk"]))
        g.append(TC(X_GAIN, mid, q["gain"], size=14.5, weight="700",
                    fill="#FFFFFF"))

    # ── 정성 (3단 카드) ──────────────────────────────────────────────
    ql = py + ph_ + 36
    g.append(R(M, ql - 8, 24, 3, r=1.5, fill=N["teal_dk"]))
    g.append(T(M + 36, ql, QUAL_LABEL, size=14, weight="700",
               fill=N["teal_dk"], spacing="2"))
    g.append(T(M + 36 + est_w(QUAL_LABEL, 14) + 30, ql, QUAL_CAPTION,
               size=13.5, fill=N["mute"]))

    gap = 24
    cw = (W - 2 * M - gap * 2) / 3
    top = ql + 20
    qh = 204
    for i, q in enumerate(QUAL):
        x = M + i * (cw + gap)
        px = x + 24                      # 카드 안쪽 여백
        g.append(R(x, top, cw, qh, r=11, fill=N["soft"],
                   stroke=N["line"], sw=1))
        iy = top + 36
        g.append(f'<circle cx="{px + 20}" cy="{iy}" r="20" '
                 f'fill="{N["teal_soft"]}"/>')
        g.append(icon(q.get("icon", ""), px + 20, iy, 22,
                      N["teal_dk"], sw=1.7))
        g.append(TC(px + 52, iy, q["title"], size=19, weight="700",
                    fill=N["ink"], anchor="start"))
        g.append(R(px, top + 68, 34, 3, r=1.5, fill=N["teal_dk"]))
        for j, ln in enumerate(q["desc"]):
            g.append(TRC(px, top + 96 + j * 25, ln, size=15, fill=N["sub"],
                         hl=N["teal_dk"]))
        if q.get("note"):
            g.append(TC(px, top + 146, q["note"], size=12.5, fill=N["mute"],
                        anchor="start"))
        # 바뀌기 전(흐린 칩) → 바뀐 뒤(진한 칩) — 바뀐 쪽이 먼저 눈에 들어오게
        cy_ = top + qh - 28
        bef, _, aft = q.get("chip", "").partition(" → ")
        c, w1 = chipc(px, cy_, bef, 13.5, N["mute"], "#FFFFFF", N["line"],
                      padx=12, h=30)
        g.append(c)
        ax_ = px + w1 + 8
        g.append(f'<path d="M {ax_},{cy_} h 16" stroke="{N["teal"]}" '
                 f'stroke-width="2.6" stroke-linecap="round"/>')
        g.append(f'<path d="M {ax_ + 14},{cy_ - 7} L {ax_ + 24},{cy_} '
                 f'L {ax_ + 14},{cy_ + 7} Z" fill="{N["teal"]}"/>')
        c, _ = chipc(ax_ + 32, cy_, aft, 15, "#FFFFFF", N["teal_dk"], None,
                     padx=14, h=34)
        g.append(c)

    # 맨 아래 한 줄 — 장표의 결론
    sy = top + qh + 20
    g.append(R(M, sy, W - 2 * M, 48, r=9, fill=N["navy"]))
    g.append(icon("shield", M + 28, sy + 24, 20, N["teal"], sw=1.7))
    g.append(TC(M + 52, sy + 24, E_SUMMARY, size=15, weight="700",
                fill="#FFFFFF", anchor="start", ghost=N["ghost_navy"]))

    fy = sy + 78
    g.append(T(M, fy, E_FOOTER_L, size=12.5, fill=N["mute"]))
    g.append(T(W - M, fy, E_FOOTER_R, size=12.5, fill=N["mute"], anchor="end"))

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
            g.append(TC(x + 20 + cwid / 2, cy + 34, lbl, size=13, weight="700",
                        fill="#FFFFFF"))
        else:
            g.append(R(x + 20, cy + 20, cwid, 28, r=14,
                       fill=N["page"] if future else "#E6ECF2",
                       stroke=N["teal_line"] if future else None))
            g.append(TC(x + 20 + cwid / 2, cy + 34, lbl, size=13, weight="700",
                        fill=N["teal_dk"] if future else N["sub"]))

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
            g.append(TC(cx, ky + 23, st["key"], size=17, weight="700", fill="#FFFFFF"))
        else:
            g.append(R(x + 20, ky, cw - 40, 46, r=10,
                       fill=N["teal_soft"] if future else "#E9EEF3",
                       stroke=N["teal_line"] if future else None))
            g.append(TC(cx, ky + 23, st["key"], size=17, weight="700",
                        fill=N["teal_dk"] if future else N["mute"]))

    # ── 근거 한 줄 ───────────────────────────────────────────────────
    ny = 746
    g.append(R(M, ny, W - 2 * M, 58, r=10, fill=N["teal_soft"],
               stroke=N["teal_line"]))
    g.append(icon("spark", M + 32, ny + 29, 22, N["teal_dk"], sw=1.7))
    g.append(TC(M + 58, ny + 29, R_NOTE, size=15, weight="700",
                fill=N["teal_dk"], anchor="start"))

    g.append(T(M, 856, R_FOOTER_L, size=13, fill=N["mute"]))
    g.append(T(W - M, 856, R_FOOTER_R, size=13, fill=N["mute"], anchor="end"))

    g.append('</svg>')
    return "".join(g)


def build_flow():
    """개선 전 · 후 업무 흐름 — 단계를 세로로 세운 비교표.

    한 행을 왼쪽에서 오른쪽으로 읽으면 그 단계가 어떻게 바뀌었는지 끝난다.
    같은 절차를 두 번 그리지 않으므로 비교가 흐려지지 않는다.
    """
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=N["page"])]

    g.append(R(0, 0, W, 104, r=0, fill=N["navy"]))
    g.append(T(M, 54, F_TITLE, size=29, weight="700", fill=N["on_navy"],
               ghost=N["ghost_navy"]))
    g.append(T(M, 81, F_SUBTITLE, size=13.5, fill=N["on_navy_sub"],
               ghost=N["ghost_navy"]))
    g.append(T(W - M, 68, F_META, size=12.5, fill=N["teal"], anchor="end",
               ghost=N["ghost_navy"]))

    # ── 표 뼈대 ──────────────────────────────────────────────────────
    c0, c1 = 200, 560
    x0 = M
    x1, x2, x3 = x0 + c0, x0 + c0 + c1, W - M
    c2 = x3 - x2
    ty, hh, rh = 128, 42, 112
    tbl_h = hh + rh * len(ROWS)

    g.append(R(x0, ty, x3 - x0, tbl_h, r=12, fill=N["page"],
               stroke=N["line"], sw=1))
    # 열 바탕 — 개선 전은 회색, 개선 후는 청록으로 칠해 두 열을 색으로 가른다
    g.append(f'<path d="M {x1},{ty + hh} h {c1} v {rh * len(ROWS)} h -{c1} z" '
             f'fill="{N["soft"]}"/>')
    g.append(f'<path d="M {x2},{ty + hh} h {c2 - 12} a 12,12 0 0 1 12,12 '
             f'v {rh * len(ROWS) - 24} a 12,12 0 0 1 -12,12 h -{c2 - 12} z" '
             f'fill="{N["teal_soft"]}" opacity="0.55"/>')

    # 머리행
    g.append(f'<path d="M {x0 + 12},{ty} h {x3 - x0 - 24} a 12,12 0 0 1 12,12 '
             f'v {hh - 12} h -{x3 - x0} v -{hh - 12} a 12,12 0 0 1 12,-12 z" '
             f'fill="{N["navy"]}"/>')
    g.append(TC(x0 + 24, ty + hh / 2, "작업 절차", size=12.5, weight="700",
                anchor="start", fill=N["teal"], spacing="1",
                ghost=N["ghost_navy"]))
    g.append(TC(x1 + 22, ty + hh / 2, ASIS_LABEL + "   " + ASIS_SUB, size=14,
                weight="700", anchor="start", fill=N["on_navy_sub"],
                ghost=N["ghost_navy"]))
    g.append(TC(x2 + 22, ty + hh / 2, TOBE_LABEL + "   " + TOBE_SUB, size=14,
                weight="700", anchor="start", fill=N["teal"],
                ghost=N["ghost_navy"]))

    for i, row in enumerate(ROWS):
        ry = ty + hh + i * rh
        if i:
            g.append(f'<path d="M {x0},{ry} h {x3 - x0}" '
                     f'stroke="{N["line"]}" stroke-width="1"/>')

        # 단계
        g.append(f'<circle cx="{x0 + 32}" cy="{ry + rh / 2}" r="14" '
                 f'fill="{N["navy"]}"/>')
        g.append(TC(x0 + 32, ry + rh / 2, row["no"], size=14, weight="700",
                    fill="#FFFFFF", keep=True))
        g.append(TC(x0 + 54, ry + rh / 2, row["step"], size=14.5,
                    weight="700", anchor="start", fill=N["ink"]))

        # 개선 전
        for j, ln in enumerate(row["before"]):
            hi = ln.startswith("*")       # 문제의 원인이 되는 줄은 강조
            g.append(f'<circle cx="{x1 + 24}" cy="{ry + 34 + j * 24 - 4}" '
                     f'r="2.2" fill="{N["amber"] if hi else N["mute"]}"/>')
            g.append(T(x1 + 34, ry + 34 + j * 24, ln[1:] if hi else ln,
                       size=13.5, weight="700" if hi else "400",
                       fill=N["amber"] if hi else N["sub"]))
        iw = 18 + est_w(row["issue"], 12.5)
        g.append(R(x1 + 22, ry + rh - 40, iw, 25, r=12.5,
                   fill=N["amber_soft"], stroke=N["amber_line"]))
        g.append(TC(x1 + 22 + iw / 2, ry + rh - 27.5, row["issue"], size=12.5,
                    weight="700", fill=N["amber"]))

        # 개선 후
        gw = 20 + est_w(row["gain"], 13)
        gx = x3 - 20 - gw
        for j, ln in enumerate(row["after"]):
            g.append(f'<path d="M {x2 + 24},{ry + 34 + j * 24 - 4} h 8" '
                     f'stroke="{N["teal_dk"]}" stroke-width="2" '
                     f'stroke-linecap="round"/>')
            g.append(T(x2 + 40, ry + 34 + j * 24, ln, size=13.5, weight="700",
                       fill=N["ink"]))
        g.append(R(gx, ry + rh / 2 - 14, gw, 28, r=14, fill=N["teal_dk"]))
        g.append(TC(gx + gw / 2, ry + rh / 2, row["gain"], size=13,
                    weight="700", fill="#FFFFFF"))

    # ── 맨 아래 한 줄 ────────────────────────────────────────────────
    ny = ty + tbl_h + 28
    g.append(R(M, ny, W - 2 * M, 44, r=9, fill=N["navy"]))
    g.append(icon("shield", M + 26, ny + 22, 19, N["teal"], sw=1.7))
    g.append(TC(M + 48, ny + 22, F_NOTE, size=13.5, weight="700",
                fill="#FFFFFF", anchor="start", ghost=N["ghost_navy"]))
    g.append(TC(W - M - 18, ny + 22, F_FOOTER_R, size=12,
                fill=N["on_navy_sub"], anchor="end", ghost=N["ghost_navy"]))
    g.append(T(M, ny + 70, F_FOOTER_L, size=12, fill=N["mute"]))

    g.append('</svg>')
    return "".join(g)


def TM(x, y, s, size=11.5, fill=None, weight="400", anchor="start"):
    """고정폭 글꼴 텍스트 (원본 XML · 로그 표시용)."""
    if not s:
        return ""
    if BLANK:
        return T(x, y, s, size=size, anchor=anchor)
    return (f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or C["ink"]}" '
            f'text-anchor="{anchor}">{esc(s)}</text>')


def chipc(x, cy, label, size, fg, bg, border=None, padx=9, h=None, mono=False):
    """세로 가운데가 cy 인 칩. (svg, 폭) 을 돌려준다."""
    h = h or round(size * 1.85)
    w = est_w(label, size) + padx * 2
    body = (TM(x + padx, cy + size * 0.33, label, size=size, fill=fg,
               weight="700") if mono else
            TC(x + padx, cy, label, size=size, weight="700", fill=fg,
               anchor="start"))
    return (R(x, cy - h / 2, w, h, r=h / 2, fill=bg, stroke=border, sw=1)
            + body), w


def _wrap2(text, size, avail):
    """두 줄까지 나눈다 (가운데에 가까운 공백 기준)."""
    if est_w(text, size) <= avail:
        return [text]
    sp = [i for i, ch in enumerate(text) if ch == " "]
    if not sp:
        return [text]
    mid = len(text) / 2
    k = min(sp, key=lambda i: abs(i - mid))
    return [text[:k], text[k + 1:]]


RED = "#C53030"


def _mock_diff(g, x, y, w):
    """시나리오 DIFF 결과 화면 — 끊어진 이동 경고 + 변경 블록 · 위치 · 변수 · 연관."""
    g.append(R(x - 4, y - 2, w + 8, 26, r=6, fill="#FFF5F5", stroke="#FEB2B2"))
    g.append(f'<circle cx="{x + 11}" cy="{y + 11}" r="6" fill="{RED}"/>')
    g.append(f'<path d="M {x + 8},{y + 11} h 6" stroke="#FFFFFF" stroke-width="2"/>')
    g.append(TC(x + 23, y + 11, "업무 FLOW 누락 1건 — 배포 전 수정 필요", size=12.5,
                weight="700", fill=RED, anchor="start"))
    g.append(TM(x + w - 6, y + 15, "W_보험금.xml 00001234 → W_인증.xml 00000400",
                size=11, fill="#9B2C2C", anchor="end"))
    y += 26
    g.append(T(x, y + 17, "W_보험금청구.xml", size=14, weight="700",
               fill=N["ink"]))
    cx = x + est_w("W_보험금청구.xml", 14) + 12
    c, cw_ = chipc(cx, y + 12, "변경 3", 11.5, N["amber"], N["amber_soft"],
                   N["amber_line"])
    g.append(c)
    c, _ = chipc(cx + cw_ + 6, y + 12, "추가 1", 11.5, N["teal_dk"],
                 N["teal_soft"], N["teal_line"])
    g.append(c)

    ry = y + 44
    c, cw_ = chipc(x, ry, "00001234", 11, "#FFFFFF", N["navy"], mono=True)
    g.append(c)
    g.append(TC(x + cw_ + 8, ry, "본인인증 분기", size=13.5, weight="700",
                fill=N["ink"], anchor="start"))
    lx = x + cw_ + 8 + est_w("본인인증 분기", 13.5) + 10
    c, _ = chipc(lx, ry, "보험금 청구 › 본인인증 STEP 3", 11.5, N["teal_dk"],
                 N["teal_soft"], N["teal_line"])
    g.append(c)

    def row(yy, label):
        g.append(TC(x, yy, label, size=11, weight="700", fill=N["mute"],
                    anchor="start", spacing="1"))
        return x + 38

    ry = y + 74
    vx = row(ry, "변수")
    g.append(TM(vx, ry + 4, "app.nAuthType", size=12.5, fill=N["ink"]))
    ox = vx + est_w("app.nAuthType", 12.5) * 1.02 + 12
    c, w1 = chipc(ox, ry, "이전 1", 11.5, RED, "#FDECEC", "#F5C2C2")
    g.append(c)
    g.append(TC(ox + w1 + 6, ry, "→", size=13, fill=N["mute"], anchor="start"))
    c, _ = chipc(ox + w1 + 24, ry, "변경 2", 11.5, N["teal_dk"], N["teal_soft"],
                 N["teal_line"])
    g.append(c)

    ry = y + 102
    sx = row(ry, "화면")
    g.append(TC(sx, ry, "메뉴 버튼 추가", size=12.5, fill=N["sub"],
                anchor="start"))
    c, _ = chipc(sx + est_w("메뉴 버튼 추가", 12.5) + 8, ry, "+ 휴대폰 인증",
                 11.5, N["teal_dk"], N["teal_soft"], N["teal_line"])
    g.append(c)

    ry = y + 130
    rx = row(ry, "연관")
    g.append(TC(rx, ry, "W_Main.xml 에서 호출 · 함께 확인 필요", size=12.5,
                fill=N["sub"], anchor="start"))
    lab = "트리에서 위치 보기"
    lw = est_w(lab, 11.5) + 18
    c, _ = chipc(x + w - lw, ry, lab, 11.5, N["teal_dk"], N["page"],
                 N["teal_dk"])
    g.append(c)


def _mock_precheck(g, x, y, w):
    """로그 조회 사전 노티 — 종료 유형 · 중단 위치 · 화면 진행 · FLOW 연결."""
    c, cw_ = chipc(x, y + 12, "고객 중단", 11.5, RED, "#FDECEC", "#F5C2C2")
    g.append(c)
    c, cw2 = chipc(x + cw_ + 6, y + 12, "보이는ARS", 11.5, "#4C51BF",
                   "#EEF0FF", "#C9CDF7")
    g.append(c)
    g.append(TC(x + cw_ + cw2 + 16, y + 12, "사전 노티", size=11,
                fill=N["mute"], anchor="start"))

    ry = y + 46
    loc = "보험금 청구 › 본인인증 STEP 3"
    g.append(TC(x, ry, loc, size=15, weight="700", fill=N["teal_dk"],
                anchor="start"))
    g.append(TC(x + est_w(loc, 15) + 6, ry, "에서 고객 중단(끊음)", size=15,
                weight="700", fill=N["ink"], anchor="start"))

    ry = y + 82
    g.append(TC(x, ry, "화면 진행", size=11, weight="700", fill=N["mute"],
                anchor="start", spacing="1"))
    cx = x + 62
    steps = ["청구 안내", "본인인증", "휴대폰 입력"]
    for i, stp in enumerate(steps):
        last = i == len(steps) - 1
        c, cw_ = chipc(cx, ry, stp, 12, "#FFFFFF" if last else N["sub"],
                       N["teal_dk"] if last else N["soft"],
                       None if last else N["line"])
        g.append(c)
        cx += cw_
        if not last:
            g.append(TC(cx + 9, ry, "›", size=15, weight="700", fill=N["mute"]))
            cx += 18

    ry = y + 120
    g.append(TC(x, ry, "종료 블록", size=11, weight="700", fill=N["mute"],
                anchor="start", spacing="1"))
    g.append(TM(x + 62, ry + 4, "W_보험금.dxml / 00001234", size=12,
                fill=N["ink"]))
    lab2, lab1 = "종료 단계 보기", "시작 단계부터 보기"
    w2 = est_w(lab2, 11.5) + 18
    w1 = est_w(lab1, 11.5) + 18
    c, _ = chipc(x + w - w2, ry, lab2, 11.5, "#FFFFFF", N["teal_dk"])
    g.append(c)
    c, _ = chipc(x + w - w2 - 6 - w1, ry, lab1, 11.5, N["teal_dk"], N["page"],
                 N["teal_dk"])
    g.append(c)


def _mock_tree(g, x, y, w):
    """업무 FLOW 뷰어 — 업무 › 단계 › 블록, 호스트 전문까지."""
    g.append(TC(x, y + 12, "보험금 청구", size=15, weight="700",
                fill=N["ink"], anchor="start"))
    g.append(TC(x + est_w("보험금 청구", 15) + 10, y + 12, "업무 전체 흐름",
                size=11.5, fill=N["mute"], anchor="start"))
    lab = "PDF로 저장"
    lw = est_w(lab, 11.5) + 18
    c, _ = chipc(x + w - lw, y + 12, lab, 11.5, N["teal_dk"], N["page"],
                 N["teal_dk"])
    g.append(c)

    tx = x + 10
    g.append(f'<path d="M {tx},{y + 26} V {y + 128}" stroke="{N["line"]}" '
             f'stroke-width="2"/>')
    items = [(44, "본인확인", "STEP 1–2", False),
             (72, "본인인증", "STEP 3", True),
             (128, "청구 접수", "STEP 4", False)]
    for dy, nm, stp, hit in items:
        yy = y + dy
        if hit:
            g.append(R(tx + 8, yy - 13, w - 18, 26, r=6, fill=N["teal_soft"],
                       stroke=N["teal_line"]))
        g.append(f'<path d="M {tx},{yy} h 14" stroke="{N["line"]}" '
                 f'stroke-width="2"/>')
        g.append(TC(tx + 22, yy, nm, size=13.5, weight="700",
                    fill=N["teal_dk"] if hit else N["ink"], anchor="start"))
        g.append(TC(tx + 22 + est_w(nm, 13.5) + 10, yy, stp, size=11.5,
                    weight="700", fill=N["mute"], anchor="start"))
        if hit:
            c, _ = chipc(x + w - 96, yy, "00001234", 11, "#FFFFFF", N["navy"],
                         mono=True)
            g.append(c)
    yy = y + 100
    g.append(TC(tx + 34, yy, "호스트 전문", size=11, weight="700",
                fill=N["mute"], anchor="start", spacing="1"))
    g.append(TM(tx + 34 + 70, yy + 4, "HLI_AUTH01", size=12, fill=N["ink"]))
    g.append(TC(tx + 34 + 70 + est_w("HLI_AUTH01", 12) * 1.02 + 10, yy,
                "입력 3 · 출력 5", size=11.5, fill=N["sub"], anchor="start"))


def _mock_redeploy(g, x, y, w):
    """배포 전 전체 변경 목록 — 이번 배포에 무엇이 함께 나가는지 한 번에."""
    g.append(T(x, y + 17, "이번 배포 전체 변경", size=14, weight="700",
               fill=N["ink"]))
    cx = x + est_w("이번 배포 전체 변경", 14) + 12
    for lab in ("시나리오 2", "블록 5", "변수 3"):
        c, cw_ = chipc(cx, y + 12, lab, 11.5, N["sub"], N["soft"], N["line"])
        g.append(c)
        cx += cw_ + 6

    rows = [("W_보험금청구.xml", "본인인증 분기", "변수 변경", False),
            ("W_보험금청구.xml", "청구 접수", "버튼 추가", False),
            ("W_인증공통.xml", "인증 결과 처리", "영향 확인 필요", True)]
    for i, (f, blk, tag_, warn) in enumerate(rows):
        yy = y + 46 + i * 28
        if warn:
            g.append(R(x - 6, yy - 12, w + 12, 24, r=6, fill=N["amber_soft"],
                       stroke=N["amber_line"]))
        g.append(TM(x + 4, yy + 4, f, size=12, fill=N["sub"]))
        g.append(TC(x + 170, yy, blk, size=13, weight="700", fill=N["ink"],
                    anchor="start"))
        tw_ = est_w(tag_, 11.5) + 18
        c, _ = chipc(x + w - tw_, yy, tag_, 11.5,
                     N["amber"] if warn else N["teal_dk"],
                     N["page"] if warn else N["teal_soft"],
                     N["amber_line"] if warn else N["teal_line"])
        g.append(c)

    yy = y + 134
    g.append(TC(x, yy, "▶  섞이거나 빠진 변경을 배포 전에 걸러 반영",
                size=13, weight="700", fill=N["teal_dk"], anchor="start"))


def build_compare():
    """개선 전 · 후 비교 — 원본(개선 전) 과 시스템 화면(개선 후) 을 마주 놓는다."""
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=N["page"])]

    g.append(R(0, 0, W, 100, r=0, fill=N["navy"]))
    g.append(T(M, 52, C_TITLE, size=29, weight="700", fill=N["on_navy"],
               ghost=N["ghost_navy"]))
    g.append(T(M, 78, C_SUBTITLE, size=13.5, fill=N["on_navy_sub"],
               ghost=N["ghost_navy"]))
    g.append(T(W - M, 66, C_META, size=12.5, fill=N["teal"], anchor="end",
               ghost=N["ghost_navy"]))

    LX, LW = M, 176
    AX, AW = LX + LW + 12, 436
    BX = AX + AW + 40
    EW = 236
    EX = W - M - EW
    BW = EX - 12 - BX
    bh, gap = 214, 12

    for i, it in enumerate(CMP):
        by = 116 + i * (bh + gap)

        # 왼쪽 : 무엇을 하는 일인가
        g.append(R(LX, by, LW, bh, r=12, fill=N["navy"]))
        g.append(f'<circle cx="{LX + 32}" cy="{by + 36}" r="14" '
                 f'fill="{N["teal"]}"/>')
        g.append(TC(LX + 32, by + 36, it["no"], size=14, weight="700",
                    fill=N["navy"], keep=True))
        if it.get("kind"):
            c, _ = chipc(LX + 54, by + 36, it["kind"], 12, N["teal"],
                         N["navy"], N["teal"])
            g.append(c)
        for j, ln in enumerate(it["title"]):
            g.append(T(LX + 18, by + 90 + j * 26, ln, size=20, weight="700",
                       fill="#FFFFFF", ghost=N["ghost_navy"]))
        for j, ln in enumerate(it["desc"]):
            g.append(T(LX + 18, by + 158 + j * 19, ln, size=12.5,
                       fill=N["on_navy_sub"], ghost=N["ghost_navy"]))

        # 가운데 왼쪽 : 개선 전에 실제로 보던 원본
        g.append(R(AX, by, AW, bh, r=12, fill="#F3F4F6", stroke=N["line"]))
        c, _ = chipc(AX + 16, by + 22, "개선 전", 12, N["sub"], "#E2E8F0")
        g.append(c)
        g.append(TC(AX + 90, by + 22, "직접 보던 원본", size=11.5,
                    fill=N["mute"], anchor="start"))
        g.append(R(AX + 16, by + 42, AW - 32, 112, r=8, fill="#FFFFFF",
                   stroke=N["line"]))
        for j, ln in enumerate(it["raw"]):
            g.append(TM(AX + 28, by + 64 + j * 20, ln, size=11.5,
                        fill="#8A94A6"))
        pw = est_w("! " + it["pain"], 12.5) + 28
        g.append(R(AX + 16, by + bh - 46, pw, 30, r=15,
                   fill=N["amber_soft"], stroke=N["amber_line"]))
        g.append(TC(AX + 30, by + bh - 31, "! " + it["pain"], size=12.5,
                    weight="700", fill=N["amber"], anchor="start"))

        # 화살표
        ax = AX + AW + 20
        g.append(f'<path d="M {ax - 11},{by + bh / 2 - 16} L {ax + 7},{by + bh / 2} '
                 f'L {ax - 11},{by + bh / 2 + 16} Z" fill="{N["teal_dk"]}"/>')

        # 가운데 오른쪽 : 개선 후 시스템이 보여 주는 화면
        g.append(R(BX, by, BW, bh, r=12, fill=N["page"],
                   stroke=N["teal_line"], sw=1.8))
        c, _ = chipc(BX + 16, by + 22, "개선 후", 12, "#FFFFFF", N["teal_dk"])
        g.append(c)
        g.append(TC(BX + 90, by + 22, "시스템이 보여 주는 화면", size=11.5,
                    fill=N["mute"], anchor="start"))
        mx, my, mw = BX + 20, by + 44, BW - 40
        {"diff": _mock_diff, "precheck": _mock_precheck,
         "tree": _mock_tree, "redeploy": _mock_redeploy}[it["mock"]](
             g, mx, my, mw)

        # 오른쪽 : 효과
        g.append(R(EX, by, EW, bh, r=12, fill=N["teal_dk"]))
        g.append(TC(EX + EW / 2, by + 50, it["big_label"], size=12.5,
                    weight="700", fill="#BFF3EA"))
        g.append(TC(EX + EW / 2, by + 96, it["big"], size=31, weight="800",
                    fill="#FFFFFF"))
        sw_ = est_w(it["sub"], 13) + 26
        g.append(R(EX + EW / 2 - sw_ / 2, by + 146, sw_, 30, r=15,
                   fill="#FFFFFF", opacity=None) if False else
                 f'<rect x="{EX + EW / 2 - sw_ / 2}" y="{by + 146}" width="{sw_}" '
                 f'height="30" rx="15" fill="#FFFFFF" fill-opacity="0.16"/>')
        g.append(TC(EX + EW / 2, by + 161, it["sub"], size=13, weight="700",
                    fill="#FFFFFF"))

    ny = 116 + 3 * (bh + gap) + 6
    g.append(R(M, ny, W - 2 * M, 42, r=9, fill=N["navy"]))
    g.append(icon("shield", M + 26, ny + 21, 19, N["teal"], sw=1.7))
    g.append(TC(M + 48, ny + 21, C_NOTE, size=14, weight="700",
                fill="#FFFFFF", anchor="start", ghost=N["ghost_navy"]))
    g.append(T(M, ny + 66, C_FOOTER_L, size=11.5, fill=N["mute"]))

    g.append('</svg>')
    return "".join(g)


def build_issues():
    """현황 및 문제점 — 네 가지 문제를 2 x 2 로. 각 칸은 수치 · 사실 · 경로 · 영향.

    네 칸이 같은 격자를 쓴다. 제목 줄과 수치 상자는 같은 세로 중심에,
    사실 두 줄 · 경로 · 영향 띠도 칸마다 같은 높이에 놓는다.
    글자 크기는 역할별로 하나씩만 쓴다 (제목 22 · 수치 24 · 본문 15 · 경로 14).
    """
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}">',
         R(0, 0, W, H, r=0, fill=N["page"])]

    g.append(R(0, 0, W, 100, r=0, fill=N["navy"]))
    g.append(T(M, 52, I_TITLE, size=29, weight="700", fill=N["on_navy"],
               ghost=N["ghost_navy"]))
    g.append(T(M, 78, I_SUBTITLE, size=14, fill=N["on_navy_sub"],
               ghost=N["ghost_navy"]))
    g.append(T(W - M, 66, I_META, size=13, fill=N["on_navy_sub"],
               anchor="end", ghost=N["ghost_navy"]))

    gap, vgap = 24, 20
    cw = (W - 2 * M - gap) / 2
    ch = 320
    pad = 26                                  # 칸 안쪽 여백
    acc = N["amber"]
    Y_HEAD, Y_RULE = 48, 90                   # 제목 줄 중심 · 구분선
    Y_FACT = (124, 156)                       # 사실 두 줄 중심
    Y_CHAIN = 212                             # 경로 중심
    Y_IMP, H_IMP = 252, 46                    # 영향 띠
    for i, it in enumerate(ISSUES):
        x = M + (i % 2) * (cw + gap)
        y = 120 + (i // 2) * (ch + vgap)
        g.append(R(x, y, cw, ch, r=12, fill=N["page"], stroke=N["line"]))

        # 제목 줄 — 번호 · 제목 (왼쪽) / 대표 수치 (오른쪽), 세로 중심 동일
        hy = y + Y_HEAD
        g.append(f'<circle cx="{x + pad + 17}" cy="{hy}" r="17" fill="{acc}"/>')
        g.append(TC(x + pad + 17, hy, it["no"], size=16, weight="700",
                    fill="#FFFFFF", keep=True))
        g.append(TC(x + pad + 46, hy, it["title"], size=22, weight="700",
                    fill=N["ink"], anchor="start"))

        fig_c = RED if it["fig"] == "Critical" else acc
        fw_ = est_w(it["fig"], 24)
        lw_ = est_w(it["fig_label"], 13)
        bw = 20 + fw_ + 14 + lw_ + 20
        bx = x + cw - pad - bw
        g.append(R(bx, hy - 24, bw, 48, r=10, fill=N["amber_soft"],
                   stroke=N["amber_line"]))
        g.append(TC(bx + 20, hy, it["fig"], size=24, weight="700",
                    fill=fig_c, anchor="start"))
        g.append(f'<path d="M {bx + 20 + fw_ + 7},{hy - 11} v 22" '
                 f'stroke="{N["amber_line"]}" stroke-width="1.2"/>')
        g.append(TC(bx + 20 + fw_ + 14, hy, it["fig_label"], size=13,
                    weight="700", fill=N["sub"], anchor="start"))

        g.append(f'<path d="M {x + pad},{y + Y_RULE} L {x + cw - pad},{y + Y_RULE}" '
                 f'stroke="{N["line"]}" stroke-width="1"/>')

        # 사실 두 줄
        for j, ln in enumerate(it["lines"]):
            ly = y + Y_FACT[j]
            g.append(f'<circle cx="{x + pad + 4}" cy="{ly}" r="2.8" '
                     f'fill="{acc}"/>')
            g.append(TC(x + pad + 16, ly, ln, size=15, fill=N["sub"],
                        anchor="start"))

        # 문제가 이어지는 경로 — 칸 폭을 꽉 채우도록 칩 폭을 늘린다
        cy_ = y + Y_CHAIN
        n = len(it["chain"])
        aw = 30                                    # 화살표 자리
        avail = cw - 2 * pad - aw * (n - 1)
        nat = [est_w(s, 14) + 24 for s in it["chain"]]
        extra = max(0.0, (avail - sum(nat)) / n)
        cx_ = x + pad
        for j, stp in enumerate(it["chain"]):
            end = j == n - 1
            w_ = nat[j] + extra
            g.append(R(cx_, cy_ - 18, w_, 36, r=18,
                       fill=RED if end else N["soft"],
                       stroke=None if end else N["line"], sw=1))
            g.append(TC(cx_ + w_ / 2, cy_, stp, size=14, weight="700",
                        fill="#FFFFFF" if end else N["ink"]))
            cx_ += w_
            if not end:
                g.append(f'<path d="M {cx_ + 7},{cy_} h 10" stroke="{N["mute"]}" '
                         f'stroke-width="2" stroke-linecap="round"/>')
                g.append(f'<path d="M {cx_ + 16},{cy_ - 5} L {cx_ + 23},{cy_} '
                         f'L {cx_ + 16},{cy_ + 5} Z" fill="{N["mute"]}"/>')
                cx_ += aw

        # 영향
        iy = y + Y_IMP
        g.append(R(x + pad, iy, cw - 2 * pad, H_IMP, r=9, fill=N["amber_soft"],
                   stroke=N["amber_line"]))
        g.append(TC(x + pad + 18, iy + H_IMP / 2, "영향", size=13, weight="700",
                    fill=acc, anchor="start"))
        g.append(f'<path d="M {x + pad + 60},{iy + 14} v {H_IMP - 28}" '
                 f'stroke="{N["amber_line"]}" stroke-width="1.2"/>')
        g.append(TC(x + pad + 74, iy + H_IMP / 2, it["impact"], size=15,
                    weight="700", fill=N["ink"], anchor="start"))

    ny = 120 + 2 * ch + vgap + 20
    g.append(R(M, ny, W - 2 * M, 48, r=9, fill=N["navy"]))
    g.append(f'<circle cx="{M + 28}" cy="{ny + 24}" r="10" fill="{acc}"/>')
    g.append(TC(M + 28, ny + 24, "!", size=14, weight="700", fill="#FFFFFF",
                keep=True))
    g.append(TC(M + 50, ny + 24, I_NOTE, size=15, weight="700",
                fill="#FFFFFF", anchor="start", ghost=N["ghost_navy"]))
    g.append(T(M, ny + 76, I_FOOTER_L, size=12.5, fill=N["mute"]))

    g.append('</svg>')
    return "".join(g)


def build_svg(page="overview"):
    if page == "effect":
        return build_effect()
    if page == "roadmap":
        return build_roadmap()
    if page == "flow":
        return build_flow()
    if page == "compare":
        return build_compare()
    if page == "issues":
        return build_issues()
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
    ap.add_argument("--page",
                    choices=["overview", "issues", "flow", "compare",
                             "effect", "roadmap", "all"],
                    default="all",
                    help="overview=구축 요약, flow=개선 전후 흐름, "
                         "effect=기대효과, roadmap=향후 계획 (기본은 전부)")
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

    pages = (["overview", "issues", "flow", "compare", "effect", "roadmap"]
             if args.page == "all"
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
