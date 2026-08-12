/**
 * Pharos Discovery SDK — TypeScript quickstart demo.
 *
 * Demonstrates the full discovery lifecycle:
 * 1. Search the live Pharos registry (https://api.getpharos.dev) for MCP servers.
 * 2. Display the returned ServerCard.
 * 3. Run the approval flow (auto-approve in this demo).
 * 4. Connect to a local mock MCP server via the ConnectionManager.
 * 5. Execute the MCP lifecycle: initialize → tools/list → tools/call.
 *
 * Prerequisites:
 *   cd ../../packages/typescript && npm install && npm run build
 *
 * Usage:
 *   # Terminal 1 — start the mock MCP server
 *   python ../mock_mcp_server.py
 *
 *   # Terminal 2 — run this demo
 *   npx tsx demo.ts
 */

import {
  PharosClient,
  ConnectionManager,
  MCPConnection,
  type ApprovalRequest,
  type ApprovalResponse,
  type ServerCard,
} from "../../packages/typescript/src/index.js";

// ---------------------------------------------------------------------------
// Auto-approve approval handler
// ---------------------------------------------------------------------------

class AutoApproveHandler {
  async requestApproval(req: ApprovalRequest): Promise<ApprovalResponse> {
    console.log(`\n  [Approval] Server: ${req.server.display_name}`);
    console.log(`  [Approval] Purpose: ${req.purpose}`);
    console.log(`  [Approval] Scopes: ${req.requested_scopes?.join(", ") ?? "(none)"}`);
    console.log("  [Approval] → AUTO-APPROVED");
    return {
      approved: true,
      approved_scopes: req.requested_scopes ?? ["*"],
      duration: "session",
    };
  }
}

// ---------------------------------------------------------------------------
// Main demo
// ---------------------------------------------------------------------------

const REGISTRY_URL = "https://api.getpharos.dev";
const MOCK_MCP_URL = "http://127.0.0.1:8765/mcp";

async function main(): Promise<void> {
  console.log("=".repeat(60));
  console.log("  Pharos Discovery SDK — TypeScript Quickstart Demo");
  console.log("=".repeat(60));

  // ---- 1. Search the live registry ----------------------------------
  console.log(`\n1. Searching registry at ${REGISTRY_URL} for 'flight'...\n`);
  const client = new PharosClient(REGISTRY_URL, {
    approvalHandler: new AutoApproveHandler(),
    connectionHandler: new ConnectionManager(),
  });

  const results = await client.search("flight", undefined, 5);
  console.log(`   Found ${results.length} result(s):`);
  for (let i = 0; i < results.length; i++) {
    const card = results[i].card;
    console.log(`   [${i}] ${card.id}`);
    console.log(`       Name:        ${card.display_name}`);
    console.log(`       Version:     ${card.version}`);
    console.log(`       Transport:   ${card.transport.join(", ")}`);
    console.log(`       Capabilities:${card.capabilities.join(", ")}`);
    console.log(`       Publisher:   ${card.publisher.id} (verified=${card.publisher.verified ?? false})`);
    console.log(`       Description: ${card.description.slice(0, 80)}`);
    console.log();
  }

  // ---- 2. Pick the first result and show the ServerCard -------------
  const registryCard = results[0].card;
  console.log(`2. Selected ServerCard: ${registryCard.id}`);
  console.log(`   Full card:`, registryCard);

  // ---- 3. Approval flow ---------------------------------------------
  console.log("\n3. Running approval flow...");
  // Construct a ServerCard pointing at our mock MCP server.
  const mockCard: ServerCard = {
    id: "mock-mcp-server",
    display_name: "Mock MCP Server (echo)",
    description: "A minimal MCP server with an echo tool for testing.",
    publisher: registryCard.publisher,
    version: "1.0.0",
    transport: ["http+sse"],
    endpoint: MOCK_MCP_URL,
    capabilities: ["tools"],
    tools_count: 1,
    auth: { type: "none" },
    availability: "native",
    source_registry: "local",
    published_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    status: "active",
    tags: ["test", "mock"],
  } as ServerCard;

  const { token, connection } = await client.connectAndApprove(
    mockCard,
    "Demo: connect to mock MCP server and call echo tool",
    { requestedScopes: ["tools:call"] },
  );
  console.log(`   ✓ Approved! Token server_id=${token.server_id}`);
  console.log(`   ✓ Connected to ${mockCard.endpoint}`);

  // ---- 4. MCP lifecycle: initialize → tools/list → tools/call -------
  console.log("\n4. MCP Lifecycle:");

  const mcp = new MCPConnection(connection, mockCard.id);

  // initialize
  console.log("   → initialize()");
  const initResult = await mcp.initialize();
  const serverInfo = (initResult.result as any)?.serverInfo ?? {};
  console.log(`   ← Server: ${serverInfo.name} v${serverInfo.version}`);

  // tools/list
  console.log("   → tools/list()");
  const toolsResult = await mcp.listTools();
  const tools = (toolsResult.result as any)?.tools ?? [];
  console.log(`   ← ${tools.length} tool(s):`);
  for (const tool of tools) {
    console.log(`      • ${tool.name}: ${tool.description}`);
  }

  // tools/call
  console.log(`   → tools/call(echo, {message: "Hello from Pharos!"})`);
  const callResult = await mcp.callTool("echo", { message: "Hello from Pharos!" });
  const content = (callResult.result as any)?.content ?? [];
  for (const item of content) {
    if (item.type === "text") {
      console.log(`   ← ${item.text}`);
    }
  }

  // ---- 5. Disconnect -------------------------------------------------
  console.log("\n5. Disconnecting...");
  await client.close();
  console.log("   ✓ Disconnected.");

  console.log("\n" + "=".repeat(60));
  console.log("  Demo complete!");
  console.log("=".repeat(60));
}

main().catch((err) => {
  console.error("\nError:", err);
  console.error("\nMake sure the mock MCP server is running:");
  console.error("  python ../mock_mcp_server.py");
  process.exit(1);
});
