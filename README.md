# md2hwpx

Markdown 파일을 한/글 문서 형식인 HWPX 파일로 변환하는 Python 프로젝트입니다.

## 현재 기능

- Markdown 안의 ` ```mermaid ` 코드블록을 PNG 이미지로 변환
- Markdown 안의 ` ```plantuml ` 코드블록을 PNG 이미지로 변환
- 변환한 코드블록을 이미지 링크 `![](파일.png)` 로 치환
- 템플릿 HWPX 를 열어 Markdown 내용을 추가하고 새 HWPX 로 저장
  - 헤딩(`#`~`######`) → `개요 1`~`개요 6` 스타일, 일반 문단 → `본문` 스타일
  - 이미지, 표(머리 행 굵게·회색 배경, 열 정렬), 코드블록(1×1 표, D2Coding 9pt, 줄간격 140%, Pygments 구문 강조)
  - `[표] 제목` / `[그림] 제목` 줄 → `[표 N] 제목` / `[그림 N] 제목` 캡션 (`표그림제목` 스타일)

HWPX 생성에는 [python-hwpx](https://github.com/airmang/python-hwpx) 를 사용합니다.
템플릿에는 `개요 1`~`개요 6`, `본문`, `표그림제목` 스타일이 있어야 합니다.
인라인 서식, 목록, 인용문은 아직 지원하지 않습니다.

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

```python
from pathlib import Path
from md2hwpx.convert import convert
from md2hwpx.diagram import render_diagrams

doc = Path("docs/guide.md")
markdown = render_diagrams(doc.read_text(), out_dir=doc.parent / "images", link_base=doc.parent)
rendered = doc.with_name("guide.rendered.md")
rendered.write_text(markdown, encoding="utf-8")

convert(rendered, Path("template.hwpx"), doc.with_suffix(".hwpx"))
```

다이어그램은 `docs/images/mermaid-<해시>.png` 같은 이름으로 저장됩니다.
반환된 Markdown 에서 코드블록은 `![](images/mermaid-<해시>.png)` 로 바뀝니다.
`convert` 는 이미지 경로를 Markdown 파일 위치 기준으로 찾으므로, 렌더링 결과는 원본과 같은 폴더에 저장합니다.

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
```

## 스펙 문서

구현 내용은 [spec](spec/README.md) 폴더에 정리되어 있습니다.

- [다이어그램 코드블록 → PNG 변환](spec/diagram-rendering.md)
- [실행용 Docker 이미지](spec/docker.md)
- [Markdown → HWPX 변환](spec/markdown-conversion.md)

## 라이선스

[MIT](LICENSE)
