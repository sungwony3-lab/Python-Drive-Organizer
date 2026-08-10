# Python Drive Organizer 개발지침

## 1. 프로젝트 개요

**프로젝트명:** Python Drive Organizer

Google Drive의 대량 파일과 폴더를 Python 기반으로 효율적으로 관리하는 경량 자동화 시스템을 구축한다.

기존 AI Drive Organizer의 Spark + Google Sheet 중심 구조는 폐기한다.  
새 프로젝트는 **Python + Google Drive API + SQLite**를 기본 구조로 사용한다.

기본 구조:

Google Drive  
→ Google Drive API  
→ Python  
→ SQLite  
→ 검색 / 분석 / 작업계획 / 실행

AI는 시스템의 중심 엔진이 아니다.  
규칙으로 처리 가능한 작업은 Python이 담당하고, 향후 AI가 필요한 경우에도 Python이 먼저 대상을 최소 범위로 줄인 뒤 애매한 판단에만 AI를 사용한다.

---

## 2. 프로젝트 기본 목표

프로젝트는 크게 **1차 목표**와 **2차 목표**로 분리한다.

### 1차 목표 — Drive Index & Analysis

Google Drive의 현재 상태를 Python + SQLite에 정확하게 동기화하고, 파일명과 메타데이터를 기반으로 검색·비교·분류 후보를 만들 수 있는 읽기 중심 기반 시스템을 완성한다.

포함 기능:

- Google Drive API 인증
- 파일/폴더 메타데이터 조회
- SQLite 저장
- 증분 동기화
- 변경 감지
- 파일명 Parser
- Revision / Copy 분석
- 파일 그룹화
- 검색
- 보고

**1차 목표에서는 Google Drive의 실제 파일이나 폴더를 변경하지 않는다.**

### 2차 목표 — Drive Execution

1차 목표에서 만들어진 데이터와 작업계획을 기반으로, 사용자가 검토한 작업만 Google Drive에 실제 반영하는 실행 시스템을 구축한다.

포함 기능:

- 작업계획 생성
- Dry Run
- 파일/폴더 이름 변경
- 파일 이동
- 폴더 이동
- 파일 복사
- 폴더 복사
- 폴더 생성
- 휴지통 이동
- 실행 로그
- 오류 복구
- 가능한 작업의 Undo 지원
- 대규모 배치 실행 및 재시도

**영구 삭제는 초기 범위에 포함하지 않으며, 별도 필요성이 확인된 후 마지막 단계에서 검토한다.**

---

## 3. 기본 기술 구조

기본 기술은 다음으로 제한한다.

- Python
- Google Drive API v3
- SQLite
- Git / GitHub
- Codex

기본 구조에 다음은 포함하지 않는다.

- Google Spark
- Google Sheet를 핵심 데이터베이스로 사용하는 구조
- Apps Script
- Cloudflare Worker
- 불필요한 별도 서버
- 불필요한 중간 API 계층

필요성이 검증되지 않은 기술이나 서비스를 미리 추가하지 않는다.

---

## 4. 개발 기본 원칙

1. 최대한 단순하고 가벼운 구조를 유지한다.

2. AI가 필요하지 않은 작업에는 AI를 사용하지 않는다.

3. 파일 목록 조회, 문자열 분석, Revision 파싱, 변경 감지, 그룹화, 검색 등은 Python의 결정론적 규칙으로 처리한다.

4. 향후 AI 판단 기능을 도입하더라도 전체 데이터를 AI에 전달하지 않는다. Python과 SQLite가 먼저 후보 범위를 최소화한 뒤 필요한 데이터만 AI에 전달한다.

5. 기본 파일 분석 범위는 파일 내부 내용이 아니라 **파일명과 메타데이터**이다.

6. 주요 메타데이터는 다음을 포함한다.
   - fileId
   - 파일명
   - 폴더 ID
   - 전체 경로
   - MIME type
   - 확장자
   - 파일 크기
   - 생성일
   - 수정일
   - MD5 checksum
   - 휴지통 상태
   - 기타 Google Drive API가 제공하는 필요한 메타데이터

7. Google Drive의 **fileId를 파일과 폴더의 핵심 고유 식별자**로 사용한다.

8. 같은 파일명이라도 서로 다른 폴더에 존재하면 기본적으로 별개의 파일로 취급한다.

9. 폴더의 마지막 이름이 `_완료`로 끝나는 경우 해당 폴더와 모든 하위 폴더는 기본 분석/정리 대상에서 제외할 수 있도록 상태값을 기록한다. 단, 사용자가 요청하면 포함할 수 있어야 한다.

10. SQLite는 현재 Google Drive 상태를 나타내는 주 데이터베이스로 사용한다. 동일 파일을 스캔할 때마다 새 행을 누적하지 않는다.

