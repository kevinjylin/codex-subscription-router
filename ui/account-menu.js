function CodexMuxUsageModal({
  initialAccountId,
  onClose,
}) {
  globalThis.__codexMuxRequestedUsageAccountId = initialAccountId || null;
  return (0, e7.jsx)(QLs, {
    defaultResetCreditsOpen: true,
    initialAvailableCount: 0,
    isRateLimitReached: false,
    onClose: () => {
      delete globalThis.__codexMuxRequestedUsageAccountId;
      onClose?.();
    },
    onResetComplete: () => {},
  });
}

function CodexMuxUseResetAccountState() {
  const cachedAccounts = codexMuxCachedAccounts().filter(
    (account) => account.connected && account.enabled,
  );
  const [accounts, setAccounts] = kXc.useState(cachedAccounts);
  const [selectedId, setSelectedId] = kXc.useState(
    () => globalThis.__codexMuxRequestedUsageAccountId || "primary",
  );
  const [resetCounts, setResetCounts] = kXc.useState({});
  const [loading, setLoading] = kXc.useState(cachedAccounts.length === 0);

  const loadAccounts = kXc.useCallback(async () => {
    const connected = (await codexMuxFetchAccounts()).filter(
      (account) => account.connected && account.enabled,
    );
    setAccounts(connected);
    setSelectedId((current) =>
      connected.some((account) => account.id === current)
        ? current
        : connected[0]?.id || "primary",
    );
    setLoading(false);
    const entries = await Promise.all(
      connected.map(async (account) => {
        try {
          const resets = await codexMuxRateLimitResets(account.id);
          return [account.id, Math.max(0, resets.available_count || 0)];
        } catch {
          return [account.id, null];
        }
      }),
    );
    setResetCounts(Object.fromEntries(entries));
  }, []);

  kXc.useEffect(() => {
    loadAccounts().catch(() => setLoading(false));
  }, [loadAccounts]);

  kXc.useEffect(
    () => () => {
      delete window.__codexMuxResetAccountId;
      delete window.__codexMuxSelectedUsageWindows;
      delete window.__codexMuxResetAccountSelector;
      delete globalThis.__codexMuxRequestedUsageAccountId;
    },
    [],
  );

  const selected =
    accounts.find((account) => account.id === selectedId) || accounts[0] || null;
  const activeId = selected?.id || selectedId;
  window.__codexMuxResetAccountId = activeId;
  window.__codexMuxSelectedUsageWindows = selected
    ? codexMuxUsageWindows(selected.rateLimits)
    : null;
  window.__codexMuxResetAccountSelector = (0, e7.jsx)(
    CodexMuxResetAccountSelector,
    {
      accounts,
      loading,
      resetCounts,
      selectedId: activeId,
      onSelect: setSelectedId,
    },
  );
}

function CodexMuxResetAccountSelector({
  accounts,
  loading,
  onSelect,
  resetCounts,
  selectedId,
}) {
  return (0, e7.jsxs)("div", {
    className: "pt-4",
    children: [
      (0, e7.jsx)("div", {
        className:
          "mb-2 px-1 text-xs font-medium text-token-text-secondary",
        children: "Subscription",
      }),
      (0, e7.jsx)("div", {
        className:
          "flex flex-wrap gap-2 rounded-2xl border border-token-border p-2",
        children: loading
          ? (0, e7.jsx)("div", {
              className: "px-2 py-2 text-sm text-token-text-secondary",
              children: "Loading subscriptions…",
            })
          : accounts.map((account) => {
              const selected = account.id === selectedId;
              const count = resetCounts[account.id];
              return (0, e7.jsxs)(
                "button",
                {
                  type: "button",
                  className: [
                    "flex min-w-fit items-center gap-2 rounded-xl px-3 py-2 text-left",
                    "transition-colors hover:bg-token-foreground/5",
                    selected
                      ? "bg-token-foreground/10 text-token-text-primary"
                      : "text-token-text-secondary",
                  ].join(" "),
                  "aria-pressed": selected,
                  onClick: () => onSelect(account.id),
                  children: [
                    (0, e7.jsx)(CodexMuxAccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-7",
                    }),
                    (0, e7.jsxs)("span", {
                      className: "flex min-w-0 flex-col",
                      children: [
                        (0, e7.jsx)("span", {
                          className: "max-w-40 truncate text-sm font-medium",
                          children: account.planLabel
                            ? `${account.label} · ${account.planLabel}`
                            : account.label,
                        }),
                        (0, e7.jsx)("span", {
                          className: "text-xs text-token-text-tertiary",
                          children:
                            count == null
                              ? "Resets unavailable"
                              : count === 1
                                ? "1 reset available"
                                : `${count} resets available`,
                        }),
                      ],
                    }),
                  ],
                },
                account.id,
              );
            }),
      }),
    ],
  });
}

