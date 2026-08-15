import { test, expect } from '@playwright/test'
import { createServer } from 'node:http'

const proof = 'I explain the reason with an example and evidence, then list the steps and workflow and make a decision.'

async function openDemo(page: any) {
  await page.goto('/')
  const apiResponses: string[] = []
  page.on('response', (response: any) => {
    if (response.url().includes('/api/')) apiResponses.push(`${response.request().method()} ${response.status()} ${response.url()}`)
  })
  await page.locator('.hero-actions button.secondary').click()
  try {
    await expect(page.locator('.plan-body')).toBeVisible({ timeout: 30_000 })
  } catch (error) {
    const toast = await page.locator('.toast').textContent().catch(() => '')
    throw new Error(`Demo did not reach plan view. toast=${toast || '<none>'}; responses=${apiResponses.join(' | ')}; cause=${String(error)}`)
  }
}

test.skip('home exposes the scenario learning run and server completion gate', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('PERSONAL LEARNING OS')).toBeVisible()
  await expect(page.locator('.hero-actions button')).toHaveCount(2)
  await openDemo(page)
  await page.locator('.title-actions button.primary').click()
  await page.getByTestId('nav-flow').click()
  await expect(page.getByTestId('flow-stage-import')).toHaveAttribute('aria-current', 'step')
  await page.getByTestId('flow-stage-knowledge').click()
  const flow = page.locator('main .content > section')
  await expect(page.getByTestId('flow-stage-knowledge')).toHaveAttribute('aria-current', 'step')
  const mapHeaderButton = flow.locator('header button').first()
  if (await mapHeaderButton.isVisible()) await mapHeaderButton.click()
  await expect(page.getByTestId('flow-stage-planning')).toBeEnabled()
  await page.getByTestId('flow-stage-planning').click()
  await expect(page.getByTestId('flow-stage-today')).toBeEnabled()
  await page.getByTestId('flow-stage-today').click()
  await flow.locator('section button').first().click()
  await expect(page.getByTestId('flow-stage-tutor')).toHaveAttribute('aria-current', 'step')
  for (let index = 0; index < 6; index += 1) {
    await expect(page.getByTestId('tutor-answer')).toBeVisible()
    await page.getByTestId('tutor-answer').fill(proof)
    await page.getByTestId('tutor-submit').click()
  }
  await expect(page.getByTestId('tutor-submit')).toBeDisabled()
  await expect(flow.locator('footer button').first()).toBeEnabled()
  await flow.locator('footer button').first().click()
  await page.getByTestId('nav-progress').click()
  await expect(page.locator('.stats-grid')).toBeVisible()
})

test.skip('reference story pack renders character art and stops at its learning gate', async ({ page }) => {
  test.setTimeout(60000)
  await openDemo(page)
  await page.locator('.title-actions button.primary').click()
  await page.locator('nav button').nth(7).click()
  await expect(page.locator('.catalog-card.ready')).toBeVisible()
  const campaignPost = page.waitForResponse(response => response.request().method() === 'POST' && response.url().includes('/api/workspaces/') && response.url().endsWith('/campaigns'))
  await page.locator('.catalog-card.ready .primary').click()
  await campaignPost
  await expect(page.locator('.narrative-page')).toBeVisible()
  await expect(page.locator('.narrative-character img')).toHaveCount(1)
  const readCurrentLine = async () => {
    const ghost = page.locator('.narrative-dialogue .ghost').first()
    if (await ghost.count()) await ghost.click()
    const continueButton = page.locator('.narrative-dialogue .primary')
    await expect(continueButton).toBeVisible()
    await expect(continueButton).toBeEnabled({ timeout: 10000 })
    await continueButton.click()
    await expect(continueButton).toBeEnabled({ timeout: 10000 }).catch(() => undefined)
  }
  await readCurrentLine()
  await readCurrentLine()
  await page.locator('.narrative-choice').first().click()
  for (let index = 0; index < 8 && await page.locator('.learning-gate').count() === 0; index += 1) {
    await readCurrentLine()
  }
  await expect(page.locator('.learning-gate')).toBeVisible()
  await expect(page.locator('.learning-gate button')).toBeEnabled()
})

