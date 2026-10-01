const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadAccountData() {
  const filename = path.join(__dirname, "account-data.js");
  const source = fs.readFileSync(filename, "utf8");
  const context = vm.createContext({
    EventSource: class {},
    fetch: async () => {
      throw new Error("unexpected request");
    },
    localStorage: {
      getItem: () => null,
      setItem: () => {},
    },
  });
  vm.runInContext(
    `${source}\n` +
      `globalThis.__test = {` +
      `codexMuxFiveHourWindow, codexMuxWeeklyWindow, codexMuxUsageWindows, ` +
      `codexMuxWindowsForNativeCadence, codexMuxPooledUsageWindow, codexMuxResetInfo` +
      `};`,
    context,
    { filename },
  );
  return context.__test;
}

function window(usedPercent, windowDurationMins, resetsAt = null) {
  return { usedPercent, windowDurationMins, resetsAt };
}

test("normalizes swapped 5-hour and weekly slots", () => {
  const helpers = loadAccountData();
  const rateLimits = {
    primary: window(40, 10_080, 2_000),
    secondary: window(20, 300, 1_000),
  };

  assert.equal(helpers.codexMuxFiveHourWindow(rateLimits).usedPercent, 20);
  assert.equal(helpers.codexMuxWeeklyWindow(rateLimits).usedPercent, 40);
  assert.deepEqual(
    Array.from(
      helpers.codexMuxUsageWindows(rateLimits),
      ({ windowMinutes, remainingPercent }) => ({
        windowMinutes,
        remainingPercent,
      }),
    ),
    [
      { windowMinutes: 300, remainingPercent: 80 },
      { windowMinutes: 10_080, remainingPercent: 60 },
    ],
  );
});

test("pools windows by cadence rather than primary or secondary slot", () => {
  const helpers = loadAccountData();
  const accounts = [
    {
      rateLimits: {
        primary: window(20, 300, 2_000),
        secondary: window(60, 10_080, 4_000),
      },
    },
    {
      rateLimits: {
        primary: window(40, 10_080, 3_000),
        secondary: window(80, 300, 1_000),
      },
    },
  ];
  const nativeFiveHour = { limit_window_seconds: 18_000, reset_at: 9_000 };
  const windows = helpers.codexMuxWindowsForNativeCadence(
    nativeFiveHour,
    accounts,
    300,
  );
  const pooled = helpers.codexMuxPooledUsageWindow(nativeFiveHour, windows);

  assert.equal(pooled.used_percent, 50);
  assert.equal(pooled.reset_at, 1_000);
});

test("does not present one known cadence as both limits", () => {
  const helpers = loadAccountData();
  const weeklyOnly = { primary: window(40, 10_080) };
  const fiveHourOnly = { secondary: window(20, 300) };

  assert.equal(helpers.codexMuxFiveHourWindow(weeklyOnly), null);
  assert.equal(helpers.codexMuxWeeklyWindow(weeklyOnly).usedPercent, 40);
  assert.equal(helpers.codexMuxFiveHourWindow(fiveHourOnly).usedPercent, 20);
  assert.equal(helpers.codexMuxWeeklyWindow(fiveHourOnly), null);
});