11. 증분 동기화 기본 원칙:
   - 신규 파일 → INSERT
   - 변경 파일 → UPDATE
   - Drive에서 사라진 파일 → 현재 상태에 맞게 DELETE 또는 상태 갱신
   - 변경 없는 파일 → SKIP

12. 실제 Drive 변경은 2차 목표의 실행기에서만 수행한다.

---

## 5. MVP 개발 원칙

각 목표는 여러 개의 작은 MVP로 나누어 구현한다.

한 MVP에서는 **하나의 핵심 기능만 구현**한다.

기본 진행 순서:

설계  
→ Codex 구현  
→ 테스트  
→ ChatGPT 검토  
→ 성공 조건 확인  
→ MVP 완료  
→ 다음 MVP

이전 MVP가 완료되지 않은 상태에서 다음 MVP의 기능을 섞어서 구현하지 않는다.

각 MVP에는 반드시 명확한 성공 조건이 있어야 한다.

---

## 6. 역할 분담

### 사용자

- 프로젝트 목표 결정
- 실제 실행 및 테스트
- 결과 확인
- 다음 단계 승인

### ChatGPT

- 전체 구조 설계
- MVP 범위 결정
- 데이터 구조 검토
- 보안 검토
- 코드 리뷰
- 오류 분석
- Codex 작업지침 작성
- 테스트 결과 검토
- 다음 단계 결정

**ChatGPT는 실제 프로젝트 코드 작성·수정을 담당하지 않는다.**

### Codex

- Python 코드 작성
- 코드 수정
- 테스트 코드 작성
- 리팩터링
- 프로젝트 파일 관리

### Python 프로그램

- Google Drive API 통신
- 메타데이터 수집
- SQLite 처리
- 파일명 Parser
- Revision / Copy 분석
- 그룹화
- 검색
- 작업계획 생성
- 2차 목표에서 실제 Drive 변경 실행

---

## 7. 코드 구조 원칙

기능별로 파일을 분리한다.

권장 예시:

- `main.py`
- `drive_client.py`
- `database.py`
- `scanner.py`
- `synchronizer.py`
- `name_parser.py`
- `grouper.py`
- `planner.py`
- `executor.py`
- `reporter.py`

한 파일에 모든 기능을 몰아넣지 않는다.

OAuth 인증정보, 비밀키, 경로 등 환경별 설정은 코드에 직접 하드코딩하지 않는다.

---

## 8. SQLite 기본 원칙

SQLite는 단순 로그 파일이 아니라 **현재 Drive 상태를 관리하는 주 데이터베이스**로 사용한다.

### 1차 목표의 최소 테이블

- `files`
- `folders`
- `file_groups`
- `scan_state`

### 2차 목표에서 추가할 테이블

- `operation_jobs`
- `operation_items`
- `operation_logs`

필요한 필드와 인덱스는 각 MVP에서 확정한다.

---

## 9. 파일명 분석 원칙

파일명 분석은 결정론적 규칙으로 수행한다.

예:

- `R1`
- `R.2`
- `REV03`
- `REV.04`

와 같이 Revision을 명시하는 표현은 Revision 정보로 파싱할 수 있다.

반면:

- `테스트1.pdf`
- `테스트2.pdf`
- `테스트3.pdf`

처럼 파일명 끝에 숫자만 있는 경우 그 숫자를 자동으로 Revision 번호로 확정하지 않는다.

Copy 관련 표현 역시 기계적으로 추출할 수 있다.

예:

- 복사본
- copy
- `(1)`
- `(2)`

Parser는 구조를 기록할 뿐 최종 의미를 임의로 추측하지 않는다.

---

## 10. 안전 원칙

읽기 기능과 쓰기 기능을 명확히 분리한다.

### 1차 목표

읽기 전용을 기본으로 한다.

### 2차 목표

실제 Drive 변경 작업은 반드시 다음 순서를 따른다.

PLAN  
→ DRY RUN  
→ CONFIRM  
→ EXECUTE  
→ VERIFY  
→ LOG

대규모 작업은 파일명만으로 실행하지 않는다.

실행 전에 대상 `fileId` 목록과 작업 내용을 고정한다.

---

## 11. 대규모 작업 원칙

대규모 이름 변경, 이동, 복사, 휴지통 이동은 항목별 상태를 추적한다.

각 작업에는 `operationId` 또는 `jobId`를 부여한다.

항목별 상태 예:

- PENDING
- RUNNING
- SUCCESS
- FAILED

일부 항목이 실패해도 전체 작업 결과를 구분할 수 있어야 한다.

Google Drive API의 일시적인 Rate Limit 또는 서버 오류는 재시도 정책을 적용한다.

무한 재시도는 금지한다.

---

## 12. 삭제 원칙

