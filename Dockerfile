# jre: PlantUML 이 쓰는 모듈만 담은 Java 런타임. 전체 JRE(174MB)보다 작다.
# 모듈 목록은 jdeps --print-module-deps --ignore-missing-deps --multi-release 17 plantuml.jar 의 결과다.
FROM alpine:3.22 AS jre
RUN apk add --no-cache openjdk17-jdk openjdk17-jmods binutils \
    && jlink --add-modules java.base,java.desktop,java.logging,java.prefs,java.scripting,jdk.unsupported \
        --strip-debug --no-man-pages --no-header-files --compress=2 --output /opt/jre

# tools: 다이어그램 도구와 Python 의존성. web 과 dev 가 이 위에 만들어진다.
FROM alpine:3.22 AS tools

ARG PLANTUML_VERSION=1.2026.9
ARG MERMAID_CLI_VERSION=11.4.2
ARG D2CODING_VERSION=1.4.0
ARG D2CODING_DATE=20261003

# chromium: mermaid-cli 렌더링, graphviz: plantuml 의 클래스/컴포넌트 다이어그램,
# font-noto-cjk: 다이어그램 안의 한글 표시, rsvg-convert: SVG 이미지를 PNG 로 변환,
# freetype ~ libpng: jre 단계의 Java 가 그림·글꼴을 다룰 때 쓰는 라이브러리
# chromium 이 끌어오는 Mesa GPU 드라이버와 LLVM(약 245MB)은 지운다. --disable-gpu 로 실행해 쓰지 않는다.
RUN apk add --no-cache \
        python3 \
        nodejs \
        chromium \
        graphviz \
        font-noto-cjk \
        rsvg-convert \
        freetype lcms2 libjpeg-turbo giflib libpng \
    && rm -rf /usr/lib/libLLVM* /usr/lib/libgallium* /usr/lib/gallium-pipe /usr/lib/dri/*

COPY --from=jre /opt/jre /opt/jre

# npm 은 설치에만 쓰므로 이미지에 남기지 않는다.
ENV PUPPETEER_SKIP_DOWNLOAD=1
RUN apk add --no-cache --virtual .npm npm \
    && npm install -g @mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION} \
    && npm cache clean --force \
    && apk del .npm

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
ENV PATH=/opt/venv/bin:/opt/jre/bin:$PATH \
    HOME=/tmp \
    PYTHONPATH=/work/src \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /work

# web: 코드와 템플릿을 넣은 웹 서비스. docker build --target web -t md2hwpx-web .
FROM tools AS web
RUN apk add --no-cache git
COPY src /app/src
# SANDBOX: 업로드한 PlantUML 이 !include 로 서버 파일이나 URL 을 읽지 못하게 한다.
ENV PYTHONPATH=/app/src \
    PLANTUML_SECURITY_PROFILE=SANDBOX
WORKDIR /app
USER nobody
EXPOSE 8000
CMD ["uvicorn", "md2hwpx.web:app", "--host", "0.0.0.0", "--port", "8000"]

# dev (기본): 코드는 실행 시 /work 에 마운트한다. docker build -t md2hwpx .
FROM tools AS dev