test("reset info converts Unix seconds to local date and time with a time zone", () => {
  const previousTimezone = process.env.TZ;
  try {
    const seconds = Date.parse("2026-10-02T00:30:00Z") / 1000;
    process.env.TZ = "America/Los_Angeles";
    const helpers = loadAccountData();
    const info = helpers.codexMuxResetInfo(window(40, 10080, seconds), seconds * 1000 - 60_000);
    assert.equal(info.dateTime, "2026-10-02T00:30:00.000Z");
    assert.match(info.label, /Oct 1, 2026/);
    assert.match(info.label, /5:30 PM PDT/);
    assert.equal(info.countdown, "in 1m");
    process.env.TZ = "UTC";
    const utc = helpers.codexMuxResetInfo(window(40, 10080, seconds));
    assert.match(utc.label, /Oct 2, 2026/);
    assert.match(utc.label, /12:30 AM UTC/);
  } finally {
    if (previousTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimezone;
  }
});

test("reset countdown handles days, hours, under a minute, and expired snapshots", () => {
  const helpers = loadAccountData();
  const now = Date.parse("2026-10-01T00:00:00Z");
  const countdown = (minutes) => helpers.codexMuxResetInfo(
    window(40, 10080, (now + minutes * 60_000) / 1000), now,
  ).countdown;
  assert.equal(countdown(2 * 1440 + 3 * 60), "in 2d 3h");
  assert.equal(countdown(60 + 20), "in 1h 20m");
  assert.equal(countdown(0.1), "in 1m");
  assert.equal(countdown(0), "Awaiting usage update");
  assert.equal(countdown(-60), "Awaiting usage update");
  for (const resetsAt of [null, undefined, 0, -1, NaN, Infinity, 1e20, "1790899200"]) {
    assert.equal(helpers.codexMuxResetInfo({ resetsAt }, now), null);
  }
});

function renderThreadSubscription(rateLimits) {
  const jsx = (type, props) => ({ type, props });
  const context = vm.createContext({
    K: { Section: "section" },
    localStorage: { getItem: () => null },
    codexMuxReact: () => ({ useState: () => [{ label: "My account", rateLimits }], useEffect() {} }),
    codexMuxJsx: () => ({ jsx, jsxs: jsx }),
    codexMuxUseRoute: () => ({ value: { routeKind: "local-thread", conversationId: "thread" } }),
  });
  vm.runInContext(`(() => { ${fs.readFileSync(path.join(__dirname, "account-data.js"), "utf8")} })();`, context);
  vm.runInContext(`${fs.readFileSync(path.join(__dirname, "thread-subscription.js"), "utf8")}\n globalThis.__render = CodexMuxThreadSubscription;`, context);
  const nodes = [];
  function walk(node) {
    if (!node || typeof node !== "object") return;
    nodes.push(node);
    const children = node.props?.children;
    for (const child of Array.isArray(children) ? children : [children]) walk(child);
  }
  walk(context.__render());
  return nodes;
}

test("upper-right Subscription renders both reset times even with swapped quota slots", () => {
  const seconds = Date.parse("2030-10-02T00:30:00Z") / 1000;
  const nodes = renderThreadSubscription({ primary: window(40, 10080, seconds), secondary: window(20, 300, seconds - 86400) });
  assert.equal(nodes[0].props.title, "Subscription");
  assert.ok(nodes.some((node) => node.props.children === "Weekly resets"));
  assert.ok(nodes.some((node) => node.props.children === "5-hour resets"));
  assert.deepEqual(nodes.filter((node) => node.type === "time").map((node) => node.props.dateTime), [
    "2030-10-01T00:30:00.000Z", "2030-10-02T00:30:00.000Z",
  ]);
  assert.ok(nodes.some((node) => node.props.children === "60% remaining"));
  assert.ok(nodes.some((node) => typeof node.props.children === "string" && node.props.children.startsWith("in ")));
});

test("Subscription keeps missing reset times separate for each cadence", () => {
  for (const rateLimits of [null, { primary: window(40, 10080) }]) {
    const nodes = renderThreadSubscription(rateLimits);
    assert.equal(nodes.filter((node) => node.props.children === "Reset time unavailable").length, 2);
    assert.ok(!nodes.some((node) => node.type === "time"));
  }
  for (const duration of [300, 10080]) {
    const nodes = renderThreadSubscription({ primary: window(40, duration, 1_800_000_000) });
    const sections = nodes.filter((node) => node.props["data-codex-mux-reset"]);
    const available = sections.find((node) => node.props["data-codex-mux-reset"] === (duration === 300 ? "five-hour" : "weekly"));
    const unavailable = sections.find((node) => node !== available);
    assert.ok(available.props.children.some((node) => node?.type === "time"));
    assert.ok(unavailable.props.children.some((node) => node?.props.children === "Reset time unavailable"));
    assert.equal(nodes.filter((node) => node.type === "time").length, 1);
  }
});


test("manual selection posts the selected account and propagates rejection", async () => {
  const source = fs.readFileSync(path.join(__dirname, "account-data.js"), "utf8");
  const requests = [];
  const context = vm.createContext({
    fetch: async (url, options) => {
      requests.push({ url, options });
      const { accountId } = JSON.parse(options.body);
      return { ok: accountId !== "depleted", json: async () => accountId === "depleted" ? { error: "Out of usage" } : { accountId } };
    },
  });
  vm.runInContext(`(() => { ${source} })();`, context);
  assert.equal((await context.codexMuxSelectAccount("other")).accountId, "other");
  assert.equal(requests[0].options.method, "POST");
  assert.ok(requests[0].url.endsWith("/account-selection"));
  assert.ok(requests[0].options.headers["X-Codex-Mux-Token"]);
  assert.equal((await context.codexMuxSelectAccount("")).accountId, "");
  await assert.rejects(context.codexMuxSelectAccount("depleted"), /Out of usage/);
});

function loadMenu(accounts, { fetch, openModal } = {}) {
  const states = [];
  let cursor = 0;
  const jsx = (type, props, key) => ({ type, props, key });
  const context = vm.createContext({
    kXc: {
      useState(initial) {
        const index = cursor++;
        if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
        return [states[index], (value) => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
      },
      useCallback: (fn) => fn,
      useEffect() {},
      useRef: () => ({ current: null }),
    },
    e7: { jsx, jsxs: jsx, Fragment: "fragment" },
    Lo() {}, Q: {}, _H: "menu-item", CH: { Separator: "separator" },
    QLs: "usage-modal",
    BW: openModal || (() => { throw new Error("Switch opened a usage modal"); }),
    document: { createElement: () => ({}), head: { append() {} } },
    window: {},
    RD: { createPortal: (node) => node },
    localStorage: { getItem: () => null },
    fetch: fetch || (async () => { throw new Error("unexpected request"); }),
    __codexMuxAccounts: accounts,
  });
  // Bundles have separate module scopes; only explicit globals cross the boundary.
  for (const name of ["account-data.js", "account-menu.js"]) {
    vm.runInContext(`(() => { ${fs.readFileSync(path.join(__dirname, name), "utf8")} })();`, context);
  }
  return {
    context,
    render() { cursor = 0; return context.CodexMuxAccountMenu().props.children; },
    renderUsage() { cursor = 0; context.CodexMuxUseResetAccountState(); },
    renderComposer() { cursor = 0; return context.codexMuxComposerAccount().type(); },
  };
}

test("profile account menu keeps management actions without session switching", () => {
  const requests = [];
  const { render } = loadMenu([{ id: "other", label: "Other", enabled: true, connected: true }], {
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => JSON.parse(options.body) };
    },
  });
  const event = { preventDefault() {} };
  render().find((row) => row.key === "codex-mux-account-other").props.onSelect(event);
  const rows = render();
  assert.ok(rows.some((row) => row.props.children === "View account usage"));
  assert.ok(rows.some((row) => row.props.children === "Copy email address"));
  assert.ok(!rows.some((row) => row.key === "codex-mux-account-other-select"));
  assert.ok(!rows.some((row) => row.key === "codex-mux-automatic-routing"));
  assert.equal(requests.length, 0);
});

