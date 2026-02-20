import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { connect, waitForPageLoad } from "/home/hacker/.codex/skills/dev-browser/src/client.ts";

type Assertion = {
  name: string;
  passed: boolean;
  detail?: unknown;
};

type Summary = {
  process: "Q2";
  run_id: string;
  started_at: string;
  base_url: string;
  assertions: Assertion[];
  screenshots: string[];
  success: boolean;
  error?: string;
};

function argValue(name: string, fallback: string): string {
  const idx = process.argv.indexOf(name);
  if (idx >= 0 && idx + 1 < process.argv.length) {
    return process.argv[idx + 1] ?? fallback;
  }
  return fallback;
}

function assertPush(summary: Summary, name: string, passed: boolean, detail?: unknown): void {
  summary.assertions.push({ name, passed, detail });
  if (!passed) {
    throw new Error(`Assertion failed: ${name} :: ${JSON.stringify(detail ?? null)}`);
  }
}

async function waitForVisible(
  page: import("playwright").Page,
  selectors: string[],
  timeoutMs = 60000
): Promise<boolean> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    for (const selector of selectors) {
      const count = await page.locator(selector).count();
      if (count > 0) {
        const visible = await page.locator(selector).first().isVisible().catch(() => false);
        if (visible) {
          return true;
        }
      }
    }
    await page.waitForTimeout(250);
  }
  return false;
}

async function waitForPromptInput(page: import("playwright").Page, timeoutMs = 90000): Promise<boolean> {
  const selectors = [
    'textarea[placeholder="What would you like to know?"]',
    'input[placeholder="What would you like to know?"]',
  ];
  return waitForVisible(page, selectors, timeoutMs);
}

async function navigateToPage(
  page: import("playwright").Page,
  baseUrl: string,
  route: string,
  summary: Summary,
  prefix: string
): Promise<void> {
  await page.goto(`${baseUrl}${route}`);
  await waitForPageLoad(page, { timeout: 45000 });
  const inputReady = await waitForPromptInput(page, 120000);
  assertPush(summary, `${prefix}_input_ready`, inputReady, { route, url: page.url() });
  assertPush(summary, `${prefix}_route_ok`, page.url().includes(route), { route, url: page.url() });
}

async function ensureModelSelected(
  page: import("playwright").Page,
  summary: Summary,
  modelHint: string
): Promise<void> {
  if (!modelHint.trim()) {
    return;
  }

  const trigger = page.locator(".header-model-section .searchable-dropdown-trigger").first();
  assertPush(summary, "o2_model_trigger_present", (await trigger.count()) > 0);

  const currentText = ((await trigger.textContent()) ?? "").toLowerCase();
  if (currentText.includes(modelHint.toLowerCase())) {
    assertPush(summary, "o2_model_selected", true, { model_hint: modelHint, selected: currentText });
    return;
  }

  await trigger.click();
  const input = page.locator(".header-model-section .searchable-dropdown-input").first();
  assertPush(summary, "o2_model_search_present", (await input.count()) > 0);
  await input.fill(modelHint);
  await input.press("Enter");
  await page.waitForTimeout(2500);

  const updatedText = ((await trigger.textContent()) ?? "").toLowerCase();
  assertPush(summary, "o2_model_selected", updatedText.includes(modelHint.toLowerCase()), {
    model_hint: modelHint,
    selected: updatedText,
  });
}

