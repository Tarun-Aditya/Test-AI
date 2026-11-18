# One image, two roles: the hub (web app) or an execution agent.
#   docker build -t autopilot .
#   docker run -p 8000:8000 -v autopilot-data:/data autopilot hub
#   docker run --shm-size=1g autopilot agent --server http://HUB:8000 --name docker-1 [--token SECRET]
# Browsers: Playwright's Chromium, Firefox and WebKit (from the base image) plus Google Chrome and Microsoft Edge;
# Selenium gets matching chromedriver / msedgedriver / geckodriver and a real Firefox. Xvfb for headed runs.
# Build behind a TLS-inspecting proxy: docker build --secret id=ca,src=ca-bundle.crt -t autopilot .
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
    pip install -r requirements.txt "playwright==1.65.2" litellm

# Google Chrome + Microsoft Edge (Playwright installs the official packages), then Selenium's drivers and a
# Selenium-compatible Firefox (Playwright's Firefox is a patched build WebDriver can't drive).
RUN --mount=type=secret,id=ca,required=false \
    set -e; \
    if [ -s /run/secrets/ca ]; then cp /run/secrets/ca /usr/local/share/ca-certificates/build-ca.crt && update-ca-certificates >/dev/null; \
      export PIP_CERT=/etc/ssl/certs/ca-certificates.crt SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt; fi; \
    python3 -m playwright install chrome msedge; \
    python3 /dev/stdin <<'PY'
import json, os
from selenium.webdriver.common.selenium_manager import SeleniumManager
sm = SeleniumManager()
found = {}
def get(key, args):
    try:
        out = sm.binary_paths(args)
        found[key] = out
        print(key, "->", out)
    except Exception as e:
        print(key, "FAILED:", e)
get("chromium", ["--browser", "chrome", "--browser-path", os.environ["AIAUTO_CHROME_BINARY"]])
get("chrome", ["--browser", "chrome"])
get("msedge", ["--browser", "MicrosoftEdge"])
get("firefox", ["--browser", "firefox"])          # downloads Firefox itself when none is installed
json.dump(found, open("/opt/browsers.json", "w"), indent=2)
missing = {"chromium", "chrome", "msedge", "firefox"} - set(found)
assert not missing, f"could not set up Selenium for {missing}"
PY
RUN if [ -f /usr/local/share/ca-certificates/build-ca.crt ]; then rm /usr/local/share/ca-certificates/build-ca.crt && update-ca-certificates --fresh >/dev/null; fi

COPY aiauto ./aiauto
COPY examples ./examples
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
CMD ["hub"]
