// Package queue is the line of analyses waiting for a worker: in memory
// inside one process, or an Azure Storage Queue that outlives every process
// and hands a job to whichever worker asks first. A message is a job id. A
// worker that takes one keeps it invisible to the others while it works,
// extends that hold as the analysis runs, and deletes it when done; a
// worker that dies lets the hold lapse and the message come back.
package queue

import (
	"context"
	"errors"
	"sync"
	"time"
)

// Message is one taken job: its body (the job id) and the receipt that
// lets the taker extend or delete it.
type Message struct {
	ID      string
	Receipt string
	Body    string
	// Dequeued counts how many times the message has been taken; a job that
	// keeps coming back is broken, not unlucky.
	Dequeued int
}

// ErrStale is returned when a receipt no longer matches the message: it was
// taken again by someone else after the hold lapsed.
var ErrStale = errors.New("queue: the message was taken again")

// Queue is what the API enqueues to and a worker takes from.
type Queue interface {
	Enqueue(ctx context.Context, body string) error
	// Receive takes one message and hides it for visibility; ok is false
	// when the queue is empty.
	Receive(ctx context.Context, visibility time.Duration) (m Message, ok bool, err error)
	// Extend keeps a taken message hidden for another visibility and
	// returns it with its new receipt.
	Extend(ctx context.Context, m Message, visibility time.Duration) (Message, error)
	// Delete removes a taken message.
	Delete(ctx context.Context, m Message) error
}

// Memory is the queue of one process: what the local application uses.
type Memory struct {
	mu       sync.Mutex
	next     int
	messages []*memoryMessage
	clock    func() time.Time
}

type memoryMessage struct {
	id, body, receipt string
	dequeued, renewed int
	visibleAt         time.Time
}

// NewMemory returns an empty in-process queue.
func NewMemory() *Memory {
	return &Memory{clock: time.Now}
}

// SetClock replaces the clock (tests).
func (q *Memory) SetClock(clock func() time.Time) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.clock = clock
}

func (q *Memory) Enqueue(ctx context.Context, body string) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.next++
	q.messages = append(q.messages, &memoryMessage{id: itoa(q.next), body: body, visibleAt: q.clock()})
	return nil
}

func (q *Memory) Receive(ctx context.Context, visibility time.Duration) (Message, bool, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	now := q.clock()
	for _, m := range q.messages {
		if m.visibleAt.After(now) {
			continue
		}
		m.dequeued++
		m.receipt = itoa(q.next + m.dequeued*1_000_003)
		m.visibleAt = now.Add(visibility)
		return Message{ID: m.id, Receipt: m.receipt, Body: m.body, Dequeued: m.dequeued}, true, nil
	}
	return Message{}, false, nil
}

func (q *Memory) Extend(ctx context.Context, m Message, visibility time.Duration) (Message, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	for _, held := range q.messages {
		if held.id == m.ID {
			if held.receipt != m.Receipt {
				return Message{}, ErrStale
			}
			// A renewed hold has a new receipt and the old one stops
			// working, as on an Azure queue: a caller that keeps using the
			// receipt it took the message with fails here as it would there.
			held.renewed++
			held.receipt = itoa(q.next + held.dequeued*1_000_003 + held.renewed*7_919)
			held.visibleAt = q.clock().Add(visibility)
			m.Receipt = held.receipt
			return m, nil
		}
	}
	return Message{}, ErrStale
}

func (q *Memory) Delete(ctx context.Context, m Message) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	for i, held := range q.messages {
		if held.id == m.ID {
			if held.receipt != m.Receipt {
				return ErrStale
			}
			q.messages = append(q.messages[:i], q.messages[i+1:]...)
			return nil
		}
	}
	return nil
}

// Len counts the messages, visible or held (tests).
func (q *Memory) Len() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.messages)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var digits []byte
	for n > 0 {
		digits = append([]byte{byte('0' + n%10)}, digits...)
		n /= 10
	}
	return string(digits)
}
