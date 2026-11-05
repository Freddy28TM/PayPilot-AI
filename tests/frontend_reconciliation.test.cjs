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
    this.style = {};
    this.disabled = false;
    this.hidden = false;
    this.value = "";
    this.files = [];
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

  setAttribute(name, value) {
    if (!this.attributes) {
      this.attributes = {};
    }
    this.attributes[name] = String(value);
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }

  focus() {}

  scrollIntoView(options) {
    this.scrollIntoViewOptions = options;
  }
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

function createHarness(responses) {
  const elements = {};
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
      if (!responses.length) throw new Error("No mock response configured");
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

async function startHarness(apiResponse) {
  const harness = createHarness([
    response({ investigations: [] }),
    apiResponse,
  ]);

  // Allow the initial investigation-history request to finish.
  await new Promise((resolve) => setImmediate(resolve));

  return harness;
}

function result(overrides = {}) {
  return {
    merchant_reference: "INV-1001",
    paypal_order_id: "ORDER-1",
    expected_amount: "25.00",
    expected_currency: "USD",
    observed_amount: "25.00",
    observed_currency: "USD",
    status: "matched",
    summary: "Amount and currency match the observed order.",
    settlement_verified: false,
    ...overrides,
  };
}

function validPayload(results = [result()]) {
  return {
    results,
    count: results.length,
    counts: { matched: results.length },
    settlement_verified: false,
  };
}

function attachFile(elements, { text = "example CSV", size } = {}) {
  elements["reconciliation-file"].files = [
    {
      size: size ?? Buffer.byteLength(text),
      async text() {
        return text;
      },
    },
  ];
}

async function submit(elements) {
  const handler = elements["reconciliation-form"].listeners.submit;
  assert.equal(typeof handler, "function", "submit handler must be registered");
  await handler({ preventDefault() {} });
}


test("analytics dashboard loads validated summary and daily trends", async () => {
  const summary = {
    total_runs: 2,
    total_rows: 5,
    recorded_rows: 5,
    record_count_consistent: true,
    status_counts: {
      matched: 3,
      amount_mismatch: 1,
      currency_mismatch: 0,
      needs_review: 1,
      order_not_found: 0,
      lookup_error: 0
    },
    matched_rows: 3,
    exception_rows: 2,
    match_rate_percent: 60,
    latest_run_at: "2026-10-20T10:00:00+00:00",
    settlement_verified: false,
    risk_signal_history_available: true
  };
  const trends = {
    count: 1,
    limit: 30,
    settlement_verified: false,
    days: [{
      day: "2026-10-20",
      total_runs: 2,
      total_rows: 5,
      matched_rows: 3,
      exception_rows: 2,
      needs_review_rows: 1,
      amount_mismatch_rows: 1,
      currency_mismatch_rows: 0,
      lookup_failure_rows: 0
    }]
  };

  const harness = createHarness([
    response({ investigations: [] }),
    response(summary),
    response(trends)
  ]);
  await new Promise((resolve) => setImmediate(resolve));

  const button = harness.elements["refresh-analytics"];
  await button.listeners.click();

  assert.equal(harness.requests[1][0], "/api/analytics/summary");
  assert.equal(harness.requests[2][0], "/api/analytics/trends?limit=30");
  assert.match(harness.elements["analytics-metrics"].textContent, /Saved runs2/);
  assert.match(harness.elements["analytics-metrics"].textContent, /Match rate60%/);
  assert.match(harness.elements["analytics-metrics"].textContent, /Risk-signal historyAvailable/);
  assert.match(harness.elements["analytics-trends"].textContent, /2026-10-20/);
  assert.match(harness.elements["analytics-trends"].textContent, /Needs review: 1/);
  assert.match(harness.elements["analytics-message"].textContent, /Settlement verified: No/);
  assert.equal(button.disabled, false);
});

test("analytics dashboard rejects settlement claims and malformed summaries", async () => {
  const harness = createHarness([
    response({ investigations: [] }),
    response({
      total_runs: 1,
      total_rows: 1,
      recorded_rows: 1,
      record_count_consistent: true,
      status_counts: {},
      matched_rows: 1,
      exception_rows: 0,
      match_rate_percent: 100,
      latest_run_at: null,
      settlement_verified: true,
      risk_signal_history_available: false
    })
  ]);
  await new Promise((resolve) => setImmediate(resolve));

  await harness.elements["refresh-analytics"].listeners.click();

  assert.match(harness.elements["analytics-message"].textContent, /unavailable or invalid/);
  assert.equal(harness.elements["analytics-metrics"].children.length, 0);
  assert.equal(harness.requests.length, 2);
  assert.equal(harness.elements["refresh-analytics"].disabled, false);
});

test("analytics dashboard displays a safe error when the summary request fails", async () => {
  const harness = createHarness([
    response({ investigations: [] }),
    response({ detail: "internal database path" }, { ok: false, status: 503 })
  ]);
  await new Promise((resolve) => setImmediate(resolve));

  await harness.elements["refresh-analytics"].listeners.click();

  assert.match(harness.elements["analytics-message"].textContent, /unavailable or invalid/);
  assert.doesNotMatch(harness.elements["analytics-message"].textContent, /internal database path/);
  assert.equal(harness.elements["refresh-analytics"].disabled, false);
});

test("successful reconciliation renders results and sends CSV text", async () => {
  const payload = validPayload([
    result(),
    result({
      merchant_reference: "INV-1002",
      paypal_order_id: "ORDER-2",
      status: "amount_mismatch",
      summary: "Expected and observed amounts differ.",
    }),
  ]);

  const { elements, requests } = await startHarness(response(payload));
  const csv = [
    "merchant_reference,paypal_order_id,expected_amount,currency_code",
    "INV-1001,ORDER-1,25.00,USD",
  ].join("\n");

  attachFile(elements, { text: csv });
  await submit(elements);

  assert.equal(requests.length, 2);
  assert.equal(requests[1][0], "/api/reconciliation");
  assert.equal(requests[1][1].method, "POST");
  assert.equal(requests[1][1].headers["Content-Type"], "application/json");
  assert.equal(JSON.parse(requests[1][1].body).csv_text, csv);

  assert.equal(elements["reconciliation-results"].hidden, false);
  assert.equal(elements["reconciliation-result-list"].children.length, 2);
  assert.match(elements["reconciliation-counts"].textContent, /Total records: 2/);
  assert.match(elements["reconciliation-counts"].textContent, /matched: 1/);
  assert.match(elements["reconciliation-counts"].textContent, /amount mismatch: 1/);
  assert.match(elements["reconciliation-counts"].textContent, /Settlement verified: No/);
  assert.match(elements["reconciliation-message"].textContent, /Settlement has not been verified/);
  assert.equal(elements["reconciliation-button"].disabled, false);
});

test("historical matches render as informational text", async () => {
  const payload = validPayload([
    result({
      historical_matches: [
        {
          match_type: "merchant_reference",
          run_id: 4,
          row_number: 2,
          matched_value: "INV-1001",
        },
        {
          match_type: "paypal_order_id",
          run_id: 5,
          row_number: 1,
          matched_value: "ORDER-1",
        },
      ],
    }),
  ]);

  const { elements } = await startHarness(response(payload));
  attachFile(elements);
  await submit(elements);

  const article = elements["reconciliation-result-list"].children[0];
  assert.match(article.textContent, /Historical matches/);
  assert.match(article.textContent, /Previous merchant reference match/);
  assert.match(article.textContent, /Run 4, row 2: INV-1001/);
  assert.match(article.textContent, /Previous paypal order id match/);
  assert.match(article.textContent, /Run 5, row 1: ORDER-1/);
  assert.match(article.textContent, /informational/i);
  assert.match(elements["reconciliation-counts"].textContent, /matched: 1/);
  assert.match(elements["reconciliation-counts"].textContent, /Settlement verified: No/);
});


test("recognized risk signals render as informational text", async () => {
  const payload = validPayload([
    result({
      risk_signals: [
        "duplicate_in_file",
        "historical_identifier_match",
      ],
    }),
  ]);

  const { elements } = await startHarness(response(payload));
  attachFile(elements);
  await submit(elements);

  const article = elements["reconciliation-result-list"].children[0];
  assert.match(article.textContent, /Risk signals/i);
  assert.match(article.textContent, /duplicate in file/i);
  assert.match(article.textContent, /historical identifier match/i);
  assert.match(article.textContent, /informational/i);
  assert.equal(article.children.some((child) => child.tagName === "img"), false);
  assert.equal(elements["reconciliation-results"].hidden, false);
  assert.match(elements["reconciliation-counts"].textContent, /matched: 1/);
  assert.match(elements["reconciliation-counts"].textContent, /Settlement verified: No/);
});

test("unsupported or malformed risk signals are rejected", async (t) => {
  const invalidSignals = [
    "duplicate_in_file",
    ["unknown_signal"],
    ["duplicate_in_file", "unknown_signal"],
    [null],
    [{}],
  ];

  for (const risk_signals of invalidSignals) {
    await t.test(JSON.stringify(risk_signals), async () => {
      const { elements } = await startHarness(
        response(validPayload([result({ risk_signals })]))
      );
      attachFile(elements);
      await submit(elements);

      assert.match(
        elements["reconciliation-message"].textContent,
        /invalid reconciliation record/
      );
      assert.equal(elements["reconciliation-results"].hidden, true);
      assert.equal(elements["reconciliation-result-list"].children.length, 0);
    });
  }
});

test("malformed historical matches are rejected", async (t) => {
  const invalidMatches = [
    "not-an-array",
    [{ match_type: "unknown", run_id: 1, row_number: 1, matched_value: "X" }],
    [{ match_type: "merchant_reference", run_id: 0, row_number: 1, matched_value: "X" }],
    [{ match_type: "paypal_order_id", run_id: 1, row_number: 0, matched_value: "X" }],
    [{ match_type: "merchant_reference", run_id: 1, row_number: 1, matched_value: " " }],
  ];

  for (const historical_matches of invalidMatches) {
    await t.test(JSON.stringify(historical_matches), async () => {
      const { elements } = await startHarness(
        response(validPayload([result({ historical_matches })]))
      );
      attachFile(elements);
      await submit(elements);

      assert.match(
        elements["reconciliation-message"].textContent,
        /invalid reconciliation record/
      );
      assert.equal(elements["reconciliation-results"].hidden, true);
      assert.equal(elements["reconciliation-result-list"].children.length, 0);
    });
  }
});

test("untrusted historical match values render as text", async () => {
  const unsafeValue = '<img src=x onerror="alert(1)">';
  const { elements } = await startHarness(response(validPayload([
    result({
      historical_matches: [{
        match_type: "merchant_reference",
        run_id: 3,
        row_number: 1,
        matched_value: unsafeValue,
      }],
    }),
  ])));

  attachFile(elements);
  await submit(elements);

  const article = elements["reconciliation-result-list"].children[0];
  assert.ok(article.textContent.includes(unsafeValue));
  assert.equal(article.children.some((child) => child.tagName === "img"), false);
});

test("untrusted reconciliation values are rendered as text", async () => {
  const unsafeReference = '<img src=x onerror="alert(1)">';
  const payload = validPayload([
    result({ merchant_reference: unsafeReference }),
  ]);

  const { elements } = await startHarness(response(payload));
  attachFile(elements);
  await submit(elements);

  const list = elements["reconciliation-result-list"];
  assert.equal(list.children.length, 1);

  const article = list.children[0];
  assert.equal(article.children[0].tagName, "h3");
  assert.equal(article.children[0].textContent, unsafeReference);
  assert.equal(article.children.some((child) => child.tagName === "img"), false);
});

test("missing file shows a helpful error without sending a request", async () => {
  const { elements, requests } = await startHarness(response(validPayload()));

  await submit(elements);

  assert.equal(requests.length, 1);
  assert.match(elements["reconciliation-message"].textContent, /Choose a CSV file/);
  assert.match(elements["reconciliation-message"].className, /error/);
  assert.equal(elements["reconciliation-button"].disabled, false);
});

test("oversized file is rejected before upload", async () => {
  const { elements, requests } = await startHarness(response(validPayload()));
  attachFile(elements, { size: 1_000_001 });

  await submit(elements);

  assert.equal(requests.length, 1);
  assert.match(elements["reconciliation-message"].textContent, /1 MB size limit/);
  assert.equal(elements["reconciliation-button"].disabled, false);
});

test("API validation error shows a safe message and restores the button", async () => {
  const { elements, requests } = await startHarness(
    response({ detail: "internal server details" }, { ok: false, status: 422 })
  );
  attachFile(elements);

  await submit(elements);

  assert.equal(requests.length, 2);
  assert.match(elements["reconciliation-message"].textContent, /CSV format or its records are invalid/);
  assert.doesNotMatch(elements["reconciliation-message"].textContent, /internal server details/);
  assert.equal(elements["reconciliation-results"].hidden, true);
  assert.equal(elements["reconciliation-button"].disabled, false);
});

test("response claiming settlement verification is rejected", async () => {
  const payload = {
    ...validPayload(),
    settlement_verified: true,
  };
  const { elements } = await startHarness(response(payload));
  attachFile(elements);

  await submit(elements);

  assert.match(elements["reconciliation-message"].textContent, /invalid reconciliation response/);
  assert.equal(elements["reconciliation-results"].hidden, true);
  assert.equal(elements["reconciliation-result-list"].children.length, 0);
});

test("record with unsupported status or settlement claim is rejected", async (t) => {
  for (const invalidRecord of [
    result({ status: "unexpected_status" }),
    result({ settlement_verified: true }),
  ]) {
    await t.test(JSON.stringify({
      status: invalidRecord.status,
      settlement_verified: invalidRecord.settlement_verified,
    }), async () => {
      const { elements } = await startHarness(
        response(validPayload([invalidRecord]))
      );
      attachFile(elements);

      await submit(elements);

      assert.match(
        elements["reconciliation-message"].textContent,
        /invalid reconciliation record/
      );
      assert.equal(elements["reconciliation-results"].hidden, true);
    });
  }
});

test("saved reconciliation runs can be loaded and viewed", async () => {
  const savedRun = {
    run_id: 7,
    created_at: "2026-10-20T10:00:00+00:00",
    record_count: 1,
    counts: { matched: 1 },
    settlement_verified: false,
  };
  const savedDetail = {
    ...savedRun,
    results: [result()],
  };

  const harness = createHarness([
    response({ investigations: [] }),
    response({ runs: [savedRun], count: 1 }),
    response(savedDetail),
  ]);
  const { elements, requests } = harness;

  await new Promise((resolve) => setImmediate(resolve));

  const refresh = elements["refresh-saved-runs"].listeners.click;
  assert.equal(typeof refresh, "function", "refresh handler must be registered");
  await refresh();

  assert.equal(requests[1][0], "/api/reconciliation/runs?limit=50");
  assert.match(elements["saved-runs-message"].textContent, /Showing 1 saved run/);
  assert.match(elements["saved-runs-list"].textContent, /Run 7/);
  assert.match(elements["saved-runs-list"].textContent, /Settlement verified: No/);

  const article = elements["saved-runs-list"].children[0];
  const viewButton = article.children.find(
    (child) => child.textContent === "View results"
  );
  assert.ok(viewButton, "saved run should have a View results button");

  await viewButton.listeners.click();

  assert.equal(requests[2][0], "/api/reconciliation/runs/7");
  assert.equal(elements["reconciliation-results"].hidden, false);
  assert.deepEqual(
    { ...elements["reconciliation-results"].scrollIntoViewOptions },
    { behavior: "smooth", block: "start" }
  );
  assert.equal(elements["reconciliation-result-list"].children.length, 1);
  assert.match(elements["reconciliation-counts"].textContent, /Saved run: 7/);
  assert.match(elements["reconciliation-counts"].textContent, /Settlement verified: No/);
  assert.match(elements["saved-runs-message"].textContent, /Loaded saved run 7/);
});

test("invalid saved reconciliation history is rejected", async () => {
  const harness = createHarness([
    response({ investigations: [] }),
    response({
      runs: [{
        run_id: 7,
        created_at: "2026-10-20T10:00:00+00:00",
        record_count: 1,
        counts: { matched: 1 },
        settlement_verified: true,
      }],
      count: 1,
    }),
  ]);
  const { elements, requests } = harness;

  await new Promise((resolve) => setImmediate(resolve));
  await elements["refresh-saved-runs"].listeners.click();

  assert.equal(requests[1][0], "/api/reconciliation/runs?limit=50");
  assert.match(elements["saved-runs-message"].textContent, /invalid saved reconciliation history/i);
  assert.equal(elements["saved-runs-list"].children.length, 0);
});
