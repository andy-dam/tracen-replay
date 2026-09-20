// Package auth holds user accounts and browser sessions for the service.
// Passwords are hashed with PBKDF2-HMAC-SHA256 from the standard library;
// sessions are random tokens whose SHA-256 is stored, so a copy of the
// database cannot be replayed as a login.
package auth

import (
	"context"
	"crypto/hmac"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/mail"
	"strconv"
	"strings"
	"sync"
	"time"
)

// User is an account as the API exposes it; the password hash never leaves
// the store.
type User struct {
	ID          string    `json:"id"`
	Email       string    `json:"email"`
	DisplayName string    `json:"display_name"`
	CreatedAt   time.Time `json:"created_at"`
}

// Store is the durable record of users and sessions.
type Store interface {
	CreateUser(ctx context.Context, user User, passwordHash string) error
	UserByEmail(ctx context.Context, email string) (User, string, error)
	UserByID(ctx context.Context, id string) (User, error)
	CreateSession(ctx context.Context, tokenHash, userID string, createdAt, expiresAt time.Time) error
	SessionUser(ctx context.Context, tokenHash string, now time.Time) (User, error)
	DeleteSession(ctx context.Context, tokenHash string) error
}

// Errors the API translates into status codes.
var (
	ErrInvalidEmail    = errors.New("enter a valid email address")
	ErrWeakPassword    = errors.New("use a password of at least 8 characters")
	ErrInvalidName     = errors.New("enter a display name of 1 to 60 characters")
	ErrEmailTaken      = errors.New("an account with this email already exists")
	ErrBadCredentials  = errors.New("email or password is incorrect")
	ErrTooManyAttempts = errors.New("too many sign-in attempts; wait a few minutes")
	ErrUnauthenticated = errors.New("sign in to continue")
	ErrNotFound        = errors.New("not found")
)

const (
	iterations = 600_000 // OWASP's 2023 figure for PBKDF2-HMAC-SHA256
	saltBytes  = 16
	keyBytes   = 32
	tokenBytes = 32
)

// HashPassword derives a storable hash: "pbkdf2-sha256$iterations$salt$key".
func HashPassword(password string) (string, error) {
	salt := make([]byte, saltBytes)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key, err := pbkdf2.Key(sha256.New, password, salt, iterations, keyBytes)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("pbkdf2-sha256$%d$%s$%s", iterations, base64.RawStdEncoding.EncodeToString(salt), base64.RawStdEncoding.EncodeToString(key)), nil
}

// VerifyPassword reports whether password matches a hash from HashPassword.
func VerifyPassword(hash, password string) bool {
	parts := strings.Split(hash, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2-sha256" {
		return false
	}
	rounds, err := strconv.Atoi(parts[1])
	if err != nil || rounds < 1 {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[2])
	if err != nil {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[3])
	if err != nil {
		return false
	}
	got, err := pbkdf2.Key(sha256.New, password, salt, rounds, len(want))
	if err != nil {
		return false
	}
	return hmac.Equal(got, want)
}

// Service registers users and issues sessions.
type Service struct {
	store   Store
	clock   func() time.Time
	ttl     time.Duration
	limiter *Limiter
}

// New builds a service with 30-day sessions and a sign-in limiter of ten
// attempts per address per five minutes.
func New(store Store) *Service {
	return &Service{store: store, clock: time.Now, ttl: 30 * 24 * time.Hour, limiter: NewLimiter(10, 5*time.Minute)}
}

// SetClock replaces the clock (tests).
func (s *Service) SetClock(clock func() time.Time) {
	s.clock = clock
	s.limiter.clock = clock
}

// TTL is the session lifetime.
func (s *Service) TTL() time.Duration { return s.ttl }

// NormalizeEmail lowercases and trims an address.
func NormalizeEmail(email string) string {
	return strings.ToLower(strings.TrimSpace(email))
}

