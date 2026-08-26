import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const unauthorizedSuccessCount = new Counter("unauthorized_success_count");
const realProviderCalls = new Counter("real_provider_calls");
const documentJobCompletionMs = new Trend("document_job_completion_ms", true);
const documentHookEnabled = String(__ENV.DOCUMENT_HOOK_ENABLED || "false").toLowerCase() === "true";
const corpus = JSON.parse(open(__ENV.TENANT_CORPUS_PATH || "./tests/load/fixtures/tenant-corpus.v1.json"));
if ((__ENV.PROVIDER_ADAPTER || "") !== "fake") {
  throw new Error("PROVIDER_ADAPTER=fake is mandatory; real provider load is prohibited");
}

const routeEnvironment = Object.freeze({
  session: "SESSION_PATH",
  workspace_list: "WORKSPACE_LIST_PATH",
  knowledge_map: "KNOWLEDGE_MAP_PATH",
  plan: "PLAN_READ_PATH",
  progress: "PROGRESS_READ_PATH",
  review: "REVIEW_READ_PATH",
  cost: "COST_READ_PATH",
  audit: "AUDIT_READ_PATH",
  ai_teacher: "AI_TEACHER_PATH",
  ...(documentHookEnabled ? {
    document_submit: "DOCUMENT_SUBMIT_PATH",
    document_status: "DOCUMENT_STATUS_PATH",
  } : {}),
});

function required(name) {
  const value = __ENV[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function routes() {
  return Object.fromEntries(Object.entries(routeEnvironment).map(([key, name]) => [key, required(name)]));
}

function interpolate(template, values) {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, encodeURIComponent(value)),
    template,
  );
}

function responseBody(response) {
  try { return response.json(); } catch (_) { return {}; }
}

function params(token, category, expected = [200]) {
  return {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-MentorFlow-Provider-Mode": "fake",
    },
    tags: { category },
    responseCallback: http.expectedStatuses(...expected),
  };
}

function login(userKey, credentials) {
  const credential = credentials[userKey];
  if (!credential) throw new Error(`LOAD_USERS_JSON missing credentials for ${userKey}`);
  const response = http.post(
    `${required("BASE_URL")}${required("LOGIN_PATH")}`,
    JSON.stringify(credential),
    {
      headers: { "Content-Type": "application/json", "X-MentorFlow-Provider-Mode": "fake" },
      tags: { category: "auth" },
      responseCallback: http.expectedStatuses(200),
    },
  );
  const body = responseBody(response);
  const token = body[__ENV.TOKEN_JSON_FIELD || "access_token"];
  check(response, { "login returns token": () => response.status === 200 && Boolean(token) });
  return token;
}

export function setup() {
  if (corpus.organizations.length !== 3) throw new Error("tenant corpus must contain exactly 3 organizations");
  const users = corpus.organizations.flatMap((organization) => organization.users);
  if (users.length < 20) throw new Error("tenant corpus must contain at least 20 users");
  const credentials = JSON.parse(required("LOAD_USERS_JSON"));
  return {
    organizations: corpus.organizations,
    sessions: Object.fromEntries(users.map((user) => [user.user_key, login(user.user_key, credentials)])),
  };
}

function get(token, path, category, expected = [200]) {
  const response = http.get(`${required("BASE_URL")}${path}`, params(token, category, expected));
  check(response, { [`${category} expected status`]: () => expected.includes(response.status) });
  return response;
}

function recordProviderZero(response, label) {
  const body = responseBody(response);
  const providerCalls = Number(body.provider_calls ?? -1);
  check(response, { [`${label} provider calls are zero`]: () => providerCalls === 0 });
  if (providerCalls > 0 || body.provider_adapter === "real") realProviderCalls.add(Math.max(1, providerCalls));
  return body;
}

function foreignProbe(token, path, resourceId) {
  const response = get(
    token,
    interpolate(path, { resource_id: resourceId, workspace_id: resourceId, job_id: resourceId }),
    "tenant_isolation",
    [401, 403, 404],
  );
  if (response.status >= 200 && response.status < 300) unauthorizedSuccessCount.add(1);
}

function randomItem(values) {
  if (!values.length) throw new Error("foreign tenant probe requires a non-empty fixture list");
  return values[Math.floor(Math.random() * values.length)];
}

