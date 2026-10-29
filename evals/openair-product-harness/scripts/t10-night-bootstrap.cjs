const apiBaseUrl = process.env.OPENAIR_PRODUCT_HARNESS_API_BASE_URL;
const email = process.env.OPENAIR_PRODUCT_HARNESS_DEV_EMAIL;
const username = process.env.OPENAIR_PRODUCT_HARNESS_DEV_USERNAME;
const password = process.env.OPENAIR_PRODUCT_HARNESS_DEV_PASSWORD;
const adminEmail = process.env.OPENAIR_PRODUCT_HARNESS_ADMIN_EMAIL || "admin@openair.dev";
const adminPassword = process.env.OPENAIR_PRODUCT_HARNESS_ADMIN_PASSWORD || "admin123456";

if (!apiBaseUrl || !email || !username || !password) {
  console.error(JSON.stringify({ stage: "preflight", error: "required_environment_missing" }));
  process.exit(2);
}

async function activateIfNeeded(userId) {
  const adminLogin = await fetch(`${apiBaseUrl}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: adminEmail, password: adminPassword }),
  });
  if (!adminLogin.ok) {
    throw new Error(`admin_login_http_${adminLogin.status}`);
  }
  const adminBody = await adminLogin.json();
  const token = adminBody.access_token;
  if (typeof token !== "string") throw new Error("admin_token_missing");

  let targetId = userId;
  if (!targetId) {
    const listed = await fetch(
      `${apiBaseUrl}/api/admin/accounts?query=${encodeURIComponent(email)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!listed.ok) throw new Error(`admin_list_http_${listed.status}`);
    const rows = await listed.json();
    const match = Array.isArray(rows) ? rows.find((row) => row.email === email) : null;
    targetId = match?.userId;
  }
  if (!targetId) throw new Error("pending_user_id_missing");

  const activated = await fetch(`${apiBaseUrl}/api/admin/accounts/${encodeURIComponent(targetId)}/active`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ isActive: true, reason: "t10 night bootstrap" }),
  });
  if (!activated.ok) throw new Error(`activate_http_${activated.status}`);
}

async function loginUser() {
  return fetch(`${apiBaseUrl}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

async function main() {
  const captcha = await fetch(`${apiBaseUrl}/api/auth/captcha`);
  if (captcha.ok) {
    const status = await captcha.json().catch(() => ({}));
    if (status.enabled) {
      throw new Error("captcha_enabled_cannot_register_headless");
    }
  }

  const register = await fetch(`${apiBaseUrl}/api/auth/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      username,
      display_name: "T10 Night Validation",
    }),
  });
  if (!register.ok && register.status !== 409) {
    throw new Error(`register_http_${register.status}`);
  }
  const registerBody = register.ok ? await register.json().catch(() => ({})) : {};
  const registeredUserId = registerBody.user?.userId || null;

  let login = await loginUser();
  if (login.status === 403) {
    await activateIfNeeded(registeredUserId);
    login = await loginUser();
  }
  const body = await login.json().catch(() => ({}));
  console.log(JSON.stringify({
    registerStatus: register.status,
    loginStatus: login.status,
    tokenPresent: typeof body.access_token === "string" && body.access_token.length > 20,
    userPresent: Boolean(body.user?.userId),
    workspacePresent: Boolean(body.user?.defaultWorkspaceId),
  }));
  if (!login.ok || typeof body.access_token !== "string") process.exit(3);

  const sessionId = process.env.OPENAIR_T10_SESSION_ID;
  if (sessionId) {
    const session = await fetch(
      `${apiBaseUrl}/api/research-start/sessions/${encodeURIComponent(sessionId)}`,
      { headers: { Authorization: `Bearer ${body.access_token}` } },
    );
    const snapshot = await session.json().catch(() => ({}));
    console.log(JSON.stringify({
      sessionHttpStatus: session.status,
      sessionStatus: snapshot.status ?? null,
      currentPhase: snapshot.currentPhase ?? null,
      resultPresent: Boolean(snapshot.result),
      userMessagePresent: typeof snapshot.userMessage === "string" && snapshot.userMessage.length > 0,
    }));
    if (!session.ok) process.exit(5);
  }
}

main().catch((error) => {
  const safeMessage = String(error?.message || "bootstrap_failed").replace(/https?:\/\/\S+/g, "<url>");
  console.error(JSON.stringify({ stage: "bootstrap", error: safeMessage }));
  process.exit(4);
});