function CodexMuxAccountMenu() {
  const modalScope = Lo(Q);
  const [accounts, setAccounts] = kXc.useState(codexMuxCachedAccounts);
  const [loading, setLoading] = kXc.useState(
    () => !codexMuxCachedAccounts().some((account) => account.connected),
  );
  const [busy, setBusy] = kXc.useState(false);
  const [selectedAccountId, setSelectedAccountId] = kXc.useState("");
  const [error, setError] = kXc.useState("");
  const [login, setLogin] = kXc.useState(null);
  const [codeCopied, setCodeCopied] = kXc.useState(false);
  const [pairing, setPairing] = kXc.useState(null);
  const [expandedAccountId, setExpandedAccountId] = kXc.useState(null);
  const loginAccountId = login?.accountId || null;

  const refresh = kXc.useCallback(async () => {
    try {
      const nextAccounts = await codexMuxFetchAccounts();
      setAccounts(nextAccounts);
      const selection = await codexMuxRequest("/account-selection");
      setSelectedAccountId(selection.accountId || "");
      setError("");
      if (nextAccounts.some((account) => account.connected)) setLoading(false);
    } catch (requestError) {
      setError(requestError.message);
      setLoading(false);
    }
  }, []);

  kXc.useEffect(() => {
    refresh();
    const events = codexMuxEvents();
    events.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (
          payload.type === "account-updated" &&
          payload.accountId === loginAccountId
        ) {
          setLogin(null);
        }
        if (payload.type === "account-updated" || payload.type === "account-selection-changed") refresh();
      } catch {}
    };
    const warmupTimer = setTimeout(refresh, 2_000);
    const loadingDeadline = setTimeout(() => {
      refresh().finally(() => setLoading(false));
    }, 6_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      clearTimeout(warmupTimer);
      clearTimeout(loadingDeadline);
      clearInterval(timer);
      events.close();
    };
  }, [refresh, loginAccountId]);

  kXc.useEffect(() => {
    if (!login) return;
    const allowEscapeDismissal = (event) => {
      if (event.key !== "Escape") return;
      setLogin(null);
    };
    window.addEventListener("keydown", allowEscapeDismissal, true);
    return () => window.removeEventListener("keydown", allowEscapeDismissal, true);
  }, [login]);

  const connected = accounts.filter(
    (account) => account.connected && account.enabled,
  );
  async function addSubscription(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await codexMuxRequest("/accounts", {
        method: "POST",
        body: JSON.stringify({ label: `Subscription ${connected.length + 1}` }),
      });
      const result = await codexMuxRequest(`/accounts/${created.account.id}/login`, {
        method: "POST",
        body: JSON.stringify({ mode: "chatgptDeviceCode" }),
      });
      const pendingLogin = result.login
        ? { ...result.login, accountId: created.account.id }
        : null;
      setCodeCopied(false);
      setLogin(pendingLogin);
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function copyCodeAndContinue(event) {
    event.preventDefault();
    const userCode = login?.userCode || "";
    const verificationUrl = login?.verificationUrl || login?.authUrl || "";
    const copy = userCode
      ? navigator.clipboard.writeText(userCode)
      : Promise.resolve();
    if (verificationUrl) {
      try {
        const destination = new URL(verificationUrl);
        const trustedHost =
          destination.hostname === "chatgpt.com" ||
          destination.hostname === "auth.openai.com";
        if (destination.protocol !== "https:" || !trustedHost) {
          throw new Error("untrusted verification URL");
        }
        window.open(destination.href, "_blank", "noopener,noreferrer");
      } catch {
        setError("The sign-in verification page could not be opened safely.");
      }
    }
    try {
      await copy;
      setCodeCopied(userCode !== "");
    } catch {
      setError("The sign-in code could not be copied.");
    }
  }

  function toggleAccount(account, event) {
    event.preventDefault();
    const next = expandedAccountId === account.id ? null : account.id;
    setExpandedAccountId(next);
    if (next !== pairing?.accountId) setPairing(null);
  }

  async function switchAccount(accountId, event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await codexMuxSelectAccount(accountId);
      setSelectedAccountId(result.accountId || "");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  function viewAccountUsage(account, event) {
    event.preventDefault();
    setExpandedAccountId(null);
    BW(modalScope, CodexMuxUsageModal, { initialAccountId: account.id });
  }

  async function pairDevice(account, event) {
    event.preventDefault();
    if (pairing?.accountId === account.id && pairing.status === "loading") return;
    setPairing({ accountId: account.id, status: "loading" });
    try {
      const status = await codexMuxRemoteControlStatus(account.id);
      if (status.status !== "enabled") {
        await codexMuxEnableRemoteControl(account.id);
      }
      const code = await codexMuxStartRemoteControlPairing(account.id);
      setPairing({
        accountId: account.id,
        status: "ready",
        code: code.manualPairingCode || code.pairingCode || "",
        expiresAt: code.expiresAt ?? null,
        copied: false,
      });
    } catch (requestError) {
      setPairing({
        accountId: account.id,
        status: "error",
        message: codexMuxPairingErrorMessage(requestError.message),
      });
    }
  }

  async function copyPairingCode(event) {
    event.preventDefault();
    if (!pairing?.code) return;
    try {
      await navigator.clipboard.writeText(pairing.code);
      setPairing({ ...pairing, copied: true });
    } catch {}
  }

  const rows = [];
  rows.push(
    (0, e7.jsx)(
      _H,
      {
        LeftIcon: S2,
        SubText: loading
          ? "Connecting subscriptions…"
          : "5-hour and weekly limits by account",
        rightIcon: (0, e7.jsx)("span", {
          className: "text-token-description-foreground tabular-nums",
          children: loading
            ? "…"
            : `${connected.length} ${connected.length === 1 ? "account" : "accounts"}`,
        }),
        onSelect: () => BW(modalScope, CodexMuxUsageModal, {}),
        children: "Usage remaining",
      },
      "codex-mux-total",
    ),
  );
  if (connected.length > 0) {
    rows.push(
      (0, e7.jsx)(CH.Separator, {}, "codex-mux-accounts-separator"),
    );
  }

  for (const account of connected) {
    const fiveHour = codexMuxFiveHourWindow(account.rateLimits);
    const weekly = codexMuxWeeklyWindow(account.rateLimits);
    const fiveHourRemaining = codexMuxRemainingPercent(fiveHour);
    const weeklyRemaining = codexMuxRemainingPercent(weekly);
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: (iconProps) =>
            (0, e7.jsx)(CodexMuxAccountAvatar, {
              ...iconProps,
              imageUrl: account.profileImageUrl,
              label: account.label,
            }),
          SubText: account.email
            ? (0, e7.jsx)(CodexMuxMaskedEmail, { email: account.email })
            : account.planType || "ChatGPT subscription",
          className: "group",
          rightIcon: (0, e7.jsxs)("span", {
            className:
              "flex shrink-0 flex-col items-end text-xs leading-4 text-token-description-foreground tabular-nums",
            children: [
              (0, e7.jsx)("span", {
                children: `5h ${codexMuxPercentLabel(fiveHourRemaining)}`,
              }),
              (0, e7.jsx)("span", {
                children: `Week ${codexMuxPercentLabel(weeklyRemaining)}`,
              }),
            ],
          }),
          onSelect: (event) => toggleAccount(account, event),
          children: account.planLabel
            ? `${account.label} · ${account.planLabel}`
            : account.label,
        },
        `codex-mux-account-${account.id}`,
      ),
    );
    if (expandedAccountId === account.id) {
      rows.push((0, e7.jsx)(_H, {
        LeftIcon: CodexMuxSwitchIcon,
        SubText: "Use for new tasks and next messages this session",
        disabled: busy || selectedAccountId === account.id,
        onSelect: (event) => switchAccount(account.id, event),
        children: selectedAccountId === account.id ? "Selected account" : busy ? "Switching…" : "Switch to account",
      }, `codex-mux-account-${account.id}-select`));
      rows.push(
        (0, e7.jsx)(
          _H,
          {
            LeftIcon: CodexMuxSwitchIcon,
            SubText: account.email || "View this subscription's usage",
            onSelect: (event) => viewAccountUsage(account, event),
            children: "View account usage",
          },
          `codex-mux-account-${account.id}-switch`,
        ),
      );
      if (pairing?.accountId === account.id) {
        rows.push(codexMuxPairingRow(pairing, copyPairingCode));
      } else {
        rows.push(
          (0, e7.jsx)(
            _H,
            {
              LeftIcon: CodexMuxPlusIcon,
              SubText: "Control this Mac from a phone or another computer as this subscription",
              onSelect: (event) => pairDevice(account, event),
              children: "Pair a device…",
            },
            `codex-mux-account-${account.id}-pair`,
          ),
        );
      }
    }
  }

  if (login) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxCopyIcon,
          SubText: login.userCode
            ? codeCopied
              ? `Code ${login.userCode} copied`
              : `Code ${login.userCode} · Click to copy`
            : "Finish signing in with ChatGPT",
          onSelect: copyCodeAndContinue,
          children: "Continue sign-in",
        },
        "codex-mux-login",
      ),
    );
  }

  rows.push((0, e7.jsx)(_H, {
    LeftIcon: CodexMuxSwitchIcon,
    SubText: selectedAccountId ? "Clear the account selection" : "Choose accounts based on available usage",
    disabled: busy || !selectedAccountId,
    onSelect: (event) => switchAccount("", event),
    children: selectedAccountId ? "Use automatic routing" : "Automatic routing enabled",
  }, "codex-mux-automatic-routing"));

  if (error) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: S2,
          SubText: error,
          tone: "danger",
          allowWrap: true,
          subTextAllowWrap: true,
          children: "Account action failed",
        },
        "codex-mux-error",
      ),
    );
  }

  if (!loading) {
    rows.push(
      (0, e7.jsx)(
        _H,
        {
          LeftIcon: CodexMuxPlusIcon,
          onSelect: addSubscription,
          children: busy ? "Adding subscription…" : "Add another subscription",
        },
        "codex-mux-add",
      ),
    );
  }
  rows.push((0, e7.jsx)(CH.Separator, {}, "codex-mux-separator"));
  return (0, e7.jsx)(e7.Fragment, { children: rows });
}