test('reference story pack reaches ending and postgame after server learning gates', async ({ page }) => {
  test.setTimeout(300000)
  const workspace = await page.request.post('/api/workspaces', { data: { title: 'Reference Story E2E Workspace' } }).then(response => response.json())
  const goal = { goal_statement: 'Reference story learning route', motivation: 'Validate the fixed story pack gates', deadline: '2026-08-30', minutes_per_day: 30, days_per_week: 5, preferred_weekdays: [1, 2, 3, 4, 5], current_level: 'beginner', expected_output: 'A verifiable answer', unavailable_dates: [], success_criteria: ['Explain the key decision'] }
  await page.request.post(`/api/workspaces/${workspace.id}/goal`, { data: goal })
  await page.request.post(`/api/workspaces/${workspace.id}/feasibility/analyze`, { data: {} })
  await page.request.post(`/api/workspaces/${workspace.id}/feasibility/confirm`, { data: { confirmed: true } })
  const plan = await page.request.post(`/api/workspaces/${workspace.id}/plans/generate`, { data: {} }).then(response => response.json())
  const campaign = await page.request.post(`/api/workspaces/${workspace.id}/campaigns`, { data: { story_pack_id: 'reference_echo_before_rain' } }).then(response => response.json())
  let campaignState = campaign
  const submitProof = async (task: any, expectedTerminalPhase: string) => {
    let run = await page.request.post(`/api/tasks/${task.id}/learning-runs`, { data: {} }).then(response => response.json())
    for (let index = 0; index < 12 && run.status !== 'passed'; index += 1) {
      run = await page.request.post(`/api/learning-runs/${run.run_id}/submit`, { data: { response: proof, evidence: proof, expected_revision: run.revision, idempotency_key: `full-story-${run.run_id}-${run.revision}` } }).then(response => response.json())
      expect(run.status).not.toBe('failed')
    }
    expect(run.status).toBe('passed')
    expect(run.phase).toBe(expectedTerminalPhase)
  }
  const walkTo = async (targetSceneId: string) => {
    let current = await page.request.get(`/api/campaigns/${campaignState.campaign_id}`).then(response => response.json())
    for (let index = 0; index < 80 && current.current_scene_id !== targetSceneId; index += 1) {
      const scene = current.current_scene
      if (!scene.lines_complete) {
        current = await page.request.post(`/api/campaigns/${current.campaign_id}/advance`, { data: { action: 'continue', expected_revision: current.revision, idempotency_key: `full-story-line-${current.revision}` } }).then(response => response.json())
      } else if (scene.choices?.length) {
        current = await page.request.post(`/api/campaigns/${current.campaign_id}/choices`, { data: { choice_id: scene.choices[0].choice_id, expected_revision: current.revision, idempotency_key: `full-story-choice-${current.revision}-${scene.choices[0].choice_id}` } }).then(response => response.json())
      } else {
        expect(current.state?.pending_learning_gate?.status || 'passed').toBe('passed')
        current = await page.request.post(`/api/campaigns/${current.campaign_id}/advance`, { data: { action: 'continue', expected_revision: current.revision, idempotency_key: `full-story-scene-${current.revision}` } }).then(response => response.json())
      }
    }
    expect(current.current_scene_id).toBe(targetSceneId)
    campaignState = current
    return current
  }
  let current = await walkTo('echo_first_pass')
  const tasks = plan.study_days.flatMap((day: any) => day.tasks).filter((task: any) => !['review', 'final', 'final_assessment', 'final_task', 'postgame'].includes(task.task_type))
  expect(tasks.length).toBeGreaterThan(0)
  for (const task of tasks) await submitProof(task, 'passed')
  current = await walkTo('echo_review')
  const reviewTask = plan.study_days.flatMap((day: any) => day.tasks).find((task: any) => task.task_type === 'review')
  expect(reviewTask).toBeTruthy()
  await submitProof(reviewTask, 'review_passed')
  current = await walkTo('echo_final_assessment')
  const finalTask = plan.study_days.flatMap((day: any) => day.tasks).find((task: any) => ['final', 'final_assessment', 'final_task'].includes(task.task_type))
  expect(finalTask).toBeTruthy()
  await submitProof(finalTask, 'final_passed')
  current = await walkTo('echo_ending_complete')
  current = await walkTo('echo_postgame')
  expect(current.state.ending_state).toBe('echo_ending_complete')
})

