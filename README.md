# Stock Signal

미국 주식 관심 종목의 시세, 뉴스, SEC 공시와 기술 지표를 주기적으로 모아 한국어 대시보드와 Telegram으로 전달하는 투자 판단 보조 서비스입니다. **자동 주문이나 매매는 실행하지 않습니다.** 현재 점수는 투명한 규칙 기반 참고 지표입니다. 생성형 AI 뉴스 해석은 아직 연결하지 않았으므로 `OPENAI_API_KEY` 값만 추가해도 AI 분석이 켜지지는 않습니다.

## 구성

- FastAPI API(실시간 수집·분석 스케줄러 포함), PostgreSQL, React/TypeScript 대시보드
- Finnhub 우선 시세/뉴스, Yahoo Finance 보조 시세, SEC EDGAR 공시
- OpenDART 공식 상장사 디렉터리·한국 회사 공시, 한국어 별칭 검색
- RSI, MACD, 이동평균, EMA, Bollinger Bands, ATR, 거래량 비율, 최근 고점/저점 계산
- 점수 변경, 중요 신규 기사/공시, 단기 변동, 최근 고점/저점 이탈 시 Telegram 알림
- 등록 종목 검색 자동완성, PostgreSQL/volume 저장, 실시간 체결에 따른 분봉 갱신
- 기사/공시, 일봉 지표와 신호 분석은 기본 5분 간격으로 갱신합니다. Finnhub 단일 trade stream은 FastAPI 프로세스 내 MarketDataManager에서 관리하고 브라우저 WebSocket에 즉시 전달합니다.

## 1. 필요한 API

- Finnhub API key: https://finnhub.io/ — symbol search, company news, quotes and websocket trades. API key plan and exchange permissions determine whether real-time trades arrive.
- Telegram bot token: Telegram에서 `@BotFather`에게 `/newbot`을 보내 발급합니다.
- SEC EDGAR는 키 없이 제공되며, SEC 요청 정책을 위해 연락 가능한 이메일을 `SEC_USER_AGENT`에 적습니다.
- Yahoo 시세 fallback은 비공식 엔드포인트입니다. Finnhub 키 없이도 시험할 수 있지만 운영 보장을 제공하지 않습니다.

## 2. 설치와 실행

