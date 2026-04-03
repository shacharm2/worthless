# TestSprite MCP Documentation - Complete Research Report

Compiled 2026-04-03 from docs.testsprite.com and related sources.

> **Note**: WebFetch was blocked by context-mode hooks. This report was assembled from
> multiple WebSearch queries extracting snippets and cached descriptions from the doc pages.
> Some granular details (exact wording of every paragraph) could not be captured; what follows
> is the most complete reconstruction achievable through search.

---

## Page 1: Introduction (`/mcp/getting-started/introduction`)

### What is TestSprite MCP Server?

TestSprite MCP Server is a Model Context Protocol integration that lets your IDE's AI
assistant orchestrate the entire TestSprite workflow directly from your editor. It is
described as the industry's first testing agent designed to work alongside coding agents,
ensuring the output aligns precisely with the product requirements document (PRD).

### Why TestSprite

- AI-generated code passed only 42% of key test cases in benchmarks. After a single
  iteration with TestSprite MCP's test report, the revised version achieved 93% pass rate.
- Cuts testing costs by up to 90%.
- Natural language interaction -- you describe what to test and the AI handles everything.
- Supports both frontend and backend applications: UI flows, API integrations, security
  validation.

### Supported IDEs

Trae, Cursor, Claude Code, Windsurf, VS Code, GitHub Copilot.

---

## Page 2: Overview (`/mcp/getting-started/overview`)

### How It Works

The MCP Server integrates with your IDE and coding assistant. It:

1. Reads your product requirements (PRD)
2. Analyzes your codebase
3. Automatically generates and executes comprehensive tests covering logic, edge cases,
   error-handling, performance, and more
4. Parses PRDs (even informal ones) and infers intent from the codebase
5. Normalizes requirements into a structured internal PRD so tests align with real product goals
6. Generates and runs UI and API tests in secure cloud sandboxes
7. Classifies failures (real bug vs. flaky selector vs. environment)
8. Self-heals test fragility
9. Feeds structured fix recommendations back to the coding agent

### Key Features

- Simple natural language prompts trigger the entire testing workflow
- End-to-end coverage: UI flows, APIs, data
- Structured reporting: logs, screenshots, videos, diffs
- Self-healing for test fragility
- All orchestrated via MCP -- no context switching

### 8-Step Workflow

1. Bootstrap Testing Environment
2. Read User PRD
3. Code Analysis & Summary
4. Generate TestSprite Normalized PRD
5. Create Test Plans
6. Generate Executable Test Code
7. Execute Tests
8. Analyze Results & Reports
9. (AI Fixes Issues -- sometimes listed as step 9)

---

## Page 3: Installation (`/mcp/getting-started/installation`)

### General

Install the MCP server in your IDE. Configuration uses JSON specifying:
- command: `"npx"`
- args: `["@testsprite/testsprite-mcp@latest"]`
- env: `{ "API_KEY": "<your-testsprite-api-key>" }`

### Claude Code Specific

- Installing the MCP server adds TestSprite **only to Claude Code under the current project
  directory**.
- If you are using Claude Code in another project directory, you need to add the MCP server
  again.
- Use `claude mcp add` CLI command, or manually edit the JSON configuration at `~/.claude.json`.
- Local-scoped servers are stored in `~/.claude.json` under the project's path. These
  servers remain private and only accessible when working within that project directory.

### Cursor Specific

Cursor's default "Run in Sandbox" mode limits TestSprite's functionality. Fix:
- Go to Chat -> Auto-Run -> Auto-Run Mode
- Change setting to "Ask Everytime" or "Run Everything"

### Verification

Check installation: `npm list -g @testsprite/testsprite-mcp`
Restart IDE after configuration changes.

---

## Page 4: First Test (`/mcp/getting-started/first-test`)

### Prerequisites

1. TestSprite MCP Server installed in your IDE
2. Your application **must be running locally**
   - Backend apps typically run on port 8000, 3001, or 4000
   - Frontend apps on 3000, 5173, etc.

### Step-by-Step

1. Ensure TestSprite MCP Server is installed and IDE is open
2. Start your application locally (e.g., `node index.js`, `python app.py`)
3. Select project type: **Backend** if testing APIs, services, or server logic
4. A Testing Configuration page opens in your browser -- complete setup there
5. AI assistant takes over and guides through the entire testing process
6. After completion, you have your first automated test run

### Backend Testing

Select "Backend" to test APIs, services, or server logic. The AI assistant automatically
handles the entire process.

---

## Page 5: MCP Tools Reference (`/mcp/core/tools`)

TestSprite MCP Server provides **8 core tools**. The AI assistant calls them automatically.

