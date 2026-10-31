# md2hwpx

Markdown 파일을 한/글 문서 형식인 HWPX 파일로 변환하는 Python 프로젝트입니다.

## 현재 기능

- Markdown 안의 ` ```mermaid ` 코드블록을 PNG 이미지로 변환
- Markdown 안의 ` ```plantuml ` 코드블록을 PNG 이미지로 변환
- 변환한 코드블록을 이미지 링크 `![](파일.png)` 로 치환
- 템플릿 HWPX 를 열어 Markdown 내용을 추가하고 새 HWPX 로 저장
  - Markdown 은 markdown-it-py 로 파싱 (CommonMark + GFM 표·취소선 + 각주). 목록·인용문 안에 어떤 블록이든 중첩 가능
  - 헤딩(ATX `#`~`######`, Setext `===`/`---`) → `개요 1`~`개요 6` 스타일, 일반 문단 → `본문` 스타일
  - 이미지(문장 안·참조식 포함, `![](a.png){width=50%}` 처럼 크기 지정 가능), 표(머리 행 굵게·회색 배경, 열 정렬), 코드블록(1×1 표, D2Coding 9pt, 줄간격 140%, Pygments 구문 강조)
  - 글머리표·번호 목록 (하위 목록 3단계까지)
  - 각주(`[^1]` / `[^1]: 내용`), `[TOC]` 자리에 한/글 차례(`[TOC 그림 표 제목]` 처럼 넣을 차례와 순서 지정, `링크없음` 으로 하이퍼링크 끄기), `\newpage`(또는 `\pagebreak`) 줄에서 쪽 나누기, `[새 번호 쪽 표 그림 수식]` 줄에서 그 번호를 새로 시작, `[감추기]` 줄에서 그 쪽의 쪽 번호·머리말·꼬리말 숨김, `[여백면]` 줄에 여백 면 한 장
  - 이미지는 PNG·JPEG·GIF 는 그대로, 그 밖의 형식(BMP·TIFF·WebP 등)은 Pillow 로, SVG 는 `rsvg-convert` 로 PNG 로 바꿔 넣음
  - 저장 전 HWPX 스키마 검증, 넣을 수 없는 이미지(없는 파일, 읽을 수 없는 형식)는 대체 이미지("이미지 파일이 없습니다")를 넣고 `파일:줄` 위치와 함께 경고
  - pandoc 의 `::: {custom-style="스타일 이름"}` ... `:::` 블록 안의 문단에 템플릿의 그 문단 스타일 적용. 템플릿에 없는 스타일이면 `본문` 으로 넣고 `파일:줄` 위치와 함께 경고
  - 파일 맨 앞의 YAML front matter(`---` ... `---`)는 변환하지 않음. `caption-numbering` 키만 읽음
  - `[표] 제목` / `[그림] 제목` 줄 → `[표 N-M] 제목` / `[그림 N-M] 제목` 캡션 (`표그림 제목` 스타일). `N` 은 장(수준 1 헤딩) 번호, `M` 은 그 장에서의 순번(한/글 자동 번호). `[그림]` 줄이 없으면 이미지의 대체 텍스트가 제목
  - front matter 에 `caption-numbering: document` 를 쓰면 장 번호 없이 문서 전체에서 센 `[표 7] 제목` 캡션. 기본은 `chapter`.
    폴더 입력에서 파일마다 값이 다르면 변환하지 않음

