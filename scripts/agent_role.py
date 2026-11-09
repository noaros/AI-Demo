"""Gives yourself the Helpdesk.Agent app role, or takes it away again. For the demo.

    python -m scripts.agent_role add
    python -m scripts.agent_role remove

Then get a new token: the role is a claim in the token, so an old token doesn't have it.
"""

import os
import sys

from scripts.setup_auth import ROLE, az, graph


def main(action: str) -> None:
    app_id = os.environ["AZURE_API_CLIENT_ID"]
    sp = graph("GET", f"/servicePrincipals?$filter=appId eq '{app_id}'")["value"][0]
    role_id = next(r["id"] for r in sp["appRoles"] if r["value"] == ROLE)
    me = az("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv").strip()
    assigned = [a for a in graph("GET", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo")["value"]
                if a["principalId"] == me and a["appRoleId"] == role_id]

    if action == "add" and not assigned:
        graph("POST", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo",
              {"principalId": me, "resourceId": sp["id"], "appRoleId": role_id})
    elif action == "remove":
        for a in assigned:
            graph("DELETE", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo/{a['id']}")
    print(f"{ROLE}: {'assigned to' if action == 'add' else 'removed from'} you. Get a new token to see the change.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "add")
