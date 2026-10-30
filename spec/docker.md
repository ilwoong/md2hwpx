# 실행용 Docker 이미지

## 목적

다이어그램 렌더링에 필요한 외부 도구(mermaid-cli, Chromium, Java, PlantUML, Graphviz)를
호스트에 설치하지 않고, 한 이미지 안에서 실행한다.

Dockerfile 은 세 단계다.

| 단계 | 내용 | 빌드 |
|------|------|------|
| `tools` | 아래 "이미지 내용" 의 도구와 Python 의존성 | (다른 단계의 바탕) |
| `web` | `tools` + `git`, 코드(`/app/src`, 내장 템플릿 포함). `nobody` 로 uvicorn 실행 ([web-service.md](web-service.md)) | `docker build --target web -t md2hwpx-web .` 또는 `docker compose build` |
| `dev` (기본) | `tools` 그대로. 코드는 실행 시 `/work` 에 마운트한다 | `docker build -t md2hwpx .` |

아래 "실행 환경 변수", "사용법", "실행 스크립트" 는 `dev` 이미지 이야기다.
`web` 이미지는 `PYTHONPATH=/app/src`, `PLANTUML_SECURITY_PROFILE=SANDBOX` 로 바꾼다.

## 구성

| 파일 | 역할 |
|------|------|
| `Dockerfile` | `alpine:3.22` 기반 이미지 |
| `docker/mmdc` | mermaid-cli 실행 스크립트. `-p /etc/puppeteer.json` 을 항상 넘긴다 |
| `docker/puppeteer.json` | 시스템 Chromium 경로와 `--no-sandbox` 설정 |
| `docker/plantuml` | `java -jar plantuml.jar` 실행 스크립트 |
| `.dockerignore` | 빌드 컨텍스트를 `pyproject.toml`, `uv.lock`, `docker/`, `src/`(`__pycache__` 제외, 내장 템플릿 포함) 로 제한 |
| `compose.yaml` | `web` 단계를 빌드해 8000 번 포트로 실행 |
| `build-images.sh` | `dev`·`web` 이미지를 빌드해 `latest` 와 날짜 태그로 남긴다 (아래 "빌드 스크립트") |

## 이미지 내용

| 항목 | 버전 / 출처 | 비고 |
|------|------|------|
| Python | Alpine `python3` (3.12) | |
| mermaid-cli | npm `@mermaid-js/mermaid-cli` 11.4.2 | `PUPPETEER_SKIP_DOWNLOAD=1`, Alpine `chromium` 사용 |
| PlantUML | GitHub 릴리스 jar 1.2026.9 | `/opt/plantuml/plantuml.jar` |
| Java | Alpine `openjdk17-jre` | headless JRE 에는 폰트 라이브러리가 없어 PlantUML 이 실패한다 |
| Graphviz | Alpine `graphviz` | PlantUML 의 시퀀스 외 다이어그램에 필요 |
| 한글 폰트 | Alpine `font-noto-cjk` | 다이어그램 안의 한글 표시 |
| rsvg-convert | Alpine `rsvg-convert` | SVG 이미지를 PNG 로 변환. 없으면 SVG 는 대체 이미지가 된다 |
| D2Coding | 네이버 GitHub 릴리스 VER1.4.0 | Regular/Bold TTF 와 `OFL.txt` 를 `/usr/share/fonts/d2coding` 에 설치. Alpine 패키지 없음 |
| Python 의존성 | `uv sync --frozen --no-install-project` → `/opt/venv` | uv 는 빌드 시에만 마운트하고 이미지에 남기지 않는다. 기본 그룹(`dev`, `web`)도 설치한다 |
| HWPX 라이브러리 | PyPI `python-hwpx` (`pyproject.toml` 의존성) | 순수 Python. 의존성 `lxml` 은 musl 휠이 있어 Alpine 에서 컴파일 없이 설치된다 |
| 구문 강조 | PyPI `pygments` (`pyproject.toml` 의존성) | 순수 Python |

버전은 `ARG PLANTUML_VERSION`, `ARG MERMAID_CLI_VERSION`, `ARG D2CODING_VERSION` / `ARG D2CODING_DATE` 로 바꿀 수 있다.
D2Coding 은 시스템 폰트로만 등록한다. 다이어그램에서 쓰려면 mermaid 는 `fontFamily`, plantuml 은 `skinparam defaultFontName` 으로 지정한다.

## 실행 환경 변수

| 변수 | 값 | 이유 |
|------|------|------|
| `PATH` | `/opt/venv/bin` 우선 | `pytest`, `python` 이 venv 를 쓴다 |
| `PYTHONPATH` | `/work/src` | 마운트한 코드를 설치 없이 import |
| `PYTHONDONTWRITEBYTECODE` | `1` | 마운트 폴더에 `__pycache__` 를 만들지 않음 |
| `HOME` | `/tmp` | `--user` 실행 시에도 Chromium 이 쓸 수 있는 HOME |

