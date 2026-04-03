# TestSprite MCP Tools Research

**Date:** 2026-04-03
**Package:** `@testsprite/testsprite-mcp` (npm, currently v0.0.22+)
**Docs:** https://docs.testsprite.com/mcp/core/tools

---

## Overview

TestSprite is an AI-powered testing agent that integrates with IDEs (Claude Code, Cursor, Windsurf, VS Code, GitHub Copilot) via Model Context Protocol. It analyzes your codebase, generates test plans from a derived PRD, creates runnable test code, executes tests in **cloud sandboxes**, and returns structured results.

Key architectural detail: **tests execute in TestSprite's cloud infrastructure, not locally.** For backend testing, the bootstrap step creates a tunnel so the cloud sandbox can reach your local server.

---

## MCP Tools Reference (8 tools)

### 1. `testsprite_bootstrap`

**Purpose:** Initialize the testing environment and configuration. This is always the first step.

| Parameter | Type | Description |
|-----------|------|-------------|
| `localPort` | number | Port where your application is running (e.g., 8000) |
| `path` | string | Specific path to test directly (optional) |
| `type` | string | Project type: `"frontend"` or `"backend"` |
| `projectPath` | string | Absolute path to your project directory |
| `testScope` | string | `"codebase"` (full) or `"diff"` (changed files only) |

**What it does:**
- Detects project type (frontend/backend)
- Discovers running application on specified port
- **Creates a tunnel** to expose your localhost to TestSprite's cloud sandbox (for backend testing, since cloud-hosted test runners need to reach your local server)
- Opens TestSprite configuration portal
- Defines testing scope (full codebase vs. git diff)
- Creates a `testsprite_tests/` directory in your project with `config.json`

**Important:** Your server must be running before bootstrap. TestSprite does not start your server for you.

### 2. `testsprite_check_account_info`

**Purpose:** Verify your TestSprite account status, API key validity, and remaining test credits/quota.

| Parameter | Type | Description |
|-----------|------|-------------|
| (none documented) | - | Uses the API_KEY from MCP server config |

**What it does:**
- Validates the configured API key
- Returns account tier, usage limits, remaining credits

### 3. `testsprite_generate_code_summary`

**Purpose:** Analyze your codebase to produce a structured summary used by downstream tools.

| Parameter | Type | Description |
|-----------|------|-------------|
| `projectPath` | string | Absolute path to project root |

**What it does:**
- Scans your source files
- Produces `code_summary.json` containing: tech stack, features (with descriptions), file references
- This output feeds into PRD generation and test planning

### 4. `testsprite_generate_standardized_prd`

**Purpose:** Generate a normalized Product Requirements Document from code analysis.

| Parameter | Type | Description |
|-----------|------|-------------|
| `projectPath` | string | Absolute path to project root |

**What it does:**
- Uses the code summary to infer product functionality
- Creates `standard_prd.json` containing: product overview/goals, user stories with acceptance criteria, functional requirements, technical specifications
- This PRD drives test plan generation (tests verify that code matches inferred requirements)

### 5. `testsprite_generate_backend_test_plan`

**Purpose:** Generate a comprehensive test plan for backend/API projects.

| Parameter | Type | Description |
|-----------|------|-------------|
| `projectPath` | string | Absolute path to project root |
| (uses outputs from bootstrap, code_summary, and standardized_prd) | | |

**What it does:**
- Reads the standardized PRD and code summary
- Generates prioritized test cases covering: API endpoints, logic paths, edge cases, error handling, performance
- Produces a test plan document with test IDs that can be selectively executed

### 6. `testsprite_generate_frontend_test_plan`

**Purpose:** Same as backend test plan but for frontend/UI projects.

| Parameter | Type | Description |
|-----------|------|-------------|
| `projectPath` | string | Absolute path to project root |

**What it does:**
- Generates UI/E2E test cases covering: page flows, user interactions, form validation, responsive behavior
- Uses Playwright or similar browser automation under the hood

### 7. `testsprite_generate_code_and_execute`

**Purpose:** Generate runnable test code from the test plan and execute it in cloud sandboxes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `projectName` | string | Name of your project |
| `projectPath` | string | Absolute path to your project directory |
| `testIds` | array | Specific test IDs to run (empty array = run all) |
| `additionalInstruction` | string | Custom instructions to guide test generation (e.g., "Focus on auth endpoints") |

**What it does:**
- Takes the test plan and generates actual executable test code
- **Executes tests in TestSprite's cloud sandbox** (not locally)
- For backend: the cloud sandbox hits your app through the tunnel created by bootstrap
- For frontend: spins up a headless browser in the cloud
- Creates files in `testsprite_tests/` directory: test files, `report_prompt.json`, `test_results.json`
- Returns structured results: pass/fail per test, failure classification (real bug vs. flaky vs. environment), screenshots/logs for failures
- Provides fix recommendations back to the AI agent

### 8. `testsprite_open_test_result_dashboard`

**Purpose:** Open the TestSprite web dashboard showing test results.