function iconLabels(row) {
  return Array.from(row.props.rightIcon.props.children).filter(Boolean).map((span) => span.props.children);
}

test("profile menu renders Plus five-hour and weekly limits alongside Pro and credits", () => {
  const accounts = [
    { id: "plus", label: "Plus", planLabel: "Plus", enabled: true, connected: true,
      rateLimits: { primary: window(40, 10080), secondary: window(20, 300) } },
    { id: "pro", label: "Pro", planLabel: "Pro", enabled: true, connected: true,
      rateLimits: { primary: window(30, 10080) } },
    { id: "credit", label: "Credits", enabled: true, connected: true,
      rateLimits: { primary: window(100, 300), secondary: window(100, 10080), credits: { hasCredits: true, balance: "750" } } },
  ];
  const rows = loadMenu(accounts).render();
  assert.deepEqual(iconLabels(rows.find((r) => r.key === "codex-mux-account-plus")), ["5h 80%", "Week 60%"]);
  assert.deepEqual(iconLabels(rows.find((r) => r.key === "codex-mux-account-pro")), ["5h –", "Week 70%"]);
  assert.deepEqual(iconLabels(rows.find((r) => r.key === "codex-mux-account-credit")), ["5h 0%", "Week 0%", "750 credits"]);
});

test("View account usage selects that account's normalized windows in the usage sheet", () => {
  let modal;
  const { context, render } = loadMenu([
    { id: "primary", label: "Primary", enabled: true, connected: true },
    { id: "plus", label: "Plus", enabled: true, connected: true,
      rateLimits: { primary: window(40, 10080), secondary: window(20, 300) } },
  ], { openModal: (scope, component, props) => { modal = { component, props }; } });
  const event = { preventDefault() {} };
  render().find((r) => r.key === "codex-mux-account-plus").props.onSelect(event);
  render().find((r) => r.props.children === "View account usage").props.onSelect(event);
  modal.component(modal.props);
  assert.equal(context.__codexMuxRequestedUsageAccountId, "plus");
  // Use fresh hook state, as the native usage sheet mounts a new component.
  const sheet = loadMenu(context.__codexMuxAccounts);
  sheet.context.__codexMuxRequestedUsageAccountId = context.__codexMuxRequestedUsageAccountId;
  sheet.renderUsage();
  assert.equal(sheet.context.window.__codexMuxResetAccountId, "plus");
  assert.deepEqual(Array.from(sheet.context.window.__codexMuxSelectedUsageWindows, (w) => w.remainingPercent), [80, 60]);
});

