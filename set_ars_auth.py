#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARS 네트워크 드라이브 접근 계정 등록 유틸리티.

평문 비밀번호를 입력받아 Windows DPAPI 로 암호화하여 ars_auth.json 에 저장합니다.

★ 매우 중요 ★
  반드시 "로그 분석 서비스가 실제로 구동되는 계정"으로 이 유틸을 실행하세요.
  DPAPI(user scope)는 암호화한 그 계정에서만 복호되기 때문입니다.

  - 서비스를 전용 로컬 계정으로 띄울 경우  → 그 계정으로 로그인해 실행
  - 서비스를 LocalSystem 으로 띄울 경우     → --machine 옵션 사용 권장
                                              (또는 psexec -s 로 SYSTEM 컨텍스트 실행)

사용 예:
  python set_ars_auth.py                     # user/비번 프롬프트, user scope
  python set_ars_auth.py --user ivradmin     # user 만 인자로
  python set_ars_auth.py --machine           # machine scope (LocalSystem 서비스용)
"""

import sys
import argparse
import getpass

import ars_auth_store as store


def main():
    parser = argparse.ArgumentParser(
        description='ARS 네트워크 드라이브 접근 계정 등록 (DPAPI 암호화)'
    )
    parser.add_argument('--user', help='ARS 접근 계정명 (미지정 시 프롬프트)')
    parser.add_argument('--machine', action='store_true',
                        help='machine scope 로 암호화 (LocalSystem 서비스용)')
    parser.add_argument('--file', help='저장 경로 (기본: ./ars_auth.json)')
    args = parser.parse_args()

    if not store._HAS_WIN32CRYPT:
        print('[오류] win32crypt(pywin32) 가 설치되어 있지 않습니다.')
        print('       pywin32-312-cp313-cp313-win_amd64.whl 반입/설치 후 다시 실행하세요.')
        sys.exit(1)

    print('=' * 60)
    print(' ARS 접근 계정 등록 (DPAPI 암호화)')
    print('=' * 60)
    print(f" 현재 실행 계정: {getpass.getuser()}")
    print(f" 암호화 scope : {'machine' if args.machine else 'user'}")
    print(' ※ 이 계정이 곧 서비스 구동 계정과 같아야 복호가 됩니다.')
    print('-' * 60)

    user = args.user or input('ARS 계정명: ').strip()
    if not user:
        print('[오류] 계정명을 입력하세요.')
        sys.exit(1)

    pw1 = getpass.getpass('비밀번호: ')
    pw2 = getpass.getpass('비밀번호 확인: ')
    if pw1 != pw2:
        print('[오류] 비밀번호가 일치하지 않습니다.')
        sys.exit(1)
    if not pw1:
        print('[오류] 비밀번호가 비어있습니다.')
        sys.exit(1)

    try:
        path = store.save_ars_auth(
            user, pw1, machine_scope=args.machine, path=args.file
        )
    except store.ArsAuthError as e:
        print(f'[오류] 저장 실패: {e}')
        sys.exit(1)

    # 저장 직후 복호 검증 (현재 계정에서 복호되는지 확인)
    try:
        creds = store.get_ars_credentials(path=args.file)
        ok = (creds['user'] == user and creds['password'] == pw1)
    except store.ArsAuthError as e:
        print(f'[경고] 저장은 됐으나 복호 검증 실패: {e}')
        sys.exit(1)

    print('-' * 60)
    print(f' 저장 완료: {path}')
    print(f' 복호 검증: {"성공" if ok else "실패"}')
    print('=' * 60)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
