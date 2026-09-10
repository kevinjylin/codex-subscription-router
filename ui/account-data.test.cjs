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
