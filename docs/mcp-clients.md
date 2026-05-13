# Connecting MCP clients

The Cloud Run service exposes the [Model Context Protocol](https://modelcontextprotocol.io) over HTTPS/SSE. Any MCP-aware client can call its nine tools. This doc covers Claude Desktop, Cursor, and the official `mcp` CLI.

## The basics

After `terraform apply` finishes, the service URL is in the Terraform outputs:

```bash
$ terraform output service_url
"https://sre-mcp-server-XXXXXXXX-uc.a.run.app"
```

The SSE endpoint is `<service_url>/sse`. Authentication is **GCP IAM identity tokens** — Cloud Run rejects anything else (because the Terraform module ships with `service_invokers = []`, i.e., private).

### Granting yourself access

By default only the service account itself can invoke. To let an MCP client call in, add yourself:

```hcl
module "sre" {
  # ...
  service_invokers = [
    "user:alice@example.com",                    # human user
    "serviceAccount:bot@p.iam.gserviceaccount.com",  # another SA
  ]
}
```

Re-apply. Or, for one-off testing:

```bash
gcloud run services add-iam-policy-binding sre-mcp-server \
  --region=us-central1 \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/run.invoker" \
  --project=YOUR_PROJECT_ID
```

### Getting an identity token

```bash
gcloud auth print-identity-token
```

Prints a JWT that's good for ~1 hour. The token includes the active gcloud user's email as the subject — Cloud Run authorizes against the `run.invoker` binding above.

For automation, mint short-lived tokens via service-account impersonation:

```bash
gcloud auth print-identity-token \
  --impersonate-service-account=bot@p.iam.gserviceaccount.com \
  --audiences="https://sre-mcp-server-XXXXXXXX-uc.a.run.app"
```

## The tool surface

Nine tools, all returning JSON-serializable dicts:

| Tool | Args | Returns |
|---|---|---|
| `pipeline_health()` | — | counts by `traffic_light` value |
| `recent_findings(scope=None, limit=20)` | optional scope filter | list of findings |
| `list_workflows()` | — | every active workflow |
| `lookup_workflow(name)` | workflow name | one workflow's full metadata |
| `register_workflow(name, ...)` | name + optional fields | confirmation |
| `run_task(task_name)` | one of 20 task names | task result dict |
| `list_tasks()` | — | every registerable task name |
| `open_incidents()` | — | currently-open incidents |
| `pending_approvals(action_kind=None)` | optional filter | items awaiting human review |

Full signatures live in [`src/sre_free_mcp/mcp_server/server.py`](../src/sre_free_mcp/mcp_server/server.py). Each tool delegates to the same code path the `sre-runner` job uses for scheduled invocations — on-demand and scheduled behavior are identical.

## Claude Desktop

Claude Desktop reads `claude_desktop_config.json` from your platform's app-support directory:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Add an entry pointing at your Cloud Run service. The trick: Claude Desktop currently spawns MCP servers via stdio, not HTTP. To bridge to a remote SSE server you need a small launcher script that forwards stdio↔SSE. The official [mcp-remote](https://www.npmjs.com/package/mcp-remote) package handles this:

```json
{
  "mcpServers": {
    "sre-free-mcp": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://sre-mcp-server-XXXXXXXX-uc.a.run.app/sse",
        "--header",
        "Authorization: Bearer ${SRE_MCP_TOKEN}"
      ],
      "env": {
        "SRE_MCP_TOKEN": "PASTE_TOKEN_HERE"
      }
    }
  }
}
```

Tokens expire after 1 hour, so refresh by running `gcloud auth print-identity-token` and updating the value. (For longer-lived access, you can write a wrapper script that runs `gcloud auth print-identity-token` inline as the header value — see "Token-refresh wrapper" below.)

Restart Claude Desktop. The nine tools appear in the tool picker.

### Token-refresh wrapper

```bash
#!/bin/bash
# ~/bin/sre-mcp-client.sh
set -euo pipefail
SRE_URL="https://sre-mcp-server-XXXXXXXX-uc.a.run.app/sse"
TOKEN="$(gcloud auth print-identity-token)"
exec npx -y mcp-remote "$SRE_URL" --header "Authorization: Bearer $TOKEN"
```

Make it executable (`chmod +x`) and reference it from Claude Desktop:

```json
{
  "mcpServers": {
    "sre-free-mcp": {
      "command": "/Users/you/bin/sre-mcp-client.sh"
    }
  }
}
```

Now the token is minted fresh every time Claude Desktop starts the connection.

## Cursor

Cursor's MCP integration uses `~/.cursor/mcp.json` (or per-workspace `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "sre-free-mcp": {
      "command": "/Users/you/bin/sre-mcp-client.sh"
    }
  }
}
```

Same wrapper-script trick as Claude Desktop. Cursor refreshes the connection on workspace open.

## Generic — the official `mcp` CLI

The [`mcp` CLI](https://modelcontextprotocol.io/quickstart/user) supports remote SSE servers natively:

```bash
TOKEN=$(gcloud auth print-identity-token)

mcp call \
  --transport sse \
  --url https://sre-mcp-server-XXXXXXXX-uc.a.run.app/sse \
  --header "Authorization: Bearer $TOKEN" \
  pipeline_health
```

Or open an interactive session:

```bash
mcp shell \
  --transport sse \
  --url https://sre-mcp-server-XXXXXXXX-uc.a.run.app/sse \
  --header "Authorization: Bearer $TOKEN"
```

## Custom Python client

```python
import asyncio
import subprocess

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


async def main():
    token = subprocess.check_output(
        ["gcloud", "auth", "print-identity-token"], text=True
    ).strip()
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://sre-mcp-server-XXXXXXXX-uc.a.run.app/sse"

    async with sse_client(url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("pipeline_health", {})
            print(result.content)


asyncio.run(main())
```

Useful for automation: a nightly cron that fetches `recent_findings(scope="cost")` and posts to Slack, etc.

## Smoke test from curl

You can't fully exercise an MCP tool with curl (it needs the SSE protocol handshake), but you can verify the endpoint is reachable + auth works:

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -v -H "Authorization: Bearer $TOKEN" \
  "https://sre-mcp-server-XXXXXXXX-uc.a.run.app/sse" \
  --max-time 5
```

Expected: HTTP 200, the connection stays open (SSE streams) until curl's timeout. If you get HTTP 401 / 403, your token isn't authorized — check the `service_invokers` setting.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| HTTP 401 / 403 from the SSE endpoint | Identity not in `service_invokers`. Add via Terraform or `gcloud run services add-iam-policy-binding`. |
| HTTP 404 on `/sse` | Wrong path. The server mounts SSE at `/sse`, not `/`. |
| Connection hangs without responses | Token expired (1-hour lifetime). Re-mint with `gcloud auth print-identity-token`. |
| Claude Desktop says "No tools available" | The wrapper script can't auth. Run it manually and check it prints valid stdio MCP messages. |
| Tool returns `{"error": "..."}` | Server-side BigQuery error. Check Cloud Run logs: `gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=sre-mcp-server' --limit=20`. |

## Public access (NOT recommended)

If you really want to skip IAM and let anyone call the service:

```hcl
service_invokers = ["allUsers"]
```

The nine tools are read-mostly (the lone write tool is `register_workflow`, which still won't ruin your day), but publicly-exposing your governance dataset is a bad idea. Don't do this.
