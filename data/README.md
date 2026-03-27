# 운영 설정(data/) 안내

이 폴더는 **사람(운영자/기획자)이 관리하는 설정/데이터**를 모아두는 영역입니다.
서버(`server.py`)는 이 파일들을 읽어 동작하며, 서버 코드 수정 없이 운영값을 변경할 수 있습니다.

## 파일 구성
- `sources.json`
  - 데이터 소스 등록(공공/구독형 구분, 커넥터 등)
  - **커넥터 `connector`** 예시:
    - `"kind": "mock"` + `"seed"` … 기존처럼 서버가 시계열 생성
    - `"kind": "csv"` + `"seriesCode"` … `series_values.csv`에서 해당 `code` 행을 읽음 (메타 단위는 `series_meta.csv`와 조인해 응답 `units`에 반영)
- `permissions.json`
  - 사용자별 허용 소스/기능 권한
- `themes/`
  - `{orgId}.json` 형태로 테마 저장 (파일 내부 `orgId` 기준으로 매핑)
- `feature-specs/`
  - 데모/검증용 FeatureSpec 샘플 모음
  - **권장(코드 주인공)**: `analysisType: correlation` + `seriesBindings: { "x": "DOMAIN....", "y": "DOMAIN...." }`  
    - 서버는 코드 첫 구간(도메인)으로 `code_registry.json`을 조회해 `sourceId`를 **유추**하고, 권한·데이터 로드에 사용합니다.
  - **레거시**: `requiredSources`에 `role: x|y` + `sourceId` (소스의 `connector`로 시계열 조회)
- `code_registry.json`
  - `domainToSource`: 시리즈 `code`의 **도메인**(첫 번째 `.` 앞) → `sources.json`의 `sourceId`  
  - 새 도메인을 쓰면 여기에 한 줄 추가해야 권한 유추가 됩니다.
- `series_meta.csv`
  - 시계열 **코드 마스터(정식 스키마)**. 컬럼 의미:
    - `code`: 전역 고유 시리즈 식별자 (`DOMAIN.분류.지표` 권장)
    - `상세설명`: 지표 정의·출처 메모
    - `영문명`: 영문 표기
    - `데이터포인트_주기`: 한 점의 주기 (`D`/`W`/`M`/`Q`)
    - `발표_주기`: 공개·갱신 리듬(서술)
    - `스케일`: `level` / `rate` / `index` 등 값의 성격
    - `단위`: 표시·해석용 (`%`, `index`, …). `build-feature` 응답 `canonicalDataset.units`에 반영
  - 로값(`series_values.csv`)의 `code`는 여기에 정의된 것만 쓰는 것을 권장합니다.
- `series_values.csv`
  - **로값(롱 포맷)**: `code`, `date`, `value`  
  - `date`는 `YYYY-MM-DD` 문자열(판다스에서는 `pd.to_datetime` 후 `DatetimeIndex`로 쓰면 됨), `value`는 float  
  - 기본 생성: 루트에서 `python tools/generate_series_values.py` (메타의 주기·2020~2024 약 5년 더미)

## 변경 반영
- 서버 재시작 없이 반영하려면 `POST /reload-config` 호출
- (선택) 환경변수 `ADMIN_TOKEN`을 설정하면 리로드 호출에 토큰을 요구합니다.

## 브라우저 데모
- 서버 실행 후 `http://localhost:8000/app` — `feature-specs`의 샘플 스펙으로 `build-feature`를 호출해 결과를 보여줍니다.

