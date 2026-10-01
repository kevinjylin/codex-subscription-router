const CODEX_MUX_THREAD_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_THREAD_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";

// React, the JSX runtime, and the route hook come from the primary bundle's
// injected menu, so this bundle only needs its own summary Section.
function CodexMuxThreadSubscription() {
  const TE = globalThis.codexMuxReact();
  const zE = globalThis.codexMuxJsx();
  const route = globalThis.codexMuxUseRoute();
  const threadId =
    route?.value?.routeKind === "local-thread" ? route.value.conversationId : null;
  const [account, setAccount] = TE.useState(null);

  TE.useEffect(() => {
    let active = true;
    if (!threadId) {
      setAccount(null);
      return () => {
        active = false;
      };
    }

    const refresh = async () => {
      try {
        const response = await fetch(
          `${CODEX_MUX_THREAD_API}/thread-account?threadId=${encodeURIComponent(threadId)}`,
          { headers: { "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN } },
        );
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        const body = await response.json();
        if (active) setAccount(body.account || null);
      } catch {
        if (active) setAccount(null);
      }
    };

    refresh();
    const unsubscribe = globalThis.codexMuxSubscribe?.((payload) => {
      if (
        payload.type === "account-updated" ||
        (["thread-moved", "thread-failed-over"].includes(payload.type) &&
          payload.data?.threadId === threadId)
      ) {
        refresh();
      }
    });
    const warmupTimer = setTimeout(refresh, 2_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      active = false;
      clearTimeout(warmupTimer);
      clearInterval(timer);
      unsubscribe?.();
    };
  }, [threadId]);

  if (!account) return null;
  const weekly = globalThis.codexMuxWeeklyWindow(account.rateLimits);
  const resets = [
    { cadence: "five-hour", title: "5-hour resets", window: globalThis.codexMuxFiveHourWindow(account.rateLimits) },
    { cadence: "weekly", title: "Weekly resets", window: weekly },
  ];
  const remaining = weekly == null ? null : Math.max(0, 100 - weekly.usedPercent);
  const credits = globalThis.codexMuxCredits?.(account.rateLimits) ?? null;
  const depleted = remaining === 0 && credits == null;
  const AccountAvatar = globalThis.CodexMuxAccountAvatar;
  return (0, zE.jsx)(K.Section, {
    sectionKey: "codex-mux-subscription",
    title: "Subscription",
    children: (0, zE.jsxs)("div", {
      className: "flex flex-col gap-2 py-1",
      children: [
        (0, zE.jsxs)("div", {
          className: "flex min-h-9 items-center justify-between gap-3 py-1 text-sm",
          children: [
            (0, zE.jsxs)("div", {
              className: "flex min-w-0 items-center gap-2",
              children: [
                AccountAvatar
                  ? (0, zE.jsx)(AccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-5 shrink-0",
                    })
                  : null,
                (0, zE.jsx)("span", {
                  className: "truncate text-token-text-primary",
                  children: account.planLabel
                    ? `${account.label} · ${account.planLabel}`
                    : account.label,
                }),
              ],
            }),
            (0, zE.jsx)("span", {
              className: "shrink-0 tabular-nums text-token-description-foreground",
              children:
                remaining == null
                  ? "Usage unavailable"
                  : depleted
                    ? "Depleted"
                    : remaining === 0
                      ? credits
                      : `${Math.round(remaining)}% remaining`,
            }),
          ],
        }),
        ...resets.map(({ cadence, title, window }) => {
          const reset = globalThis.codexMuxResetInfo(window);
          return (0, zE.jsxs)("div", {
            className: "flex flex-col gap-1 text-xs text-token-description-foreground",
            "data-codex-mux-reset": cadence,
            children: [
              (0, zE.jsx)("span", {
                className: "font-medium text-token-text-primary",
                children: title,
              }),
              reset
                ? (0, zE.jsx)("time", {
                    dateTime: reset.dateTime,
                    children: reset.label,
                  })
                : (0, zE.jsx)("span", { children: "Reset time unavailable" }),
              reset
                ? (0, zE.jsx)("span", {
                    className: "tabular-nums",
                    children: reset.countdown,
                  })
                : null,
            ],
          }, cadence);
        }),
      ],
    }),
  });
}