// Register creates an account. The first account is not special: every user
// sees only their own recordings and jobs.
func (s *Service) Register(ctx context.Context, email, password, displayName string) (User, error) {
	email = NormalizeEmail(email)
	if len(email) > 254 {
		return User{}, ErrInvalidEmail
	}
	if parsed, err := mail.ParseAddress(email); err != nil || parsed.Address != email || !strings.Contains(email, "@") {
		return User{}, ErrInvalidEmail
	}
	if len(password) < 8 || len(password) > 256 {
		return User{}, ErrWeakPassword
	}
	displayName = strings.TrimSpace(displayName)
	if displayName == "" || len([]rune(displayName)) > 60 {
		return User{}, ErrInvalidName
	}
	if _, _, err := s.store.UserByEmail(ctx, email); err == nil {
		return User{}, ErrEmailTaken
	} else if !errors.Is(err, ErrNotFound) {
		return User{}, err
	}
	hash, err := HashPassword(password)
	if err != nil {
		return User{}, err
	}
	user := User{ID: randomHex(16), Email: email, DisplayName: displayName, CreatedAt: s.clock()}
	if err := s.store.CreateUser(ctx, user, hash); err != nil {
		return User{}, err
	}
	return user, nil
}

// Login checks the credentials and returns the user with a new session
// token. The token is shown to the browser once; only its hash is stored.
func (s *Service) Login(ctx context.Context, email, password, address string) (User, string, error) {
	if !s.limiter.Allow(address) {
		return User{}, "", ErrTooManyAttempts
	}
	email = NormalizeEmail(email)
	user, hash, err := s.store.UserByEmail(ctx, email)
	if err != nil {
		if errors.Is(err, ErrNotFound) {
			// Spend the same time as a real check so the response does not
			// reveal whether the address exists.
			VerifyPassword("pbkdf2-sha256$600000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", password)
			return User{}, "", ErrBadCredentials
		}
		return User{}, "", err
	}
	if !VerifyPassword(hash, password) {
		return User{}, "", ErrBadCredentials
	}
	token, err := s.issue(ctx, user.ID)
	if err != nil {
		return User{}, "", err
	}
	s.limiter.Reset(address)
	return user, token, nil
}

func (s *Service) issue(ctx context.Context, userID string) (string, error) {
	raw := make([]byte, tokenBytes)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	token := base64.RawURLEncoding.EncodeToString(raw)
	now := s.clock()
	if err := s.store.CreateSession(ctx, TokenHash(token), userID, now, now.Add(s.ttl)); err != nil {
		return "", err
	}
	return token, nil
}

// Authenticate resolves a session token to its user.
func (s *Service) Authenticate(ctx context.Context, token string) (User, error) {
	if token == "" {
		return User{}, ErrUnauthenticated
	}
	user, err := s.store.SessionUser(ctx, TokenHash(token), s.clock())
	if err != nil {
		if errors.Is(err, ErrNotFound) {
			return User{}, ErrUnauthenticated
		}
		return User{}, err
	}
	return user, nil
}

// Logout ends a session; an unknown token is not an error.
func (s *Service) Logout(ctx context.Context, token string) error {
	if token == "" {
		return nil
	}
	err := s.store.DeleteSession(ctx, TokenHash(token))
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	return err
}

// TokenHash is the stored form of a session token.
func TokenHash(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

// Limiter allows a bounded number of attempts per key inside a sliding
// window: sign-ins per address, registrations per address, frame requests
// per user. Keys that fall silent are forgotten as they are next seen, so
// the map holds only what the window covers.
type Limiter struct {
	mu       sync.Mutex
	limit    int
	window   time.Duration
	clock    func() time.Time
	attempts map[string][]time.Time
}

// NewLimiter allows limit attempts per key in every window.
func NewLimiter(limit int, window time.Duration) *Limiter {
	return &Limiter{limit: limit, window: window, clock: time.Now, attempts: map[string][]time.Time{}}
}

// SetClock replaces the clock (tests).
func (l *Limiter) SetClock(clock func() time.Time) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.clock = clock
}

// Allow records an attempt for key and reports whether it is within the limit.
func (l *Limiter) Allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.clock()
	kept := l.attempts[key][:0]
	for _, t := range l.attempts[key] {
		if now.Sub(t) < l.window {
			kept = append(kept, t)
		}
	}
	if len(kept) >= l.limit {
		l.attempts[key] = kept
		return false
	}
	l.attempts[key] = append(kept, now)
	return true
}

// Reset forgets a key's attempts (a successful sign-in).
func (l *Limiter) Reset(key string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.attempts, key)
}
