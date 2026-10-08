package backend

import "testing"

func TestSQLiteHistoryOverrideIsPrimaryOnly(t *testing.T) {
	env := []string{"CODEX_MUX_PRIMARY_SQLITE_HOME=/official"}
	if got := sqliteHome("primary", "/router", env); got != "/official" {
		t.Fatalf("primary history moved: %q", got)
	}
	if got := sqliteHome("secondary", "/secondary", env); got != "/secondary" {
		t.Fatalf("secondary history escaped its account: %q", got)
	}
	if got := sqliteHome("primary", "/custom", nil); got != "/custom" {
		t.Fatalf("default SQLite home changed: %q", got)
	}
}