async function startFreshConversation(
  page: import("playwright").Page,
  summary: Summary,
  prefix: string
): Promise<void> {
  const historyValue = page.locator(".header-history-section .searchable-dropdown-value").first();
  assertPush(summary, `${prefix}_history_value_present`, (await historyValue.count()) > 0);

  const button = page.locator('button[title="Start New Chat"]').first();
  assertPush(summary, `${prefix}_new_chat_button_present`, (await button.count()) > 0);
  await button.click();
  await page.waitForTimeout(1200);

  let historyLabel = ((await historyValue.textContent()) ?? "").trim();
  if (!/start new chat/i.test(historyLabel)) {
    const historyTrigger = page.locator(".header-history-section .searchable-dropdown-trigger").first();
    assertPush(summary, `${prefix}_history_trigger_present`, (await historyTrigger.count()) > 0);
    await historyTrigger.click();
    const startNewOption = page.locator(".searchable-dropdown-option:has-text('Start New Chat')").first();
    assertPush(summary, `${prefix}_history_start_new_option_present`, (await startNewOption.count()) > 0);
    await startNewOption.click();
    await page.waitForTimeout(1200);
    historyLabel = ((await historyValue.textContent()) ?? "").trim();
  }
  assertPush(summary, `${prefix}_history_start_new_selected`, /start new chat/i.test(historyLabel), {
    history_label: historyLabel,
  });

  const inputReady = await waitForPromptInput(page, 60000);
  assertPush(summary, `${prefix}_new_chat_ready`, inputReady);
}

async function ensureLoginIfNeeded(
  page: import("playwright").Page,
  email: string,
  password: string,
  summary: Summary
): Promise<void> {
  const maxAttempts = 4;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const passwordInput = page.locator('input[type="password"]');
    const needsLogin = page.url().includes("/login") || (await passwordInput.count()) > 0;
    if (!needsLogin) {
      return;
    }

    const emailInput = page.locator('input[type="email"], input[name="email"]');
    assertPush(summary, `o2_login_email_input_present_attempt_${attempt}`, (await emailInput.count()) > 0);

    await emailInput.first().fill(email);
    await passwordInput.first().fill(password);

    const submitCandidates = [
      page.getByRole("button", { name: /sign in|login|log in/i }),
      page.locator('button[type="submit"]'),
    ];

    let clicked = false;
    for (const candidate of submitCandidates) {
      const count = await candidate.count();
      if (count > 0) {
        await candidate.first().click();
        clicked = true;
        break;
      }
    }
    assertPush(summary, `o2_login_submit_clicked_attempt_${attempt}`, clicked);

    await waitForPageLoad(page, { timeout: 30000 });
    await page.waitForTimeout(2500 + attempt * 1000);
    if (!page.url().includes("/login")) {
      assertPush(summary, "o2_login_redirected", true, { url: page.url(), attempt });
      return;
    }
  }

  assertPush(summary, "o2_login_redirected", false, { url: page.url() });
}

async function sendPrompt(
  page: import("playwright").Page,
  prompt: string,
  summary: Summary,
  prefix: string
): Promise<{ elapsedMs: number; pendingVisible: boolean }> {
  const inputReady = await waitForPromptInput(page, 90000);
  assertPush(summary, `${prefix}_input_present`, inputReady);

  const input = page.locator(
    'textarea[placeholder="What would you like to know?"], input[placeholder="What would you like to know?"]'
  );
  assertPush(summary, `${prefix}_input_locator_present`, (await input.count()) > 0);
  await input.first().fill(prompt);

  const pendingLocator = page.locator("text=PENDING");
  const approvalCopyLocator = page.locator("text=Approval required to run mutating tool");
  const pendingBaseline = await pendingLocator.count();
  const approvalCopyBaseline = await approvalCopyLocator.count();

  const sendCandidates = [
    page.getByRole("button", { name: /send message/i }),
    page.locator('button[aria-label="Send message"]'),
  ];

  let sent = false;
  for (const candidate of sendCandidates) {
    const count = await candidate.count();
    if (count > 0) {
      await candidate.first().click();
      sent = true;
      break;
    }
  }
  assertPush(summary, `${prefix}_send_clicked`, sent);

  const start = Date.now();
  let pendingVisible = false;
  const deadline = Date.now() + 65000;
  while (Date.now() < deadline) {
    const pendingCount = await pendingLocator.count();
    const approvalCopyCount = await approvalCopyLocator.count();
    if (pendingCount > pendingBaseline || approvalCopyCount > approvalCopyBaseline) {
      pendingVisible = true;
      break;
    }
    await page.waitForTimeout(250);
  }
  const elapsed = Date.now() - start;
  assertPush(summary, `${prefix}_pending_visible`, pendingVisible, {
    elapsed_ms: elapsed,
    pending_visible: pendingVisible,
  });
  if (pendingVisible) {
    assertPush(summary, `${prefix}_approval_latency_under_60s`, elapsed <= 60000, { elapsed_ms: elapsed });
  }

  if (pendingVisible) {
    const copyVisible = await waitForVisible(page, ["text=Approval required to run mutating tool"], 10000);
    assertPush(summary, `${prefix}_approval_copy_visible`, copyVisible);
  }

  return { elapsedMs: elapsed, pendingVisible };
}

