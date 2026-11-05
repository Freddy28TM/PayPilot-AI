"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const html = fs.readFileSync(
  path.join(__dirname, "../frontend/index.html"),
  "utf8"
);
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(scriptMatch, "frontend must contain its inline script");

class MockNode {
  constructor(id) {
    this.id = id;
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.listeners = {};
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() {}
  reset() {}
  replaceChildren() {}
}

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; },
  };
}

function createHarness(fetchHandler) {
  const elements = {};
  const document = {
    cookie: "",
    getElementById(id) {
      if (!elements[id]) elements[id] = new MockNode(id);
      return elements[id];
    },
    createElement(id) { return new MockNode(id); },
    createTextNode(text) { return new MockNode(String(text)); },
  };
  const requests = [];
  const context = vm.createContext({
    document,
    fetch: async (...args) => {
      requests.push(args);
      return fetchHandler(args, document);
    },
    console,
  });
  vm.runInContext(scriptMatch[1], context, { filename: "frontend/index.html" });
  return { context, document, elements, requests };
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("unauthenticated visitors see the login screen and no business data is loaded", async () => {
  const harness = createHarness(async ([url]) => {
    if (url === "/api/auth/me") return response({ detail: "Authentication is required." }, 401);
    throw new Error(`Unexpected request: ${url}`);
  });
  await tick();
  assert.equal(harness.elements["auth-shell"].hidden, false);
  assert.equal(harness.elements["app-main"].hidden, true);
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0][0], "/api/auth/me");
});

test("login submits credentials and opens the dashboard after success", async () => {
  const harness = createHarness(async ([url, options], document) => {
    if (url === "/api/auth/me") return response({}, 401);
    if (url === "/api/auth/login") {
      assert.equal(options.method, "POST");
      assert.deepEqual(JSON.parse(options.body), {
        username: "owner.test",
        password: "Correct-Horse-Battery-2026!",
      });
      document.cookie = "paypilot_csrf=csrf-value";
      return response({ user: { id: 1, username: "owner.test", role: "owner" } });
    }
    if (url === "/api/investigations?limit=50") return response({ investigations: [] });
    throw new Error(`Unexpected request: ${url}`);
  });
  await tick();
  harness.document.getElementById("login-username").value = "owner.test";
  harness.document.getElementById("login-password").value = "Correct-Horse-Battery-2026!";
  await harness.elements["login-form"].listeners.submit({ preventDefault() {} });
  await tick();
  assert.equal(harness.elements["auth-shell"].hidden, true);
  assert.equal(harness.elements["app-main"].hidden, false);
  assert.equal(harness.elements["account-name"].textContent, "owner.test · owner");
});

test("protected state-changing requests attach the CSRF cookie as a header", async () => {
  let receivedOptions;
  const harness = createHarness(async ([url, options]) => {
    if (url === "/api/auth/me") return response({ user: { id: 1, username: "owner.test", role: "owner" } });
    if (url === "/api/investigations?limit=50") return response({ investigations: [] });
    if (url === "/api/reconciliation") {
      receivedOptions = options;
      return response({ accepted: true });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  harness.document.cookie = "paypilot_csrf=csrf-from-cookie";
  await tick();
  await vm.runInContext(
    'apiFetch("/api/reconciliation", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })',
    harness.context
  );
  assert.equal(receivedOptions.headers["X-CSRF-Token"], "csrf-from-cookie");
  assert.equal(receivedOptions.credentials, "same-origin");
});

test("logout sends CSRF header and returns the UI to the login screen", async () => {
  let logoutOptions;
  const harness = createHarness(async ([url, options]) => {
    if (url === "/api/auth/me") return response({ user: { id: 1, username: "owner.test", role: "owner" } });
    if (url === "/api/investigations?limit=50") return response({ investigations: [] });
    if (url === "/api/auth/logout") {
      logoutOptions = options;
      return response({ status: "logged_out" });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  harness.document.cookie = "paypilot_csrf=logout-csrf";
  await tick();
  await harness.elements["logout-button"].listeners.click();
  assert.equal(logoutOptions.method, "POST");
  assert.equal(logoutOptions.headers["X-CSRF-Token"], "logout-csrf");
  assert.equal(harness.elements["auth-shell"].hidden, false);
  assert.equal(harness.elements["app-main"].hidden, true);
});
