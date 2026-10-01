package mux

import (
	"context"
	"encoding/json"
	"github.com/b-nnett/codex-subscription-router/internal/state"
	"io"
	"path/filepath"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func TestIsUsageLimitResponseRecognizesStructuredError(t *testing.T) {
	message := protocol.Message{Error: &protocol.RPCError{
		Code:    -32000,
		Message: "turn failed",
		Data:    json.RawMessage(`{"codexErrorInfo":"usage_limit_exceeded"}`),
	}}
	if !isUsageLimitResponse(message) {
		t.Fatal("expected usage-limit error to be recognized")
	}
}

func TestIsUsageLimitResponseIgnoresUnrelatedError(t *testing.T) {
	message := protocol.Message{Error: &protocol.RPCError{
		Code:    -32000,
		Message: "workspace folder is unavailable",
	}}
	if isUsageLimitResponse(message) {
		t.Fatal("unrelated error was misclassified as a usage limit")
	}
}

func TestAllSubscriptionsDepletedUsesActionableMessage(t *testing.T) {
	message := allSubscriptionsDepleted(json.RawMessage(`7`), nil)
	if message.Error == nil || message.Error.Code != -32026 {
		t.Fatalf("unexpected error response: %#v", message)
	}
	if message.Error.Message != "All connected subscriptions are depleted. Add another subscription or wait for usage to reset." {
		t.Fatalf("unexpected depletion message: %q", message.Error.Message)
	}
}

func TestAllSubscriptionsDepletedShowsKnownResetTime(t *testing.T) {
	reset := time.Date(2026, time.August, 16, 10, 30, 0, 0, time.Local).Unix()
	message := allSubscriptionsDepleted(json.RawMessage(`7`), &reset)
	if message.Error == nil {
		t.Fatal("expected an error response")
	}
	want := "All connected subscriptions are depleted. Usage resets on Sunday, 16 August at 10:30 AM."
	if message.Error.Message != want {
		t.Fatalf("unexpected reset message: %q", message.Error.Message)
	}
}

func TestCapacityRequiresBothQuotaWindows(t *testing.T) {
	for _, used := range []float64{99, 100, 101} {
		snapshot := AccountSnapshot{Enabled: true, Connected: true, AuthType: "chatgpt", RateLimits: &RateLimits{
			Primary: &RateLimitWindow{UsedPercent: used}, Secondary: &RateLimitWindow{UsedPercent: 20},
		}}
		if got := accountHasCapacity(snapshot); got != (used < 100) {
			t.Errorf("short usage %v: capacity=%v", used, got)
		}
		limits, err := aggregateRateLimits([]AccountSnapshot{snapshot})
		if err != nil {
			t.Fatal(err)
		}
		if got := limits.RateLimitReachedType != nil; got != (used >= 100) {
			t.Errorf("short usage %v: pool depleted=%v", used, got)
		}
	}
}

func TestChooseAccountSkipsExhaustedShortWindowDespiteEarlierWeeklyReset(t *testing.T) {
	root := t.TempDir()
	store, err := state.Open(filepath.Join(root, "mux"), filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	other, err := store.AddAccount("Other")
	if err != nil {
		t.Fatal(err)
	}
	m, err := New(Options{RealExecutable: "unused", Store: store, Output: io.Discard})
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	shortMinutes, weeklyMinutes := int64(300), int64(10080)
	for _, account := range store.Accounts() {
		used := 10.0
		reset := now.Add(6 * 24 * time.Hour).Unix()
		if account.ID == "primary" {
			used = 100
			reset = now.Add(time.Hour).Unix()
		}
		m.snapshots.put(AccountSnapshot{ID: account.ID, Enabled: true, Connected: true, AuthType: "chatgpt", RateLimits: &RateLimits{
			Primary:   &RateLimitWindow{UsedPercent: used, WindowDurationMins: &shortMinutes},
			Secondary: &RateLimitWindow{UsedPercent: 20, WindowDurationMins: &weeklyMinutes, ResetsAt: &reset},
		}}, now)
		m.cacheResetCreditMetadata(account.ID, resetCreditMetadata{Known: true}, time.Hour)
	}
	chosen, _, err := m.chooseAccount(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if chosen.ID != other.ID {
		t.Fatalf("selected exhausted account %s", chosen.ID)
	}
}