function codexMuxPairingRow(pairing, onCopy) {
  if (pairing.status === "loading") {
    return (0, e7.jsx)(
      _H,
      {
        LeftIcon: CodexMuxCopyIcon,
        SubText: "Enabling remote control and requesting a code",
        children: "Pair a device…",
      },
      "codex-mux-pairing",
    );
  }
  if (pairing.status === "error") {
    return (0, e7.jsx)(
      _H,
      {
        LeftIcon: S2,
        SubText: pairing.message,
        tone: "danger",
        allowWrap: true,
        subTextAllowWrap: true,
        children: "Pairing unavailable",
      },
      "codex-mux-pairing",
    );
  }
  const expiresAt =
    pairing.expiresAt == null
      ? null
      : pairing.expiresAt < 1e12
        ? pairing.expiresAt * 1000
        : pairing.expiresAt;
  const expiry =
    expiresAt == null
      ? ""
      : ` · Expires ${new Date(expiresAt).toLocaleTimeString([], {
          hour: "numeric",
          minute: "2-digit",
        })}`;
  return (0, e7.jsx)(
    _H,
    {
      LeftIcon: CodexMuxCopyIcon,
      SubText: pairing.copied
        ? `Code ${pairing.code} copied${expiry}`
        : `Code ${pairing.code} · Click to copy${expiry}`,
      onSelect: onCopy,
      children: "Enter this code on your phone or other computer",
    },
    "codex-mux-pairing",
  );
}