test('learning run is server-evaluated and forged public events are rejected', async ({ page }) => {
  const workspace = await page.request.post('/api/workspaces', { data: { title: 'Learning Run E2E Workspace' } }).then(response => response.json())
  const goal = { goal_statement: 'A verifiable learning loop', motivation: 'Validate the complete learning chain', deadline: '2030-08-30', minutes_per_day: 30, days_per_week: 5, preferred_weekdays: [1, 2, 3, 4, 5], current_level: 'beginner', expected_output: 'A verifiable answer', unavailable_dates: [], success_criteria: ['Explain the key decision'] }
  await page.request.post(`/api/workspaces/${workspace.id}/goal`, { data: goal })
  expect((await page.request.post(`/api/workspaces/${workspace.id}/feasibility/analyze`, { data: {} })).ok()).toBeTruthy()
  await page.request.post(`/api/workspaces/${workspace.id}/feasibility/confirm`, { data: { confirmed: true } })
  const plan = await page.request.post(`/api/workspaces/${workspace.id}/plans/generate`, { data: {} }).then(response => response.json())
  const task = plan.study_days.flatMap((day: any) => day.tasks).find((item: any) => item.task_type === 'practice')
  expect(task).toBeTruthy()
  let run = await page.request.post(`/api/tasks/${task.id}/learning-runs`, { data: {} }).then(response => response.json())
  for (let index = 0; index < 5; index += 1) run = await page.request.post(`/api/learning-runs/${run.run_id}/submit`, { data: { response: proof, evidence: proof, expected_revision: run.revision } }).then(response => response.json())
  expect(run.phase).toBe('daily_variant_test')
  run = await page.request.post(`/api/learning-runs/${run.run_id}/submit`, { data: { response: proof, evidence: proof, expected_revision: run.revision } }).then(response => response.json())
  expect(run.status).toBe('passed')
  const campaign = await page.request.post(`/api/workspaces/${workspace.id}/campaigns`, { data: { story_pack_id: 'dev_fixture_minimal' } }).then(response => response.json())
  const rejected = await page.request.post(`/api/campaigns/${campaign.campaign_id}/learning-events`, { data: { event_type: 'INDEPENDENT_PROBLEM_PASSED', source_type: 'evaluator', source_id: 'browser-forged', idempotency_key: 'browser-forged' } })
  expect([403, 404]).toContain(rejected.status())
})

test.skip('same reference Story Pack compiles different law and algorithm adaptations', async ({ page }) => {
  const createAdapted = async (title: string, goalStatement: string, sourceText: string) => {
    const workspace = await page.request.post('/api/workspaces', { data: { title } }).then(response => response.json())
    await page.request.post(`/api/workspaces/${workspace.id}/sources/text`, { data: { title: 'uploaded notes', text: sourceText } })
    await page.request.post(`/api/workspaces/${workspace.id}/goal`, { data: { goal_statement: goalStatement, motivation: 'adaptive e2e', deadline: '2030-08-30', minutes_per_day: 30, days_per_week: 5, preferred_weekdays: [1, 2, 3, 4, 5], current_level: 'beginner', expected_output: 'verifiable result', unavailable_dates: [], success_criteria: ['Explain key evidence'] } })
    const profile = await page.request.post(`/api/workspaces/${workspace.id}/task-profile/analyze`, { data: {} }).then(response => response.json())
    const campaign = await page.request.post(`/api/workspaces/${workspace.id}/campaigns`, { data: { story_pack_id: 'reference_echo_before_rain' } }).then(response => response.json())
    return { profile, blueprint: campaign.adaptation_blueprint, contract: await page.request.get(`/api/campaigns/${campaign.campaign_id}/scene-contracts/current`).then(response => response.json()) }
  }
  const law = await createAdapted('Law adaptation', 'Law case analysis and evidence argument', 'law evidence testimony precedent argument')
  const algorithms = await createAdapted('Algorithm adaptation', 'LeetCode graph traversal and code repair', 'LeetCode binary trees, graph traversal, code complexity and route verification')
  expect(law.profile.domain).toBe('law')
  expect(law.blueprint.fusion_adapter_ids).toEqual(['evidence_argument'])
  expect(law.contract.story_object_type).toBe('testimony_analysis')
  expect(algorithms.profile.recommended_adapter_id).toBe('coding_practice')
  expect(algorithms.blueprint.fusion_adapter_ids).toEqual(['coding_practice'])
  expect(algorithms.contract.story_object_type).toBe('system_patch')
  expect(law.contract.story_problem).not.toBe(algorithms.contract.story_problem)
})

