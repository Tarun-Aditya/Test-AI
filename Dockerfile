# One image, two roles: the hub (web app) or an execution agent.
#   docker build -t autopilot .
#   docker run -p 8000:8000 -v autopilot-data:/data autopilot hub
#   docker run --shm-size=1g autopilot agent --server http://HUB:8000 --name docker-1 [--token SECRET]
# Playwright's image already has Chromium, Firefox, WebKit, their system libraries and Xvfb.
# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    AIAUTO_CHROME_BINARY=/ms-playwright/chromium-1255/chrome-linux64/chrome \
    SE_CACHE_PATH=/opt/selenium-cache \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements.txt .
# playwright pinned to the version whose browsers the base image ships
# (optional build secret "ca": extra CA bundle for builds behind a TLS-inspecting proxy; not kept in the image)
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install -r requirements.txt "playwright==1.65.2"

COPY aiauto ./aiauto
COPY examples ./examples
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Selenium: fetch the chromedriver matching the bundled Chromium now, so agents start offline-ready
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    chmod +x /usr/local/bin/entrypoint.sh \
 && test -x "$AIAUTO_CHROME_BINARY" \
 && python3 - <<'PY'
import json, os, subprocess
from selenium.webdriver.common.selenium_manager import SeleniumManager
sm = SeleniumManager()
out = sm.binary_paths(["--browser", "chrome", "--browser-path", os.environ["AIAUTO_CHROME_BINARY"]])
print("chromedriver:", out["driver_path"])
open("/opt/chromedriver-path", "w").write(out["driver_path"])
PY

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
CMD ["hub"]
