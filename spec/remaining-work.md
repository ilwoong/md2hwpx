# 남은 작업

주석 마커(`<!-- @hwpx ... -->`)를 들인 뒤 이어서 할 일. 끝낸 항목은 이 문서에서 지운다.

## 지금 상태 (2026-11-16)

- `feature/deprecate-marker-lines`: 이 프로젝트만의 표기 줄(`[TOC]`·`[새 번호]`·`[감추기]`·`[빈 줄]`·`[여백면]`)에
  폐지 예고 경고(`source.old_markers`), 샘플을 주석 마커로 이전. 샘플 HWPX 는 이전 전후로 같다(필드 id 제외).
- `feature/marker-near-miss` (위 브랜치에서 갈라짐): `@HWPX`·`@hwpx:`·`@hwp`·`@ hwpx`·`@hwpx쪽나눔` 처럼
  마커와 비슷한 주석을 경고 (`parser._LIKE_MARKER_START`, 이유 `'@hwpx 명령' 꼴이 아니어서`).
  띄어 쓴 명령(`쪽 나눔`)에 맞는 이름을 알려 주고 (`모르는 명령이라 (쪽나눔?)`), 스펙에 주석 마커의 한계를 적었다.
  README 에 uv 최소 버전(0.4.27)을 적었다: 그보다 옛 uv 나 `UV_NO_DEV` 에서 `uv run pytest` 가 `Failed to spawn: pytest` 로 실패한다.
- 두 브랜치 모두 아직 main 에 합치지 않았다. `feature/marker-near-miss` 하나로 PR 을 만들면 둘 다 들어간다.

## 할 일

### 1. 없어질 표기 지우기 (폐지 3단계)

- 언제: 쓰는 원고를 변환해도 `없어질 표기입니다` 경고가 하나도 나오지 않을 때. 다른 사용자가 있으면 버전을 올리며 알린다.
- 먼저: 주석 마커가 표기 줄을 거쳐 읽힌다 (`parser._line_command` 가 `"[새 번호{}]"` 를 만들어 `pieces.match_marker` 로 검사).
  마커가 인자를 직접 읽도록 바꾼 뒤, 표기 줄과 같은 결과인지 `tests/test_comment_markers.py` 로 확인한다.
- 그다음 지울 것: `pieces.MARKERS` 의 `toc`·`restart`·`hide_page`·`blank_page`·`blank_lines`,
  `pieces._LIKE_MARKER`·`source.marker_typos` 의 해당 부분, `source.old_markers`, 이 표기들의 `\[` 이스케이프 설명,
  표기 줄을 쓰는 테스트(`tests/test_markers.py`, `tests/test_toc.py` 등)는 주석 마커로 옮긴다.
- 지운 뒤에는 그 줄이 글자로 들어간다 (따로 경고하지 않는다).

### 2. 남은 기존 표기를 둘지 정하기

- pandoc 표기 `\newpage`·`\pagebreak`, `{width=…}`, `::: {custom-style=…}` 는 pandoc 과 원고를 함께 쓸 수 있어 두기로 했다.
- `<!-- [cols: ...] -->` 는 이미 뷰어에 보이지 않아 비용이 작지만 `@hwpx 열너비` 와 겹친다. 둘지 정한다.
- 샘플은 pandoc 표기를 그대로 쓴다 (`samples/sample.md`, `custom-style.md`, `hide-page.md` 등). 마커로 옮길지 정한다.
