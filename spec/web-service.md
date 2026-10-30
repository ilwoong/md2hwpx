# 웹 서비스

## 목적

브라우저에서 Markdown 을 올려 HWPX 파일을 내려받는다. 명령줄과 같은 변환(`md2hwpx.cli.convert_input`)을 쓴다.
다이어그램 도구가 필요하므로 [docker.md](docker.md) 의 `web` 이미지로 실행한다.

## 실행

```sh
docker compose up -d --build        # http://localhost:8000
# 또는
docker build --target web -t md2hwpx-web .
docker run --rm -p 8000:8000 md2hwpx-web
```

로컬에서는 `uv run uvicorn md2hwpx.web:app` (다이어그램은 `mmdc`, `plantuml` 이 PATH 에 있을 때만).

## 구성

| 파일 | 역할 |
|------|------|
| `src/md2hwpx/web.py` | FastAPI 앱 (`md2hwpx.web:app`) |
| `src/md2hwpx/static/index.html` | 변환 페이지. 외부 리소스 없이 한 파일 |
| `compose.yaml` | `web` 이미지를 빌드해 8000 번 포트로 실행 |

의존성은 `pyproject.toml` 의 `web` 그룹(`fastapi`, `uvicorn`, `python-multipart`)이다.
`[tool.uv] default-groups` 에 넣어 `uv sync` 와 Docker 이미지에 함께 설치한다.

## API

### `GET /`

변환 페이지. 입력 방식(파일 / zip / git 저장소)을 고르고, 템플릿을 선택해 올릴 수 있다.
변환이 끝나면 응답 헤더의 파일 이름으로 내려받고, 실패하면 `detail` 메시지를 보여 준다.

### `POST /convert`

`multipart/form-data`.

| 필드 | 값 |
|------|------|
| `source` | `files`, `zip`, `git` 중 하나 (필수) |
| `files` | `source=files`: md 와 이미지 파일들 (여러 개). 파일 이름만 쓰고 폴더는 버린다 |
| `archive` | `source=zip`: zip 파일 |
| `repo` | `source=git`: `https://` 저장소 주소 |
| `ref` | `source=git`: 브랜치 또는 태그 (선택, 비우면 기본 브랜치) |
| `path` | `zip`·`git`: 변환할 md 파일 또는 폴더의 상대 경로 (선택) |
| `template` | 템플릿 HWPX (선택, 비우면 저장소의 `template.hwpx`) |

- 성공: `200`, `Content-Type: application/hwp+zip`,
  `Content-Disposition: attachment; filename="document.hwpx"; filename*=UTF-8''<이름>.hwpx`.
  - 넣을 수 없는 이미지(없는 파일, 읽을 수 없는 형식 등)는 대체 이미지로 변환하고, `X-Md2hwpx-Warnings` 헤더에 경고를 한 줄씩 담는다
    (`docs/a.md:3: 이미지 파일이 없어 대체 이미지를 넣었습니다: img/b.png`).
    헤더는 Latin-1 만 담으므로 퍼센트 인코딩하고, 페이지가 풀어서 내려받기 안내 아래에 경고 색으로 보여 준다.
    경고가 없으면 헤더도 없다.
- 실패: `400` (업로드 크기 초과는 `413`), `{"detail": "<한국어 메시지>"}`.
  메시지와 경고의 경로는 서버 임시 폴더가 아니라 올린 내용의 맨 위 폴더 기준이다.

## 입력 처리

요청마다 임시 폴더를 만들고, 그 안의 `input/` 을 맨 위 폴더(root)로 삼는다. 응답 후 지운다.

- `files`: 올린 파일을 root 에 나란히 저장한다. 이미지는 파일 이름으로 찾으므로 `![](a.png)` 처럼 써야 한다.
- `zip`: root 에 압축을 푼다.
  - UTF-8 플래그가 없는 항목 이름은 cp949 로 읽는다 (한국어 Windows 의 zip). cp949 로 읽을 수 없으면 zipfile 기본(cp437)대로 둔다.
  - 암호가 걸렸거나 zip 이 아니면 오류.
- `git`: `git clone --depth 1 [--branch <ref>]` 로 root 에 받고 `.git` 을 지운다. 제한 시간 120 초.
  `GIT_TERMINAL_PROMPT=0` 이라 인증이 필요한 저장소는 기다리지 않고 실패한다.