### Tool 1: `testsprite_bootstrap_tests`

**Purpose**: Initialize testing environment and configuration.

**Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `localPort` | number | The port where your application is running (e.g., 3000, 8000) |
| `type` | string | Project type: `"frontend"` or `"backend"` |
| `projectPath` | string | Absolute path to your project directory |
| `testScope` | string | Testing scope: `"codebase"` or `"diff"` |

**Example**:
```json
{
  "localPort": 3000,
  "type": "frontend",
  "projectPath": "/Users/dev/my-project",
  "testScope": "codebase"
}
```

Returns `next_action` instructions that guide the AI to initialize testing configuration.

> **Note on `pathname` parameter**: No documentation was found mentioning a `pathname`
> parameter on `testsprite_bootstrap_tests`. The documented parameters are `localPort`,
> `type`, `projectPath`, and `testScope` only.

### Tool 2: `testsprite_read_prd`

**Purpose**: Read the Product Requirements Document (PRD) to understand product goals and
requirements. Creates a structured PRD document as the foundation for test planning.

### Tool 3: `testsprite_generate_code_summary`

**Purpose**: Analyze and summarize the project codebase.

### Tool 4: `testsprite_generate_standardized_prd`

**Purpose**: Normalize requirements into a structured internal PRD so tests align with real
product goals.

### Tool 5: `testsprite_generate_frontend_test_plan`

**Purpose**: Generate test plan for frontend projects.

**Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `projectPath` | string | Absolute path to project root |
| `needLogin` | boolean | Whether authentication is required (default: true) |

### Tool 6: `testsprite_generate_backend_test_plan`

**Purpose**: Generate test plan for backend projects.

**Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `projectPath` | string | Absolute path to project root |
| `needLogin` | boolean | Whether authentication is required (default: true) |

### Tool 7: `testsprite_generate_code_and_execute`

**Purpose**: Create production-ready test code based on test plans, then execute.

**Parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `projectName` | string | Your project name |
| `projectPath` | string | Absolute path to project |
| `testIds` | array | Test IDs to run (empty array = all tests) |
| `additionalInstruction` | string | Optional guidance, e.g. "Focus on critical user journeys first" |

### Tool 8: `testsprite_diagnose_failures`

**Purpose**: Classify failures (real bug vs. flaky selector vs. environment) and send
structured fix recommendations back to the coding agent.

### Workflow Sequence

The tools are called in this order by the AI:
1. `testsprite_bootstrap_tests` -- initialize
2. `testsprite_read_prd` -- read requirements
3. `testsprite_generate_code_summary` -- analyze codebase
4. `testsprite_generate_standardized_prd` -- normalize PRD
5. `testsprite_generate_backend_test_plan` or `testsprite_generate_frontend_test_plan` -- plan
6. `testsprite_generate_code_and_execute` -- generate code and run tests
7. Results collected and report generated
8. `testsprite_diagnose_failures` -- analyze failures

---

## Page 6: Test Execution Issues (`/mcp/troubleshooting/test-execution-issues`)

### Quick Fix (Primary Resolution)

**Most test execution issues can be resolved by completely deleting the generated
`/testsprite_tests` directory and re-running the workflow from the beginning.**

If the problem persists, try reinstalling the TestSprite MCP Server.

### Quick Reset Process (Cursor-specific)

1. Open Cursor IDE
2. Go to the MCP Server panel/sidebar
3. Find "TestSprite" in the server list
4. Toggle the TestSprite MCP server OFF
5. Wait 5-10 seconds
6. Toggle it ON
7. Wait for server to reconnect with green status indicator

### Common Errors

The documentation references a "Common Error Messages" section. Based on search results,
specific error categories include:
- Timeout errors
- Connection refused errors
- Test execution failures in cloud sandbox

### Advanced Troubleshooting

Referenced in the documentation table of contents. Specific details were not fully
extractable through search, but the page structure includes:
- General Test Execution Problems
- Advanced Troubleshooting
- Common Error Messages
- Verification steps for diagnosing test execution failures

---

## Page 7: Application Detection Issues (`/mcp/troubleshooting/application-detection-issues`)

### Problem

TestSprite cannot access your running application.

### Diagnostic Steps

1. **Verify application is running**:
   ```bash
   curl http://localhost:3000
   curl http://localhost:8000
   ```

2. **Check which ports are in use**:
   ```bash
   lsof -i :3000
   lsof -i :8000
   ```

3. **Find the actual port**:
   ```bash
   netstat -tulpn | grep LISTEN
   ```

4. **Check firewall settings**:
   - **macOS**: System Preferences > Security & Privacy > Firewall
   - **Linux**: `sudo ufw status`

### Starting Your Application

