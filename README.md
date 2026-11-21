# md2hwpx

Markdown 파일을 한/글 문서 형식인 HWPX 파일로 변환하는 Python 프로젝트입니다.

- 템플릿 HWPX 의 스타일(`개요 1`, `본문` 등)로 헤딩·문단·목록·표·코드블록·이미지·수식·각주를 넣습니다.
- ` ```mermaid `, ` ```plantuml ` 코드블록은 그림으로 바꿔 넣습니다.
- 표·그림 캡션에 번호를 매기고, 글 안에서 그 번호를 참조할 수 있습니다.
- 차례·쪽 나누기 같은 한/글 기능은 Markdown 뷰어에 보이지 않는 주석 마커로 지정합니다.
- 명령줄, 웹 서비스, Python 에서 쓸 수 있습니다.
- 변환하지 못한 것(없는 이미지, 틀린 표기 등)은 `파일:줄` 위치와 함께 경고합니다.

## 빠른 시작

Docker 만 있으면 됩니다. 다이어그램 도구(mermaid-cli, Chromium, Java, PlantUML)와 rsvg-convert 는 이미지에 들어 있습니다.

```sh
docker build -t md2hwpx .

./md2hwpx.sh docs/guide.md            # 파일 하나 → ./guide.hwpx
./md2hwpx.sh sample-report/ -o out/   # 폴더의 .md 를 이름 순으로 합쳐 → out/sample-report.hwpx
```

[sample-report](sample-report/README.md) 폴더는 표지·차례·본문·참고문헌을 갖춘 보고서 예제입니다.
통째로 복사해 내용을 바꿔 쓰면 됩니다.

브라우저에서 쓰려면 웹 서비스를 띄웁니다.

```sh
docker compose up -d --build    # http://localhost:8000
```

## 원고 쓰는 법

자세한 규칙은 [Markdown → HWPX 변환 스펙](spec/markdown-conversion.md) 에 있습니다.

### 지원하는 Markdown

CommonMark 에 GFM 표·취소선, 각주, 수식을 더한 문법을 받습니다. 목록·인용문 안에는 어떤 블록이든 넣을 수 있습니다.

| Markdown | 한/글 문서 |
|----------|-----------|
| 헤딩 `#` ~ `######` | `개요 1` ~ `개요 6` 스타일 |
| 문단 | `본문` 스타일 |
| 글머리표·번호 목록 | 3단계까지 들여 씀 (더 깊은 항목은 3단계로) |
| 표 | 머리 행은 `표 헤더` 스타일. 열 정렬, 셀 안 `<br>` 줄바꿈 |
| 코드블록 | 1×1 표 (`코드블록` 스타일, 없으면 D2Coding 9pt), 구문 강조 |
| 이미지 | PNG·JPEG·GIF 는 그대로, 그 밖의 형식과 SVG 는 PNG 로 바꿔 넣음 |
| ` ```mermaid `, ` ```plantuml ` | 그림 |
| 수식 `$...$`, `$$...$$` | 한/글 수식 개체 (바꿀 수 없는 LaTeX 는 글자로 둠) |
| 각주 `[^1]` | 한/글 각주 (문단만) |
| 굵게, 기울임, 취소선, 코드, 링크 | 글자 모양, 하이퍼링크 |
| 인용문, 수평선, 강제 줄바꿈 | 왼쪽 선, 선, 줄바꿈 |

원시 HTML 은 글자 그대로 들어가고, HTML 주석은 넣지 않습니다.

### 캡션과 번호 참조

```markdown
[표 #결과] 측정 결과

| 항목 | 값 |
|------|----|
| A    | 1  |

자세한 값은 [[@결과]] 에 있다.
```

- `[표] 제목`, `[그림] 제목` 줄은 `[표 N-M] 제목` 캡션이 됩니다. `N` 은 장(수준 1 헤딩) 번호, `M` 은 그 장에서의 순번입니다.
- `[그림]` 줄이 없으면 이미지의 대체 텍스트가 그림 제목입니다.
- 캡션에 `#이름` 을 붙이면 글 안의 `[@이름]` 이 `표 N-M` 으로 바뀝니다 (`[[@이름]]` → `[표 N-M]`).

