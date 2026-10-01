const CODEX_MUX_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";

async function codexMuxRequest(path, options = {}) {
  const send = () =>
    fetch(`${CODEX_MUX_API}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Codex-Mux-Token": CODEX_MUX_TOKEN,
        ...options.headers,
      },
    });
  let response;
  try {
    response = await send();
  } catch (error) {
    // A pooled connection the server closed while idle fails on reuse;
    // a read is safe to send again on a fresh one.
    if (options.method && options.method !== "GET") throw error;
    response = await send();
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

const CODEX_MUX_ACCOUNTS_CACHE_KEY = "codex-mux.accounts";

function codexMuxCachedAccounts() {
  if (Array.isArray(globalThis.__codexMuxAccounts)) {
    return globalThis.__codexMuxAccounts;
  }
  try {
    const stored = JSON.parse(localStorage.getItem(CODEX_MUX_ACCOUNTS_CACHE_KEY));
    if (Array.isArray(stored)) {
      globalThis.__codexMuxAccounts = stored;
      return stored;
    }
  } catch {}
  return [];
}


function codexMuxRememberAccounts(accounts) {
  globalThis.__codexMuxAccounts = accounts;
  try {
    localStorage.setItem(CODEX_MUX_ACCOUNTS_CACHE_KEY, JSON.stringify(accounts));
  } catch {}
}

// Every surface listens to the one event stream: the renderer may only hold
// a few connections to the router at once, and each open stream is one.
const codexMuxEventListeners = new Set();
let codexMuxEventSource = null;

function codexMuxSubscribe(listener) {
  codexMuxEventListeners.add(listener);
  if (codexMuxEventSource == null) {
    codexMuxEventSource = new EventSource(
      `${CODEX_MUX_API}/events?token=${encodeURIComponent(CODEX_MUX_TOKEN)}`,
    );
    codexMuxEventSource.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      for (const subscriber of codexMuxEventListeners) subscriber(payload);
    };
  }
  return () => {
    codexMuxEventListeners.delete(listener);
    if (codexMuxEventListeners.size === 0 && codexMuxEventSource != null) {
      codexMuxEventSource.close();
      codexMuxEventSource = null;
    }
  };
}

async function codexMuxFetchAccounts() {
  const result = await codexMuxRequest("/accounts");
  const accounts = result.accounts || [];
  codexMuxRememberAccounts(accounts);
  return accounts;
}

const CODEX_MUX_ACCOUNT_SCOPED_PLUGIN_METHODS = new Set([
  "app/installed",
  "app/list",
  "app/read",
  "mcpServer/oauth/login",
  "mcpServerStatus/list",
  "list-installed-apps",
  "list-apps",
  "read-apps",
  "login-mcp-server",
  "list-mcp-server-status",
]);

function codexMuxScopePluginRequest(method, params) {
  const accountId = globalThis.__codexMuxPluginAccountId;
  if (
    !accountId ||
    !CODEX_MUX_ACCOUNT_SCOPED_PLUGIN_METHODS.has(method) ||
    (params != null &&
      (typeof params !== "object" || Array.isArray(params)))
  ) {
    return params;
  }
  return { ...(params || {}), codexMuxAccountId: accountId };
}

async function codexMuxProfileData(accountId = null) {
  const query = accountId
    ? `?accountId=${encodeURIComponent(accountId)}`
    : "";
  const result = await codexMuxRequest(`/profile/combined${query}`);
  globalThis.__codexMuxCombinedProfileAccounts = result.accounts || [];
  return result.profile;
}

// The renderer polls `/wham/usage` over HTTP for the Primary account only, so
// its usage banners, sidebar alert, and reset prompts describe one account
// while the multiplexer routes across the pool. Replace the rate-limit
// windows with the pooled view (mean usage, earliest reset) and clear the
// limit-reached fields while any connected subscription still has
// capacity. A fully depleted pool keeps the native limit-reached response.
async function codexMuxFilterUsageStatus(status) {
  if (status == null || typeof status !== "object") return status;
  let accounts;
  try {
    accounts = (await codexMuxRequest("/accounts")).accounts || [];
  } catch {
    return status;
  }
  const pool = accounts.filter(
    (account) =>
      account.enabled &&
      account.connected &&
      (!account.authType || account.authType === "chatgpt"),
  );
  if (pool.length < 2) return status;
  const poolHasCapacity = pool.some((account) =>
    [account.rateLimits?.primary, account.rateLimits?.secondary].every(
      (window) => window == null || window.usedPercent < 100,
    ) || codexMuxCredits(account.rateLimits) != null,
  );
  const rateLimit = status.rate_limit;
  const pooledRateLimit =
    rateLimit == null
      ? rateLimit
      : {
          ...rateLimit,
          primary_window: codexMuxPooledUsageWindow(
            rateLimit.primary_window,
            codexMuxWindowsForNativeCadence(
              rateLimit.primary_window,
              pool,
              CODEX_MUX_FIVE_HOUR_WINDOW_MINS,
            ),
          ),
          secondary_window: codexMuxPooledUsageWindow(
            rateLimit.secondary_window,
            codexMuxWindowsForNativeCadence(
              rateLimit.secondary_window,
              pool,
              CODEX_MUX_WEEKLY_WINDOW_MINS,
            ),
          ),
        };
  if (!poolHasCapacity) return { ...status, rate_limit: pooledRateLimit };
  return {
    ...status,
    rate_limit_upsell: null,
    rate_limit_reached_type: null,
    rate_limit:
      pooledRateLimit == null
        ? pooledRateLimit
        : { ...pooledRateLimit, allowed: true, limit_reached: false },
  };
}

function codexMuxPooledUsageWindow(window, accountWindows) {
  if (window == null) return window;
  const windows = accountWindows.filter(Boolean);
  if (windows.length === 0) return window;
  const usedPercent =
    windows.reduce((total, entry) => total + entry.usedPercent, 0) /
    windows.length;
  const resets = windows
    .map((entry) => entry.resetsAt)
    .filter((value) => value != null);
  const resetsAt = resets.length === 0 ? null : Math.min(...resets);
  return {
    ...window,
    used_percent: usedPercent,
    reset_at: resetsAt ?? window.reset_at,
  };
}

const CODEX_MUX_FIVE_HOUR_WINDOW_MINS = 5 * 60;
const CODEX_MUX_WEEKLY_WINDOW_MINS = 7 * 24 * 60;
const CODEX_MUX_WINDOW_DURATION_TOLERANCE_MINS = 1;

function codexMuxRateLimitWindows(rateLimits) {
  return [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
}

function codexMuxWindowForDuration(rateLimits, targetMinutes) {
  return (
    codexMuxRateLimitWindows(rateLimits).find(
      (window) =>
        window.windowDurationMins != null &&
        Math.abs(window.windowDurationMins - targetMinutes) <=
          CODEX_MUX_WINDOW_DURATION_TOLERANCE_MINS,
    ) || null
  );
}

function codexMuxWindowsForNativeCadence(window, accounts, fallbackMinutes) {
  const targetMinutes =
    window?.limit_window_seconds == null
      ? fallbackMinutes
      : window.limit_window_seconds / 60;
  return accounts
    .map((account) =>
      codexMuxWindowForDuration(account.rateLimits, targetMinutes),
    )
    .filter(Boolean);
}

function codexMuxFiveHourWindow(rateLimits) {
  const exact = codexMuxWindowForDuration(
    rateLimits,
    CODEX_MUX_FIVE_HOUR_WINDOW_MINS,
  );
  if (exact) return exact;
  if (codexMuxWindowForDuration(rateLimits, CODEX_MUX_WEEKLY_WINDOW_MINS)) {
    return null;
  }
  const windows = codexMuxRateLimitWindows(rateLimits);
  return (
    windows.sort(
      (left, right) =>
        (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
    )[0] ||
    null
  );
}

async function codexMuxRateLimitResets(accountId) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/rate-limit-resets`,
  );
}

