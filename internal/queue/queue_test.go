package queue

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

// A taken message is hidden until its hold lapses, an extended hold keeps
// it hidden, a stale receipt neither extends nor deletes, and a deleted
// message is gone for good.
func TestMemoryQueue(t *testing.T) {
	now := time.Date(2026, 9, 21, 8, 0, 0, 0, time.UTC)
	q := NewMemory()
	q.SetClock(func() time.Time { return now })
	ctx := context.Background()
	if _, ok, _ := q.Receive(ctx, time.Minute); ok {
		t.Fatal("an empty queue gives nothing")
	}
	q.Enqueue(ctx, "job-1")
	q.Enqueue(ctx, "job-2")
	first, ok, err := q.Receive(ctx, time.Minute)
	if err != nil || !ok || first.Body != "job-1" || first.Dequeued != 1 {
		t.Fatalf("first: %+v %v %v", first, ok, err)
	}
	second, ok, _ := q.Receive(ctx, time.Minute)
	if !ok || second.Body != "job-2" {
		t.Fatalf("second: %+v", second)
	}
	if _, ok, _ := q.Receive(ctx, time.Minute); ok {
		t.Fatal("both are held")
	}
	// The first hold lapses; the message comes back, counted again.
	now = now.Add(61 * time.Second)
	again, ok, _ := q.Receive(ctx, time.Minute)
	if !ok || again.Body != "job-1" || again.Dequeued != 2 || again.Receipt == first.Receipt {
		t.Fatalf("returned message: %+v", again)
	}
	if _, err := q.Extend(ctx, first, time.Minute); !errors.Is(err, ErrStale) {
		t.Fatalf("the old receipt is stale: %v", err)
	}
	if err := q.Delete(ctx, first); !errors.Is(err, ErrStale) {
		t.Fatalf("the old receipt cannot delete: %v", err)
	}
	// An extended hold outlives the original visibility.
	renewed, err := q.Extend(ctx, again, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	// The renewal gave a new receipt, and the one before it stops working.
	if renewed.Receipt == again.Receipt {
		t.Fatal("a renewed hold kept its receipt")
	}
	if err := q.Delete(ctx, again); !errors.Is(err, ErrStale) {
		t.Fatalf("the receipt from before the renewal deleted the message: %v", err)
	}
	again = renewed
	now = now.Add(5 * time.Minute)
	// The extended message stays hidden; the second, whose minute lapsed, is
	// the only one given out.
	if m, ok, _ := q.Receive(ctx, time.Hour); !ok || m.Body != "job-2" {
		t.Fatalf("after the extension: %+v %v", m, ok)
	}
	if _, ok, _ := q.Receive(ctx, time.Minute); ok {
		t.Fatal("both are held again")
	}
	if err := q.Delete(ctx, again); err != nil {
		t.Fatal(err)
	}
	if err := q.Delete(ctx, again); err != nil {
		t.Fatalf("deleting twice is harmless: %v", err)
	}
	if q.Len() != 1 {
		t.Fatalf("len %d", q.Len())
	}
	now = now.Add(2 * time.Hour)
	if m, ok, _ := q.Receive(ctx, time.Minute); !ok || m.Body != "job-2" || m.Dequeued != 3 {
		t.Fatalf("only the second remains: %+v %v", m, ok)
	}
}

// Against an Azure Storage Queue when a connection string is given.
func TestAzureQueue(t *testing.T) {
	connection := os.Getenv("TRACEN_TEST_AZURE_STORAGE")
	if connection == "" {
		t.Skip("set TRACEN_TEST_AZURE_STORAGE to an Azure Storage connection string")
	}
	ctx := context.Background()
	q, err := NewAzureFromConnectionString(ctx, connection, "tracen-test-analyses")
	if err != nil {
		t.Fatal(err)
	}
	for {
		m, ok, err := q.Receive(ctx, time.Second)
		if err != nil {
			t.Fatal(err)
		}
		if !ok {
			break
		}
		q.Delete(ctx, m)
	}
	if err := q.Enqueue(ctx, "job-a"); err != nil {
		t.Fatal(err)
	}
	m, ok, err := q.Receive(ctx, 30*time.Second)
	if err != nil || !ok || m.Body != "job-a" || m.Receipt == "" {
		t.Fatalf("receive: %+v %v %v", m, ok, err)
	}
	if _, ok, _ := q.Receive(ctx, 30*time.Second); ok {
		t.Fatal("a held message is not given twice")
	}
	extended, err := q.Extend(ctx, m, 30*time.Second)
	if err != nil || extended.Receipt == m.Receipt {
		t.Fatalf("extend: %+v %v", extended, err)
	}
	if _, err := q.Extend(ctx, m, 30*time.Second); !errors.Is(err, ErrStale) {
		t.Fatalf("the old receipt is stale: %v", err)
	}
	if err := q.Delete(ctx, extended); err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := q.Receive(ctx, time.Second); ok {
		t.Fatal("deleted")
	}
}