test.skip('story catalog keeps three algorithm candidates and rematches for law', async ({ page }) => {
  test.setTimeout(120000)
  const createWorkspace = async (title: string, goalStatement: string, sourceText: string) => {
    await page.goto('/')
    const workspace = await page.request.post('/api/workspaces', { data: { title, execution_mode: 'real' } }).then(response => response.json())
    await page.reload()
    await expect(page.locator('.space-switch strong')).toContainText(title)
    await page.request.post(`/api/workspaces/${workspace.id}/sources/text`, { data: { title: 'matching material', text: sourceText } })
    await page.request.post(`/api/workspaces/${workspace.id}/goal`, { data: { goal_statement: goalStatement, motivation: 'catalog selection e2e', deadline: '2030-08-30', minutes_per_day: 45, days_per_week: 5, preferred_weekdays: [1, 2, 3, 4, 5], current_level: 'beginner', expected_output: 'verifiable result', unavailable_dates: [], success_criteria: ['explain and apply'] } })
    return workspace
  }
  await createWorkspace('Catalog algorithm workspace', 'LeetCode graph traversal and code repair', 'LeetCode binary trees, graph traversal, code complexity and verification')
  await page.locator('nav button').nth(7).click()
  await expect(page.getByTestId('task-profile-summary')).toContainText('编程练习')
  await expect(page.locator('.catalog-card')).toHaveCount(3)
  await expect(page.locator('.catalog-card.ready')).toHaveCount(1)
  await expect(page.locator('.catalog-card.pack-required')).toHaveCount(2)
  await expect(page.getByTestId('required-section')).toContainText('PACK_REQUIRED')
  await page.getByTestId('required-section').getByRole('button', { name: '提交策展请求' }).first().click()
  await expect(page.locator('[role="status"]')).toContainText('已创建策展请求')
  const algorithmCards = await page.locator('.catalog-card h2').allTextContents()
  await createWorkspace('Catalog law workspace', 'Law case analysis and evidence argument', 'law evidence testimony precedent argument')
  await page.locator('nav button').nth(7).click()
  await expect(page.getByTestId('task-profile-summary')).toContainText('案例分析')
  await expect(page.locator('.catalog-card')).toHaveCount(3)
  const lawCards = await page.locator('.catalog-card h2').allTextContents()
  expect(lawCards).not.toEqual(algorithmCards)
})
test('create flow reaches real import controls and uploads a file', async ({ page }) => {
  const server = createServer((request, response) => { if (request.url?.endsWith('/chat/completions')) { let body = ''; request.on('data', chunk => { body += chunk }); request.on('end', () => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify({ model: 'mock-model', choices: [{ message: { content: JSON.stringify({ title: 'Mock plan', message: 'Mock tutor', status: 'grounded' }) } }] })) }); return } response.writeHead(404); response.end() })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address(); const baseUrl = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`
  try {
    await page.goto('/'); await page.locator('nav button').nth(5).click(); await page.getByLabel('Provider').selectOption('openai-compatible'); await page.getByLabel('Base URL').fill(baseUrl); await page.getByLabel('Model').fill('mock-model'); await page.getByLabel('API Key').fill('e2e-secret'); await page.getByRole('button', { name: /OpenAI Compatible/ }).click(); await expect(page.locator('.provider-form')).toBeVisible(); await page.locator('nav button').nth(0).click(); await page.locator('.hero-actions button.primary').click(); await page.locator('.form-panel input').first().fill('Playwright workspace'); await page.locator('.form-actions button.primary').click();
    const picker = page.getByTestId('file-input'); await expect(picker).toBeAttached(); await picker.setInputFiles({ name: 'e2e.md', mimeType: 'text/markdown', buffer: Buffer.from('E2E evidence') }); await expect(page.locator('.source-import-panel button').first()).toBeEnabled(); await page.locator('.source-import-panel button').first().click(); await expect(page.locator('.source-import-panel [role="status"]')).toContainText('完成')
  } finally { await new Promise<void>(resolve => server.close(() => resolve())) }
})

test('real provider mock remains connected through tutor', async ({ page }) => {
  const server = createServer((request, response) => { if (request.url?.endsWith('/chat/completions')) { let body = ''; request.on('data', chunk => { body += chunk }); request.on('end', () => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify({ model: 'mock-model', choices: [{ message: { content: JSON.stringify({ title: 'Mock plan', message: 'Mock tutor response', status: 'grounded' }) } }] })) }); return } response.writeHead(404); response.end() })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve)); const address = server.address(); const baseUrl = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`
  try {
    const provider = await page.request.post('/api/provider-sessions', { data: { provider: 'openai-compatible', base_url: baseUrl, model: 'mock-model', api_key: 'e2e-secret' } }).then(response => response.json())
    expect(provider.provider_session_id).toBeTruthy()
    const tested = await page.request.post(`/api/provider-sessions/${provider.provider_session_id}/test`)
    expect(tested.ok(), await tested.text()).toBeTruthy()
    const workspace = await page.request.post('/api/workspaces', { data: { title: 'Provider E2E Workspace', execution_mode: 'real', provider_session_id: provider.provider_session_id } }).then(response => response.json())
    await page.request.post(`/api/workspaces/${workspace.id}/sources/text`, { data: { title: 'Provider evidence', text: '# Evidence\n\nProvider evidence.' } })
    await page.request.post(`/api/workspaces/${workspace.id}/goal`, { data: { goal_statement: 'Real provider learning flow', motivation: 'provider e2e', deadline: '2030-08-30', minutes_per_day: 30, days_per_week: 5, preferred_weekdays: [1, 2, 3, 4, 5], current_level: 'beginner', expected_output: 'Provider result', unavailable_dates: [], success_criteria: ['Complete verification'] } })
    await page.request.post(`/api/workspaces/${workspace.id}/feasibility/analyze`, { data: { provider_session_id: provider.provider_session_id } })
    await page.request.post(`/api/workspaces/${workspace.id}/feasibility/confirm`, { data: { confirmed: true } })
    const planResponse = await page.request.post(`/api/workspaces/${workspace.id}/plans/generate`, { data: { provider_session_id: provider.provider_session_id } })
    expect(planResponse.ok(), await planResponse.text()).toBeTruthy()
    const plan = await planResponse.json()
    const lessonId = plan.milestones[0].modules[0].lessons[0].id
    const session = await page.request.post(`/api/workspaces/${workspace.id}/sessions`, { data: { lesson_id: lessonId, provider_session_id: provider.provider_session_id } }).then(response => response.json())
    const tutor = await page.request.post(`/api/sessions/${session.id}/messages`, { data: { message: 'Explain the evidence', provider_session_id: provider.provider_session_id } })
    expect(tutor.ok()).toBeTruthy()
    const tutorBody = await tutor.json()
    expect(tutorBody.generation_meta.fallback_used).toBe(false)
    expect(tutorBody.generation_meta.provider_session_id).toBe(provider.provider_session_id)
  } finally { await new Promise<void>(resolve => server.close(() => resolve())) }
})

