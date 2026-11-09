"""Creates (or updates) the Entra ID app registration that represents the helpdesk API.

`azd up` / `azd provision` run it before provisioning; `azd down` deletes the app registration.
It uses your `az login` session and Microsoft Graph, so you need rights to create app registrations.

The app registration:
- exposes one delegated scope, Helpdesk.Chat: "this client may call the helpdesk as the signed-in user";
- defines one app role, Helpdesk.Agent: Service Desk staff, who may act for other people;
- pre-authorizes Azure CLI, so `az account get-access-token --scope api://<id>/Helpdesk.Chat` works
  without a consent prompt. A real chat front end would be registered and pre-authorized the same way.
"""

import json
import os
import subprocess
import uuid

AZURE_CLI_APP_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Microsoft's well-known client id for Azure CLI
SCOPE = "Helpdesk.Chat"
ROLE = "Helpdesk.Agent"


def az(*args: str) -> str:
    return subprocess.run(["az", *args], check=True, capture_output=True, text=True).stdout


def graph(method: str, path: str, body: dict | None = None) -> dict:
    args = ["rest", "--method", method, "--url", f"https://graph.microsoft.com/v1.0{path}"]
    if body is not None:
        args += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    out = az(*args)
    return json.loads(out) if out.strip() else {}


def main() -> None:
    name = f"contoso-helpdesk-api-{os.environ['AZURE_ENV_NAME']}"
    found = graph("GET", f"/applications?$filter=displayName eq '{name}'")["value"]
    app = found[0] if found else graph("POST", "/applications", {"displayName": name, "signInAudience": "AzureADMyOrg"})
    app_id, object_id = app["appId"], app["id"]

    # Keep existing ids on re-runs: tokens and role assignments refer to them.
    scope_id = next((s["id"] for s in app.get("api", {}).get("oauth2PermissionScopes", []) if s["value"] == SCOPE),
                    str(uuid.uuid4()))
    role_id = next((r["id"] for r in app.get("appRoles", []) if r["value"] == ROLE), str(uuid.uuid4()))

    graph("PATCH", f"/applications/{object_id}", {
        "identifierUris": [f"api://{app_id}"],
        "appRoles": [{
            "id": role_id, "value": ROLE, "isEnabled": True, "allowedMemberTypes": ["User"],
            "displayName": "Service Desk agent",
            "description": "May look up anyone's tickets and reset other people's passwords.",
        }],
        "api": {
            "requestedAccessTokenVersion": 2,
            "oauth2PermissionScopes": [{
                "id": scope_id, "value": SCOPE, "type": "User", "isEnabled": True,
                "adminConsentDisplayName": "Use the Contoso IT helpdesk",
                "adminConsentDescription": "Chat with the IT helpdesk agent as the signed-in user.",
                "userConsentDisplayName": "Use the Contoso IT helpdesk",
                "userConsentDescription": "Chat with the IT helpdesk agent as you.",
            }],
        },
    })
    # Pre-authorizing refers to the scope's id, so it can only be set once the scope exists.
    graph("PATCH", f"/applications/{object_id}", {
        "api": {"preAuthorizedApplications": [{"appId": AZURE_CLI_APP_ID, "delegatedPermissionIds": [scope_id]}]},
    })
    # A service principal is the app's presence in this tenant: tokens and role assignments need one.
    if not graph("GET", f"/servicePrincipals?$filter=appId eq '{app_id}'")["value"]:
        graph("POST", "/servicePrincipals", {"appId": app_id})

    subprocess.run(["azd", "env", "set", "AZURE_API_CLIENT_ID", app_id], check=True)
    print(f"App registration '{name}': api://{app_id}, scope {SCOPE}, app role {ROLE}.")


if __name__ == "__main__":
    main()
