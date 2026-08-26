export const BASELINE_REQUIRED = Object.freeze({
  status: "BASELINE_REQUIRED",
  first_real_staging_run: "measure raw category distributions",
  approval: "Architect approval required before freezing production thresholds",
  current_values: "provisional safety gates, not a benchmark claim",
});

const thresholds = Object.freeze({
  http_req_failed: ["rate<0.01"],
  checks: ["rate>0.99"],
  "http_req_duration{category:auth}": ["p(95)<1200"],
  "http_req_duration{category:session}": ["p(95)<750"],
  "http_req_duration{category:workspace_read}": ["p(95)<1000"],
  "http_req_duration{category:knowledge_map_read}": ["p(95)<1500"],
  "http_req_duration{category:plan_read}": ["p(95)<1250"],
  "http_req_duration{category:progress_read}": ["p(95)<1250"],
  "http_req_duration{category:review_read}": ["p(95)<1250"],
  "http_req_duration{category:cost_read}": ["p(95)<1250"],
  "http_req_duration{category:audit_read}": ["p(95)<1250"],
  "http_req_duration{category:document_submit}": ["p(95)<1500"],
  "http_req_duration{category:document_status}": ["p(95)<1250"],
  "http_req_duration{category:ai_teacher_controlled}": ["p(95)<5000"],
  "http_req_duration{category:tenant_isolation}": ["p(95)<1500"],
  document_job_completion_ms: ["p(95)<120000"],
  unauthorized_success_count: ["count==0"],
  real_provider_calls: ["count==0"],
});

const scenarios = Object.freeze({
  smoke: { executor: "shared-iterations", vus: 1, iterations: 2, maxDuration: "2m" },
  average: { executor: "constant-vus", vus: 10, duration: __ENV.K6_DURATION || "10m" },
  stress: { executor: "ramping-vus", startVUs: 5, stages: [
    { duration: "3m", target: 25 }, { duration: "7m", target: 50 }, { duration: "3m", target: 0 },
  ] },
  spike: { executor: "ramping-vus", startVUs: 2, stages: [
    { duration: "30s", target: 75 }, { duration: "90s", target: 75 }, { duration: "60s", target: 2 },
  ] },
  soak: { executor: "constant-vus", vus: 15, duration: __ENV.K6_DURATION || "30m" },
  "tenant-isolation": { executor: "constant-vus", vus: 21, duration: __ENV.K6_DURATION || "10m" },
});

export function buildOptions(profile) {
  if (!scenarios[profile]) throw new Error(`unsupported load profile: ${profile}`);
  return { scenarios: { [profile]: scenarios[profile] }, thresholds };
}