HWPX 생성에는 [python-hwpx](https://github.com/airmang/python-hwpx) 를 사용합니다.
템플릿에는 `개요 1`~`개요 6`, `본문`, `바탕글`, `표그림 제목` 스타일이 있어야 합니다.
헤딩이 새 쪽에서 시작할지는 템플릿의 `개요` 스타일 문단 모양("문단 앞에서 항상 쪽 나눔")이 정합니다.
내장 템플릿은 `개요 1`(장)만 새 쪽에서 시작합니다.
인라인 서식은 코드(D2Coding 글꼴), 굵게, 기울임, 취소선, 링크(한/글 하이퍼링크, 참조식 포함)를 지원합니다.
인용문(왼쪽 선), 수평선, 강제 줄바꿈(줄 끝 공백 두 칸 또는 `\`)도 지원합니다. 원시 HTML 은 글자 그대로 들어갑니다.

## 실행 환경

다이어그램 렌더링에 mermaid-cli, Chromium, Java, PlantUML 이, SVG 이미지 변환에 rsvg-convert 가 필요합니다.
이 도구들을 모두 담은 Docker 이미지를 제공하며, 코드는 컨테이너에 마운트해서 실행합니다.

```sh
docker build -t md2hwpx .

# 테스트 실행
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work md2hwpx pytest -q -p no:cacheprovider
```

`--user` 를 주면 마운트한 폴더에 root 소유 파일이 생기지 않습니다.
Python 의존성(`pyproject.toml`, `uv.lock`)이 바뀌면 이미지를 다시 빌드합니다.

## 사용 예

### 명령줄

```sh
# Docker (다이어그램 렌더링 도구 포함). 어느 폴더에서나 실행할 수 있다.
/path/to/md2hwpx/md2hwpx.sh docs/guide.md -o docs/

# 위 스크립트가 실행하는 명령 (저장소 루트에서)
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work md2hwpx \
    python -m md2hwpx docs/guide.md -o docs/

# 로컬 (uv sync 후)
uv run md2hwpx docs/guide.md          # ./guide.hwpx
uv run md2hwpx docs/chapters/         # 폴더 안의 .md 를 이름 순으로 합쳐 ./chapters.hwpx
```

- `md2hwpx.sh` 는 현재 폴더를 컨테이너에 마운트하므로 입력·출력·템플릿은 현재 폴더 안에 있어야 한다.
- 인자 없이 실행하면 도움말을 보여 준다.
- 입력: Markdown 파일, 또는 `.md` 파일들이 있는 폴더.
  폴더면 그 폴더 바로 안의 `.md` 파일을 이름 순(숫자는 크기 순: `2.md` → `10.md`)으로 이어 붙여 하나의 HWPX 로 만든다.
  하위 폴더는 읽지 않는다.
- `-o`, `--output`: 저장할 HWPX 파일 또는 폴더.
  - 폴더면 그 폴더에 `입력 이름.hwpx` 로 저장한다.
  - 생략하면 현재 작업 폴더에 `입력 이름.hwpx` 로 저장한다.
  - 같은 이름의 파일이 있으면 덮어쓰지 않고 `이름-1.hwpx`, `이름-2.hwpx` ... 로 저장한다.
- `-t`, `--template`: 템플릿 HWPX 파일. 생략하면 현재 폴더의 `template.hwpx`, 그것도 없으면 내장 템플릿
  (`src/md2hwpx/template.hwpx`)을 쓴다.

다이어그램은 임시 폴더에 렌더링해 HWPX 에 넣고 지우므로, 입력 폴더에 이미지 파일이 남지 않습니다.
성공하면 저장한 파일 경로를 출력하고, 실패하면 오류를 stderr 에 출력하고 종료 코드 1 로 끝납니다.

### Python

```python
from pathlib import Path
from md2hwpx.convert import DEFAULT_TEMPLATE, caption_numbering, convert_markdown, read_markdown
from md2hwpx.diagram import render_diagrams

doc = Path("docs/guide.md")
markdown = render_diagrams(read_markdown(doc), out_dir=doc.parent / "images", link_base=doc.parent)
convert_markdown(markdown, doc.parent, DEFAULT_TEMPLATE, doc.with_suffix(".hwpx"), caption_numbering([doc]))
```

다이어그램은 `docs/images/mermaid-<해시>.png` 같은 이름으로 저장되고,
반환된 Markdown 에서 코드블록은 `![](images/mermaid-<해시>.png)` 로 바뀝니다.
이미지 경로는 `convert_markdown` 의 두 번째 인자(기준 폴더)를 기준으로 찾습니다.
`read_markdown` 은 front matter 를 지우므로 캡션 번호 방식은 `caption_numbering` 으로 따로 읽어 넘깁니다 (생략하면 `chapter`).
`DEFAULT_TEMPLATE` 은 내장 템플릿입니다. 다른 템플릿은 그 경로를 넘깁니다.
파일을 바로 변환할 때는 `convert(md_path, template_path, out_path)` 를 씁니다.

### 웹 서비스

브라우저에서 Markdown 을 올려 HWPX 를 내려받습니다. 다이어그램 도구를 담은 Docker 이미지로 실행합니다.

```sh
docker compose up -d --build    # http://localhost:8000
```

이미지를 날짜 태그와 함께 빌드하려면 `build-images.sh` 를 씁니다. 개발용 `md2hwpx` 와 웹 서비스용 `md2hwpx-web` 을
모두 빌드해 각각 `latest` 와 오늘 날짜 태그(`md2hwpx-web:261030` 처럼 YYMMDD)를 붙이므로, 나중에 다시 빌드해도
그날의 이미지로 되돌릴 수 있습니다. 같은 날 다시 빌드하면 그날 태그도 새 이미지로 옮겨 갑니다.
빌드만 하므로 실행 중인 웹 서비스는 `docker compose up -d` 로 바꿉니다.

```sh
./build-images.sh && docker compose up -d
```

- 입력: md 와 이미지 파일 여러 개, md·이미지가 든 폴더의 zip, 또는 `http://`·`https://` git 저장소 주소(브랜치·태그 선택, 폐쇄망 http 저장소 포함).
  zip·git 은 변환할 md 파일이나 폴더의 경로를 지정할 수 있습니다.
- 템플릿 HWPX 를 함께 올리면 그 템플릿으로 변환합니다.
- 폐쇄망 GitLab·Gitea 의 비공개 저장소는 페이지에서 그 서버에 로그인(OAuth2)해 받습니다.
  설정은 아래 "GitLab·Gitea 로그인 설정" 을 따릅니다.
- 웹 서비스 자체의 접근 인증은 없으니 외부에 공개할 때는 앞단에서 접근을 제한하세요. 자세한 내용은 [웹 서비스 스펙](spec/web-service.md).

#### GitLab·Gitea 로그인 설정

로그인을 쓰지 않으면 이 설정은 필요 없습니다. 공개 저장소는 로그인 없이 받습니다.
아래 예는 웹 서비스를 `http://localhost:8000`, Gitea 를 `http://192.168.1.2:3000` 에서 쓰는 경우입니다.

**1. 주소 정하기**

- 웹 서비스 주소(`MD2HWPX_BASE_URL`): 사용자가 **브라우저 주소창에 입력하는 주소** 그대로. 뒤의 `/` 는 뺍니다.
  `localhost` 와 `127.0.0.1` 도 서로 다른 주소로 취급되므로, 앞으로 접속할 쪽 하나로 정합니다.
- GitLab·Gitea 주소: **브라우저와 md2hwpx 컨테이너 양쪽에서 접속되는 주소**.
  브라우저는 이 주소의 로그인 화면으로 이동하고, 컨테이너는 이 주소에서 토큰을 받고 저장소를 clone 합니다.
  컨테이너 안의 `localhost` 는 md2hwpx 자신이므로 `http://localhost:3000` 은 쓸 수 없습니다. 호스트 IP 나 도메인을 씁니다.
  확인: `docker compose exec web wget -q -O- http://192.168.1.2:3000/api/v1/version` (Gitea) 이 버전을 보여 주면 됩니다.

**2. OAuth 애플리케이션 등록**

| | GitLab | Gitea |
|---|---|---|
| 메뉴 | 사용자 설정 > Applications. 관리자는 관리자 영역 > Applications (모든 사용자용) | 사용자 설정 > 애플리케이션 > OAuth2 애플리케이션 관리. 관리자는 사이트 관리 > 통합 > 애플리케이션 |
| Redirect URI | `<웹 서비스 주소>/oauth/gitlab/callback` | `<웹 서비스 주소>/oauth/gitea/callback` |
| 기밀(Confidential) | 켬 | 켬 (Confidential client) |
| Scope | `read_repository` 만 | 등록 화면에 없음 (로그인할 때 `read:repository` 를 요청) |
| 그 밖 | 관리자 영역에서 만들고 Trusted 를 켜면 승인 화면을 건너뜀 | |

Redirect URI 는 웹 서비스 주소와 **글자 그대로** 같아야 합니다 (예: `http://localhost:8000/oauth/gitea/callback`).
등록하면 나오는 Client ID(GitLab: Application ID) 와 Client Secret 을 다음 단계에 씁니다. Secret 은 다시 볼 수 없으니 바로 옮겨 둡니다.

**3. `.env` 작성**

```sh
cp .env.example .env
```

```sh
MD2HWPX_BASE_URL=http://localhost:8000
MD2HWPX_GITEA_URL=http://192.168.1.2:3000
MD2HWPX_GITEA_CLIENT_ID=<Client ID>
MD2HWPX_GITEA_CLIENT_SECRET=<Client Secret>
```

쓰지 않는 서버의 값은 비워 둡니다. 한 서버의 세 값과 `MD2HWPX_BASE_URL` 이 모두 있어야 그 서버의 로그인이 켜집니다.
`.env` 는 비밀 값이 들어 있어 커밋하지 않습니다 (`.gitignore` 에 있음).

**4. 다시 실행**

```sh
docker compose up -d    # .env 를 바꾼 뒤에도 다시 실행해야 컨테이너에 반영된다
```

**5. 확인**

1. 웹 서비스 주소로 접속해 "git 저장소" 탭을 고르면 저장소 주소 아래에 `Gitea (http://192.168.1.2:3000): 로그인` 이 보입니다.
   보이지 않으면 `.env` 의 값이 빠졌거나 다시 실행하지 않은 것입니다.
2. "로그인" → Gitea 로그인·승인 → 페이지로 돌아와 `로그인됨 · 로그아웃` 으로 바뀝니다.
3. 비공개 저장소 주소를 넣고 변환합니다.

| 증상 | 원인 |
|---|---|
| 로그인을 누르면 Gitea 의 `/oauth/authorize?...` 로 가서 404 | Gitea 값을 `MD2HWPX_GITLAB_*` 에 넣음. `MD2HWPX_GITEA_*` 로 옮기고, Gitea 앱의 Redirect URI 도 `/oauth/gitea/callback` 으로 고친 뒤 다시 실행 |
| 로그인을 누르면 GitLab 의 `/login/oauth/authorize?...` 로 가서 404 | 반대로 GitLab 값을 `MD2HWPX_GITEA_*` 에 넣음. `MD2HWPX_GITLAB_*` 로 옮김 |
| 로그인을 누르면 서버가 "redirect_uri 가 맞지 않는다" 는 오류를 보여 줌 | Redirect URI 와 `MD2HWPX_BASE_URL` 이 다름 (`localhost` / `127.0.0.1`, 포트, 끝의 `/` 등) |
| 로그인 후 "로그인 요청이 올바르지 않거나 오래되었습니다" | 로그인을 시작한 주소와 `MD2HWPX_BASE_URL` 이 달라 쿠키가 이어지지 않음. `MD2HWPX_BASE_URL` 로 접속해서 로그인 |
| 로그인 후 "토큰을 받지 못했습니다" | 컨테이너에서 서버 주소로 접속되지 않거나 Client Secret 이 틀림 |
| 변환 시 "비공개 저장소라면 … 로그인하세요" | 로그인하지 않았거나 토큰이 만료됨 (GitLab 2시간, Gitea 1시간). 다시 로그인 |
| 로그인했는데 이 안내 없이 변환이 실패함 | 그 사용자에게 저장소 읽기 권한이 없거나, 저장소 주소가 `.env` 의 서버 주소로 시작하지 않음 (예: 서버는 IP 로, 저장소는 도메인으로 적음) |

서버를 다시 시작하면 로그인이 풀립니다 (토큰을 메모리에만 둠).

## 로컬 개발

컨테이너 없이 개발할 때는 [uv](https://docs.astral.sh/uv/) 를 사용합니다.

```sh
uv sync
uv run pytest -q
```

`mmdc`, `plantuml`, `rsvg-convert` 가 PATH 에 없으면 해당 렌더링 테스트는 skip 됩니다.

## 프로젝트 구조

```
src/md2hwpx/    소스 코드와 내장 템플릿(template.hwpx)
tests/          pytest 테스트
spec/           구현 스펙 문서
samples/        변환 테스트용 샘플 Markdown (결과 HWPX 는 커밋하지 않음)
docker/         Docker 이미지용 실행 스크립트와 설정
Dockerfile      실행용 Alpine 이미지 (dev: 코드 마운트, web: 웹 서비스)
compose.yaml    웹 서비스 실행
md2hwpx.sh      Docker 이미지로 변환하는 실행 스크립트
build-images.sh 개발용·웹 서비스 이미지를 latest·날짜 태그로 빌드하는 스크립트
```

## 스펙 문서

구현 내용은 [spec](spec/README.md) 폴더에 정리되어 있습니다.

- [다이어그램 코드블록 → PNG 변환](spec/diagram-rendering.md)
- [실행용 Docker 이미지](spec/docker.md)
- [Markdown → HWPX 변환](spec/markdown-conversion.md)
- [웹 서비스](spec/web-service.md)

## 라이선스

[MIT](LICENSE)