function codexMuxPairingErrorMessage(message) {
  if (/multi-factor authentication required/i.test(message)) {
    return "This ChatGPT account needs multi-factor authentication before it can be controlled remotely. Turn it on at chatgpt.com under Settings › Security, then try again.";
  }
  return message;
}

function codexMuxRemainingPercent(window) {
  return window == null ? null : Math.max(0, 100 - window.usedPercent);
}

function codexMuxPercentLabel(value) {
  return value == null ? "–" : `${Math.round(value)}%`;
}

// Profile images are kept decoded between menu opens so rows render with
// their avatars on the first frame instead of after a network round-trip.
// Warming happens from the avatar component because resolving the URL is a
// React hook.
const codexMuxWarmAvatars = new Map();

function codexMuxWarmAvatar(imageUrl) {
  if (!imageUrl || codexMuxWarmAvatars.has(imageUrl)) return;
  const image = new Image();
  image.referrerPolicy = "no-referrer";
  image.decoding = "sync";
  image.src = imageUrl;
  codexMuxWarmAvatars.set(imageUrl, image);
}

function CodexMuxPlusIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsx)("path", {
      d: "M10 4.25v11.5M4.25 10h11.5",
      stroke: "currentColor",
      strokeWidth: 1.5,
      strokeLinecap: "round",
    }),
  });
}

function CodexMuxCopyIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsxs)(e7.Fragment, {
      children: [
        (0, e7.jsx)("rect", {
          x: 6.25,
          y: 6.25,
          width: 9.5,
          height: 9.5,
          rx: 2,
          stroke: "currentColor",
          strokeWidth: 1.5,
        }),
        (0, e7.jsx)("path", {
          d: "M13.75 6.25V6A1.75 1.75 0 0 0 12 4.25H6A1.75 1.75 0 0 0 4.25 6v6c0 .97.78 1.75 1.75 1.75h.25",
          stroke: "currentColor",
          strokeWidth: 1.5,
          strokeLinecap: "round",
        }),
      ],
    }),
  });
}

function CodexMuxSwitchIcon(props) {
  return (0, e7.jsx)("svg", {
    viewBox: "0 0 20 20",
    fill: "none",
    "aria-hidden": true,
    ...props,
    children: (0, e7.jsxs)(e7.Fragment, {
      children: [
        (0, e7.jsx)("path", {
          d: "M3.75 6.5h10.5m0 0-2.75-2.75M14.25 6.5l-2.75 2.75",
          stroke: "currentColor",
          strokeWidth: 1.5,
          strokeLinecap: "round",
          strokeLinejoin: "round",
        }),
        (0, e7.jsx)("path", {
          d: "M16.25 13.5H5.75m0 0 2.75 2.75M5.75 13.5l2.75-2.75",
          stroke: "currentColor",
          strokeWidth: 1.5,
          strokeLinecap: "round",
          strokeLinejoin: "round",
        }),
      ],
    }),
  });
}

function CodexMuxMaskedEmail({ email }) {
  return (0, e7.jsxs)(e7.Fragment, {
    children: [
      (0, e7.jsx)("span", {
        className: "group-hover:hidden",
        children: "••••••••",
      }),
      (0, e7.jsx)("span", {
        className: "hidden group-hover:inline",
        children: email,
      }),
    ],
  });
}