Docker Desktop 또는 Docker Engine과 Compose v2가 필요합니다. 프로젝트 폴더에서 다음을 실행하세요.

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
notepad .env
notepad backend/.env
docker compose up -d --build
docker compose ps
```

루트 `.env`에는 DB 비밀번호와 도메인/Caddy 설정을, `backend/.env`에는 `FINNHUB_API_KEY`, `OPENDART_API_KEY`, `SEC_USER_AGENT`, Telegram 키 등 API 설정을 넣습니다. 두 `.env` 파일은 Git과 Docker 이미지에 포함되지 않습니다. 로컬 Docker 배포는 `APP_DOMAIN=localhost`로 접속할 수 있습니다. Caddy 인증 비밀번호 해시를 설정하지 않으면 Caddy가 실행되지 않습니다. API 문서는 접속 후 `/api/docs`에서 확인할 수 있습니다.

## 3. Telegram 연결

1. BotFather에서 봇을 만들고 token을 `backend/.env`의 `TELEGRAM_BOT_TOKEN`에 입력합니다.
2. 새 봇 대화방을 열어 `/start`를 보냅니다.
3. PowerShell에서 프로젝트 폴더에 있는 아래 명령을 실행합니다.

```powershell
$token = (Get-Content backend/.env | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=' }) -replace '^TELEGRAM_BOT_TOKEN=', ''
Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates" | ConvertTo-Json -Depth 10
```

4. 응답의 `message.chat.id`를 `backend/.env`의 `TELEGRAM_CHAT_ID`에 넣습니다.
4. `docker compose up -d`로 다시 시작한 뒤 대시보드의 **Telegram 테스트** 버튼을 눌러 확인합니다.

## 4. 종목 추가와 알림

검색창에 회사명, 한글 기업명, 영문명 또는 티커를 입력하면 후보가 표시됩니다. KRX 전체 한글 상장사 검색과 공시에는 OpenDART API key를 서버 환경에 설정해야 합니다. Finnhub가 거래소 코드를 확인하지 못한 KRX 종목은 잘못된 `.KS`/`.KQ`를 추측하지 않고 시장 확인 필요로 표시합니다. KRX 가격은 EOD/지연 상태이며 Finnhub 미국 실시간 체결로 LIVE 처리하지 않습니다. `+`를 눌러 추가하면 종목이 DB에 보존되어 페이지 새로고침이나 서버 재시작 후에도 유지됩니다. 행 오른쪽 `×`는 사용자가 종목을 명시적으로 삭제할 때만 제거합니다. 지원되는 미국 종목은 단일 Finnhub WebSocket에 자동 구독되고 삭제 종목은 구독 해제됩니다. 수집 작업은 매 5분 실행되며 신호 변경, 영향도 80 이상인 새 뉴스/SEC 공시, 기본 설정 이상 단기 변동, 최근 20일 지지/저항 돌파를 Telegram으로 알립니다. 알림 임계값은 `backend/.env`의 `ALERT_MOVE_10M_PCT`로 바꿀 수 있습니다. 점수 가중치는 `WEIGHT_NEWS`, `WEIGHT_TREND`, `WEIGHT_VOLUME`, `WEIGHT_TECHNICAL`, `WEIGHT_FUNDAMENTAL`, `WEIGHT_MARKET`로 조절합니다. 현재 미연결된 펀더멘털/시장 데이터 항목은 중립 점수로 처리되고 위험요인에 표시됩니다. `REALTIME_SHOW_MILLISECONDS=true`로 바꾸면 LAST TRADE에 밀리초까지 표시합니다.

## 5. VPS 배포와 운영

1. Ubuntu VPS에 Docker Engine/Compose를 설치합니다.
2. 프로젝트 폴더와 `.env`를 서버에 배치하고 `POSTGRES_PASSWORD`를 고유한 긴 값으로 설정합니다.
3. 도메인의 DNS A/AAAA 레코드를 VPS로 연결합니다. `.env`의 `APP_DOMAIN`에 도메인, `APP_USERNAME`에 로그인 이름을 넣고 긴 임의 비밀번호를 Caddy bcrypt 해시로 설정합니다. 예를 들어 `docker run --rm caddy:2-alpine caddy hash-password --plaintext '원하는-긴-비밀번호'` 출력값을 `APP_PASSWORD_HASH`에 저장합니다. 이 비밀번호를 채팅이나 코드에 붙여넣지 마세요.
4. 공개 방화벽에는 80/443만 허용하고 Postgres와 API 포트는 외부에 열지 마세요. Compose의 Caddy가 HTTPS와 로그인 보호를 맡습니다.
5. `docker compose up -d --build` 실행 후 로그인해 `https://도메인/api/health` 응답과 Telegram 테스트를 확인합니다.
6. `docker compose logs -f api`로 실행 로그를 확인합니다. 재시작 정책은 `unless-stopped`, DB/Caddy는 persistent volume을 사용합니다.
7. 업데이트 때 새 코드를 배치하고 `docker compose up -d --build`를 실행합니다. 데이터베이스 schema는 Alembic migration으로 갱신됩니다.

백업 예시: `docker compose exec -T db pg_dump -U stocks stocks > stocks-backup.sql`. 별도 저장소에 백업을 보관하고 복구도 주기적으로 확인하세요.

## 6. Free sleeping hosting and retained history

The prepared Render Blueprint uses one Free Docker web service and a free `onrender.com` address. Render's free web service sleeps after inactivity, so no news refresh or Finnhub trades are collected while the process is stopped. Opening the site wakes the service. The page then calls `POST /api/digest/refresh`, which refreshes the current watchlist, retains fetched records, and displays a separate **놓친 소식 요약** panel. The detailed **최신 뉴스 · 공시** list remains in place. A first visit uses a 24-hour baseline; later digests start after the last successful sync. If a provider fails, the digest is labeled stale and the watermark does not move. Provider coverage is limited to the configured sources' lookback windows (Finnhub news: 7 days, SEC recent filings: 30 days, OpenDART query: 90 days).

Durable storage is designed as:

- **Neon PostgreSQL** for watchlist, price snapshots, news/filings, signal history, and the visit watermark. Render's temporary filesystem and expiring free database are not used for primary records. PostgreSQL connections enable TLS for Neon and use a small reconnecting pool.
- **Cloudflare R2 Standard** for a second copy of the database, compressed and encrypted with a server-side Fernet key. A successful catch-up can create at most one snapshot per day. Old archives are never deleted automatically. The uploader stops before `R2_MAX_STORAGE_BYTES` (default 9 GB) and warns at 80%; reaching the cap does not delete database history or switch to paid usage.

To connect those services when creating the Render service, create a Neon database and a private R2 bucket first. Add the Neon connection URL, Finnhub API key, a new single-user `APP_USERNAME` and `APP_PASSWORD`, and optional OpenDART/Telegram/R2 credentials in the Render dashboard's secret settings. Do not put credentials in this file or in chat. Keep Render's plan on Free and do not configure paid overages. Generate the R2 encryption key in a trusted local terminal with `cd backend; uv run --with-requirements requirements.txt python -m app.backup generate-key`; save it directly as Render's `BACKUP_ENCRYPTION_KEY` secret. Give the R2 key only bucket read/list/write access needed for the private backup bucket; do not enable public bucket access or lifecycle deletion.

Before the first public start, preserve the records currently in the local SQLite database. Make a file copy of that DB first. Temporarily set the new Neon `DATABASE_URL` in the ignored `backend/.env`, then run from `backend`: `uv run --with-requirements requirements.txt alembic upgrade head` followed by `uv run --with-requirements requirements.txt python -m app.storage_transfer "<path-to-the-current-SQLite-file>"`. The import copies watchlist, snapshots, news/filings, signal history, and any visit watermark while preserving IDs. It refuses a non-empty destination instead of overwriting Neon data. Remove the temporary Neon URL from `backend/.env` after the import; configure it in Render's secret settings for runtime.

The container runs Alembic migrations before starting FastAPI and serves the React app, HTTP API, and authenticated browser WebSocket from one origin. Render health checks use `/api/health`. To restore an archive, deploy the application version whose migration head matches the archive manifest, configure the Neon/R2 secrets, and run from the backend directory: `uv run --with-requirements requirements.txt python -m app.backup restore <R2-object-key>`. Restore uses `pg_restore --clean`; choose a separate/empty target database or take a separate backup first. The restore command refuses a schema-version mismatch and verifies the encrypted archive checksum before writing.

This checkout currently has no Git repository and has not been connected to Render or provisioned with Neon/R2 accounts. The `Dockerfile` and `render.yaml` prepare deployment, but do not themselves publish a public site or create provider resources. A private source connection and dashboard credentials are still needed. Before a public URL is enabled, review its exact URL and the Basic Auth behavior; the deployed app requires a username and password and keeps its Finnhub WebSocket behind the same login.

## 구현 범위와 한계

- 실제 체결을 받으면 가격과 차트가 거래 이벤트마다 갱신되고 마지막 체결 시각을 초 단위로 표시합니다. 거래소에서 체결이 없는 순간에는 마지막 실제 가격을 유지합니다. 미국 주식은 장이 닫힌 시간이나 휴일에는 새 체결이 없는 것이 정상입니다. 실시간 제공 여부는 Finnhub API 키와 계정/거래소 권한에 달렸습니다.
- 단일 FastAPI 프로세스가 Finnhub upstream 연결, 브라우저 WebSocket 방송, 실시간 신호 평가와 5분 데이터 갱신을 실행합니다. 데이터베이스에는 관심 종목·뉴스·가격 스냅샷·신호 이력을 저장합니다. 재시작 시 메모리의 마지막 체결과 분봉은 초기화되고, 저장된 DB 데이터로 화면을 채운 뒤 새 실제 체결을 반영합니다.
- 종목 데이터는 첫 수집 완료 전까지 비어 있습니다. 키가 없는 동안 Yahoo fallback을 시도하지만 접근 제한이 있을 수 있습니다. 이름 자동완성은 SEC 상장사 디렉터리를 사용하며 SEC가 보장하지 않는 비보고/해외 종목은 검색 결과에 없을 수 있습니다.
- 현재 가격/뉴스 신호는 규칙 기반이며 기업 펀더멘털 점수, 경제 캘린더, OpenAI 분석, 백테스트, 사용자 계정/다중 사용자 권한은 구현되지 않았습니다. 잘못된 투자 판단 가능성이 있으므로 매매 지시로 쓰면 안 됩니다.
- 기술 지표와 과거 차트는 일봉 이력 기준이며, 실시간 분봉은 Finnhub 체결 스트림 연결 시 생성됩니다.
- API 쓰기 요청에는 기본 요청 제한이 있고, Compose 공개 게이트웨이는 HTTPS와 단일 Basic Auth 계정으로 보호합니다. 다중 사용자 계정 및 권한은 구현되지 않았습니다.
- 수집 시각과 데이터 출처가 provider 정책에 따라 지연될 수 있습니다. 정확한 거래 시간 시세는 공급자 계약 조건을 확인하세요.