### 주석 마커

한/글 기능은 `<!-- @hwpx 명령 인자 -->` 로 지정합니다. 한 줄에 마커 하나만 둡니다.

| 주석 마커 | 하는 일 |
|-----------|---------|
| `<!-- @hwpx 차례 -->` | 차례. `차례 그림 표 제목` 처럼 넣을 차례와 순서를 지정 |
| `<!-- @hwpx 쪽나눔 -->` | 쪽 나누기 |
| `<!-- @hwpx 새번호 쪽 표 그림 수식 -->` | 적은 번호를 새로 시작 |
| `<!-- @hwpx 감추기 -->` | 그 쪽의 쪽 번호·머리말·꼬리말 숨김 |
| `<!-- @hwpx 여백면 "이 면은 여백" -->` | 여백 면 한 장 (문구는 없어도 됨) |
| `<!-- @hwpx 빈줄 3 -->` | 빈 문단 세 개 (10 개까지) |
| `<!-- @hwpx 열너비 30% 70% -->` | 바로 다음 표의 열 너비 비율 |
| `<!-- @hwpx 그림크기 너비=50% 높이=30mm -->` | 다음 블록의 첫 그림 크기 (`%` 또는 `mm`, 하나만 쓰면 비율 유지) |
| `<!-- @hwpx 스타일 "요약문-본문" -->` ... `<!-- @hwpx 스타일끝 -->` | 사이 문단에 템플릿의 그 문단 스타일 적용 |