function CodexMuxAccountAvatar({ imageUrl, label, className }) {
  const [failed, setFailed] = kXc.useState(false);
  const resolvedImageUrl = jLa(imageUrl || null);
  if (resolvedImageUrl && !failed) {
    codexMuxWarmAvatar(resolvedImageUrl);
    return (0, e7.jsx)("img", {
      src: resolvedImageUrl,
      alt: "",
      className: `${className || "icon-sm"} rounded-full object-cover`,
      referrerPolicy: "no-referrer",
      decoding: "sync",
      onError: () => setFailed(true),
    });
  }
  const initials = label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (0, e7.jsx)("span", {
    className: `${className || "icon-sm"} flex items-center justify-center rounded-full bg-token-charts-purple/10 text-[9px] leading-none text-token-charts-purple`,
    "aria-hidden": true,
    children: initials || "?",
  });
}

function CodexMuxOverlappingAvatars({ accounts, size = "size-20" }) {
  const overlapClass = size === "size-20" ? "-ml-10" : "-ml-2";
  return (0, e7.jsx)("div", {
    className: "flex items-center justify-center",
    children: accounts.map((account, index) =>
      (0, e7.jsx)(
        "span",
        {
          className: `${index === 0 ? "" : overlapClass} rounded-full border-4 border-token-bg-primary`,
          title: account.planLabel
            ? `${account.label} · ${account.planLabel}`
            : account.label,
          children: (0, e7.jsx)(CodexMuxAccountAvatar, {
            imageUrl: account.profileImageUrl,
            label: account.label,
            className: size,
          }),
        },
        account.id,
      ),
    ),
  });
}