async function clickStrictApprovalButton(
  page: import("playwright").Page,
  buttonName: "Approve" | "Reject",
  summary: Summary,
  prefix: string
): Promise<void> {
  const buttons = page.getByRole("button", { name: new RegExp(`^${buttonName}$`, "i") });
  const deadline = Date.now() + 20000;
  let visibleIndexes: number[] = [];

  while (Date.now() < deadline) {
    const count = await buttons.count();
    visibleIndexes = [];
    for (let idx = 0; idx < count; idx += 1) {
      const visible = await buttons.nth(idx).isVisible().catch(() => false);
      if (visible) {
        visibleIndexes.push(idx);
      }
    }
    if (visibleIndexes.length > 0) {
      break;
    }
    await page.waitForTimeout(250);
  }

  assertPush(summary, `${prefix}_${buttonName.toLowerCase()}_button_visible`, visibleIndexes.length > 0, {
    visible_count: visibleIndexes.length,
  });
  assertPush(summary, `${prefix}_${buttonName.toLowerCase()}_button_unique`, visibleIndexes.length === 1, {
    visible_count: visibleIndexes.length,
  });

  const targetIndex = visibleIndexes[visibleIndexes.length - 1] ?? 0;
  await buttons.nth(targetIndex).click();
  assertPush(summary, `${prefix}_${buttonName.toLowerCase()}_clicked`, true, { index: targetIndex });
}

async function resolveRejectSemantics(
  page: import("playwright").Page,
  summary: Summary,
  prefix: string
): Promise<void> {
  await clickStrictApprovalButton(page, "Reject", summary, prefix);
  const rejectOutcome = await waitForVisible(
    page,
    ["text=REJECTED", "text=did not run mutating tool", "text=Task creation was rejected"],
    45000
  );
  assertPush(summary, `${prefix}_reject_semantics`, rejectOutcome);
}

async function resolveApproveSemantics(
  page: import("playwright").Page,
  summary: Summary,
  prefix: string
): Promise<void> {
  await clickStrictApprovalButton(page, "Approve", summary, prefix);
  const approveOutcome = await waitForVisible(
    page,
    ["text=APPROVED", "text=newly created task", "text=successfully saved", "text=Task ID"],
    45000
  );
  assertPush(summary, `${prefix}_approve_semantics`, approveOutcome);
}

async function assertPendingToolHint(
  page: import("playwright").Page,
  summary: Summary,
  prefix: string,
  expectedTool: string | string[]
): Promise<void> {
  const expectedList = (Array.isArray(expectedTool) ? expectedTool : [expectedTool])
    .map((item) => item.toLowerCase())
    .filter((item) => item.length > 0);
  const deadline = Date.now() + 20000;
  let matched = false;
  while (Date.now() < deadline) {
    const bodyText = (await page.locator("body").innerText().catch(() => "")).toLowerCase();
    if (
      expectedList.some((expected) => bodyText.includes(`tool '${expected}'`) || bodyText.includes(expected))
    ) {
      matched = true;
      break;
    }
    await page.waitForTimeout(250);
  }
  assertPush(summary, `${prefix}_pending_tool_${expectedList.join("_or_")}`, matched, {
    expected_tool: expectedTool,
  });
}

