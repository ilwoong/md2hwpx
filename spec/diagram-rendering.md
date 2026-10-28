# 다이어그램 코드블록 → PNG 변환

## 목적

HWPX는 mermaid / plantuml 문법을 직접 표시할 수 없다.
따라서 변환 전에 Markdown 안의 다이어그램 코드블록을 PNG 이미지로 렌더링하고,
코드블록을 이미지 참조로 바꾼다. 이후 단계(HWPX 생성)는 일반 이미지로 다루면 된다.

## 범위

- 포함: ` ```mermaid `, ` ```plantuml ` 펜스 코드블록 감지, PNG 렌더링, 코드블록 → `![](경로)` 치환.
- 제외: HWPX 생성, SVG 등 다른 출력 형식, 원격 렌더링 서버(Kroki, PlantUML 서버) 사용.

## 외부 의존성

렌더링은 로컬 CLI 도구를 subprocess 로 호출한다.

| 언어 | 명령 | 설치 |
|------|------|------|
| mermaid | `mmdc -i - -o <out.png>` (stdin 입력) | `npm install -g @mermaid-js/mermaid-cli` (Chrome/puppeteer 필요) |
| plantuml | `plantuml -tpng -pipe` (stdin → stdout) | PlantUML jar + Java. `plantuml` 실행 스크립트가 PATH 에 있어야 함 |

명령이 PATH 에 없으면 `DiagramError` 를 발생시킨다.
두 도구가 모두 설치된 실행 환경은 [docker.md](docker.md) 의 이미지로 제공한다.

## 코드블록 감지 규칙

CommonMark 펜스 코드블록 규칙의 부분집합을 따른다.

1. 여는 펜스: 줄 앞 공백 0~3칸 + ` ``` ` 또는 `~~~` 3개 이상 + info string.
2. info string 의 첫 단어가 `mermaid` 또는 `plantuml` 이면 대상이다 (대소문자 구분 없음).
3. 닫는 펜스: 같은 문자, 여는 펜스 이상의 길이, 뒤에 공백 외 내용 없음.
4. 닫는 펜스가 없으면 대상에서 제외하고 원문을 그대로 둔다.
5. 다른 언어의 코드블록 안에 있는 ` ```mermaid ` 줄은 감지하지 않는다.

## 출력 파일 규칙

- 파일명: `<언어>-<코드 SHA-256 앞 12자리>.png` (예: `mermaid-3f2a9c1b0d4e.png`).
  같은 코드는 같은 파일명이 되므로 중복 렌더링을 피할 수 있다.
- 출력 폴더는 호출자가 지정하며, 없으면 생성한다.
- 이미 같은 이름의 파일이 있으면 렌더링을 생략한다.

## 치환 규칙

코드블록 전체(펜스 포함)를 다음 한 줄로 바꾼다.

```
![](<image_path>)
```

`<image_path>` 는 `link_base` 인자가 주어지면 그 폴더 기준 상대 경로, 없으면 출력 폴더 기준 파일명이다.
경로 구분자는 항상 `/` 를 사용한다.

## 공개 API (`md2hwpx.diagram`)

```python
class DiagramError(Exception): ...

@dataclass(frozen=True)
class DiagramBlock:
    lang: str    # "mermaid" | "plantuml"
    code: str    # 펜스를 제외한 본문
    start: int   # 여는 펜스 줄 인덱스 (0부터)
    end: int     # 닫는 펜스 줄 인덱스 (포함)

def find_diagram_blocks(markdown: str) -> list[DiagramBlock]
def render_mermaid(code: str, out_path: Path) -> None
def render_plantuml(code: str, out_path: Path) -> None
def render_diagrams(markdown: str, out_dir: Path, link_base: Path | None = None) -> str
```

- `render_*` 는 도구가 0이 아닌 코드로 끝나거나 PNG 를 만들지 못하면 `DiagramError` 를 발생시킨다.
  메시지에는 도구의 stderr 를 포함한다.
- `render_diagrams` 는 치환된 Markdown 문자열을 반환한다. 다이어그램이 없으면 입력을 그대로 반환한다.

## 검증 기준

| 항목 | 방법 |
|------|------|
| 블록 감지 (백틱/물결, 들여쓰기, 긴 펜스, 미종료, 중첩) | 단위 테스트, 외부 도구 불필요 |
| mermaid PNG 생성 | `mmdc` 있을 때만 실행, PNG 시그니처 확인 |
| plantuml PNG 생성 | `plantuml` 있을 때만 실행, PNG 시그니처 확인 |
| 문법 오류 시 `DiagramError` | 각 도구로 잘못된 코드 렌더링 |
| 치환 결과 | 이미지 링크 줄로 바뀌고 나머지 본문은 유지 |
