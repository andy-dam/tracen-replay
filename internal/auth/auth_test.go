package auth

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"
)

type memoryStore struct {
	mu       sync.Mutex
	users    map[string]User
	hashes   map[string]string
	sessions map[string]session
}

type session struct {
	userID  string
	expires time.Time
}

func newMemoryStore() *memoryStore {
	return &memoryStore{users: map[string]User{}, hashes: map[string]string{}, sessions: map[string]session{}}
}

func (m *memoryStore) CreateUser(ctx context.Context, user User, hash string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.users[user.ID] = user
	m.hashes[user.ID] = hash
	return nil
}

func (m *memoryStore) UserByEmail(ctx context.Context, email string) (User, string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, u := range m.users {
		if u.Email == email {
			return u, m.hashes[u.ID], nil
		}
	}
	return User{}, "", ErrNotFound
}

func (m *memoryStore) UserByID(ctx context.Context, id string) (User, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	u, ok := m.users[id]
	if !ok {
		return User{}, ErrNotFound
	}
	return u, nil
}

func (m *memoryStore) CreateSession(ctx context.Context, tokenHash, userID string, createdAt, expiresAt time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.sessions[tokenHash] = session{userID: userID, expires: expiresAt}
	return nil
}

func (m *memoryStore) SessionUser(ctx context.Context, tokenHash string, now time.Time) (User, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.sessions[tokenHash]
	if !ok || !now.Before(s.expires) {
		return User{}, ErrNotFound
	}
	return m.users[s.userID], nil
}

func (m *memoryStore) DeleteSession(ctx context.Context, tokenHash string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.sessions[tokenHash]; !ok {
		return ErrNotFound
	}
	delete(m.sessions, tokenHash)
	return nil
}

func TestPasswordHashesVerifyAndDoNotRepeat(t *testing.T) {
	a, err := HashPassword("correct horse battery")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := HashPassword("correct horse battery")
	if a == b {
		t.Fatal("two hashes of one password must differ (random salt)")
	}
	if !VerifyPassword(a, "correct horse battery") || VerifyPassword(a, "correct horse batter") {
		t.Fatal("verification is wrong")
	}
	if VerifyPassword("garbage", "x") || VerifyPassword("pbkdf2-sha256$1$!!$!!", "x") {
		t.Fatal("malformed hashes must not verify")
	}
	if !strings.HasPrefix(a, "pbkdf2-sha256$600000$") {
		t.Fatalf("unexpected hash format %s", a)
	}
}

func TestRegisterValidatesAndRejectsDuplicates(t *testing.T) {
	svc := New(newMemoryStore())
	ctx := context.Background()
	if _, err := svc.Register(ctx, "not-an-email", "longenough", "Andy"); !errors.Is(err, ErrInvalidEmail) {
		t.Fatalf("want invalid email, got %v", err)
	}
	if _, err := svc.Register(ctx, "a@example.com", "short", "Andy"); !errors.Is(err, ErrWeakPassword) {
		t.Fatalf("want weak password, got %v", err)
	}
	if _, err := svc.Register(ctx, "a@example.com", "longenough", "   "); !errors.Is(err, ErrInvalidName) {
		t.Fatalf("want invalid name, got %v", err)
	}
	user, err := svc.Register(ctx, " A@Example.com ", "longenough", "Andy")
	if err != nil {
		t.Fatal(err)
	}
	if user.Email != "a@example.com" || user.ID == "" {
		t.Fatalf("unexpected user %+v", user)
	}
	if _, err := svc.Register(ctx, "a@example.com", "longenough", "Again"); !errors.Is(err, ErrEmailTaken) {
		t.Fatalf("want email taken, got %v", err)
	}
}

func TestLoginIssuesSessionsThatExpireAndLogOut(t *testing.T) {
	store := newMemoryStore()
	svc := New(store)
	now := time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC)
	svc.SetClock(func() time.Time { return now })
	ctx := context.Background()
	if _, err := svc.Register(ctx, "a@example.com", "longenough", "Andy"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := svc.Login(ctx, "a@example.com", "wrong password", "127.0.0.1"); !errors.Is(err, ErrBadCredentials) {
		t.Fatalf("want bad credentials, got %v", err)
	}
	if _, _, err := svc.Login(ctx, "nobody@example.com", "longenough", "127.0.0.1"); !errors.Is(err, ErrBadCredentials) {
		t.Fatalf("an unknown address must look like a wrong password, got %v", err)
	}
	user, token, err := svc.Login(ctx, "A@example.com", "longenough", "127.0.0.1")
	if err != nil || token == "" {
		t.Fatalf("login failed: %v", err)
	}
	if _, ok := store.sessions[token]; ok {
		t.Fatal("the raw token must not be stored")
	}
	got, err := svc.Authenticate(ctx, token)
	if err != nil || got.ID != user.ID {
		t.Fatalf("authenticate: %v %+v", err, got)
	}
	if _, err := svc.Authenticate(ctx, "not-a-token"); !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("want unauthenticated, got %v", err)
	}
	now = now.Add(31 * 24 * time.Hour)
	if _, err := svc.Authenticate(ctx, token); !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("an expired session must not authenticate, got %v", err)
	}
	now = now.Add(-31 * 24 * time.Hour)
	if err := svc.Logout(ctx, token); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Authenticate(ctx, token); !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("a logged-out session must not authenticate, got %v", err)
	}
	if err := svc.Logout(ctx, token); err != nil {
		t.Fatalf("logging out twice is not an error, got %v", err)
	}
}

func TestSignInAttemptsAreLimitedPerAddress(t *testing.T) {
	svc := New(newMemoryStore())
	now := time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC)
	svc.SetClock(func() time.Time { return now })
	ctx := context.Background()
	svc.Register(ctx, "a@example.com", "longenough", "Andy")
	for i := 0; i < 10; i++ {
		if _, _, err := svc.Login(ctx, "a@example.com", "wrong", "10.0.0.1"); !errors.Is(err, ErrBadCredentials) {
			t.Fatalf("attempt %d: %v", i, err)
		}
	}
	if _, _, err := svc.Login(ctx, "a@example.com", "longenough", "10.0.0.1"); !errors.Is(err, ErrTooManyAttempts) {
		t.Fatalf("the eleventh attempt must be limited, got %v", err)
	}
	if _, _, err := svc.Login(ctx, "a@example.com", "longenough", "10.0.0.2"); err != nil {
		t.Fatalf("another address is not limited: %v", err)
	}
	now = now.Add(6 * time.Minute)
	if _, _, err := svc.Login(ctx, "a@example.com", "longenough", "10.0.0.1"); err != nil {
		t.Fatalf("the window has passed: %v", err)
	}
}
