# -*- coding: utf-8 -*-
"""
build_info.py — 지금 돌고 있는 코드가 어느 시점 것인지 알려준다.

폐쇄망에서는 파일을 손으로 옮기다 일부 폴더(특히 routes/)가 예전 것으로
남는 일이 흔하다. 그러면 '분명히 고쳤는데 증상 그대로'가 되고, 디스크의
파일을 봐도 정말 그 코드가 '실행 중'인지는 알 수 없다.

그래서 검사 대상을 디스크가 아니라 '이미 import 된 모듈'로 잡는다.
inspect 로 실행 중인 모듈의 소스를 직접 읽으므로, 어느 경로에서 로드됐든
지금 돌고 있는 코드가 그 수정을 담고 있는지 확실하게 판정된다.
"""

BUILD = '2026-09-22 f / DIFF 자가진단 + 화면 버전 표시'

# (모듈명, 표식, 없으면 무엇이 안 고쳐진 것인지)
MARKERS = [
    ('routes.servers', "'updated': True",
     "서버 재등록이 갱신(upsert)으로 동작 — 없으면 '중복'으로 막힌다"),
    ('routes.servers', 'no-store',
     '서버 목록 캐시 금지'),
    ('routes.servers', "'stale': True",
     '낡은 화면 목록으로 엉뚱한 서버가 삭제되는 것 방지'),
    ('config_manager', 'os.replace',
     'config.json 원자적 저장'),
    ('config_manager', '_recover_from_backup',
     'config.json 자동 복구'),
    ('routes.search', 'diag_servers.py',
     '설정 손상 시 검색이 원인 표시'),
    ('ars_ssh_fetcher', 'ArsReadError',
     '원격 읽기 중단 감지'),
    ('ars_ssh_fetcher', 'while($t -lt $b.Length)',
     '짧은 읽기 보정'),
    ('ars_indexer', '_unseal_if_grown',
     '덜 읽힌 확정 파일 자동 재개'),
    ('ars_index_store', 'sealed_files',
     '확정분 전수 조회'),
    ('vgw_monitor', 'stop_ev',
     'VGW 모니터 정지 신호 분리'),
    ('scenario_deploy', 'ssh_identity',
     '시나리오 배포가 config.json 의 SSH 키를 사용 — 없으면 비밀번호를 묻는다'),
    ('scenario_deploy_diff', '_btn_key',
     '메뉴 버튼(BTNM)을 개별 비교 — 없으면 버튼 변경이 하나만 잡힌다'),
    ('scenario_deploy_diff', '_var_changes',
     '변수 단위 변경 + 블록 전문 + 연관 시나리오 (배포 리뷰용)'),
    ('scenario_deploy', 'REPORT_VERSION',
     '리포트 캐시 버전 — 없으면 분석기를 고쳐도 예전 리포트가 계속 나온다'),
    ('routes.search', 'PATTERN_BUDGET_SEC',
     '패턴 검색 제한 시간 + 병렬 — 없으면 서버 수만큼 시간이 곱해진다'),
    ('ssh_fetcher', 'with_filename',
     'AICC 패턴 검색 출처 파일 표시'),
]


def collect():
    """실행 중인 모듈을 검사해 (요약, 항목리스트) 반환."""
    import sys
    import os
    import inspect
    from datetime import datetime

    items, seen = [], {}
    for mod_name, marker, why in MARKERS:
        mod = sys.modules.get(mod_name)
        if mod is None:
            items.append({'module': mod_name, 'ok': None, 'why': why,
                          'detail': '아직 로드되지 않음'})
            continue

        if mod_name not in seen:
            try:
                seen[mod_name] = inspect.getsource(mod)
            except Exception as e:      # noqa: BLE001 - 진단용이라 원인만 남긴다
                seen[mod_name] = ''
                logger_detail = f'소스를 읽지 못함: {e}'
                items.append({'module': mod_name, 'ok': None, 'why': why,
                              'detail': logger_detail})
                continue
        src = seen[mod_name]

        path = getattr(mod, '__file__', '') or ''
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
        except OSError:
            mtime = '?'
        items.append({
            'module': mod_name,
            'file': path,
            'mtime': mtime,
            'ok': marker in src,
            'why': why,
        })

    stale = sorted({i['module'] for i in items if i['ok'] is False})
    return {
        'build': BUILD,
        'up_to_date': not stale,
        'stale_modules': stale,
        'items': items,
    }


def log_summary(logger):
    """기동 시 로그에 한 줄로 남긴다."""
    info = collect()
    logger.info("빌드: %s", info['build'])
    if info['up_to_date']:
        logger.info("코드 점검: 모든 모듈이 최신입니다")
    else:
        logger.warning("★ 코드 점검: 예전 파일이 남아 있습니다 — %s",
                       ', '.join(info['stale_modules']))
        for it in info['items']:
            if it.get('ok') is False:
                logger.warning("   %s (%s) — 빠짐: %s",
                               it['module'], it.get('file', ''), it['why'])
