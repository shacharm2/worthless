// WOR-300 stub — implementation pending.
// Returns 500 so that test/*.test.ts fails RED until WOR-300 lands the real Worker.
//
// Contract the Worker must fulfill (driven by tests in ./test):
//   - curl/wget/go-http UA     → 200 text/plain, body = install.sh
//   - browser UA               → 302 to REDIRECT_URL
//   - missing/empty UA         → 302 to REDIRECT_URL (fail-safe)
//   - ?explain=1 with curl UA  → 200 text/plain with walkthrough

export interface Env {
  GITHUB_RAW_URL: string;
  REDIRECT_URL: string;
}

export default {
  async fetch(_req: Request, _env: Env): Promise<Response> {
    return new Response("not implemented — see WOR-300", { status: 500 });
  },
} satisfies ExportedHandler<Env>;
