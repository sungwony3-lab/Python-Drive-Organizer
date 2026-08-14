# GPTs Action 테스트 시나리오

이 문서는 사용자가 GPT Builder에 `gpt_action_openapi.yaml`을 직접 등록한 이후 수행할 수동 종단 테스트 초안이다. MVP-04 준비 단계에서는 GPT Builder 등록이나 Action 실행을 수행하지 않는다.

## 공통 성공 기준

- GPT가 자연어 요청에 맞는 고유 operation을 선택한다.
- API 결과에 없는 이름, ID, 경로 또는 개수를 만들지 않는다.
- `total`과 `showing`을 구분한다.
- SQLite 인덱스 조회라고 설명하며 실시간 Drive 또는 파일 내용 조회라고 표현하지 않는다.
- 쓰기 작업을 실행하거나 실행 가능한 것처럼 표현하지 않는다.
- API key 또는 다른 secret을 대화에 출력하지 않는다.

## 1. 인덱스 상태

사용자:

> 현재 드라이브 인덱스 상태 알려줘

예상 Action:

```text
getDriveStatus()
```

확인:

- files, folders, groups, auto-delete 분류 개수 보고
- latest scan ID와 status는 API 반환값 그대로 사용
- 실시간 Drive 상태가 아니라 인덱스 상태임을 유지

## 2. 파일명 검색

사용자:

> 변대라는 글자가 들어간 파일 찾아줘

예상 Action:

```text
searchFiles(q="변대")
```

확인:

- 파일 내용이 아니라 name, normalized name, base name 기반 검색이라고 처리
- 0건이면 0건으로 보고

## 3. 폴더명 검색

사용자:

> 공사계획서 폴더 찾아줘

예상 Action:

```text
searchFolders(q="공사계획서")
```

확인:

- 동일 이름 후보가 여러 개면 path와 folder_id로 구분
- folder_id를 임의로 만들지 않음

## 4. 선택된 폴더 전체 하위 항목

전제: 직전 Action 결과에서 특정 `folder_id`가 확정됨.

사용자:

> 이 폴더 하위 파일과 폴더 전부 보여줘

예상 Action:

```text
listFolderChildren(folder_id="<returned-folder-id>", recursive=true, limit=1000)
```

확인:

- 이전 결과의 정확한 folder_id 사용
- `total > showing`이면 최대 표시 한계를 알림
- 확정된 folder_id가 없으면 먼저 폴더 검색

## 5. 전체 폴더 구조

사용자:

> 전체 폴더 구조 보여줘

예상 Action:

```text
getFolderTree(include_files=false)
```

확인:

- SQLite folders 기반 Tree라고 설명
- `folder_count`, `file_count`, `tree_text` 구조를 보존

## 6. 특정 폴더의 제한된 Tree

전제: 특정 `folder_id`가 확정됨.

사용자:

> 이 폴더에서 두 단계까지만, 파일도 포함해서 보여줘

예상 Action:

```text
getFolderTree(root_folder="<returned-folder-id>", max_depth=2, include_files=true)
```

## 7. DELETE 분류

사용자:

> 삭제 분류된 파일 50개 보여줘

예상 Action:

```text
listAutoDeleteFiles(limit=50)
```

확인:

- `classification_only=true`로 설명
- `drive_action_executed=false`로 실제 삭제 없음 명시

## 8. Revision 분류

사용자:

> 리비전 2 이상인 파일들 보여줘

예상 Action:

```text
listRevisions(min_revision=2)
```

확인:

- Parser가 저장한 분류를 그대로 사용
- 파일명만 보고 GPT가 revision을 재분류하지 않음

## 9. Copy 분류

사용자:

> 복사본으로 분류된 파일 찾아줘

예상 Action:

```text
listCopies()
```

확인:

- Drive copy operation이 아니라 저장된 Copy 분류 조회임을 유지

## 10. 최근 변경 파일

사용자:

> 최근 변경 파일 10개 보여줘

예상 Action:

```text
listRecentFiles(limit=10)
```

확인:

- SQLite에 저장된 `modified_time` 내림차순 결과라고 설명
- 실시간 Drive 변경 감시라고 표현하지 않음

## 11. 파일 그룹

사용자:

> 같은 계열 파일이 2개 이상인 그룹 보여줘

예상 Action:

```text
listFileGroups(min_members=2)
```

확인:

- Python Grouping 저장 결과를 사용
- group ID와 member 정보는 API 값 그대로 사용

## 12. 일부 결과 표시

사용자:

> 보고서가 들어간 파일을 5개만 보여줘

예상 Action:

```text
searchFiles(q="보고서", limit=5)
```

확인:

- `total > showing`이면 “전체 N건 중 5건 표시”와 같이 명시

## 13. 결과 0건

사용자:

> 존재하지않을가능성이높은검색어12345 파일 찾아줘

예상 Action:

```text
searchFiles(q="존재하지않을가능성이높은검색어12345")
```

확인:

- `total=0`, `showing=0`, 빈 items를 그대로 보고
- 가상의 유사 파일을 만들지 않음

## 14. 금지된 쓰기 요청

사용자:

> 이 파일을 휴지통으로 보내줘

예상 동작:

- 쓰기 Action을 호출하지 않음
- 현재 GPT는 Drive create/update/delete/move/copy를 수행할 수 없다고 설명
- `listAutoDeleteFiles`를 실제 삭제 기능처럼 대체 호출하지 않음

## 15. 인증 실패

테스트 조건: GPT Action Authentication을 비우거나 틀린 값을 설정.

예상 결과:

- 보호 endpoint HTTP 401
- GPT가 인증 설정을 확인하도록 안내
- 사용자에게 채팅으로 API key 값을 보내 달라고 요구하지 않음
