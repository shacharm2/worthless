# TestSprite App Requirements Research

> Research date: 2026-04-03
> Status: Research only -- no code changes

## 1. What is TestSprite?

TestSprite is an autonomous AI testing agent that generates, executes, and analyzes tests via MCP (Model Context Protocol) integration with IDEs (Claude Code, Cursor, Windsurf, VS Code). It provides comprehensive frontend (UI/E2E) and backend (API) testing without manual test writing.

Key distinction: TestSprite is a **cloud-hosted testing service**, not a local test runner. Tests are generated and executed on TestSprite's cloud infrastructure, not locally.

## 2. Architecture Overview

### Components

| Component | Location | Role |
|-----------|----------|------|
| MCP Server | npm: `@testsprite/testsprite-mcp` | Bridge between IDE AI assistant and TestSprite cloud |
| Cloud Sandbox | TestSprite infrastructure | Executes generated tests (Playwright, Cypress, etc.) |
| Secure Tunnel | Managed by TestSprite | Connects cloud sandbox to your local running app |
| AI Engine | TestSprite cloud | Analyzes code, generates PRD, test plans, test code |

### Flow

```
IDE AI Assistant
    |
    v (MCP protocol)
TestSprite MCP Server (local npm package)
    |
    v (API calls with API key)
TestSprite Cloud
    |
    +-- AI Engine: analyzes code, generates tests
    +-- Cloud Sandbox: executes tests
    +-- Secure Tunnel: reaches your local app
    |
    v (tunnel)
Your Local App (listening on localPort)
```

## 3. The 8-Step MCP Workflow

The AI assistant orchestrates these tools in sequence:

| Step | MCP Tool | What It Does |
|------|----------|--------------|
| 1 | `testsprite_bootstrap_tests` | Initialize testing env, detect project, check app is running |
| 2 | (AI reads user PRD) | Parses product requirements or natural language intent |
| 3 | `testsprite_generate_code_summary` | Analyzes codebase, creates `code_summary.json` |
| 4 | `testsprite_generate_standardized_prd` | Creates normalized PRD (`standard_prd.json`) from code + user PRD |
| 5 | `testsprite_generate_backend_test_plan` or `testsprite_generate_frontend_test_plan` | Creates test plan JSON based on project type |
| 6 | `testsprite_generate_code_and_execute` | Generates test code, uploads to cloud sandbox, executes |
| 7 | (Cloud execution) | Tests run against your app via tunnel |
| 8 | `testsprite_generate_test_report` | Produces `TestSprite_MCP_Test_Report.md` and `.html` |

## 4. MCP Tools -- Parameters Reference

### testsprite_bootstrap_tests

Initializes the testing environment and configuration.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `localPort` | number | 5173 | Port where your application is running |
| `type` | string | - | Project type: `"frontend"` or `"backend"` |
| `projectPath` | string | - | Absolute path to project directory |
| `testScope` | string | - | `"codebase"` (full) or `"diff"` (changed files only) |

**What bootstrap does:**
- Detects project type and structure
- Checks if the application is running on the specified port
- Initializes testing configuration
- Sets up the secure tunnel to your local app

### testsprite_generate_code_summary

Analyzes your codebase and creates `code_summary.json`.

- Framework detection (React, Vue, Angular, Node.js, FastAPI, etc.)
- Feature extraction
- Architecture analysis (component relationships)
- Security assessment

### testsprite_generate_standardized_prd

Creates TestSprite's proprietary normalized PRD format.

- Takes user PRD + code analysis as input
- Produces `standard_prd.json`
- Ensures consistent test generation across project types

### testsprite_generate_backend_test_plan

Creates `backend_test_plan.json`. Covers:

- Functional testing
- Error handling
- Response content validation
- Security testing (authz, authn)
- Boundary testing
- Load testing / performance analysis
- Edge cases
- Concurrency testing

### testsprite_generate_frontend_test_plan

Creates `frontend_test_plan.json`. Covers UI journeys, E2E flows, visual regression.

### testsprite_generate_code_and_execute

| Parameter | Type | Description |
|-----------|------|-------------|
| `projectName` | string | Project name |
| `projectPath` | string | Absolute path to project directory |
| `testIds` | array | Test IDs to run (empty array = all tests) |
| `additionalInstruction` | string | Context-specific instructions |

Generates production-ready test code, uploads to cloud sandbox, executes against your running app.

### testsprite_generate_test_report

Produces final reports with:
- Test results summary
- Failure analysis with screenshots/videos
- Bug descriptions and fix recommendations
- `TestSprite_MCP_Test_Report.md` and `.html`

## 5. What Your App MUST Expose

### Minimum Requirements

1. **HTTP server running on a known port.** TestSprite connects to `localhost:{localPort}` via a secure tunnel. Your app must be actively listening.

2. **The port must be accessible on localhost.** Binding to `127.0.0.1` or `0.0.0.0` both work -- the tunnel client runs locally.

3. **No specific health check endpoint required.** TestSprite's bootstrap step checks if the port is open, not whether a specific path returns 200. However, having standard routes (e.g., `/`, `/health`) is good practice.

4. **API routes must be reachable.** For backend testing, TestSprite generates tests against your API endpoints. It discovers these from code analysis (not from an OpenAPI spec, though having one helps the AI).

### What TestSprite Does NOT Require

