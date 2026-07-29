# Windows 로그 분석 시스템 (OpenSSH 네이티브 버전)

## 🚀 핵심 특징

**Windows OpenSSH를 직접 사용**하여 paramiko 없이 원격 로그를 수집합니다.

### ✨ 주요 장점
- ✅ **의존성 최소화**: paramiko, cryptography 불필요
- ✅ **SSH Config 활용**: 서버 설정 간소화 (1줄로 완료)
- ✅ **자동 키 등록**: `ssh-copy-id`로 1줄에 완료
- ✅ **Windows 네이티브**: OS 내장 기능 사용
- ✅ **안정성 향상**: 표준 SSH 클라이언트 사용

---

## 📋 빠른 시작 (5분)

### 전제 조건
- Windows 10 1809 이상
- Python 3.7 이상

### 1. OpenSSH 설치 확인
```powershell
ssh -V
# 출력: OpenSSH_for_Windows_8.1p1
```

설치되지 않았다면:
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

### 2. SSH 키 생성 및 등록
```powershell
# 키 생성
ssh-keygen -t rsa -b 4096

# 공개키 등록 (각 서버)
ssh-copy-id loguser@10.19.17.194
ssh-copy-id loguser@10.19.17.197
ssh-copy-id loguser@10.10.99.55
```

### 3. SSH Config 설정
```powershell
notepad C:\Users\yourname\.ssh\config
```

```
Host server1
    HostName 10.19.17.194
    User loguser
    IdentityFile ~/.ssh/id_rsa

Host server2
    HostName 10.19.17.197
    User loguser
    IdentityFile ~/.ssh/id_rsa

Host server3
    HostName 10.10.99.55
    User loguser
    IdentityFile ~/.ssh/id_rsa
```

### 4. config.json 설정
```json
{
  "remote_servers": [
    {
      "hostname": "server1",
      "enabled": true,
      "log_paths": ["/hli_app/log/vgw/.../log"]
    },
    {
      "hostname": "server2",
      "enabled": true,
      "log_paths": ["/hli_app/log/vgw/.../log"]
    }
  ]
}
```

### 5. 실행
```powershell
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://localhost:5000` 접속

---

## 📁 프로젝트 구조

```
log_analysis_openssh/
├── app.py                      # Flask 앱 (OpenSSH 사용)
├── requirements.txt            # 의존성 (paramiko 제외)
├── config.json                 # 서버 설정 (hostname만!)
├── start.bat                   # Windows 실행 스크립트
├── .gitignore
│
├── docs/
│   ├── OPENSSH_SETUP.md       # OpenSSH 설치 가이드
│   ├── SSH_CONFIG_GUIDE.md    # SSH Config 상세 가이드
│   └── QUICK_START.md         # 5분 빠른 시작
│
└── logs/
    └── app.log                 # 애플리케이션 로그
```

---

## 🎯 config.json 설정 방법

### 방법 1: SSH Config 활용 (권장) ⭐

**config.json:**
```json
{
  "remote_servers": [
    {
      "hostname": "server1",  // SSH Config의 Host 이름만 입력
      "log_paths": ["/path/to/log"]
    }
  ]
}
```

**장점:**
- IP, User, 키 경로 모두 불필요
- 서버 변경 시 SSH Config만 수정
- SSH 명령어로도 바로 접속 가능

### 방법 2: 직접 지정 (SSH Config 미사용)

```json
{
  "remote_servers": [
    {
      "ip": "10.19.17.194",
      "user": "loguser",
      "ssh_port": 22,
      "log_paths": ["/path/to/log"]
    }
  ]
}
```

---

## 🔍 주요 기능

### 1. custId 기반 로그 검색
```
사용자 입력: custId="12345"
    ↓
① OpenSSH로 원격 로그 수집
    ↓
② custId 찾기
    ↓
③ callSessionKey 추출
    ↓
④ 전체 플로우 검색
    ↓
⑤ 시간순 정렬 후 반환
```

### 2. 다중 서버 지원
- 여러 서버 동시 검색
- 각 서버별 에러 처리
- 병렬 처리 가능

### 3. 에러 처리
- SSH 인증 실패
- 네트워크 오류
- 타임아웃
- 파일 없음
- 각 에러별 상세 메시지

---

## 🔧 명령줄 테스트

### SSH 연결 테스트
```powershell
# SSH Config 사용
ssh server1

# 또는 직접 접속
ssh loguser@10.19.17.194
```

### 로그 파일 확인
```powershell
# SSH로 파일 목록 확인
ssh server1 "ls /hli_app/log/vgw/*/vgw_api/log/"

# 로그 내용 미리보기
ssh server1 "head /hli_app/log/vgw/.../log/api_2024-01-15.log"
```

### 연결 디버깅
```powershell
# 상세 로그 출력
ssh -v server1

# 더 상세한 로그
ssh -vv server1
```

---

## ⚠️ 문제 해결

### OpenSSH가 설치되지 않음
```powershell
# 확인
ssh -V

# 설치
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

### SSH 인증 실패
```powershell
# 공개키 재등록
ssh-copy-id loguser@10.19.17.194

# 또는 수동 등록
type $env:USERPROFILE\.ssh\id_rsa.pub | ssh loguser@10.19.17.194 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

### SSH Config를 읽지 못함
```powershell
# 파일 확인
dir C:\Users\yourname\.ssh\config

# 생성
notepad $env:USERPROFILE\.ssh\config
```

---

## 📊 Paramiko vs OpenSSH 비교

| 항목 | Paramiko 방식 | OpenSSH 방식 |
|------|---------------|--------------|
| **의존성** | paramiko + cryptography | 없음 (OS 내장) |
| **설정 복잡도** | 높음 (5개 항목) | 낮음 (1개 항목) |
| **키 등록** | 수동 9단계 | 1줄 (`ssh-copy-id`) |
| **디버깅** | Python 로그 | `ssh -v` |
| **성능** | 보통 | 빠름 (네이티브) |
| **안정성** | 라이브러리 의존 | OS 네이티브 |
| **서버 추가** | config.json 5줄 | SSH Config + config.json 각 1줄 |

**결론: OpenSSH 방식이 모든 면에서 우수** ✅

---

## 📚 상세 문서

- **설치 가이드**: [docs/OPENSSH_SETUP.md](docs/OPENSSH_SETUP.md)
- **SSH Config**: [docs/SSH_CONFIG_GUIDE.md](docs/SSH_CONFIG_GUIDE.md)
- **빠른 시작**: [docs/QUICK_START.md](docs/QUICK_START.md)

---

## 🆘 지원

**문제 발생 시:**
1. `logs/app.log` 확인
2. SSH 연결 테스트: `ssh server1`
3. 상세 로그: `ssh -vv server1`
4. 에러 메시지와 함께 문의

---

## 📝 변경 이력

### v3.0.0 (2025-01-14) - OpenSSH 네이티브
- ✅ paramiko 제거, Windows OpenSSH 사용
- ✅ SSH Config 완벽 지원
- ✅ ssh-copy-id 자동 등록
- ✅ 설정 간소화 (hostname만 필요)
- ✅ 성능 및 안정성 향상

### v2.0.0 - SSH/SFTP 버전
- paramiko 사용
- SSH 키 인증 지원

### v1.0.0 - 초기 버전
- 로컬 파일만 지원

---

**Last Updated**: 2025-01-14  
**Version**: 3.0.0 (OpenSSH Native)  
**License**: MIT
