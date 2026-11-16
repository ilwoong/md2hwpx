# 남은 작업

주석 마커(`<!-- @hwpx ... -->`)를 들인 뒤 이어서 할 일. 끝낸 항목은 이 문서에서 지운다.

## 지금 상태 (2026-11-16)

- `feature/deprecate-marker-lines`: 이 프로젝트만의 표기 줄(`[TOC]`·`[새 번호]`·`[감추기]`·`[빈 줄]`·`[여백면]`)에
  폐지 예고 경고(`source.old_markers`), 샘플을 주석 마커로 이전. 샘플 HWPX 는 이전 전후로 같다(필드 id 제외).
- `feature/marker-near-miss` (위 브랜치에서 갈라짐): `@HWPX`·`@hwpx:`·`@hwp`·`@ hwpx`·`@hwpx쪽나눔` 처럼
  마커와 비슷한 주석을 경고 (`parser._LIKE_MARKER_START`, 이유 `'@hwpx 명령' 꼴이 아니어서`).
- 두 브랜치 모두 아직 main 에 합치지 않았다. `feature/marker-near-miss` 하나로 PR 을 만들면 둘 다 들어간다.

## 할 일

### 1. 모르는 명령 경고에 맞는 이름 알려 주기

- `<!-- @hwpx 쪽 나눔 -->` 은 `모르는 명령이라` 경고만 낸다. 기존 표기(`[새 번호]`·`[빈 줄]`)는 띄어 쓰고
  마커 명령(`새번호`·`빈줄`)은 붙여 써서 헷갈리기 쉽다.
- 공백을 뺀 앞 낱말들이 `parser._COMMANDS` 의 명령과 같으면 그 이름을 경고에 담는다. 예: `모르는 명령이라 (쪽나눔?)`.
- 고칠 곳: `parser._read_marker`, 스펙 "주석 마커" 의 이유 목록, `tests/test_comment_markers.py`.

### 2. 스펙에 주석 마커의 한계 적기

- 다음 블록에 붙는 `그림크기`·`열너비` 는 편집 중 사이에 다른 그림·표가 끼면 그것에 조용히 붙는다
  (`열너비` 는 열 수가 다르면 경고한다).
- 인자에 `-->` 를 쓸 수 없다 (HTML 주석의 끝). 지금은 `줄에 따로 있지 않아` 경고가 나고 라벨이 잘린다.
- 문장 안의 비슷한 주석(`다 <!-- @HWPX 쪽나눔 --> 라`)은 경고하지 않는다.
- 고칠 곳: `spec/markdown-conversion.md` 의 "주석 마커".

### 3. 없어질 표기 지우기 (폐지 3단계)

- 언제: 쓰는 원고를 변환해도 `없어질 표기입니다` 경고가 하나도 나오지 않을 때. 다른 사용자가 있으면 버전을 올리며 알린다.
- 먼저: 주석 마커가 표기 줄을 거쳐 읽힌다 (`parser._line_command` 가 `"[새 번호{}]"` 를 만들어 `pieces.match_marker` 로 검사).
  마커가 인자를 직접 읽도록 바꾼 뒤, 표기 줄과 같은 결과인지 `tests/test_comment_markers.py` 로 확인한다.
- 그다음 지울 것: `pieces.MARKERS` 의 `toc`·`restart`·`hide_page`·`blank_page`·`blank_lines`,
  `pieces._LIKE_MARKER`·`source.marker_typos` 의 해당 부분, `source.old_markers`, 이 표기들의 `\[` 이스케이프 설명,
  표기 줄을 쓰는 테스트(`tests/test_markers.py`, `tests/test_toc.py` 등)는 주석 마커로 옮긴다.
- 지운 뒤에는 그 줄이 글자로 들어간다 (따로 경고하지 않는다).

### 4. 남은 기존 표기를 둘지 정하기

- pandoc 표기 `\newpage`·`\pagebreak`, `{width=…}`, `::: {custom-style=…}` 는 pandoc 과 원고를 함께 쓸 수 있어 두기로 했다.
- `<!-- [cols: ...] -->` 는 이미 뷰어에 보이지 않아 비용이 작지만 `@hwpx 열너비` 와 겹친다. 둘지 정한다.
- 샘플은 pandoc 표기를 그대로 쓴다 (`samples/sample.md`, `custom-style.md`, `hide-page.md` 등). 마커로 옮길지 정한다.

### 5. 테스트 명령 확인

- README 의 `uv run pytest -q` 가 한 환경(WSL)에서 `Failed to spawn: pytest` 로 실패했다. `.venv/bin/python -m pytest -q` 는 된다.
  그 환경만의 문제인지 확인하고, 아니면 README 를 고친다 (`uv run --group dev pytest` 등).
