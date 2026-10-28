# md2hwpx

Markdown 파일을 한/글 문서 형식인 HWPX 파일로 변환하는 Python 프로젝트입니다.

## 현재 기능

- Markdown 안의 ` ```mermaid ` 코드블록을 PNG 이미지로 변환
- Markdown 안의 ` ```plantuml ` 코드블록을 PNG 이미지로 변환
- 변환한 코드블록을 이미지 링크 `![](파일.png)` 로 치환
- 템플릿 HWPX 를 열어 Markdown 내용을 추가하고 새 HWPX 로 저장
  - Markdown 은 markdown-it-py 로 파싱 (CommonMark + GFM 표·취소선 + 각주). 목록·인용문 안에 어떤 블록이든 중첩 가능
  - 헤딩(ATX `#`~`######`, Setext `===`/`---`) → `개요 1`~`개요 6` 스타일, 일반 문단 → `본문` 스타일
  - 이미지(문장 안·참조식 포함), 표(머리 행 굵게·회색 배경, 열 정렬), 코드블록(1×1 표, D2Coding 9pt, 줄간격 140%, Pygments 구문 강조)
  - 글머리표·번호 목록 (하위 목록 3단계까지)
  - 각주(`[^1]` / `[^1]: 내용`), `[TOC]` 자리에 한/글 차례, `\newpage` 줄에서 쪽 나누기
  - 저장 전 HWPX 스키마 검증, 없는 이미지는 `파일:줄` 위치와 함께 알림
  - 파일 맨 앞의 YAML front matter(`---` ... `---`)는 변환하지 않고 무시
  - `[표] 제목` / `[그림] 제목` 줄 → `[표 N] 제목` / `[그림 N] 제목` 캡션 (`표그림제목` 스타일)

HWPX 생성에는 [python-hwpx](https://github.com/airmang/python-hwpx) 를 사용합니다.
템플릿에는 `개요 1`~`개요 6`, `본문`, `표그림제목` 스타일이 있어야 합니다.
인라인 서식은 코드(D2Coding 글꼴), 굵게, 기울임, 취소선, 링크(한/글 하이퍼링크, 참조식 포함)를 지원합니다.
인용문(왼쪽 선), 수평선, 강제 줄바꿈(줄 끝 공백 두 칸 또는 `\`)도 지원합니다. 원시 HTML 은 글자 그대로 들어갑니다.

## 실행 환경

다이어그램 렌더링에 mermaid-cli, Chromium, Java, PlantUML 이 필요합니다.
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
    python -m md2hwpx docs/guide.md -o docs/ -t template.hwpx

# 로컬 (uv sync 후)
uv run md2hwpx docs/guide.md          # ./guide.hwpx
uv run md2hwpx docs/chapters/         # 폴더 안의 .md 를 이름 순으로 합쳐 ./chapters.hwpx
```

- `md2hwpx.sh` 는 현재 폴더를 컨테이너에 마운트하므로 입력·출력·템플릿은 현재 폴더 안에 있어야 한다.
  `-t` 를 생략하고 현재 폴더에 `template.hwpx` 가 없으면 이 저장소의 `template.hwpx` 를 쓴다.
- 인자 없이 실행하면 도움말을 보여 준다.
- 입력: Markdown 파일, 또는 `.md` 파일들이 있는 폴더.
  폴더면 그 폴더 바로 안의 `.md` 파일을 이름 순(숫자는 크기 순: `2.md` → `10.md`)으로 이어 붙여 하나의 HWPX 로 만든다.
  하위 폴더는 읽지 않는다.
- `-o`, `--output`: 저장할 HWPX 파일 또는 폴더.
  - 폴더면 그 폴더에 `입력 이름.hwpx` 로 저장한다.
  - 생략하면 현재 작업 폴더에 `입력 이름.hwpx` 로 저장한다.
  - 같은 이름의 파일이 있으면 덮어쓰지 않고 `이름-1.hwpx`, `이름-2.hwpx` ... 로 저장한다.
- `-t`, `--template`: 템플릿 HWPX 파일. 생략하면 현재 폴더의 `template.hwpx`.

다이어그램은 임시 폴더에 렌더링해 HWPX 에 넣고 지우므로, 입력 폴더에 이미지 파일이 남지 않습니다.
성공하면 저장한 파일 경로를 출력하고, 실패하면 오류를 stderr 에 출력하고 종료 코드 1 로 끝납니다.

### Python

```python
from pathlib import Path
from md2hwpx.convert import convert_markdown
from md2hwpx.diagram import render_diagrams

doc = Path("docs/guide.md")
markdown = render_diagrams(doc.read_text(encoding="utf-8"), out_dir=doc.parent / "images", link_base=doc.parent)
convert_markdown(markdown, doc.parent, Path("template.hwpx"), doc.with_suffix(".hwpx"))
```

다이어그램은 `docs/images/mermaid-<해시>.png` 같은 이름으로 저장되고,
반환된 Markdown 에서 코드블록은 `![](images/mermaid-<해시>.png)` 로 바뀝니다.
이미지 경로는 `convert_markdown` 의 두 번째 인자(기준 폴더)를 기준으로 찾습니다.
파일을 바로 변환할 때는 `convert(md_path, template_path, out_path)` 를 씁니다.

## 로컬 개발

컨테이너 없이 개발할 때는 [uv](https://docs.astral.sh/uv/) 를 사용합니다.

```sh
uv sync
uv run pytest -q
```

`mmdc` 나 `plantuml` 이 PATH 에 없으면 해당 렌더링 테스트는 skip 됩니다.

## 프로젝트 구조

```
src/md2hwpx/    소스 코드
tests/          pytest 테스트
spec/           구현 스펙 문서
samples/        변환 테스트용 샘플 Markdown (결과 HWPX 는 커밋하지 않음)
docker/         Docker 이미지용 실행 스크립트와 설정
Dockerfile      실행용 Alpine 이미지
template.hwpx   변환에 사용하는 템플릿 HWPX
md2hwpx.sh      Docker 이미지로 변환하는 실행 스크립트
```

## 스펙 문서

구현 내용은 [spec](spec/README.md) 폴더에 정리되어 있습니다.

- [다이어그램 코드블록 → PNG 변환](spec/diagram-rendering.md)
- [실행용 Docker 이미지](spec/docker.md)
- [Markdown → HWPX 변환](spec/markdown-conversion.md)

## 라이선스

[MIT](LICENSE)