test("composer captions and availability respect five-hour exhaustion and purchased credits", () => {
  const { context } = loadMenu([]);
  const account = { planLabel: "Plus", rateLimits: { primary: window(100, 300), secondary: window(20, 10080) } };
  // Evaluate helpers inside a separate module to mirror the injected renderer.
  vm.runInContext(`(() => { ${fs.readFileSync(path.join(__dirname, "account-menu.js"), "utf8")}
    globalThis.__caption = codexMuxAccountCaption; globalThis.__exhausted = codexMuxAccountExhausted;
  })();`, context);
  assert.equal(context.__caption(account), "Plus · 5h 0% · Week 80%");
  assert.equal(context.__exhausted(account), true);
  account.rateLimits.credits = { hasCredits: true, balance: "750" };
  assert.equal(context.__exhausted(account), false);
  assert.match(context.__caption(account), /750 credits/);
});

for (const credits of [false, true]) {
  test(`pooled native status respects short-window exhaustion with credits=${credits}`, async () => {
    const accounts = [
      { enabled: true, connected: true, authType: "chatgpt", rateLimits: {
        primary: window(100, 300, 1000), secondary: window(20, 10080, 4000),
        ...(credits ? { credits: { hasCredits: true, balance: "750" } } : {}),
      } },
      { enabled: true, connected: true, authType: "chatgpt", rateLimits: {
        primary: window(40, 10080, 3000), secondary: window(100, 300, 2000),
      } },
    ];
    const { context } = loadMenu(accounts, {
      fetch: async () => ({ ok: true, json: async () => ({ accounts }) }),
    });
    const status = { rate_limit_reached_type: "rate_limit_reached", rate_limit: {
      allowed: false, limit_reached: true,
      primary_window: { limit_window_seconds: 18000 }, secondary_window: { limit_window_seconds: 604800 },
    } };
    const result = await context.codexMuxFilterUsageStatus(status);
    assert.equal(result.rate_limit.primary_window.used_percent, 100);
    assert.equal(result.rate_limit.secondary_window.used_percent, 30);
    assert.equal(result.rate_limit.primary_window.reset_at, 1000);
    assert.equal(result.rate_limit.allowed, credits);
    assert.equal(result.rate_limit.limit_reached, !credits);
  });
}


test("composer choice clears a legacy session override after saving its new-chat preference", async () => {
  const requests = [];
  const accounts = [
    { id: "plus", label: "Plus", enabled: true, connected: true },
    { id: "primary", label: "Primary", enabled: true, connected: true },
  ];
  const { renderComposer } = loadMenu(accounts, {
    fetch: async (url, options) => {
      requests.push({ url, options });
      const body = url.endsWith("/accounts") ? { accounts } : { accountId: "plus" };
      return { ok: true, json: async () => body };
    },
  });
  let composer = renderComposer();
  composer.props.children[0].props.onClick();
  composer = renderComposer();
  const accountRow = composer.props.children[1].props.children.find((row) => row?.key === "plus");
  await accountRow.props.onClick();
  const mutations = requests.filter(({ options }) => options.method);
  assert.deepEqual(mutations.map(({ url }) => url.split("/v1")[1]), ["/preferred-account", "/account-selection"]);
  assert.equal(JSON.parse(mutations[0].options.body).accountId, "plus");
  assert.equal(JSON.parse(mutations[1].options.body).accountId, "");
});