- pandoc 원고와 이전 원고의 표기(`\newpage`, `{width=50%}`, `::: {custom-style="..."}`, `[TOC]`, `[감추기]` 등)도 받습니다.
  이 가운데 `[TOC]`·`[새 번호]`·`[감추기]`·`[여백면]`·`[빈 줄]` 은 없어질 표기라 경고합니다.
  대응표는 스펙의 ["주석 마커"](spec/markdown-conversion.md#주석-마커) 에 있습니다.
- 이 프로젝트의 표기를 글자 그대로 쓰려면 맨 앞에 `\` 를 붙입니다 (`\[감추기]`, `\[@결과]`, `\\newpage`).

### front matter

파일 맨 앞의 YAML front matter(`---` ... `---`)는 문서에 넣지 않으며, 다음 두 키만 읽습니다.

| 키 | 뜻 |
|----|----|
| `draft: true` | 이 파일을 변환에서 뺌 (`--include-draft` 로 포함) |
| `caption-numbering: document` | 장 번호 없이 문서 전체에서 센 `[표 7] 제목` 캡션. 기본은 `chapter` |

## 템플릿

`-t` 로 템플릿 HWPX 를 주지 않으면 현재 폴더의 `template.hwpx`, 그것도 없으면 내장 템플릿(`src/md2hwpx/template.hwpx`)을 씁니다.
직접 만든 템플릿에는 다음 문단 스타일이 있어야 합니다.

| 스타일 | 필수 | 쓰이는 곳 |
|--------|------|-----------|
| `개요 1` ~ `개요 6` | 필수 | 헤딩. 새 쪽에서 시작할지는 이 스타일의 문단 모양이 정함 (내장 템플릿은 `개요 1` 만) |
| `본문` | 필수 | 문단 |
| `바탕글` | 필수 | 표 셀, 그림을 담는 문단 |
| `표그림 제목` | 필수 | 캡션 |
| `코드블록` | 선택 | 코드블록과 문장 안 코드 (없으면 D2Coding 9pt) |
| `표 헤더` | 선택 | 표 머리 행 |
| `글머리표 1`, `글머리표 2`, … | 선택 | 글머리표 목록의 단계별 모양 (없으면 `-`, `○`, `□`) |
| `차례 제목`, `차례 1` ~ `차례 3` | 선택 | 차례의 제목과 수준별 항목 |

자세한 내용은 스펙의 ["템플릿 요구사항"](spec/markdown-conversion.md#템플릿-요구사항) 에 있습니다.

## 사용법

### 명령줄

실행 방법은 네 가지이며, 옵션은 모두 같습니다.

| 실행 방법 | 필요한 것 | 예 |
|-----------|-----------|----|
| `md2hwpx.sh` | 이 저장소, `md2hwpx` 이미지 | `/path/to/md2hwpx/md2hwpx.sh docs/guide.md -o docs/` |
| `md2hwpx-standalone.sh` | 이 파일 하나, `md2hwpx-web` 이미지 (amd64) | `~/bin/md2hwpx-standalone.sh docs/` |
| `md2hwpx-remote.py` | Python 3.9 이상, 실행 중인 웹 서비스 | `python3 md2hwpx-remote.py docs/guide.md --server http://md2hwpx.internal:8000` |
| `uv run md2hwpx` | `uv sync`, PATH 의 다이어그램 도구 | `uv run md2hwpx docs/guide.md` |

- 두 `.sh` 스크립트는 어느 폴더에서나 실행할 수 있고, 경로는 현재 폴더 밖이어도 됩니다.
  다만 입력 문서의 이미지는 입력 폴더나 현재 폴더 안에 있어야 합니다. 자세한 내용은 [Docker 스펙](spec/docker.md).
- `md2hwpx-remote.py` 는 웹 페이지의 "스크립트" 탭에서 내려받습니다 (저장소에서는 `src/md2hwpx/static/md2hwpx-remote.py`).
  md 와 그 문서가 쓰는 이미지를 묶어 웹 서비스에 보내고 HWPX 를 받아 저장합니다.
  서비스 주소는 `--server`, 환경 변수 `MD2HWPX_SERVER`, `http://localhost:8000` 순으로 정합니다.

```
md2hwpx INPUT [-o OUTPUT] [-t TEMPLATE.hwpx] [--include-draft] [--include PATTERN ...] [--chapter-start N]
```

| 인자 | 뜻 | 생략하면 |
|------|----|----------|
| `INPUT` | Markdown 파일, 또는 `.md` 파일이 든 폴더. 폴더면 바로 안의 `.md` 를 이름 순(`2.md` → `10.md`)으로 합쳐 문서 하나로 만듦 | 도움말 (`md2hwpx-remote.py` 는 사용법과 오류) |
| `-o`, `--output` | 저장할 HWPX 파일 또는 폴더 (`/` 로 끝나면 폴더로 보고 없으면 만듦) | 현재 폴더의 `입력 이름.hwpx` |
| `-t`, `--template` | 템플릿 HWPX | 현재 폴더의 `template.hwpx`, 없으면 내장 템플릿 |
| `--include PATTERN` | 이름이 패턴에 맞는 `.md` 만 변환. 여러 번 줄 수 있음 | 모든 `.md` |
| `--include-draft` | `draft: true` 파일도 변환 | draft 파일은 뺌 |
| `--chapter-start N` | 캡션의 장 번호를 `N` 부터 셈 (`ch_2*.md` 만 변환해도 `[표 2-1]`) | 1 |

- `--include` 의 패턴은 셸이 펼치지 않게 `'ch_2*.md'` 처럼 따옴표로 감쌉니다.
- 같은 이름의 파일이 있으면 덮어쓰지 않고 `이름-1.hwpx`, `이름-2.hwpx` ... 로 저장합니다.
- 성공하면 저장한 파일 경로를 출력합니다 (경고가 있어도 종료 코드 0). 변환에 실패하면 종료 코드 1, 옵션이 틀리면 2 입니다.

자세한 내용은 스펙의 ["명령줄"](spec/markdown-conversion.md#명령줄-md2hwpxcli) 에 있습니다.

### 웹 서비스

브라우저에서 Markdown 을 올려 HWPX 를 내려받습니다.

```sh
docker compose up -d --build              # http://localhost:8000
./build-images.sh && docker compose up -d  # 날짜 태그(md2hwpx-web:261030)도 함께 남기며 빌드
```

- 입력: md 와 이미지 파일 여러 개, md·이미지가 든 폴더의 zip, 또는 `http://`·`https://` git 저장소 주소.
- 변환할 경로, 파일 이름 패턴, draft 포함, 시작 장 번호, 템플릿은 페이지의 "선택 옵션" 에서 정합니다.
- 한 번에 올릴 수 있는 크기는 50 MB 이며, `.env` 의 `MD2HWPX_MAX_UPLOAD_MB` 로 바꿉니다 (`.env.example` 참고).
- 폐쇄망 GitLab·Gitea 의 비공개 저장소는 페이지에서 그 서버에 로그인(OAuth2)해 받습니다.
  설정은 스펙의 ["GitLab·Gitea 로그인"](spec/web-service.md#gitlabgitea-로그인-oauth2) 을 따릅니다.
- 웹 서비스 자체의 접근 인증은 없으니 외부에 공개할 때는 앞단에서 접근을 제한하세요.

API 와 보안은 [웹 서비스 스펙](spec/web-service.md) 에 있습니다.

### Python

```python
from pathlib import Path
from md2hwpx.convert import DEFAULT_TEMPLATE, convert

doc = Path("docs/guide.md")
convert(doc, DEFAULT_TEMPLATE, doc.with_suffix(".hwpx"))
```

`convert` 는 다이어그램 코드블록을 그림으로 바꾸지 않습니다. 다이어그램이 있으면 먼저 `render_diagrams` 로 바꿉니다.

```python
from md2hwpx.convert import convert_markdown
from md2hwpx.diagram import render_diagrams
from md2hwpx.source import caption_numbering, read_markdown

markdown = render_diagrams(read_markdown(doc), out_dir=doc.parent / "images", link_base=doc.parent)
convert_markdown(markdown, doc.parent, DEFAULT_TEMPLATE, doc.with_suffix(".hwpx"), caption_numbering([doc]))
```

함수별 설명은 스펙의 ["공개 API"](spec/markdown-conversion.md#공개-api) 와 [다이어그램 스펙](spec/diagram-rendering.md) 에 있습니다.

## 개발

```sh
# Docker (다이어그램 도구 포함)
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work md2hwpx pytest -q -p no:cacheprovider

# 로컬 (uv 0.4.27 이상)
uv sync
uv run pytest -q
```

- Python 의존성(`pyproject.toml`, `uv.lock`)이 바뀌면 Docker 이미지를 다시 빌드합니다.
- 로컬에서 `mmdc`, `plantuml`, `rsvg-convert` 가 PATH 에 없으면 해당 렌더링 테스트는 skip 됩니다.
- `Failed to spawn: pytest` 가 나면 `uv --version` 과 `UV_NO_DEV` 같은 환경 변수가 설정되어 있지 않은지 확인하세요.

```
src/md2hwpx/    소스 코드와 내장 템플릿(template.hwpx)
tests/          pytest 테스트
spec/           구현 스펙 문서
samples/        변환 테스트용 샘플 Markdown (결과 HWPX 는 커밋하지 않음)
sample-report/  여러 파일로 쓴 보고서 예제
docker/         Docker 이미지용 실행 스크립트와 설정
Dockerfile      실행용 Alpine 이미지 (dev: 코드 마운트, web: 웹 서비스)
compose.yaml    웹 서비스 실행
md2hwpx.sh      Docker 이미지로 변환하는 실행 스크립트 (저장소 코드를 마운트)
md2hwpx-standalone.sh  복사해 혼자 쓰는 실행 스크립트 (웹 서비스 이미지의 코드로 변환)
build-images.sh 개발용·웹 서비스 이미지를 latest·날짜 태그로 빌드하는 스크립트
```

## 스펙 문서

구현 내용은 [spec](spec/README.md) 폴더에 정리되어 있습니다.

- [Markdown → HWPX 변환](spec/markdown-conversion.md)
- [다이어그램 코드블록 → PNG 변환](spec/diagram-rendering.md)
- [실행용 Docker 이미지](spec/docker.md)
- [웹 서비스](spec/web-service.md)

## 라이선스

[MIT](LICENSE)