## 사용법

```sh
docker build -t md2hwpx .

# 테스트 실행. --user 로 마운트 폴더에 root 소유 파일이 생기지 않게 한다.
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work md2hwpx pytest -q -p no:cacheprovider
```

Python 의존성(`pyproject.toml`, `uv.lock`)이 바뀌면 이미지를 다시 빌드한다.

## 실행 스크립트 (`md2hwpx.sh`)

긴 `docker run` 명령 없이, 어느 폴더에서나 변환할 수 있게 하는 POSIX sh 스크립트.

```sh
/path/to/md2hwpx/md2hwpx.sh docs/guide.md [-o OUTPUT] [-t TEMPLATE]
```

- 현재 폴더를 `/work` (작업 폴더)로, 이 저장소를 `/md2hwpx` 로 읽기 전용 마운트하고
  `PYTHONPATH=/md2hwpx/src` 로 `python -m md2hwpx` 를 실행한다. 인자는 그대로 넘긴다.
  그래서 변환할 문서가 있는 폴더에 코드가 없어도 된다.
- `--user "$(id -u):$(id -g)"` 로 실행해 결과 파일이 root 소유가 되지 않게 한다.
- `-t` / `--template` 가 없으면 명령줄과 같이 현재 폴더의 `template.hwpx`, 없으면 내장 템플릿을 쓴다
  (마운트한 저장소의 `src/md2hwpx/template.hwpx`).
- 제한: 컨테이너에서는 현재 폴더 아래만 보이므로, 입력·출력·템플릿 경로는 현재 폴더 안의 상대 경로여야 한다.
  `../` 처럼 밖을 가리키면 "No such file or directory" 오류가 난다.
- 이미지 이름은 `md2hwpx` 로 고정이다 (`docker build -t md2hwpx .` 로 만든 이미지).

## 빌드 스크립트 (`build-images.sh`)

이미지를 빌드할 때마다 그날 날짜 태그를 남겨, 이전 이미지로 되돌릴 수 있게 하는 POSIX sh 스크립트.

```sh
/path/to/md2hwpx/build-images.sh
```

- 저장소 폴더로 옮겨 `dev` 이미지(`docker build -t md2hwpx .`)와 `web` 이미지(`docker compose build web`)를 빌드한 뒤,
  각각의 `latest` 에 `<YYMMDD>` 태그(`date +%y%m%d`, 예: `md2hwpx:261030`, `md2hwpx-web:261030`)를 붙인다.
  어느 폴더에서나 실행할 수 있다. 두 이미지는 `tools` 단계를 함께 쓰므로 공통 부분은 한 번만 빌드된다.
- 같은 날 다시 빌드하면 그날 태그를 새 이미지로 옮긴다. 이전 날짜 태그는 그대로 남는다.
- 빌드만 한다. 실행 중인 웹 서비스는 `docker compose up -d` 로 바꾼다. 중간에 실패하면 거기서 멈추고 태그를 붙이지 않는다.
- 바뀐 것이 없어 모든 단계가 캐시돼도 이미지 ID 는 빌드마다 바뀔 수 있다. 빌드 기록(attestation)에 빌드 시각이 들어가기 때문이며,
  레이어는 같다.

## 검증 기준

| 항목 | 방법 |
|------|------|
| 도구 설치 | 컨테이너에서 `mmdc --version`, `plantuml -version` |
| python-hwpx | 컨테이너에서 PNG 를 넣은 HWPX 를 저장하고 다시 열기, `hwpx-validate` 통과 |
| 전체 테스트 | root 와 `--user` 두 방식 모두 skip 없이 통과 |
| D2Coding | `fc-list` 에 Regular/Bold 가 보이고, 폰트를 지정한 두 다이어그램이 고정폭으로 그려짐 |
| 한글 렌더링 | 한글 라벨을 가진 mermaid / plantuml 다이어그램 PNG 를 눈으로 확인 |
| 실행 스크립트 | 저장소 밖 폴더에서 다이어그램이 있는 문서 변환, `-o` 폴더, 인자 없이 도움말, 내장 템플릿 자동 사용, 밖 경로 오류 |
| 빌드 스크립트 | 저장소 밖 폴더에서 실행, 두 이미지 모두 `latest` 와 날짜 태그가 같은 이미지(같은 레이어) |
| 마운트 폴더 오염 없음 | 실행 후 프로젝트 폴더에 core 덤프, `?` 폴더 등 새 파일이 없음 |
