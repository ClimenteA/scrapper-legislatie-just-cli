# Legislatie Just — local MCP server

Run the Legislatie Just MCP server locally in Docker and connect an MCP client
such as OpenCode to it over HTTP.

## Requirements

- Docker with the `docker compose` plugin.
- The repository data folder `legislatie-descarcata/`, with the built index
  (`4-processed/law.db` and `4-processed/graph.db`) and expanded HTML in
  `3-expanded/`.

## Start the server

Create `.env` from the example and set the two required secrets:

```bash
cp .env.example .env
```

```dotenv
ADMIN_SECRET_KEY=choose-a-secret-for-issuing-tokens
TOKEN_SIGNING_SECRET=replace-with-a-long-random-value
```

Start the app from the repository root:

```bash
docker compose up -d
```

The MCP endpoint is `http://localhost:8000/mcp`. Check that the app is
available at <http://localhost:8000/health>.

## Get an MCP token

Open <http://localhost:8000/docs>, expand `POST /admin/tokens`, and select
**Try it out**. Submit your email, an ISO 8601 expiration date, and the same
`ADMIN_SECRET_KEY` value configured in `.env`:

```json
{
  "user_email": "you@example.com",
  "token_expire_date": "2027-01-01",
  "admin_secret_key": "choose-a-secret-for-issuing-tokens"
}
```

Copy the returned token; it is shown only once. MCP clients authenticate with
it using the `Authorization: Bearer <token>` header.

## Connect with OpenCode

Configure OpenCode with a remote HTTP MCP server at
`http://localhost:8000/mcp` and set its `Authorization` header to
`Bearer <token copied from /docs>`. Open the repository in OpenCode and verify
the connection with `opencode mcp list` or `/mcps` in the TUI. Then ask a
question about Romanian legislation; the client can use the `legislatie_*` MCP
tools.

For another MCP client, configure a remote/HTTP server at
`http://localhost:8000/mcp` and pass the same bearer-token header.

## Stop the server

```bash
docker compose down
```