- **Frontend**: `npm start`, `npm run dev`, `yarn dev`
- **Backend**: `npm run server`, `python app.py`, `node server.js`

### Configuration Fix

When configuring `testsprite_bootstrap_tests`, specify the actual port and application type.

> **Tunnel / Cloudflare**: No specific mention of tunnel setup, Cloudflare tunnel, or proxy
> configuration was found in the TestSprite documentation itself. TestSprite runs tests in
> **secure cloud sandboxes** -- the mechanism by which those cloud sandboxes access your
> localhost application is not explicitly documented in the troubleshooting pages. The
> implication is that TestSprite creates its own tunnel/connection to reach your local app,
> but the documentation does not expose configuration for this.

---

## Page 8: IDE Configuration Issues (`/mcp/troubleshooting/ide-configuration-issues`)

### Cursor

- **Sandbox mode problem**: Cursor's default "Run in Sandbox" mode limits TestSprite.
- **Fix**: Chat -> Auto-Run -> Auto-Run Mode -> change to "Ask Everytime" or "Run Everything"

### Claude Code

- MCP server is project-scoped. Must be added per project directory.
- No specific "Run in Terminal" or sandbox configuration mentioned for Claude Code
  (unlike Cursor's sandbox toggle).

### General

- Check installation: `npm list -g @testsprite/testsprite-mcp`
- Verify API key is correct
- Restart IDE after configuration changes

---

## Key Findings Summary

### Backend Testing Workflow with Claude Code

1. Start your backend application locally on its port (e.g., 8000)
2. Prompt the AI: "Test my backend APIs" or similar natural language
3. AI calls `testsprite_bootstrap_tests` with `type: "backend"`, `localPort: 8000`, `projectPath: "/absolute/path"`, `testScope: "codebase"`
4. AI reads PRD, analyzes code, generates normalized PRD
5. AI calls `testsprite_generate_backend_test_plan`
6. AI calls `testsprite_generate_code_and_execute`
7. Tests run in TestSprite's cloud sandbox
8. Report generated with pass/fail, logs, fix recommendations

### Tunnel / Proxy Configuration

**No explicit documentation found** about tunnel setup, proxy configuration, or port
forwarding requirements. TestSprite's cloud sandbox needs to reach your localhost, but the
mechanism is not documented in the user-facing docs. The application detection troubleshooting
page only says to ensure your app is running and accessible locally, and to check firewall
settings.

### The `pathname` Parameter

**Not documented anywhere** in the official TestSprite docs. The `testsprite_bootstrap_tests`
tool accepts only: `localPort`, `type`, `projectPath`, `testScope`.

### "Run in Terminal" Step

Not explicitly documented as a separate concept. The workflow is fully automated through MCP
tool calls. The Cursor-specific issue is about "Run in Sandbox" vs "Run Everything" mode for
auto-running MCP tool calls.

### How Cloud Sandbox Accesses Local App

The cloud execution process described is:
1. Sandbox Creation -- isolated testing environment
2. Dependency Installation -- installs required packages
3. Test Execution -- runs all generated tests
4. Result Collection -- gathers results, screenshots, logs
5. Report Generation -- creates comprehensive test report

The connection between the cloud sandbox and your localhost is handled internally by
TestSprite and is not user-configurable based on available documentation.

### Health Check / Root Path Requirements

No documentation mentions specific health check endpoints, root path responses, or HTTP
status code requirements. The only requirement is that your application must be running and
responding on the specified port. The diagnostic commands (`curl http://localhost:PORT`) imply
your app should respond to HTTP requests.

---

## Sources

- [Introduction](https://docs.testsprite.com/mcp/getting-started/introduction)
- [Overview](https://docs.testsprite.com/mcp/getting-started/overview)
- [Installation](https://docs.testsprite.com/mcp/getting-started/installation)
- [First Test](https://docs.testsprite.com/mcp/getting-started/first-test)
- [MCP Tools Reference](https://docs.testsprite.com/mcp/core/tools)
- [Test Execution Issues](https://docs.testsprite.com/mcp/troubleshooting/test-execution-issues)
- [Application Detection Issues](https://docs.testsprite.com/mcp/troubleshooting/application-detection-issues)
- [IDE Configuration Issues](https://docs.testsprite.com/mcp/troubleshooting/ide-configuration-issues)
- [Create Tests for New Projects](https://docs.testsprite.com/mcp/core/create-tests-new-project)
- [npm Package](https://www.npmjs.com/package/@testsprite/testsprite-mcp)
- [TestSprite Solutions/MCP](https://www.testsprite.com/solutions/mcp)
- [Cursor Directory Listing](https://cursor.directory/mcp/testsprite-mcp)