function CodexMuxProfileAvatarStack({ onSelect }) {
  const [accounts, setAccounts] = kXc.useState(
    globalThis.__codexMuxCombinedProfileAccounts || [],
  );
  const [selectedId, setSelectedId] = kXc.useState(
    globalThis.__codexMuxSelectedProfileAccountId || null,
  );
  kXc.useEffect(() => {
    let live = true;
    codexMuxRequest("/accounts")
      .then((result) => {
        if (!live) return;
        const connected = (result.accounts || []).filter(
          (account) => account.connected && account.enabled,
        );
        globalThis.__codexMuxCombinedProfileAccounts = connected;
        setAccounts(connected);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  kXc.useEffect(() => {
    globalThis.__codexMuxSelectedProfileAccountId = null;
    setSelectedId(null);
    onSelect?.();
    return () => {
      globalThis.__codexMuxSelectedProfileAccountId = null;
    };
  }, []);
  if (accounts.length === 0) return null;
  const visibleAccounts = selectedId
    ? accounts.filter((account) => account.id === selectedId)
    : accounts;
  return (0, e7.jsx)("div", {
    className: "mb-4",
    "aria-label": selectedId
      ? "Selected subscription profile"
      : `${accounts.length} connected subscriptions`,
    children: (0, e7.jsx)("div", {
      className: "mx-auto flex w-max items-center justify-center",
      children: visibleAccounts.map((account, index) =>
        (0, e7.jsx)(
          "button",
          {
            type: "button",
            className: `${index === 0 ? "" : "-ml-5"} size-20 shrink-0 rounded-full border-4 border-token-bg-primary transition-transform hover:z-10 hover:scale-105 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-token-focus-border`,
            style: {
              marginLeft: index === 0 ? 0 : -20,
              zIndex: index,
            },
            "aria-label": selectedId
              ? `Show combined profile stats`
              : `Show ${account.label} profile stats`,
            title: account.planLabel
              ? `${account.label} · ${account.planLabel}`
              : account.label,
            onClick: () => {
              const nextId = selectedId === account.id ? null : account.id;
              globalThis.__codexMuxSelectedProfileAccountId = nextId;
              setSelectedId(nextId);
              onSelect?.();
            },
            children: (0, e7.jsx)(CodexMuxAccountAvatar, {
              imageUrl: account.profileImageUrl,
              label: account.label,
              className: "size-full",
            }),
          },
          account.id,
        ),
      ),
    }),
  });
}

function CodexMuxPluginScope() {
  const cachedAccounts = codexMuxCachedAccounts().filter(
    (account) => account.connected && account.enabled,
  );
  const [accounts, setAccounts] = kXc.useState(cachedAccounts);
  const [selectedId, setSelectedId] = kXc.useState("primary");
  const [loading, setLoading] = kXc.useState(cachedAccounts.length === 0);
  const queryClient = lt();
  kXc.useEffect(() => {
    let live = true;
    codexMuxFetchAccounts()
      .then((connectedAccounts) => {
        if (!live) return;
        setAccounts(
          connectedAccounts.filter(
            (account) => account.connected && account.enabled,
          ),
        );
      })
      .catch(() => {})
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  kXc.useEffect(() => {
    globalThis.__codexMuxPluginAccountId = selectedId;
    return () => {
      delete globalThis.__codexMuxPluginAccountId;
    };
  }, [selectedId]);

  async function selectAccount(accountId) {
    if (accountId === selectedId) return;
    globalThis.__codexMuxPluginAccountId = accountId;
    setSelectedId(accountId);
    await queryClient.invalidateQueries({
      predicate: (query) => {
        const root = query.queryKey?.[0];
        return root === "apps" || root === "plugins" || root === "mcp";
      },
    });
  }

  const selected =
    accounts.find((account) => account.id === selectedId) || accounts[0] || null;

  return (0, e7.jsxs)("div", {
    className:
      "mb-5 rounded-2xl border border-token-border-light p-3",
    children: [
      (0, e7.jsxs)("div", {
        className: "px-1",
        children: [
          (0, e7.jsx)("div", {
            className: "text-sm font-medium text-token-text-primary",
            children: "Plugin connections",
          }),
          (0, e7.jsx)("div", {
            className: "mt-0.5 text-xs text-token-text-secondary",
            children: selected
              ? `Installs are shared. Connection access below is for ${selected.label}.`
              : "Installs are shared. Choose a subscription for connection access.",
          }),
        ],
      }),
      loading
        ? (0, e7.jsx)("div", {
            className: "mt-3 px-1 text-sm text-token-text-tertiary",
            children: "Loading subscriptions…",
          })
        : (0, e7.jsx)("div", {
            className: "mt-3 flex flex-wrap gap-2",
            children: accounts.map((account) => {
              const active = account.id === selected?.id;
              return (0, e7.jsxs)(
                "button",
                {
                  type: "button",
                  className: [
                    "flex items-center gap-2 rounded-xl px-2.5 py-2 text-sm transition-colors",
                    active
                      ? "bg-token-foreground/10 text-token-text-primary"
                      : "text-token-text-secondary hover:bg-token-foreground/5",
                  ].join(" "),
                  "aria-pressed": active,
                  onClick: () => selectAccount(account.id),
                  children: [
                    (0, e7.jsx)(CodexMuxAccountAvatar, {
                      imageUrl: account.profileImageUrl,
                      label: account.label,
                      className: "size-7",
                    }),
                    (0, e7.jsx)("span", {
                      children: account.planLabel
                        ? `${account.label} · ${account.planLabel}`
                        : account.label,
                    }),
                  ],
                },
                account.id,
              );
            }),
          }),
    ],
  });
}

// The Profile, Plugins, and thread summary surfaces render from other bundles
// and share the avatar component's image resolution and initials fallback.
globalThis.CodexMuxAccountAvatar = CodexMuxAccountAvatar;
globalThis.CodexMuxProfileAvatarStack = (props) =>
  (0, e7.jsx)(CodexMuxProfileAvatarStack, props || {});
globalThis.CodexMuxPluginScope = () =>
  (0, e7.jsx)(CodexMuxPluginScope, {});