변환할 대상:

- `path` 가 있으면 root 아래의 그 파일 또는 폴더.
- 없으면, root 에 폴더 하나만 있을 때(폴더를 압축한 zip) 그 폴더로 들어간 뒤,
  `.md` 가 하나면 그 파일, 아니면 그 폴더 (명령줄의 폴더 입력처럼 바로 안의 `.md` 를 이름 순으로 이어 붙인다).
- 내려받는 파일 이름: 대상이 파일이면 확장자를 뺀 이름, 폴더면 `path` 의 마지막 이름,
  `path` 가 없으면 zip 파일 이름 / 저장소 이름 / 처음 올린 md 이름 (없으면 `document`).

## 보안

변환 요청이 서버의 파일을 읽어 HWPX 에 담거나 다른 곳에 접속하지 못하게 한다.

| 위험 | 막는 방법 |
|------|------|
| 이미지 경로로 서버 파일 읽기 (`../x.png`, `/etc/x`, `%2E%2E/`) | 변환 전에 모든 md 의 이미지 경로(`convert.image_paths`)를 풀어 root 밖이면 거부 |
| 심볼릭 링크 (git 저장소 안) | `core.symlinks=false` 로 받고, 남은 심볼릭 링크는 모두 지운다 |
| zip slip (`../evil.md`) | 푼 위치가 root 밖이면 거부 |
| `path` 로 root 밖 지정 | 풀어 root 밖이거나 없으면 거부 |
| git 의 `file://`, ssh, 로컬 경로 | 주소는 `https://` 만. `-c protocol.allow=never -c protocol.https.allow=always` |
| `ref` 를 git 옵션으로 넘기기 (`--upload-pack=…`) | 글자·숫자·`_` 로 시작하고 `./-` 만 더 허용. 주소 앞에 `--` |
| PlantUML `!include` 로 파일·URL 읽기 | 이미지에 `PLANTUML_SECURITY_PROFILE=SANDBOX` |
| 큰 업로드·zip 폭탄 | 요청마다 디스크에 쓰는 양(업로드와 푼 파일, 템플릿 합계) 50 MB, zip 항목 2000 개 |
| 컨테이너 권한 | `nobody` 사용자로 실행 |

제한:

- `https://` 주소라도 서버가 닿는 내부 주소로는 clone 을 시도할 수 있다. 외부에 공개할 때는 네트워크에서 막는다.
- git 저장소는 받는 크기를 제한하지 않는다 (제한 시간만 있다).
- 동시 요청 수를 제한하지 않는다. 다이어그램 렌더링(Chromium, Java)은 요청마다 프로세스를 띄운다.
- 인증이 없다. 공개된 곳에 둘 때는 앞단(리버스 프록시 등)에서 접근을 제한한다.

## 검증 기준

| 항목 | 방법 |
|------|------|
| 파일 입력 | md + 이미지 → 그림이 든 HWPX, 한글 파일 이름 헤더, 없는 이미지의 대체 이미지와 경고 헤더(올린 이름 기준), 경고 없으면 헤더 없음, md 없음 |
| zip 입력 | 폴더 하나를 압축한 zip 의 이름 순 합치기와 하위 폴더 이미지, `.md` 로 끝나는 폴더 제외, `path` 로 파일 고르기, cp949 이름, zip 아님 |
| git 입력 | clone 명령의 `--branch`, `--`, 프로토콜·심볼릭 링크 설정 (테스트는 `subprocess.run` 을 대신함), `.git` 제거 |
| 보안 | root 밖 이미지 3가지, zip slip, root 밖 `path`, https 아닌 주소 4가지, 옵션 모양의 `ref`, 심볼릭 링크 제거, 업로드 크기 제한 |
| 템플릿 | 올린 템플릿으로 변환, HWPX 가 아닌 템플릿은 오류 |
| 컨테이너 | `docker compose up` 후 세 입력 방식 모두 다이어그램 포함 변환, PlantUML `!include /etc/passwd` 실패, `nobody` 실행 |
| 페이지 | 헤드리스 Chromium 으로 파일 올려 내려받기, 탭 전환, 오류 메시지 표시 |
