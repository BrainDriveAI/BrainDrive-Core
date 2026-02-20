import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { connect, waitForPageLoad } from "/home/hacker/.codex/skills/dev-browser/src/client.ts";

type Assertion = {
  name: string;
  passed: boolean;
  detail?: unknown;
};

type Summary = {
  process: "S2_PROFILE";
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
      const locator = page.locator(selector);
      const count = await locator.count();
      if (count > 0) {
        const visible = await locator.first().isVisible().catch(() => false);
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

async function navigateToAiChat(
  page: import("playwright").Page,
  baseUrl: string,
  summary: Summary
): Promise<void> {
  await page.goto(`${baseUrl}/pages/ai-chat`);
  await waitForPageLoad(page, { timeout: 45000 });
  const inputReady = await waitForPromptInput(page, 120000);
  assertPush(summary, "s2_profile_input_ready", inputReady, { url: page.url() });
  assertPush(summary, "s2_profile_route_ok", page.url().includes("/pages/ai-chat"), { url: page.url() });
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
  assertPush(summary, "s2_profile_model_trigger_present", (await trigger.count()) > 0);

  const currentText = ((await trigger.textContent()) ?? "").toLowerCase();
  if (currentText.includes(modelHint.toLowerCase())) {
    assertPush(summary, "s2_profile_model_selected", true, { model_hint: modelHint, selected: currentText });
    return;
  }

  await trigger.click();
  const input = page.locator(".header-model-section .searchable-dropdown-input").first();
  assertPush(summary, "s2_profile_model_search_present", (await input.count()) > 0);
  await input.fill(modelHint);
  await input.press("Enter");
  await page.waitForTimeout(2500);

  const updatedText = ((await trigger.textContent()) ?? "").toLowerCase();
  assertPush(summary, "s2_profile_model_selected", updatedText.includes(modelHint.toLowerCase()), {
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
    assertPush(
      summary,
      `s2_profile_login_email_input_present_attempt_${attempt}`,
      (await emailInput.count()) > 0
    );

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
    assertPush(summary, `s2_profile_login_submit_clicked_attempt_${attempt}`, clicked);

    await waitForPageLoad(page, { timeout: 30000 });
    await page.waitForTimeout(2500 + attempt * 1000);
    if (!page.url().includes("/login")) {
      assertPush(summary, "s2_profile_login_redirected", true, { url: page.url(), attempt });
      return;
    }
  }

  assertPush(summary, "s2_profile_login_redirected", false, { url: page.url() });
}

async function sendPrompt(
  page: import("playwright").Page,
  prompt: string,
  summary: Summary,
  prefix: string,
  requirePending = true
): Promise<boolean> {
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
  if (requirePending) {
    assertPush(summary, `${prefix}_pending_visible`, pendingVisible, { elapsed_ms: elapsed });
    if (pendingVisible) {
      assertPush(summary, `${prefix}_approval_latency_under_60s`, elapsed <= 60000, { elapsed_ms: elapsed });
      const copyVisible = await waitForVisible(page, ["text=Approval required to run mutating tool"], 10000);
      assertPush(summary, `${prefix}_approval_copy_visible`, copyVisible);
    }
  } else {
    summary.assertions.push({
      name: `${prefix}_pending_visible_optional`,
      passed: true,
      detail: { elapsed_ms: elapsed, pending_visible: pendingVisible },
    });
  }
  return pendingVisible;
}

async function inspectPendingProfileToolHint(
  page: import("playwright").Page,
  summary: Summary,
  prefix: string
): Promise<{ toolMatched: boolean; profilePathMatched: boolean }> {
  const deadline = Date.now() + 20000;
  let toolMatched = false;
  let profilePathMatched = false;
  while (Date.now() < deadline) {
    const bodyText = (await page.locator("body").innerText().catch(() => "")).toLowerCase();
    if (bodyText.includes("write_markdown") || bodyText.includes("create_markdown")) {
      toolMatched = true;
    }
    if (bodyText.includes("me/profile.md")) {
      profilePathMatched = true;
    }
    if (toolMatched && profilePathMatched) {
      break;
    }
    await page.waitForTimeout(250);
  }
  summary.assertions.push({
    name: `${prefix}_pending_profile_hint_observed`,
    passed: true,
    detail: { tool_matched: toolMatched, profile_path_matched: profilePathMatched },
  });
  return { toolMatched, profilePathMatched };
}

async function clickStrictApprovalButton(
  page: import("playwright").Page,
  buttonName: "Approve" | "Reject",
  summary: Summary,
  prefix: string
): Promise<void> {
  const buttonLocator = page.getByRole("button", { name: buttonName });
  const count = await buttonLocator.count();
  assertPush(summary, `${prefix}_${buttonName.toLowerCase()}_button_present`, count > 0, { count });
  const visibleCount = await buttonLocator.evaluateAll((nodes) =>
    nodes.filter((node) => {
      const style = window.getComputedStyle(node as HTMLElement);
      const hidden = style.display === "none" || style.visibility === "hidden";
      const rect = (node as HTMLElement).getBoundingClientRect();
      return !hidden && rect.width > 0 && rect.height > 0;
    }).length
  );
  assertPush(summary, `${prefix}_${buttonName.toLowerCase()}_button_visible_unique`, visibleCount === 1, {
    visible_count: visibleCount,
  });
  await buttonLocator.first().click();
}

async function resolveRejectSemantics(
  page: import("playwright").Page,
  summary: Summary,
  prefix: string
): Promise<void> {
  await clickStrictApprovalButton(page, "Reject", summary, prefix);
  const rejectOutcome = await waitForVisible(
    page,
    ["text=REJECTED", "text=did not run mutating tool", "text=cancelled"],
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
    ["text=APPROVED", "text=successfully saved", "text=updated", "text=written", "text=saved"],
    45000
  );
  assertPush(summary, `${prefix}_approve_semantics`, approveOutcome);
}

async function main(): Promise<void> {
  const baseUrl = argValue("--base-url", "http://localhost:5173").replace(/\/$/, "");
  const email = argValue("--email", "cccc@gmail.com");
  const password = argValue("--password", "10012002");
  const modelHint = argValue("--model-hint", "qwen3:8b");
  const outputDir = argValue(
    "--output-dir",
    "/home/hacker/BrainDriveDev/BrainDrive/tmp/live-process-s2-profile-browser"
  );

  const runId = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  const summary: Summary = {
    process: "S2_PROFILE",
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
  const page = await client.page("process-s2-profile", { viewport: { width: 1440, height: 1000 } });

  try {
    await page.goto(`${baseUrl}/dashboard`);
    await waitForPageLoad(page, { timeout: 30000 });
    await page.waitForTimeout(2000);

    await ensureLoginIfNeeded(page, email, password, summary);
    assertPush(summary, "s2_profile_session_ready", true, { url: page.url() });

    await navigateToAiChat(page, baseUrl, summary);
    await ensureModelSelected(page, summary, modelHint);

    await startFreshConversation(page, summary, "s2_profile_reject");
    const rejectPrompt = `Update my profile with: prefers concise weekly check-ins and Friday planning summaries (${runId}).`;
    let rejectPending = await sendPrompt(page, rejectPrompt, summary, "s2_profile_reject", false);
    if (!rejectPending) {
      const rejectFallbackPrompt =
        `[LIBRARY SCOPE - Life / finances] Use write_markdown on me/profile.md ` +
        `and append bullet "- prefers concise weekly check-ins (${runId}-fallback-reject)".`;
      rejectPending = await sendPrompt(page, rejectFallbackPrompt, summary, "s2_profile_reject_fallback", true);
    }
    assertPush(summary, "s2_profile_reject_pending_final", rejectPending);
    const rejectHints = await inspectPendingProfileToolHint(page, summary, "s2_profile_reject");
    assertPush(
      summary,
      "s2_profile_reject_profile_hint_seen",
      rejectHints.toolMatched || rejectHints.profilePathMatched,
      rejectHints
    );
    await resolveRejectSemantics(page, summary, "s2_profile_reject");

    await startFreshConversation(page, summary, "s2_profile_approve");
    const approvePrompt = `Update my profile with: prefers concise weekly check-ins and Friday planning summaries (${runId}-approve).`;
    let approvePending = await sendPrompt(page, approvePrompt, summary, "s2_profile_approve", false);
    if (!approvePending) {
      const approveFallbackPrompt =
        `[LIBRARY SCOPE - Life / finances] Use write_markdown on me/profile.md ` +
        `and append bullet "- prefers concise weekly check-ins (${runId}-fallback-approve)".`;
      approvePending = await sendPrompt(
        page,
        approveFallbackPrompt,
        summary,
        "s2_profile_approve_fallback",
        true
      );
    }
    assertPush(summary, "s2_profile_approve_pending_final", approvePending);
    await inspectPendingProfileToolHint(page, summary, "s2_profile_approve");
    await resolveApproveSemantics(page, summary, "s2_profile_approve");

    const screenshotPath = path.resolve(artifactDir, `s2-profile-browser-${runId}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    summary.screenshots.push(screenshotPath);

    summary.success = summary.assertions.every((a) => a.passed);
  } catch (error) {
    summary.success = false;
    summary.error = error instanceof Error ? error.message : String(error);
    const errorShot = path.resolve(artifactDir, `s2-profile-browser-${runId}-error.png`);
    try {
      await page.screenshot({ path: errorShot, fullPage: true });
      summary.screenshots.push(errorShot);
    } catch {
      // ignore screenshot failure on error path
    }
  } finally {
    const outPath = path.resolve(artifactDir, `s2-profile-browser-${runId}.json`);
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
