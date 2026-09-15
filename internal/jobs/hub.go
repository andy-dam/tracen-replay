package jobs

import (
	"sync"
	"time"
)

// Event is a job progress notification for a browser subscriber.
type Event struct {
	JobID    string    `json:"job_id"`
	Status   Status    `json:"status"`
	Stage    string    `json:"stage,omitempty"`
	Percent  *float64  `json:"percent,omitempty"`
	Terminal bool      `json:"terminal"`
	At       time.Time `json:"at"`
}

// Hub fans job events out to subscribers. A slow subscriber loses events
// rather than stalling the worker loop; the job record stays authoritative.
type Hub struct {
	mu   sync.Mutex
	subs map[string]map[chan Event]struct{}
}

// NewHub creates an empty hub.
func NewHub() *Hub { return &Hub{subs: map[string]map[chan Event]struct{}{}} }

// Subscribe returns a channel of events for one job and a function that
// ends the subscription.
func (h *Hub) Subscribe(jobID string) (<-chan Event, func()) {
	ch := make(chan Event, 64)
	h.mu.Lock()
	if h.subs[jobID] == nil {
		h.subs[jobID] = map[chan Event]struct{}{}
	}
	h.subs[jobID][ch] = struct{}{}
	h.mu.Unlock()
	var once sync.Once
	return ch, func() {
		once.Do(func() {
			h.mu.Lock()
			delete(h.subs[jobID], ch)
			if len(h.subs[jobID]) == 0 {
				delete(h.subs, jobID)
			}
			h.mu.Unlock()
			close(ch)
		})
	}
}

// Publish delivers an event to every subscriber of its job without blocking.
func (h *Hub) Publish(e Event) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for ch := range h.subs[e.JobID] {
		select {
		case ch <- e:
		default:
		}
	}
}