초기 버전에서는 영구 삭제 기능을 구현하지 않는다.

파일 제거가 필요한 경우 기본 방식은 **휴지통 이동**으로 한다.

영구 삭제는 별도의 MVP에서 필요성, 보안, 복구 가능성을 검토한 후 결정한다.

---

## 13. Undo / 복구 원칙

Google Drive 전체 작업에 자동 트랜잭션 롤백이 있다고 가정하지 않는다.

2차 목표에서 가능한 작업은 실행 전 원래 상태를 기록한다.

예:

- 이름 변경 → 기존 파일명 기록
- 이동 → 기존 parentFolderId 기록
- 휴지통 이동 → 원래 상태 기록

이를 기반으로 가능한 범위에서 Undo 기능을 제공한다.

---

## 14. 기존 프로젝트 활용 원칙

기존 AI Drive Organizer의 코드와 아키텍처는 새 프로젝트의 기반 코드로 사용하지 않는다.

필요한 **개념과 규칙만 참고**할 수 있다.

참고 가능:

- Revision Parser 규칙
- Copy Parser 규칙
- groupKey 개념
- `_완료` 폴더 처리 개념
- scanId 개념
- 실행 로그 개념

다음 기존 구조는 그대로 가져오지 않는다.

- Spark 중심 구조
- Google Sheet 중심 데이터베이스
- Apps Script
- Cloudflare Worker
- GPT Action 기반 실행 구조

---

## 15. 프로젝트 목표 변경 금지

사용자가 확정한 프로젝트 목표를 임의로 확장하거나 변경하지 않는다.

현재 MVP 구현에 필요한 방법 제안은 가능하다.

가능한 예:

> 현재 검색 속도를 위해 SQLite 인덱스를 추가하는 것이 필요합니다.

불가능한 예:

> 향후 더 강력하게 만들기 위해 지금 웹서버와 별도 클라우드 DB도 같이 만듭시다.

---

## 16. 1차 목표 MVP 구성

### MVP-01 — Python 실행환경
- Python 설치 확인
- 프로젝트 폴더 생성
- 가상환경 구성
- requirements 관리
- Git/GitHub 연결
- 기본 실행 확인

### MVP-02 — Google Drive OAuth + 읽기 연결
- OAuth 인증
- Drive API v3 연결
- 테스트 폴더 파일/폴더 목록 조회

### MVP-03 — SQLite Drive Index
- files
- folders
- scan_state
- Drive 메타데이터 저장

### MVP-04 — 증분 동기화
- INSERT
- UPDATE
- DELETE 또는 상태 갱신
- SKIP
- fileId 중복 검증

### MVP-05 — File Name Parser
- normalized_name
- base_name
- revision_type
- revision_number
- copy_number
- extension

### MVP-06 — File Grouping
- 같은 폴더 기준 그룹화
- groupKey
- revision/copy 그룹 요약

### MVP-07 — 검색 및 보고
- 폴더 검색
- 파일명 검색
- Revision 후보 검색
- Copy 후보 검색
- 최근 변경 조회
- 필요한 경우 Excel/CSV 출력

---

## 17. 2차 목표 MVP 구성

### MVP-01 — Operation Plan
실제 실행 전 작업계획 생성

### MVP-02 — Dry Run
실행 예정 작업을 변경 없이 미리보기

### MVP-03 — Rename
fileId 기반 이름 변경

### MVP-04 — Move
파일/폴더 이동

### MVP-05 — Copy
파일 복사 및 폴더 재귀 복사

### MVP-06 — Trash
휴지통 이동

### MVP-07 — Bulk Execution Engine
- 배치 실행
- Rate Limit 대응
- 재시도
- 중단/재개
- 부분 실패 처리

### MVP-08 — Verify / Undo
- 실행 후 Drive 재확인
- 실행 로그 검증
- 가능한 작업의 되돌리기

---

## 18. MVP 완료 기준

각 MVP는 다음 항목을 모두 확인한 후 완료 처리한다.

- 정상 동작
- 오류 처리
- 중복 여부
- 재실행 결과
- 데이터 일관성
- 실제 Google Drive 상태와의 일치 여부

“대충 작동한다”는 이유로 완료 처리하지 않는다.

---

## 19. 현재 시작 지점

기존 AI Drive Organizer 프로젝트는 폐기되었다.

새 프로젝트 **Python Drive Organizer**는 다음 단계부터 시작한다.

**1차 목표 → MVP-01 — Python 실행환경 구축**

이 단계에서는 Google Drive API, OAuth, SQLite 기능을 아직 구현하지 않는다.

먼저 Windows PC에서 Python 프로젝트가 정상적으로 실행되고, Codex와 Git/GitHub가 해당 프로젝트를 안정적으로 관리할 수 있는 개발환경을 확정한다.
