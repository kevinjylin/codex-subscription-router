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
      `codexMuxWindowsForNativeCadence, codexMuxPooledUsageWindow` +
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

test("Switch to account menu action selects routing without opening usage", async () => {
  const states = [];
  let cursor = 0;
  const requests = [];
  const account = { id: "other", label: "Other", enabled: true, connected: true };
  const jsx = (type, props, key) => ({ type, props, key });
  const context = vm.createContext({
    kXc: {
      useState(initial) {
        const index = cursor++;
        if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
        return [states[index], (value) => { states[index] = value; }];
      },
      useCallback: (fn) => fn,
      useEffect() {},
    },
    e7: { jsx, jsxs: jsx, Fragment: "fragment" },
    Lo() {}, Q: {}, _H: "menu-item", S2: "icon", CH: { Separator: "separator" },
    BW() { throw new Error("Switch opened a usage modal"); },
    localStorage: { getItem: () => null },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => JSON.parse(options.body) };
    },
    __codexMuxAccounts: [account],
  });
  // Bundles have separate module scopes; only explicit globals cross the boundary.
  vm.runInContext(`(() => { ${fs.readFileSync(path.join(__dirname, "account-data.js"), "utf8")} })();`, context);
  vm.runInContext(`(() => { ${fs.readFileSync(path.join(__dirname, "account-menu.js"), "utf8")}
    globalThis.CodexMuxAccountMenu = CodexMuxAccountMenu; })();`, context);
  function render() { cursor = 0; return context.CodexMuxAccountMenu().props.children; }
  const event = { preventDefault() {} };
  render().find((row) => row.key === "codex-mux-account-other").props.onSelect(event);
  await render().find((row) => row.props.children === "Switch to account").props.onSelect(event);
  assert.equal(JSON.parse(requests[0].options.body).accountId, "other");
  assert.ok(render().some((row) => row.props.children === "Selected account"));
  await render().find((row) => row.props.children === "Use automatic routing").props.onSelect(event);
  assert.equal(JSON.parse(requests[1].options.body).accountId, "");
});