async function main(): Promise<void> {
  const baseUrl = argValue("--base-url", "http://localhost:5173").replace(/\/$/, "");
  const email = argValue("--email", "cccc@gmail.com");
  const password = argValue("--password", "10012002");
  const modelHint = argValue("--model-hint", "qwen3:8b");
  const outputDir = argValue("--output-dir", "/home/hacker/BrainDriveDev/BrainDrive/tmp/live-process-q2-browser");

  const runId = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  const summary: Summary = {
    process: "Q2",
    run_id: runId,
    started_at: new Date().toISOString(),
    base_url: baseUrl,
    assertions: [],
    screenshots: [],
    success: false,
  };

  const artifactDir = path.resolve(outputDir);
  await mkdir(artifactDir, { recursive: true });

  const client = await connect();
  const page = await client.page("process-o2", { viewport: { width: 1440, height: 1000 } });

  try {
    await page.goto(`${baseUrl}/dashboard`);
    await waitForPageLoad(page, { timeout: 30000 });
    await page.waitForTimeout(2000);

    await ensureLoginIfNeeded(page, email, password, summary);
    assertPush(summary, "o2_session_ready", true, { url: page.url() });

    await navigateToPage(page, baseUrl, "/pages/finances", summary, "q2_finances_preflight");
    await ensureModelSelected(page, summary, modelHint);
    await startFreshConversation(page, summary, "q2_finances_create_task_reject");

    const rejectPrompt = `[LIBRARY SCOPE - Life / finances] Create a task Q2 task reject ${runId} due 2026-03-22 for Dave J.`;
    await sendPrompt(page, rejectPrompt, summary, "q2_finances_create_task_reject");
    await assertPendingToolHint(page, summary, "q2_finances_create_task_reject", "create_task");

    await resolveRejectSemantics(page, summary, "q2_finances_create_task");

    await startFreshConversation(page, summary, "q2_finances_create_task_approve");
    const approvePrompt = `[LIBRARY SCOPE - Life / finances] Create a task Q2 task approve ${runId} due 2026-03-23 for Dave J.`;
    await sendPrompt(page, approvePrompt, summary, "q2_finances_create_task_approve");
    await assertPendingToolHint(page, summary, "q2_finances_create_task_approve", "create_task");

    await resolveApproveSemantics(page, summary, "q2_finances_create_task");

    await navigateToPage(page, baseUrl, "/pages/ai-chat", summary, "q2_ai_chat_preflight");
    await ensureModelSelected(page, summary, modelHint);
    await startFreshConversation(page, summary, "q2_ai_chat_create_markdown_reject");

    const aiRejectPrompt = `[LIBRARY SCOPE - Life / finances] Create markdown file q2-create-note-reject-${runId}.md in life/finances with content \"Q2 create markdown reject ${runId}\".`;
    await sendPrompt(page, aiRejectPrompt, summary, "q2_ai_chat_create_markdown_reject");
    await assertPendingToolHint(page, summary, "q2_ai_chat_create_markdown_reject", "create_markdown");
    await resolveRejectSemantics(page, summary, "q2_ai_chat_create_markdown");

    await startFreshConversation(page, summary, "q2_ai_chat_create_markdown_approve");
    const aiApprovePrompt = `[LIBRARY SCOPE - Life / finances] Create markdown file q2-create-note-approve-${runId}.md in life/finances with content \"Q2 create markdown approve ${runId}\".`;
    await sendPrompt(page, aiApprovePrompt, summary, "q2_ai_chat_create_markdown_approve");
    await assertPendingToolHint(page, summary, "q2_ai_chat_create_markdown_approve", "create_markdown");
    await resolveApproveSemantics(page, summary, "q2_ai_chat_create_markdown");

    const screenshotPath = path.resolve(artifactDir, `q2-browser-${runId}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    summary.screenshots.push(screenshotPath);

    summary.success = summary.assertions.every((a) => a.passed);
  } catch (error) {
    summary.success = false;
    summary.error = error instanceof Error ? error.message : String(error);
    const errorShot = path.resolve(artifactDir, `o2-browser-${runId}-error.png`);
    try {
      await page.screenshot({ path: errorShot, fullPage: true });
      summary.screenshots.push(errorShot);
    } catch {
      // ignore
    }
  } finally {
    const outPath = path.resolve(artifactDir, `q2-browser-${runId}.json`);
    await writeFile(outPath, JSON.stringify(summary, null, 2) + "\n", "utf-8");
    console.log(JSON.stringify(summary, null, 2));
    await client.disconnect();
  }

  if (!summary.success) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
