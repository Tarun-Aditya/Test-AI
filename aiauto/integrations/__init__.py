"""Native connectors to trackers, docs, code and test-management tools. Token sign-in only."""
from __future__ import annotations

from .azure import AzureDevOps
from .base import Connector, IntegrationError, Issue
from .confluence import Confluence
from .custom import Custom
from .github import GitHub
from .jira import Jira
from .xray import Xray
from .zephyr import ZephyrScale

IMPLEMENTED = {c.kind: c for c in (Jira, Confluence, GitHub, ZephyrScale, Xray, AzureDevOps, Custom)}

# what each tool needs; `secret` fields are stored encrypted and never shown again
F = lambda key, label, **kw: {"key": key, "label": label, **kw}
CATALOG = [
    {"kind": "jira", "label": "Jira", "roles": ["tracker"], "status": "standard",
     "help": "Cloud: your Atlassian e-mail + an API token (id.atlassian.com > Security > API tokens). Data Center: a personal access token.",
     "fields": [F("base_url", "Site URL", placeholder="https://your-site.atlassian.net"),
                F("deployment", "Deployment", options=["cloud", "datacenter"]),
                F("email", "E-mail (Cloud only)", placeholder="you@company.com"),
                F("token", "API token / personal access token", secret=True),
                F("project_key", "Project key", placeholder="SHOP"),
                F("ac_field", "Acceptance criteria field id (optional)", placeholder="customfield_10045"),
                F("pass_transition", "Move the story to, when all its tests pass (optional)", placeholder="QA Passed"),
                F("fail_transition", "Move the story to, when a test fails (optional)", placeholder="In Progress"),
                F("bug_type", "Bug issue type", placeholder="Bug"),
                F("create_bugs", "Open a bug for a failing test", options=["yes", "no"])]},
    {"kind": "confluence", "label": "Confluence", "roles": ["docs"], "status": "standard",
     "help": "Same token type as Jira (Cloud: e-mail + API token; Data Center: personal access token).",
     "fields": [F("base_url", "Site URL", placeholder="https://your-site.atlassian.net"),
                F("deployment", "Deployment", options=["cloud", "datacenter"]),
                F("email", "E-mail (Cloud only)"), F("token", "API token / personal access token", secret=True),
                F("spaces", "Spaces to search (keys, comma separated)", placeholder="SHOP, ARCH")]},
    {"kind": "github", "label": "GitHub", "roles": ["code"], "status": "standard",
     "help": "A fine-grained personal access token with read access to Contents and Pull requests on these repositories.",
     "fields": [F("base_url", "API URL", placeholder="https://api.github.com (Enterprise: https://host/api/v3)"),
                F("token", "Personal access token", secret=True), F("repos", "Repositories (owner/name, comma separated)")]},
    {"kind": "zephyr_scale", "label": "Zephyr Scale", "roles": ["tests"], "status": "standard",
     "help": "A Zephyr Scale API access token (Jira > Apps > Zephyr API Access Tokens).",
     "fields": [F("base_url", "API URL", placeholder="https://api.zephyrscale.smartbear.com/v2"),
                F("token", "API access token", secret=True), F("project_key", "Jira project key", placeholder="SHOP"),
                F("folder", "Folder for new test cases (optional)", placeholder="Autopilot"),
                F("environment", "Environment name (optional, must exist in Zephyr)")]},
    {"kind": "xray", "label": "Xray", "roles": ["tests"], "status": "under development",
     "help": "An Xray API key: client id + client secret (Xray > Global Settings > API Keys).",
     "fields": [F("region", "Region", options=["global", "us", "eu", "au"]), F("client_id", "Client id", secret=True),
                F("client_secret", "Client secret", secret=True), F("project_key", "Jira project key"),
                F("base_url", "API URL (optional, overrides the region)", placeholder="https://xray.cloud.getxray.app")]},
    {"kind": "azure_devops", "label": "Azure DevOps", "roles": ["tracker", "tests"], "status": "under development",
     "help": "A personal access token with Work Items (read & write) and Test Management (read & write).",
     "fields": [F("org_url", "Organisation URL", placeholder="https://dev.azure.com/your-org"), F("project", "Project"),
                F("token", "Personal access token", secret=True),
                F("pass_transition", "State when all tests pass (optional)"), F("fail_transition", "State when a test fails (optional)")]},
    {"kind": "custom", "label": "Custom tool (JSON)", "roles": ["tracker", "tests"], "status": "under development",
     "help": "Your own service: GET <stories URL>?q= returns stories as JSON; results are POSTed to the results URL.",
     "fields": [F("issues_url", "Stories URL (optional)"), F("results_url", "Results URL (optional)"),
                F("header", "Token header (optional)", placeholder="Authorization"), F("token", "Token (optional)", secret=True)]},
    {"kind": "github_issues", "label": "GitHub Issues", "roles": ["tracker"], "status": "planned", "fields": []},
    {"kind": "zephyr_squad", "label": "Zephyr Squad (Essential)", "roles": ["tests"], "status": "planned", "fields": []},
    {"kind": "testrail", "label": "TestRail", "roles": ["tests"], "status": "planned", "fields": []},
    {"kind": "alm", "label": "OpenText ALM / ALM Octane", "roles": ["tracker", "tests"], "status": "planned", "fields": []},
    {"kind": "gitlab", "label": "GitLab", "roles": ["code"], "status": "planned", "fields": []},
    {"kind": "bitbucket", "label": "Bitbucket", "roles": ["code"], "status": "planned", "fields": []},
]
CATALOG_BY_KIND = {c["kind"]: c for c in CATALOG}
SECRET_FIELDS = {c["kind"]: [f["key"] for f in c["fields"] if f.get("secret")] for c in CATALOG}


def make(kind: str, settings: dict, secrets: dict) -> Connector:
    cls = IMPLEMENTED.get(kind)
    if not cls:
        raise IntegrationError(f"{CATALOG_BY_KIND.get(kind, {}).get('label', kind)} is planned, not available yet")
    return cls(settings, secrets)


__all__ = ["CATALOG", "CATALOG_BY_KIND", "SECRET_FIELDS", "Connector", "IntegrationError", "Issue", "make"]
