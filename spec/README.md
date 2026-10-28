# md2hwpx 스펙

Markdown 파일을 HWPX 파일로 변환하는 Python 프로젝트.

## 문서 목록

| 문서 | 내용 | 상태 |
|------|------|------|
| [diagram-rendering.md](diagram-rendering.md) | mermaid / plantuml 코드블록을 PNG로 변환 | 구현됨 |
| [docker.md](docker.md) | 필요한 도구를 모두 담은 실행용 Docker 이미지 | 구현됨 |

## 공통 원칙

- 루트의 `CLAUDE.md` 지침을 따른다. 요청된 기능만 최소한으로 구현한다.
- Python 3.10 이상, `src/` 레이아웃, `uv` 로 의존성을 관리한다.
- 테스트는 `pytest` 로 작성한다. 외부 도구가 없으면 해당 테스트는 skip 한다.