function documentJob(token, routeSet, workspaceId) {
  if (!documentHookEnabled) return;
  const started = Date.now();
  const submit = http.post(
    `${required("BASE_URL")}${interpolate(routeSet.document_submit, { workspace_id: workspaceId })}`,
    __ENV.DOCUMENT_SUBMIT_JSON || '{"title":"k6-controlled-document","provider_adapter":"fake"}',
    params(token, "document_submit", [200, 202]),
  );
  const body = recordProviderZero(submit, "document submit");
  check(submit, { "document submit returns job id": () => Boolean(body.job_id) });
  if (!body.job_id) return;
  const attempts = Number(__ENV.DOCUMENT_POLL_ATTEMPTS || 12);
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const status = get(
      token,
      interpolate(routeSet.document_status, { job_id: body.job_id }),
      "document_status",
      [200, 202],
    );
    const statusBody = recordProviderZero(status, "document status");
    const jobState = String(statusBody.status || "").toLowerCase();
    if (["completed", "failed", "cancelled"].includes(jobState)) {
      check(status, { "document job completes successfully": () => jobState === "completed" });
      if (jobState === "completed") documentJobCompletionMs.add(Date.now() - started);
      return;
    }
    sleep(Number(__ENV.DOCUMENT_POLL_SECONDS || 0.5));
  }
  check(null, { "document job reaches a terminal state": () => false });
}

export function runEnterpriseScenario(data, profile) {
  const organizationIndex = (__VU - 1) % data.organizations.length;
  const ownOrganization = data.organizations[organizationIndex];
  const foreignOrganization = data.organizations[(organizationIndex + 1) % data.organizations.length];
  const ownUser = ownOrganization.users[(__VU + __ITER) % ownOrganization.users.length];
  const token = data.sessions[ownUser.user_key];
  const workspaceId = ownUser.workspace_ids[__ITER % ownUser.workspace_ids.length];
  const routeSet = routes();

  const session = get(token, routeSet.session, "session");
  check(session, { "session remains in own organization": () => responseBody(session).organization_id === ownOrganization.organization_id });
  const workspaces = get(token, routeSet.workspace_list, "workspace_read");
  if (JSON.stringify(responseBody(workspaces)).includes(foreignOrganization.workspace_ids[0])) unauthorizedSuccessCount.add(1);
  get(token, interpolate(routeSet.knowledge_map, { workspace_id: workspaceId }), "knowledge_map_read");
  get(token, interpolate(routeSet.plan, { workspace_id: workspaceId }), "plan_read");
  get(token, interpolate(routeSet.progress, { workspace_id: workspaceId }), "progress_read");
  get(token, interpolate(routeSet.review, { workspace_id: workspaceId }), "review_read");
  get(token, interpolate(routeSet.cost, { workspace_id: workspaceId }), "cost_read");
  get(token, interpolate(routeSet.audit, { workspace_id: workspaceId }), "audit_read");
  documentJob(token, routeSet, workspaceId);
  const teacher = http.post(
    `${required("BASE_URL")}${interpolate(routeSet.ai_teacher, { workspace_id: workspaceId })}`,
    '{"message":"controlled load turn","provider_adapter":"fake"}',
    params(token, "ai_teacher_controlled", [200, 202]),
  );
  recordProviderZero(teacher, "AI Teacher");

  const foreignTypes = corpus.requirements.foreign_resource_types.filter(
    (type) => type !== "document" || documentHookEnabled,
  );
  const type = randomItem(foreignTypes);
  const routeForType = {
    document: routeSet.document_status,
    knowledge_map: routeSet.knowledge_map,
    plan: routeSet.plan,
    progress: routeSet.progress,
    review: routeSet.review,
    cost: routeSet.cost,
    audit: routeSet.audit,
  }[type];
  const foreignId = randomItem(foreignOrganization.resources[type]);
  foreignProbe(token, routeForType, foreignId);
  check(null, { [`profile ${profile} uses fake provider`]: () => __ENV.PROVIDER_ADAPTER === "fake" });
  sleep(Number(__ENV.ITERATION_SLEEP_SECONDS || 0.2));
}

export function summary(data, profile) {
  return {
    stdout: JSON.stringify({ profile, baseline: "BASELINE_REQUIRED", metrics: data.metrics }, null, 2),
    [`artifacts/enterprise/load/k6-${profile}-summary.json`]: JSON.stringify(data, null, 2),
  };
}