| Parameter | Type | Description |
|-----------|------|-------------|
| (none or projectPath) | - | Opens browser to TestSprite dashboard |

**What it does:**
- Opens `https://www.testsprite.com/dashboard/mcp/tests` (or project-specific URL)
- Shows visual test results: pass/fail, logs, screenshots, videos
- Provides a shareable link to test reports

---

## Expected Workflow

```
Step 1: testsprite_bootstrap
         Your server must be running. Bootstrap detects it, creates tunnel,
         sets up config in testsprite_tests/

Step 2: testsprite_generate_code_summary
         Analyzes codebase -> code_summary.json

Step 3: testsprite_generate_standardized_prd
         Infers PRD from code -> standard_prd.json

Step 4: testsprite_generate_backend_test_plan  (or frontend)
         Creates prioritized test plan from PRD + code summary

Step 5: testsprite_generate_code_and_execute
         Generates test code, runs in cloud sandbox, returns results

Step 6: testsprite_open_test_result_dashboard
         Visual inspection of results in browser
```

Optional tools used anytime:
- `testsprite_check_account_info` -- verify credits/quota before running
- Steps 2-3 can sometimes be implicit (some workflows combine them)

---

## Key Questions Answered

### 1. Does TestSprite need my server running?

**Yes, for backend testing.** You must start your server (e.g., `uvicorn app:app --port 8000`) before calling `testsprite_bootstrap`. Bootstrap discovers the running app on the specified `localPort` and creates a tunnel so cloud test runners can reach it.

### 2. Does bootstrap create a tunnel?

**Yes.** Bootstrap sets up a tunnel from your localhost to TestSprite's cloud infrastructure. This is how the cloud-hosted test execution environment reaches your local backend server. The exact tunneling technology is not publicly documented (likely cloudflared or a proprietary solution).

### 3. Where does test code execute?

**In TestSprite's cloud sandboxes.** Tests never run locally. The cloud sandbox:
- For backend: sends HTTP requests through the tunnel to your local server
- For frontend: runs a headless browser pointing at your app (via tunnel)
- Captures logs, screenshots, videos
- Classifies failures (real bug vs. flaky vs. environment issue)

### 4. What files does TestSprite create locally?

All artifacts go in `<projectPath>/testsprite_tests/`:
- `config.json` -- project configuration
- `code_summary.json` -- codebase analysis
- `standard_prd.json` -- inferred PRD
- `report_prompt.json` -- AI analysis data
- `test_results.json` -- execution results
- Test source files

### 5. What does the flow look like for Worthless proxy testing?

```bash
# 1. Start the proxy server
cd /Users/shachar/Projects/worthless/worthless
uv run uvicorn worthless.proxy.app:app --port 8000

# 2. In Claude Code, the AI would call:
testsprite_bootstrap(localPort=8000, type="backend",
    projectPath="/Users/shachar/Projects/worthless/worthless",
    testScope="codebase")

# 3. Then sequentially:
testsprite_generate_code_summary(...)
testsprite_generate_standardized_prd(...)
testsprite_generate_backend_test_plan(...)
testsprite_generate_code_and_execute(projectName="worthless", testIds=[], ...)

# 4. Review results:
testsprite_open_test_result_dashboard()
```

---

## Caveats and Limitations

1. **Cloud-only execution.** You cannot run tests locally or behind a firewall without the tunnel. Air-gapped environments are not supported.
2. **API key required.** TestSprite requires an account and API key. Check account limits with `testsprite_check_account_info`.
3. **Tunnel stability.** If the tunnel drops (e.g., bootstrap session ends), cloud tests cannot reach your backend.
4. **No custom test frameworks.** TestSprite generates its own test code. It does not run your existing pytest/jest suites -- it creates new tests from scratch based on the inferred PRD.
5. **Inferred PRD accuracy.** The standardized PRD is auto-generated from code analysis. For projects with complex business logic, the inferred PRD may miss nuances. You can provide `additionalInstruction` to guide test generation.

---

## Sources

- [TestSprite MCP Tools Reference](https://docs.testsprite.com/mcp/core/tools)
- [TestSprite MCP Workflow](https://docs.testsprite.com/concepts/workflow)
- [TestSprite MCP Concepts - Tools](https://docs.testsprite.com/concepts/tools)
- [npm: @testsprite/testsprite-mcp](https://www.npmjs.com/package/@testsprite/testsprite-mcp)
- [TestSprite Installation Guide](https://docs.testsprite.com/mcp/getting-started/installation)
- [TestSprite First Test Guide](https://docs.testsprite.com/mcp/getting-started/first-test)
- [TestSprite MCP Solutions Page](https://www.testsprite.com/solutions/mcp)
- [TestSprite MCP on Cursor Directory](https://cursor.directory/mcp/testsprite-mcp)
- [TestSprite Create Tests for New Projects](https://docs.testsprite.com/mcp/core/create-tests-new-project)
- [TestSprite Application Detection Troubleshooting](https://docs.testsprite.com/mcp/troubleshooting/application-detection-issues)