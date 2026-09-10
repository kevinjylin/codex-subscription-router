package mux

import (
	"bytes"
	"context"
	"io"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func selectionFixture(t *testing.T) (*Multiplexer, string) {
	t.Helper()
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
	for _, account := range store.Accounts() {
		m.snapshots.put(AccountSnapshot{ID: account.ID, Enabled: true, Connected: true, AuthType: "chatgpt", RateLimits: &RateLimits{Primary: &RateLimitWindow{UsedPercent: 20}}}, m.now())
		m.cacheResetCreditMetadata(account.ID, resetCreditMetadata{Known: true}, time.Hour)
	}
	return m, other.ID
}

func TestManualSelectionOverridesScoringAndCanBeCleared(t *testing.T) {
	m, other := selectionFixture(t)
	ctx := context.Background()
	events, cancel := m.SubscribeEvents()
	defer cancel()
	if err := m.SelectAccount(ctx, other); err != nil {
		t.Fatal(err)
	}
	if event := <-events; event.Type != "account-selection-changed" || event.AccountID != other {
		t.Fatalf("wrong event: %#v", event)
	}
	chosen, _, err := m.chooseAccount(ctx)
	if err != nil || chosen.ID != other {
		t.Fatalf("selection ignored: %s %v", chosen.ID, err)
	}
	chosen, _, err = m.chooseAccountExcluding(ctx, map[string]struct{}{other: {}})
	if err != nil || chosen.ID != "primary" {
		t.Fatalf("exclusion ignored: %s %v", chosen.ID, err)
	}
	if err := m.SelectAccount(ctx, ""); err != nil {
		t.Fatal(err)
	}
	if m.SelectedAccount() != "" {
		t.Fatal("selection was not cleared")
	}
}

func TestSelectionRejectsUnavailableAccountsAndFailsOverWhenDepleted(t *testing.T) {
	m, other := selectionFixture(t)
	ctx := context.Background()
	if err := m.SelectAccount(ctx, other); err != nil {
		t.Fatal(err)
	}
	if err := m.SelectAccount(ctx, "unknown"); err == nil {
		t.Fatal("accepted unknown account")
	}
	if m.SelectedAccount() != other {
		t.Fatal("failed switch changed selection")
	}
	m.snapshots.updateRateLimits(other, RateLimits{Primary: &RateLimitWindow{UsedPercent: 100}, Secondary: &RateLimitWindow{UsedPercent: 20}}, m.now())
	chosen, _, err := m.chooseAccount(ctx)
	if err != nil || chosen.ID != "primary" {
		t.Fatalf("depleted selection prevented failover: %s %v", chosen.ID, err)
	}
	if err := m.SelectAccount(ctx, other); err == nil {
		t.Fatal("accepted depleted account")
	}
}

func TestFailedManualMoveDoesNotChangeThreadOwner(t *testing.T) {
	m, other := selectionFixture(t)
	if err := m.store.SetThreadOwner("thread", "primary"); err != nil {
		t.Fatal(err)
	}
	if err := m.SelectAccount(context.Background(), other); err != nil {
		t.Fatal(err)
	}
	// No source backend: moving must fail without silently routing on the old account.
	output := &bytes.Buffer{}
	m.output = output
	m.routeTurnStart(protocol.Request("turn/start", protocol.StringID("1"), nil), "thread", "primary")
	if owner, _ := m.store.ThreadOwner("thread"); owner != "primary" {
		t.Fatal("failed move changed owner")
	}
	if !strings.Contains(output.String(), "Cannot switch this task") {
		t.Fatalf("missing move error: %s", output.String())
	}
}