test('real provider failure is visible and never becomes Fake', async ({ page }) => {
  const server = createServer((request, response) => { if (request.url?.endsWith('/chat/completions')) { response.writeHead(401, { 'content-type': 'application/json' }); response.end(JSON.stringify({ error: 'unauthorized' })); return } response.writeHead(404); response.end() })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve)); const address = server.address(); const baseUrl = `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`
  try {
    await page.goto('/')
    await page.getByTestId('nav-settings').click()
    await page.getByLabel('Provider').selectOption('openai-compatible')
    await page.getByLabel('Base URL').fill(baseUrl)
    await page.getByLabel('Model').fill('bad-model')
    await page.getByLabel('API Key').fill('bad-key')
    await page.getByRole('button', { name: /OpenAI Compatible/ }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.locator('.diagnostic-panel pre')).toContainText('"provider": "openai-compatible"')
    await expect(page.locator('.diagnostic-panel pre')).not.toContainText('"provider": "fake"')
  } finally { await new Promise<void>(resolve => server.close(() => resolve())) }
})

const normalZipBase64 = 'UEsDBBQAAAAIAEwE7Fzr2X4VGwAAABkAAAANAAAAbm90ZXMvamF2YS5tZFNW8Arz5eJyLctMSc1LTlVIK8rPVYjyDNADAFBLAwQUAAAACABMBOxcViQN2g8AAAANAAAACwAAAHNyYy9tYWluLnB5KyjKzCvRUCpOTEtV0gQAUEsBAhQAFAAAAAgATATsXOvZfhUbAAAAGQAAAA0AAAAAAAAAAAAAAIABAAAAAG5vdGVzL2phdmEubWRQSwECFAAUAAAACABMBOxcViQN2g8AAAANAAAACwAAAAAAAAAAAAAAgAFGAAAAc3JjL21haW4ucHlQSwUGAAAAAAIAAgB0AAAAfgAAAAAA'
const evilZipBase64 = 'UEsDBBQAAAAIAEwE7FxSMfuNBgAAAAQAAAAMAAAALi4vZXNjYXBlLm1kSy3LzAEAUEsBAhQAFAAAAAgATATsXFIx+40GAAAABAAAAAwAAAAAAAAAAAAAAIABAAAAAC4uL2VzY2FwZS5tZFBLBQYAAAAAAQABADoAAAAwAAAAAAA='

