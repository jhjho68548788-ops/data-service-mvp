# 데이터서비스 MVP 실행 가이드 (간단 데모)

이 폴더의 `server.py`는 **의존성 없이**(Python 표준 라이브러리) 다음 API를 제공합니다.

- `GET /app` (브라우저 유저 데모 HTML)
- `GET /feature-specs/{파일명}.json`
- `GET /sources`
- `GET /themes/{orgId}`
- `POST /build-feature`

MVP에서는 공공/구독 데이터 소스를 모두 **mock connector**로 처리하고, 정규화/조립/권한 체크를 end-to-end로 동작시키는 것을 목표로 합니다.

---

## 1) 실행

터미널을 이 폴더로 둔 뒤:

```powershell
$repoPath = "c:\Users\user\Desktop\데이터서비스 프로토타입"
cd $repoPath
python server.py
```

기본 포트는 `8000`입니다.

기본 바인드 주소는 **`127.0.0.1`** 입니다(같은 PC에서만 접속). 다른 기기에서 접속하려면:

```powershell
$env:MVP_HOST="0.0.0.0"
python server.py
```

---

## 1.15) `/`에 `GET /app`이 없거나 `/app`이 Not found일 때

1. `http://127.0.0.1:8000/` 을 열고 JSON에 **`mvpBuild`** 와 **`GET /app`** 이 있는지 확인합니다. 없으면 **다른 프로세스**가 8000에서 응답 중인 겁니다.
2. 8000 포트 점유를 정리합니다:

```powershell
netstat -ano | findstr ":8000"
taskkill /PID <LISTENING에_나온_PID> /F
```

3. 이 폴더에서 서버를 **한 번만** 다시 실행한 뒤, 브라우저는 **Ctrl+F5** 또는 시크릿 창으로 `http://127.0.0.1:8000/app` 을 엽니다.

---

## 1.2) 브라우저(유저 단) 데모

서버를 띄운 뒤 주소창에 아래를 입력합니다.

- **유저 화면(권장)**: `http://127.0.0.1:8000/app`
- (대안) `http://localhost:8000/app` — 위와 달리 동작하면 `127.0.0.1` 로 통일하세요.

페이지에서 사용자(`full-access` / `public-only`)를 고르고 **대시보드 만들기**를 누르면, 내부에서 `GET /feature-specs/...` → `POST /build-feature` 를 호출해 결과를 표시합니다.

- **같은 출처 주의**: `file://` 로 HTML을 열면 API 호출이 차단될 수 있으므로, 반드시 위 주소처럼 **서버가 제공하는 `/app`** 으로 접속하세요.

---

## 1.1) 운영자가 관리하는 파일

사람이 수정하는 영역은 `data/`로 분리되어 있습니다.

- **데이터 소스 등록**: `data/sources.json`
- **권한(사용자별 접근)**: `data/permissions.json`
- **테마**: `data/themes/*.json`
- **데모 FeatureSpec 샘플**: `data/feature-specs/*.json`

운영 설정을 수정한 뒤, 서버를 재시작하지 않고 반영하려면:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/reload-config"
```

환경변수 `ADMIN_TOKEN`을 설정해두면, 아래처럼 헤더 토큰을 요구하도록 사용할 수 있습니다.

```powershell
$token = "<your token>"
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/reload-config" -Headers @{ "X-Admin-Token" = $token }
```

---

## 2) API 테스트

### 2.1 소스 목록
```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/sources"
```

### 2.2 테마 가져오기
```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/themes/org-acme"
```

---

## 3) build-feature (코릴레이션 대시보드)

요청 바디는 아래 형태입니다.

```json
{
  "userId": "full-access",
  "spec": {
    "specId": "feature-corr-ecos-steel-v1",
    "title": "기준금리 vs 철강 가격 상관 대시보드",
    "requestedAt": "2026-03-26T00:00:00Z",
    "analysisType": "correlation",
    "themeId": "acme-default",
    "layout": { "grid": "2-col", "cards": ["kpi", "timeseries", "correlation-matrix"] },
    "requiredSources": [
      { "sourceId": "bank-ecos", "role": "x" },
      { "sourceId": "vendor-steel", "role": "y" }
    ]
  }
}
```

PowerShell에서 실행 예:

```powershell
$body = @{
  userId = "full-access"
  spec = @{
    specId = "feature-corr-ecos-steel-v1"
    title = "기준금리 vs 철강 가격 상관 대시보드"
    requestedAt = "2026-03-26T00:00:00Z"
    analysisType = "correlation"
    themeId = "acme-default"
    layout = @{ grid = "2-col"; cards = @("kpi","timeseries","correlation-matrix") }
    requiredSources = @(
      @{ sourceId = "bank-ecos"; role = "x" },
      @{ sourceId = "vendor-steel"; role = "y" }
    )
  }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/build-feature" -ContentType "application/json" -Body $body
```

---

## 4) 권한 체크 확인

- `userId = "public-only"`는 `vendor-steel` 접근이 막혀서 `403`이 반환됩니다.
- `userId = "full-access"`는 `bank-ecos` + `vendor-steel` 모두 허용됩니다.

