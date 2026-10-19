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
  constructor(tagName = "#text", text = "") {
    this.tagName = tagName;
    this.children = [];
    this.className = "";
    this.disabled = false;
    this.hidden = false;
    this.value = "";
    this.listeners = {};
    this._text = text;
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  append(...nodes) {
    for (const node of nodes) {
      this.children.push(
        typeof node === "string" ? new MockNode("#text", node) : node
      );
    }
  }

  appendChild(node) {
    this.children.push(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }

  focus() {}
}

function createHarness(responses) {
  const ids = [
    "investigation-form",
    "order-id",
    "investigate-button",
    "message",
    "results",
    "history-list",
    "history-message",
    "refresh-history",
  ];

  const elements = Object.fromEntries(
    ids.map((id) => [id, new MockNode(id)])
  );
  const requests = [];

  const document = {
    getElementById(id) {
      if (!elements[id]) elements[id] = new MockNode(id);
      return elements[id];
    },
    createElement(tagName) {
      return new MockNode(tagName);
    },
    createTextNode(text) {
      return new MockNode("#text", String(text));
    },
  };

  const context = vm.createContext({
    document,
    fetch: async (...args) => {
      requests.push(args);
      if (!responses.length) {
        throw new Error("No mock response configured");
      }
      const next = responses.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    console,
  });

  vm.runInContext(scriptMatch[1], context, {
    filename: "frontend/index.html",
  });

  return { elements, requests };
}

function response(body, { ok = true, status = 200, jsonError = false } = {}) {
  return {
    ok,
    status,
    async json() {
      if (jsonError) throw new Error("Invalid JSON");
      return body;
    },
  };
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("empty history displays a helpful empty state", async () => {
  const { elements, requests } = createHarness([
    response({ investigations: [] }),
  ]);

  await tick();

  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "/api/investigations?limit=50");
  assert.match(elements["history-message"].textContent, /No saved investigations yet/);
  assert.equal(elements["history-list"].children.length, 0);
  assert.equal(elements["refresh-history"].disabled, false);
});

test("API failure displays an error and refresh can recover", async () => {
  const { elements, requests } = createHarness([
    response({}, { ok: false, status: 503 }),
    response({
      investigations: [{
        order_id: "ORDER-RECOVERED",
        observed_status: "COMPLETED",
        classification: "sandbox_order",
        investigated_at: "2026-10-19T08:00:00Z",
      }],
    }),
  ]);

  await tick();

  assert.match(elements["history-message"].textContent, /Could not load investigation history/);
  assert.equal(elements["refresh-history"].disabled, false);

  await elements["refresh-history"].listeners.click();

  assert.equal(requests.length, 2);
  assert.equal(elements["history-list"].children.length, 1);
  assert.match(elements["history-list"].textContent, /ORDER-RECOVERED/);
  assert.match(elements["history-message"].textContent, /Showing 1 saved investigation/);
});

test("API-provided values are displayed as text, not inserted as HTML", async () => {
  const orderId = '<img src=x onerror="alert(1)">';
  const { elements } = createHarness([
    response({
      investigations: [{
        order_id: orderId,
        observed_status: "<script>unexpected()</script>",
        classification: "needs_review",
        investigated_at: "not-a-date",
      }],
    }),
  ]);

  await tick();

  const list = elements["history-list"];
  assert.equal(list.children.length, 1);

  const article = list.children[0];
  assert.equal(article.children[0].tagName, "h3");
  assert.equal(article.children[0].textContent, orderId);
  assert.match(article.textContent, /<script>unexpected\(\)<\/script>/);
  assert.equal(article.children.length, 4);
  assert.match(article.textContent, /needs review/);
  assert.match(article.textContent, /Not available/);
});

test("malformed JSON is handled and the refresh button is re-enabled", async () => {
  const { elements } = createHarness([
    response(null, { jsonError: true }),
  ]);

  await tick();

  assert.match(elements["history-message"].textContent, /Could not load investigation history/);
  assert.equal(elements["history-list"].children.length, 0);
  assert.equal(elements["refresh-history"].disabled, false);
});

test("multiple investigations are rendered and counted", async () => {
  const { elements } = createHarness([
    response({
      investigations: [
        {
          order_id: "ORDER-FIRST",
          observed_status: "COMPLETED",
          classification: "sandbox_order",
          investigated_at: "2026-10-19T08:00:00Z",
        },
        {
          order_id: "ORDER-SECOND",
          observed_status: "PENDING",
          classification: "needs_review",
          investigated_at: "2026-10-19T09:00:00Z",
        },
      ],
    }),
  ]);

  await tick();

  const list = elements["history-list"];
  assert.equal(list.children.length, 2);
  assert.match(list.children[0].textContent, /ORDER-FIRST/);
  assert.match(list.children[1].textContent, /ORDER-SECOND/);
  assert.match(elements["history-message"].textContent, /Showing 2 saved investigation\(s\)\./);
  assert.equal(elements["refresh-history"].disabled, false);
});

test("invalid response structure displays an error without rendering records", async () => {
  const { elements } = createHarness([
    response({ unexpected: [] }),
  ]);

  await tick();

  assert.match(elements["history-message"].textContent, /Could not load investigation history/);
  assert.equal(elements["history-list"].children.length, 0);
  assert.equal(elements["refresh-history"].disabled, false);
});
