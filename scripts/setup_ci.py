"""Lets GitHub Actions run the evaluations against this environment, without any stored secret.

    python -m scripts.setup_ci noaros/AI-Demo      # then: azd provision (to grant its roles)

It creates an Entra ID app registration for CI and trusts GitHub's OIDC tokens for this repository: a workflow
run gets a short-lived token from GitHub saying "repo X, pull request" or "repo X, branch main", and Entra ID
exchanges it for an Azure token. Nothing to leak, nothing to rotate. `azd provision` then gives that identity
data access only (infra/resources.bicep, "CI"). Finally it prints the variables to add to the GitHub repository.
`azd down` deletes the app registration.
"""

import os
import subprocess
import sys

from scripts.setup_auth import graph

GITHUB_ISSUER = "https://token.actions.githubusercontent.com"


def main(repo: str) -> None:
    name = f"contoso-helpdesk-ci-{os.environ['AZURE_ENV_NAME']}"
    found = graph("GET", f"/applications?$filter=displayName eq '{name}'")["value"]
    app = found[0] if found else graph("POST", "/applications", {"displayName": name, "signInAudience": "AzureADMyOrg"})
    sps = graph("GET", f"/servicePrincipals?$filter=appId eq '{app['appId']}'")["value"]
    sp = sps[0] if sps else graph("POST", "/servicePrincipals", {"appId": app["appId"]})

    # Which GitHub workflow runs may sign in as this identity: pull requests, and runs on main.
    # Newer repositories use "immutable" subjects (repo:owner@<id>/name@<id>:...), so ask GitHub for the prefix.
    prefix = subprocess.run(["gh", "api", f"repos/{repo}/actions/oidc/customization/sub", "-q", ".sub_claim_prefix"],
                            capture_output=True, text=True).stdout.strip() or f"repo:{repo}"
    wanted = {"pull-requests": f"{prefix}:pull_request", "main": f"{prefix}:ref:refs/heads/main"}
    existing = {}
    for c in graph("GET", f"/applications/{app['id']}/federatedIdentityCredentials")["value"]:
        if c["subject"] in wanted.values():
            existing[c["subject"]] = c
        else:  # stale, e.g. the old subject format
            graph("DELETE", f"/applications/{app['id']}/federatedIdentityCredentials/{c['id']}")
    for label, subject in wanted.items():
        if subject not in existing:
            graph("POST", f"/applications/{app['id']}/federatedIdentityCredentials", {
                "name": f"github-{label}", "issuer": GITHUB_ISSUER, "subject": subject,
                "audiences": ["api://AzureADTokenExchange"],
            })

    subprocess.run(["azd", "env", "set", "AZURE_CI_PRINCIPAL_ID", sp["id"]], check=True)
    subprocess.run(["azd", "env", "set", "AZURE_CI_CLIENT_ID", app["appId"]], check=True)
    print(f"CI identity '{name}' trusts GitHub Actions in {repo}.")
    print("Next: `azd provision` to give it data access. Then add these variables to the repository")
    print(f"(https://github.com/{repo}/settings/variables/actions), or with `gh variable set NAME --body VALUE`:\n")
    variables = {"AZURE_CLIENT_ID": app["appId"], "AZURE_TENANT_ID": os.environ["AUTH_TENANT_ID"]}
    for key in ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
                "AZURE_OPENAI_JUDGE_DEPLOYMENT",
                "AZURE_SEARCH_ENDPOINT", "AZURE_COSMOS_ENDPOINT", "CONTENT_SAFETY_ENDPOINT",
                "AUTH_TENANT_ID", "AUTH_CLIENT_ID"]:
        variables[key] = os.environ[key]
    for key, value in variables.items():
        print(f"  {key}={value}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        sys.exit("usage: python -m scripts.setup_ci <github-owner>/<repo>")
    main(sys.argv[1])
