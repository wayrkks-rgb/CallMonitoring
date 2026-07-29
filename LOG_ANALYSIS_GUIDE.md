# 로그 분석 시스템 - 종합 개발 가이드

> **대상**: 이 프로젝트를 처음 보는 개발자, 유지보수 담당자  
> **목적**: 구조 파악 + 실행 방법 + 수정 포인트를 한 곳에서 확인

---

## 목차

1. [이 시스템이 하는 일](#1-이-시스템이-하는-일)
2. [파일 구조](#2-파일-구조)
3. [핵심 개념 (기초 지식)](#3-핵심-개념-기초-지식)
4. [설치 및 실행](#4-설치-및-실행)
5. [코드 구조 설명](#5-코드-구조-설명)
6. [config.json 관리](#6-configjson-관리)
7. [API 엔드포인트 목록](#7-api-엔드포인트-목록)
8. [수정하는 방법](#8-수정하는-방법)
9. [문제 해결](#9-문제-해결)

---

## 1. 이 시스템이 하는 일

```
사용자가 웹 브라우저에서 고객 ID(custId)를 입력
          ↓
원격 서버에 SSH로 접속해서 로그 파일을 가져옴
          ↓
로그 안에서 custId를 찾아 세션 키(callSessionKey) 추출
          ↓
그 세션 키로 전체 통화 흐름(flow)을 시간순으로 수집
          ↓
결과를 웹 화면에 표시
```

**한 줄 요약**: 원격 서버의 VGW(Voice Gateway) API 로그에서 특정 고객의 통화 흐름을 추적하는 도구

---

## 2. 파일 구조

```
프로젝트 폴더/
│
├── app.py                  ← 핵심! 서버 코드 전체 (1012줄)
├── config.json             ← 서버 접속 정보 설정
├── requirements.txt        ← 필요한 Python 패키지 목록
│
├── templates/
│   └── index.html          ← 웹 화면 (HTML + JS)
│
├── static/
│   ├── css/fontawesome.css ← 아이콘 스타일
│   └── js/socket.io.js     ← 실시간 통신 라이브러리
│
└── logs/                   ← 자동 생성됨
    └── app.log             ← 실행 로그 기록
```

### 각 파일의 역할

| 파일 | 수정 필요성 | 설명 |
|------|-----------|------|
| `app.py` | 자주 | 모든 기능의 핵심. 서버 로직 전체 |
| `config.json` | 항상 | 어떤 서버에서 로그를 가져올지 설정 |
| `requirements.txt` | 거의 없음 | 패키지 추가할 때만 |
| `index.html` | 가끔 | UI 변경 시 |
| `fontawesome.css` / `socket.io.js` | 없음 | 라이브러리 파일, 건드리지 않음 |

---

## 3. 핵심 개념 (기초 지식)

처음 보는 분들을 위해 이 코드에서 쓰이는 핵심 개념을 설명합니다.

### 3.1 Flask란?

Python으로 웹 서버를 만드는 라이브러리입니다.

```python
from flask import Flask
app = Flask(__name__)

@app.route('/search', methods=['POST'])  # '/search' URL로 POST 요청이 오면
def search():                            # 이 함수가 실행됨
    return jsonify({'result': 'ok'})     # JSON 형태로 응답
```

브라우저에서 버튼을 누르면 → `/search` URL로 요청 → Flask 함수 실행 → 결과 반환

### 3.2 SSH란?

원격 서버에 네트워크로 접속하는 방법입니다.  
이 프로젝트는 **Windows에 내장된 OpenSSH**를 사용합니다 (`paramiko` 라이브러리 불필요).

```python
# 코드 내부에서 이런 식으로 SSH 명령을 실행합니다
subprocess.run(['ssh', 'server1', 'cat /hli_app/log/.../api_2025-01-15.log'])
#                       ↑               ↑
#              SSH Config의 별칭    원격 서버에서 실행할 명령
```

SSH를 쓰려면 **SSH 키 인증**이 설정되어 있어야 합니다 (비밀번호 없이 자동 접속).

### 3.3 SSH Config란?

`C:\Users\사용자명\.ssh\config` 파일에 서버 접속 정보를 저장해두면, `ssh server1`처럼 짧게 접속할 수 있습니다.

```
# 이 설정이 있으면:
Host server1
    HostName 10.19.17.194
    User loguser
    IdentityFile ~/.ssh/id_rsa

# 이렇게 짧게 접속 가능:
ssh server1   (= ssh loguser@10.19.17.194 -i ~/.ssh/id_rsa)
```

### 3.4 정규식(Regex)이란?

로그 파일에서 특정 패턴을 찾는 방법입니다.

```python
# 예: 로그 한 줄에서 callSessionKey 값 추출
line = '{"custId":"12345","callSessionKey":"abc-def-789","type":"INVITE"}'

pattern = r'"callSessionKey"\s*:\s*"([^"]+)"'
#                                    ↑
#                              이 부분을 캡처 (괄호 안)

match = re.search(pattern, line)
session_key = match.group(1)  # → "abc-def-789"
```

### 3.5 JSON이란?

데이터를 주고받는 형식입니다. 이 프로젝트에서 두 곳에 쓰입니다.

```json
// config.json - 서버 설정 저장
{"remote_servers": [{"hostname": "server1", "log_paths": [...]}]}

// API 응답 - 브라우저에 결과 전달
{"success": true, "cust_id": "12345", "flow_results": [...]}
```

---

## 4. 설치 및 실행

### 4.1 전제 조건

- Windows 10/11 (OpenSSH 내장)
- Python 3.7 이상

### 4.2 최초 1회 설정 (SSH 키 설정)

#### Step 1: OpenSSH 확인
```powershell
ssh -V
# 출력 예: OpenSSH_for_Windows_8.1p1
```
없으면:
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

#### Step 2: SSH 키 생성
```powershell
ssh-keygen -t rsa -b 4096
# 모든 질문에 그냥 Enter
```
생성 위치: `C:\Users\사용자명\.ssh\id_rsa` (비밀키), `id_rsa.pub` (공개키)

#### Step 3: 원격 서버에 공개키 등록
```powershell
ssh-copy-id loguser@10.19.17.194   # server1
ssh-copy-id loguser@10.19.17.197   # server2

# ssh-copy-id가 없으면:
type $env:USERPROFILE\.ssh\id_rsa.pub | ssh loguser@10.19.17.194 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

#### Step 4: SSH Config 파일 작성
`C:\Users\사용자명\.ssh\config` 파일 생성 (없으면 새로 만들기):

```
Host server1
    HostName 10.19.17.194
    User loguser
    IdentityFile ~/.ssh/id_rsa
    ServerAliveInterval 60

Host server2
    HostName 10.19.17.197
    User loguser
    IdentityFile ~/.ssh/id_rsa
    ServerAliveInterval 60
```

#### Step 5: 접속 테스트
```powershell
ssh server1   # 비밀번호 없이 바로 접속되면 성공
```

### 4.3 config.json 설정

```json
{
  "remote_servers": [
    {
      "hostname": "server1",
      "enabled": true,
      "log_paths": [
        "/hli_app/log/vgw/isac_vgw_ob1/vgw_api/log"
      ]
    },
    {
      "hostname": "server2",
      "enabled": true,
      "log_paths": [
        "/hli_app/log/vgw/isac_vgw_ob1/vgw_api/log",
        "/hli_app/log/vgw/isac_vgw_ob2/vgw_api/log"
      ]
    }
  ]
}
```

> **주의**: `hostname`은 SSH Config에서 정의한 `Host` 이름과 일치해야 합니다.  
> IP 직접 사용 시 `"ip"`, `"user"` 필드를 함께 입력합니다.

### 4.4 패키지 설치 및 실행

```powershell
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://localhost:5000` 접속

---

## 5. 코드 구조 설명

`app.py`는 크게 4개 부분으로 나뉩니다.

### 5.1 전역 설정 (1~70번째 줄)

```python
HTTP_PORT = int(os.environ.get('HTTP_PORT', 5000))   # 포트 번호 (기본 5000)
LOG_DIR = Path(__file__).parent / 'logs'              # 로그 저장 폴더
BASE_DIR = Path(__file__).parent                      # 프로젝트 폴더

TOTAL_SEARCHES = 0    # 총 검색 횟수 (통계용)
SEARCH_RUNNING = False # 현재 검색 중인지 여부
```

### 5.2 OpenSSHLogFetcher 클래스 (129~280번째 줄)

원격 서버에서 로그를 가져오는 역할입니다.

```
OpenSSHLogFetcher
├── __init__()              OpenSSH 설치 여부 확인
├── fetch_log_from_remote() 특정 서버/경로/날짜의 로그 가져오기
├── _build_ssh_target()     접속 대상 문자열 구성 (hostname or user@ip)
└── _execute_ssh_command()  실제 SSH 명령 실행 (subprocess 사용)
```

**로그 파일 찾는 방식**:
- 날짜 지정 시: `cat /경로/api_2025-01-15.log` 또는 `.gz` 압축 파일도 처리
- 날짜 미지정 시: 가장 최신 로그 파일 자동 선택

**에러 종류** (`ErrorType` Enum):

| 에러 | 원인 |
|------|------|
| SSH_AUTH_ERROR | 공개키 인증 실패 (`ssh-copy-id` 재실행 필요) |
| NETWORK_ERROR | 서버에 연결 거부됨 |
| TIMEOUT_ERROR | 30초 내 응답 없음 |
| FILE_NOT_FOUND | 해당 날짜 로그 파일 없음 |
| SSH_NOT_FOUND | Windows에 OpenSSH 미설치 |

### 5.3 LogSearcher 클래스 (282~528번째 줄)

가져온 로그에서 실제 검색을 수행합니다.

```
LogSearcher (검색 시작 시 생성됨)
├── __init__(search_date)       날짜 설정, 로그 데이터 로드 시작
├── _load_log_data()            config.json 읽어서 모든 서버 로그를 메모리에 로드
│                                → self.log_data = {"server1:/경로": [로그줄들, ...]}
├── _find_custid_lines()        메모리에서 custId 포함 줄 검색
├── _extract_session_keys()     찾은 줄에서 callSessionKey 추출
├── _find_session_flow()        세션 키로 전체 플로우 검색
├── _sort_by_timestamp()        시간순 정렬
└── search_by_custid_flow()     위 과정을 순서대로 실행하는 메인 함수
```

**검색 흐름 상세**:

```
1. _load_log_data()
   - config.json에서 서버 목록 읽기
   - 각 서버 × 각 log_path마다 SSH로 로그 가져오기
   - self.log_data 딕셔너리에 저장

2. _find_custid_lines(cust_id)
   - self.log_data의 모든 줄 스캔
   - '"custId":"12345"' 또는 'custId=12345' 패턴 검색

3. _extract_session_keys(custid_lines)
   - 찾은 줄들에서 callSessionKey 값 추출
   - 정규식: r'"callSessionKey"\s*:\s*"([^"]+)"'

4. _find_session_flow(session_key)
   - 세션 키가 포함된 모든 줄 수집 (전체 통화 흐름)

5. _sort_by_timestamp()
   - 타임스탬프 기준 시간순 정렬
```

### 5.4 Flask 라우트 (531번째 줄 이후)

웹 요청을 받아 처리합니다.

```
주요 함수:
├── load_config()       config.json 읽기
├── save_config()       config.json 쓰기 (자동 백업 포함)
├── backup_config()     config.json.backup_날짜시간 파일 생성
├── validate_ip_address()  IP 형식 검증
└── validate_server_data() 서버 데이터 유효성 검사
```

---

## 6. config.json 관리

### 6.1 필드 설명

```json
{
  "remote_servers": [
    {
      "hostname": "server1",     // SSH Config의 Host 이름 (이걸 쓰면 아래 필드 불필요)
      "ip": "",                  // 직접 IP 지정 시 (hostname 대신 사용)
      "user": "loguser",         // SSH 접속 사용자 (ip 사용 시 필요)
      "ssh_port": 22,            // SSH 포트 (기본 22)
      "ssh_key_path": null,      // 키 파일 경로 (SSH Config 사용 시 불필요)
      "enabled": true,           // false로 하면 이 서버는 검색에서 제외
      "log_paths": [             // 로그가 있는 원격 경로들 (여러 개 가능)
        "/hli_app/log/vgw/isac_vgw_ob1/vgw_api/log"
      ]
    }
  ],
  "server": {
    "http_port": 5000            // 웹 서버 포트
  }
}
```

### 6.2 서버 추가 방법

**방법 A: 웹 UI에서 추가** (권장)
1. `http://localhost:5000` 접속
2. 서버 관리 메뉴에서 서버 정보 입력
3. 저장 → `config.json` 자동 업데이트

**방법 B: 직접 파일 편집**
```json
{
  "remote_servers": [
    {...기존 서버...},
    {
      "hostname": "server3",
      "enabled": true,
      "log_paths": ["/hli_app/log/vgw/isac_vgw_dev2/vgw_api/log"]
    }
  ]
}
```

### 6.3 자동 백업

`save_config()` 함수가 호출될 때마다 자동으로 백업이 생성됩니다.

```
config.json.backup_20250116_143025   ← 날짜_시간 형식
config.json.backup_20250116_152341
```

백업 파일이 많아지면 주기적으로 오래된 것은 삭제해도 됩니다.

---

## 7. API 엔드포인트 목록

웹 화면(index.html)과 서버(app.py)가 통신하는 방식입니다.

| 메서드 | URL | 기능 |
|--------|-----|------|
| GET | `/` | 메인 웹 페이지 |
| POST | `/search` | custId 검색 (핵심 기능) |
| GET | `/servers` | 서버 목록 조회 |
| POST | `/servers` | 서버 추가 |
| PUT | `/servers/<id>` | 서버 정보 수정 |
| DELETE | `/servers/<id>` | 서버 삭제 |
| GET | `/servers/<id>/log-paths` | 서버의 로그 경로 목록 |
| POST | `/servers/<id>/log-paths` | 로그 경로 추가 |
| DELETE | `/servers/<id>/log-paths` | 로그 경로 삭제 |
| GET | `/system/stats` | CPU/메모리 등 시스템 상태 |
| GET | `/system-stats` | 시스템 상태 (구버전 경로, 동일 기능) |
| GET | `/config` | config.json 내용 조회 |

> **참고**: `/system/stats`와 `/system-stats` 두 경로가 모두 존재합니다. 기존 호환성을 위해 둘 다 유지하고 있습니다.

### 검색 API 예시

**요청**:
```json
POST /search
{
  "custId": "1234567890",
  "date": "2025-01-15"   // 생략 시 최신 로그 검색
}
```

**응답**:
```json
{
  "success": true,
  "cust_id": "1234567890",
  "session_count": 1,
  "flow_results": [
    {
      "session_key": "abc-def-789",
      "line_count": 42,
      "flow_lines": [
        {
          "line": "2025-01-15 09:00:01.123 ...",
          "file_path": "server1:/hli_app/log/...",
          "line_number": 1024,
          "timestamp": "2025-01-15 09:00:01.123"
        }
      ]
    }
  ]
}
```

---

## 8. 수정하는 방법

### 8.1 로그 경로 패턴 변경

로그 파일 이름 형식이 `api_YYYY-MM-DD.log`가 아닌 경우:

```python
# app.py의 fetch_log_from_remote() 함수 안 (149번째 줄 근처)

# 현재:
log_file = f'{log_path}/api_{date}.log'

# 변경 예시 (파일 이름 형식이 다를 때):
log_file = f'{log_path}/vgw_{date}.log'
```

### 8.2 custId 검색 패턴 변경

로그 형식이 달라졌을 때:

```python
# app.py의 _find_custid_lines() 함수 안 (360번째 줄 근처)

# 현재 (두 가지 패턴 지원):
if f'"custId":"{cust_id}"' in line or f'custId={cust_id}' in line:

# 패턴 추가 예시:
if f'"custId":"{cust_id}"' in line or f'custId={cust_id}' in line or f'cust_id={cust_id}' in line:
```

### 8.3 세션 키 추출 패턴 변경

```python
# app.py의 _extract_session_keys() 함수 안 (405번째 줄 근처)

# 현재 패턴들:
patterns = [
    r'"callSessionKey"\s*:\s*"([^"]+)"',   # JSON 형식
    r'callSessionKey=([a-zA-Z0-9\-_:]+)'   # key=value 형식
]

# 패턴 추가 예시:
patterns = [
    r'"callSessionKey"\s*:\s*"([^"]+)"',
    r'callSessionKey=([a-zA-Z0-9\-_:]+)',
    r'sessionId=([a-zA-Z0-9\-_:]+)'        # 새로운 형식 추가
]
```

### 8.4 SSH 타임아웃 변경

연결이 자꾸 끊기거나 느린 서버가 있을 때:

```python
# app.py의 _execute_ssh_command() 함수 안 (192번째 줄 근처)

# ConnectTimeout: 연결 시도 대기 시간 (초)
'-o', 'ConnectTimeout=10',   # 10 → 20으로 늘리기

# subprocess timeout: 명령 실행 최대 시간 (초)
result = subprocess.run(..., timeout=30)  # 30 → 60으로 늘리기
```

### 8.5 포트 변경

5000번 포트가 이미 사용 중일 때:

```json
// config.json
{
  "server": {
    "http_port": 8080
  }
}
```

또는 환경변수로:
```powershell
$env:HTTP_PORT = "8080"
python app.py
```

---

## 9. 문제 해결

### 9.1 SSH 관련

**비밀번호를 물어볼 때**
```
원인: 공개키 인증이 안 됨
해결:
  ssh-copy-id loguser@서버IP
  또는
  ssh -v server1  # 어디서 막히는지 확인
```

**"Could not resolve hostname server1"**
```
원인: SSH Config 파일이 없거나 Host 이름이 다름
해결:
  1. C:\Users\사용자명\.ssh\config 파일 존재 여부 확인
  2. config 파일의 Host 이름과 config.json의 hostname 일치 여부 확인
```

**"Connection refused"**
```
원인: 서버가 꺼져있거나 방화벽 차단
해결:
  ping 10.19.17.194  # 서버 응답 확인
  ssh -p 22 loguser@10.19.17.194  # 포트 직접 지정 시도
```

### 9.2 앱 실행 관련

**"ModuleNotFoundError"**
```powershell
pip install -r requirements.txt
```

**"Address already in use" (포트 충돌)**
```powershell
# 현재 5000 포트 사용 프로세스 찾기
netstat -ano | findstr :5000

# 또는 다른 포트로 실행
$env:HTTP_PORT = "5001"
python app.py
```

**"OpenSSH 미설치" 경고**
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
# 설치 후 PowerShell 재시작
```

### 9.3 검색 관련

**"로그 데이터를 불러올 수 없음"**
```
체크 목록:
  □ ssh server1  접속 되는지 확인
  □ config.json의 log_paths 경로가 실제 서버에 존재하는지 확인
  □ ssh server1 "ls /hli_app/log/.../vgw_api/log"  경로 확인
  □ logs/app.log 파일에서 오류 메시지 확인
```

**"custId를 찾을 수 없음"**
```
체크 목록:
  □ 날짜가 맞는지 확인 (YYYY-MM-DD 형식)
  □ 해당 날짜의 로그 파일이 존재하는지 확인:
    ssh server1 "ls /hli_app/log/.../vgw_api/log/api_2025-01-15*"
  □ custId 형식 확인 (따옴표, 특수문자 주의)
```

### 9.4 로그 확인

실행 중 문제가 생기면 `logs/app.log` 파일을 확인하세요.

```powershell
# 실시간 로그 보기
Get-Content logs\app.log -Wait

# 최근 50줄만 보기
Get-Content logs\app.log -Tail 50
```

---

## 불필요해진 문서 목록 (삭제 가능)

이 가이드 하나로 대체되었기 때문에 아래 파일들은 삭제해도 됩니다.

| 파일명 | 이유 |
|--------|------|
| `CHANGES_SUMMARY.md` | v1→v2 변경 이력. 현재 app.py가 이미 v2 완성 버전 |
| `OPENSSH_FINAL_GUIDE.md` | 이 문서의 설치 섹션으로 통합됨 |
| `OPENSSH_SETUP.md` | 동일 내용 통합됨 |
| `QUICK_START.md` | 이 문서의 4장으로 통합됨 |
| `SSH_CONFIG_GUIDE.md` | 핵심 내용 이 문서에 포함됨 |
| `PROJECT_INSPECTION_REPORT.md` | 과거 분석 보고서. 현재 코드와 일부 불일치 (API 누락 문제는 이미 해결됨) |
| `PROJECT_SUMMARY.md` | paramiko vs OpenSSH 비교 내용. 이미 OpenSSH로 전환 완료 |
| `VERIFICATION_REPORT.md` | 검증 보고서. 개발 당시 참고용 |
| `COMPREHENSIVE_DEVELOPMENT_GUIDE.md` | 이 문서로 대체 |

**유지할 문서**:
- `README.md` - 프로젝트 소개용 (외부 공유 시 유용)
- 이 파일 (`LOG_ANALYSIS_GUIDE.md`) - 실제 개발/유지보수용