async function codexMuxConsumeRateLimitReset(accountId, input) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/rate-limit-resets/consume`,
    {
      method: "POST",
      body: JSON.stringify({
        creditId: input.creditId ?? null,
        redeemRequestId: input.redeemRequestId,
      }),
    },
  );
}

async function codexMuxRemoteControlStatus(accountId) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/remote-control`,
  );
}

async function codexMuxEnableRemoteControl(accountId) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/remote-control/enable`,
    { method: "POST" },
  );
}

async function codexMuxStartRemoteControlPairing(accountId) {
  return codexMuxRequest(
    `/accounts/${encodeURIComponent(accountId)}/remote-control/pairing`,
    { method: "POST" },
  );
}

// codexMuxCredits is the purchased balance an account can spend once its
// plan windows are exhausted, or null when it holds none.
function codexMuxCredits(rateLimits) {
  const credits = rateLimits?.credits;
  if (!credits || !(credits.hasCredits || credits.unlimited)) return null;
  if (credits.unlimited) return "unlimited credits";
  const balance = Number(credits.balance);
  return `${Number.isFinite(balance) ? Math.round(balance) : credits.balance} credits`;
}

function codexMuxWeeklyWindow(rateLimits) {
  const exact = codexMuxWindowForDuration(
    rateLimits,
    CODEX_MUX_WEEKLY_WINDOW_MINS,
  );
  if (exact) return exact;
  if (codexMuxWindowForDuration(rateLimits, CODEX_MUX_FIVE_HOUR_WINDOW_MINS)) {
    return null;
  }
  const windows = codexMuxRateLimitWindows(rateLimits);
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.at(-1) || null;
}

function codexMuxUsageWindows(rateLimits) {
  const fiveHour = codexMuxFiveHourWindow(rateLimits);
  const weekly = codexMuxWeeklyWindow(rateLimits);
  return [fiveHour, weekly]
    .filter((window, index, windows) => window && windows.indexOf(window) === index)
    .map((window) => ({
      usedPercent: window.usedPercent,
      remainingPercent: Math.max(0, 100 - window.usedPercent),
      windowMinutes: window.windowDurationMins || 0,
      resetsAt: window.resetsAt ?? null,
    }));
}

function codexMuxResetInfo(window, now = Date.now()) {
  const seconds = window?.resetsAt;
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  const reset = new Date(seconds * 1_000);
  if (!Number.isFinite(reset.getTime())) return null;
  const minutes = Math.ceil((reset.getTime() - now) / 60_000);
  const days = Math.floor(minutes / 1_440);
  const hours = Math.floor((minutes % 1_440) / 60);
  const remainder = minutes % 60;
  const remaining = days > 0
    ? `${days}d${hours > 0 ? ` ${hours}h` : ""}`
    : hours > 0
      ? `${hours}h${remainder > 0 ? ` ${remainder}m` : ""}`
      : `${minutes}m`;
  return {
    dateTime: reset.toISOString(),
    label: reset.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", year: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short",
    }),
    countdown: minutes > 0 ? `in ${remaining}` : "Awaiting usage update",
  };
}

// The menu, profile, plugin, and thread surfaces render from other bundles.
Object.assign(globalThis, {
  codexMuxRequest,
  codexMuxSubscribe,
  codexMuxCredits,
  codexMuxCachedAccounts,
  codexMuxRememberAccounts,
  codexMuxFetchAccounts,
  codexMuxSelectAccount,
  codexMuxScopePluginRequest,
  codexMuxProfileData,
  codexMuxFilterUsageStatus,
  codexMuxRateLimitResets,
  codexMuxConsumeRateLimitReset,
  codexMuxRemoteControlStatus,
  codexMuxEnableRemoteControl,
  codexMuxStartRemoteControlPairing,
  codexMuxFiveHourWindow,
  codexMuxWeeklyWindow,
  codexMuxUsageWindows,
  codexMuxResetInfo,
});

async function codexMuxSelectAccount(accountId) {
  return codexMuxRequest("/account-selection", {
    method: "POST",
    body: JSON.stringify({ accountId }),
  });
}