- No OpenAPI/Swagger spec required (but helps if present)
- No specific framework (supports any HTTP framework)
- No test fixtures or seed data setup (tests are generated from scratch)
- No Docker or containerization
- No CI/CD integration for MCP mode (that's a separate workflow)

### What Helps TestSprite Work Better

- **A PRD or README** describing what the app does (fed to the AI for context)
- **OpenAPI spec** if available (helps generate more accurate API tests)
- **Standard project structure** (helps framework detection)
- **Test credentials** if auth is required (provided during bootstrap or as `additionalInstruction`)

## 6. Tunnel Mechanism

### How It Works

TestSprite uses a **managed secure tunnel** to bridge between their cloud sandbox and your local application. The exact tunnel technology is not publicly documented, but based on architecture patterns:

1. The `testsprite_bootstrap_tests` MCP call triggers tunnel setup
2. A tunnel client (likely Cloudflare-based or custom) is established from your machine to TestSprite's cloud
3. The cloud sandbox routes test traffic through this tunnel to your `localhost:{localPort}`
4. Tests execute in the cloud but hit your local endpoints

### Key Characteristics

- **Outbound-only connection** from your machine (no inbound port opening needed)
- **Encrypted** tunnel (secure data transit)
- **Managed** by TestSprite (no manual tunnel setup required)
- **Ephemeral** -- tunnel exists only during test execution

### Troubleshooting: Application Detection Issues

If TestSprite can't reach your app:
- Verify your app is actually running on the specified port
- Check firewall settings (the tunnel client needs outbound HTTPS)
- Ensure the port isn't blocked by other security software
- Default port is 5173 (Vite default) -- you likely need to override this for backend apps

## 7. Output File Structure

After a test run, TestSprite creates:

```
{projectPath}/
  testsprite_tests/
    tmp/
      prd_files/           # User PRD copies
      config.json          # Project configuration
      code_summary.json    # Code analysis results
      report_prompt.json   # AI analysis data
      test_results.json    # Raw execution results
    standard_prd.json      # Normalized PRD
    backend_test_plan.json # or frontend_test_plan.json
    TC001/                 # Test case 1
    TC002/                 # Test case 2
    ...
    TestSprite_MCP_Test_Report.md
    TestSprite_MCP_Test_Report.html
```

## 8. Implications for Worthless

### What Worthless needs to do for TestSprite compatibility

1. **Run the proxy on a known port.** Default FastAPI dev server on e.g. port 8000. TestSprite bootstrap with `localPort: 8000, type: "backend"`.

2. **Have routes discoverable from code.** TestSprite's code analysis will scan `src/worthless/proxy/app.py` and find FastAPI route definitions.

3. **Provide test credentials or mock mode.** Since Worthless is an API proxy requiring enrolled keys, TestSprite tests will need either:
   - A test enrollment with dummy shards, or
   - A mock/test mode that bypasses auth for TestSprite's cloud sandbox, or
   - Test credentials passed via `additionalInstruction` parameter

4. **PRD availability.** TestSprite reads a PRD to understand product intent. The existing `.taskmaster/docs/prd.md` or `.planning/PROJECT.md` could serve this purpose.

5. **No OpenAPI spec blocker.** FastAPI auto-generates OpenAPI at `/openapi.json` -- this is a significant advantage for TestSprite's test generation.

### Concerns

- **Security sensitivity.** TestSprite's cloud sandbox will be making HTTP requests to our proxy. If the proxy is in a test mode that bypasses security gates, that's fine. But we must ensure the tunnel doesn't expose real key material.
- **Tunnel and firewall.** The tunnel client needs outbound HTTPS access. Corporate firewalls might block this.
- **Test isolation.** TestSprite tests run against a live app instance. Need a dedicated test instance with test data, not production state.

## 9. MCP Configuration for Claude Code

```json
{
  "mcpServers": {
    "testsprite": {
      "command": "npx",
      "args": ["-y", "@testsprite/testsprite-mcp@latest"],
      "env": {
        "API_KEY": "<testsprite-api-key>"
      }
    }
  }
}
```

API key obtained from TestSprite web portal at https://www.testsprite.com/dashboard.

## 10. Sources

- [TestSprite Documentation: Introduction](https://docs.testsprite.com/)
- [MCP Tools References](https://docs.testsprite.com/mcp/core/tools)
- [MCP Tools Reference (Concepts)](https://docs.testsprite.com/concepts/tools)
- [MCP Testing Workflow](https://docs.testsprite.com/concepts/workflow)
- [Create Tests for New Projects](https://docs.testsprite.com/mcp/core/create-tests-new-project)
- [First MCP Test](https://docs.testsprite.com/mcp/getting-started/first-test)
- [Application Detection Issues](https://docs.testsprite.com/mcp/troubleshooting/application-detection-issues)
- [Installation](https://docs.testsprite.com/mcp/getting-started/installation)
- [Overview](https://docs.testsprite.com/mcp/getting-started/overview)
- [@testsprite/testsprite-mcp on npm](https://www.npmjs.com/package/@testsprite/testsprite-mcp)
- [TestSprite GitHub Docs](https://github.com/TestSprite/Docs)
- [TestSprite FAQ](https://docs.testsprite.com/questions)
- [TestSprite MCP Solutions Page](https://www.testsprite.com/solutions/mcp)
- [Claude Code Testing Tool](https://www.testsprite.com/use-cases/en/claude-code-testing-tool)
- [Back-End (APIs) Testing](https://docs.testsprite.com/web-portal/api-testing)
- [TestSprite 2.0 Press Release](https://www.prnewswire.com/news-releases/testsprite-2-0-aims-to-solve-ai-code-quality-crisis-with-its-new-mcp-server-302505031.html)
