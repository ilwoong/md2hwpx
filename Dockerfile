FROM alpine:3.22

ARG PLANTUML_VERSION=1.2026.9
ARG MERMAID_CLI_VERSION=11.4.2
ARG D2CODING_VERSION=1.4.0
ARG D2CODING_DATE=20261003

# chromium: mermaid-cli 렌더링, graphviz: plantuml 의 클래스/컴포넌트 다이어그램,
# font-noto-cjk: 다이어그램 안의 한글 표시
RUN apk add --no-cache \
        python3 \
        nodejs npm \
        chromium \
        openjdk17-jre \
        graphviz \
        font-noto-cjk

ENV PUPPETEER_SKIP_DOWNLOAD=1
RUN npm install -g @mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION} \
    && npm cache clean --force

RUN mkdir -p /opt/plantuml \
    && wget -q -O /opt/plantuml/plantuml.jar \
        https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar

# D2Coding 은 Alpine 패키지가 없어 네이버 GitHub 릴리스에서 Regular/Bold TTF 만 설치한다.
RUN wget -q -O /tmp/d2coding.zip \
        https://github.com/naver/d2-coding-font/releases/download/VER${D2CODING_VERSION}/D2Coding-Ver${D2CODING_VERSION}-${D2CODING_DATE}.zip \
    && mkdir -p /usr/share/fonts/d2coding \
    && unzip -q -j /tmp/d2coding.zip 'D2Coding/*.ttf' OFL.txt -d /usr/share/fonts/d2coding \
    && rm /tmp/d2coding.zip \
    && fc-cache -f

COPY docker/puppeteer.json /etc/puppeteer.json
COPY --chmod=755 docker/mmdc docker/plantuml /usr/local/bin/

# Python 의존성은 이미지에 설치한다. 프로젝트 코드는 실행 시 /work 에 마운트한다.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock /tmp/project/
# uv 는 빌드에만 쓰므로 이미지에 남기지 않는다.
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.17,source=/uv,target=/usr/local/bin/uv \
    cd /tmp/project && uv sync --frozen --no-install-project --no-cache && rm -rf /tmp/project

# HOME=/tmp: --user 로 호스트 사용자 권한 실행 시 chromium 이 쓸 수 있는 HOME 이 필요하다.
ENV PATH=/opt/venv/bin:$PATH \
    HOME=/tmp \
    PYTHONPATH=/work/src \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
