package backend

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
	"time"
)

const oversizedReply = 80 << 20

// A subscription holding a chat with a large history answered one request
// with a single line past the old 64 MB limit; the reader gave up and every
// later request to that subscription waited forever.
func TestRequestReceivesReplyLargerThanOldLineLimit(t *testing.T) {
	if os.Getenv("CODEX_MUX_FAKE_APP_SERVER") == "1" {
		answerWithOversizedReply()
		os.Exit(0)
	}
	child, err := Start(
		"test", t.TempDir(), os.Args[0], []string{"-test.run=^TestRequestReceivesReplyLargerThanOldLineLimit$"},
		append(os.Environ(), "CODEX_MUX_FAKE_APP_SERVER=1"), make(chan Inbound, 1),
	)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = child.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	reply, err := child.Request(ctx, "thread/read", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(reply.Result) < oversizedReply {
		t.Fatalf("result has %d bytes", len(reply.Result))
	}
}

// answerWithOversizedReply answers the first request and then, like a real
// app-server, stays up until the router closes its stdin.
func answerWithOversizedReply() {
	stdin := bufio.NewReader(os.Stdin)
	line, _ := stdin.ReadBytes('\n')
	var request struct {
		ID json.RawMessage `json:"id"`
	}
	_ = json.Unmarshal(line, &request)
	writer := bufio.NewWriter(os.Stdout)
	fmt.Fprintf(writer, `{"id":%s,"result":"%s"}`+"\n", request.ID, strings.Repeat("x", oversizedReply))
	_ = writer.Flush()
	_, _ = io.Copy(io.Discard, stdin)
}

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
