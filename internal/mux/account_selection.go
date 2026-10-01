package mux

import (
	"context"
	"fmt"
)

// SelectedAccount is a session preference; empty means automatic routing.
func (m *Multiplexer) SelectedAccount() string {
	m.selectionMu.RLock()
	defer m.selectionMu.RUnlock()
	return m.selectedAccountID
}

func (m *Multiplexer) SelectAccount(ctx context.Context, accountID string) error {
	if accountID != "" {
		snapshot, err := m.routingSnapshot(ctx, accountID)
		if err != nil {
			return err
		}
		if !accountHasCapacity(snapshot) {
			return fmt.Errorf("This subscription is disconnected, disabled, or out of usage. Choose another account.")
		}
	}
	m.selectionMu.Lock()
	m.selectedAccountID = accountID
	m.selectionMu.Unlock()
	m.publish(Event{Type: "account-selection-changed", AccountID: accountID})
	return nil
}