async function openDemoSourcePanel(page: any) {
  await openDemo(page)
  await page.getByTestId('nav-create').click()
  await page.locator('.stepper button').nth(1).click()
  await expect(page.locator('.source-import-panel')).toBeVisible()
}

test('normal ZIP preview import keeps internal paths', async ({ page }) => {
  await openDemoSourcePanel(page)
  await page.getByTestId('file-input').setInputFiles({ name: 'study_bundle.zip', mimeType: 'application/zip', buffer: Buffer.from(normalZipBase64, 'base64') })
  await page.locator('.source-import-panel button').first().click()
  await expect(page.getByRole('dialog', { name: 'ZIP preview' })).toBeVisible()
  await expect(page.getByText('notes/java.md')).toBeVisible()
  await page.locator('label.archive-item', { hasText: 'src/main.py' }).locator('input').uncheck()
  await page.locator('.archive-preview-card .primary').click()
  await expect(page.locator('.source-list')).toContainText('study_bundle.zip / notes/java.md')
})

test('malicious ZIP traversal is rejected before source import', async ({ page }) => {
  await openDemoSourcePanel(page)
  await page.getByTestId('file-input').setInputFiles({ name: 'evil.zip', mimeType: 'application/zip', buffer: Buffer.from(evilZipBase64, 'base64') })
  await page.locator('.source-import-panel button').first().click()
  await expect(page.locator('.source-import-panel [role="status"]')).toBeVisible()
  await expect(page.locator('.source-list')).not.toContainText('escape.md')
})
