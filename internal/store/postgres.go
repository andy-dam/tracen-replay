package store

import (
	"context"
	"database/sql"
	"fmt"
	"strconv"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib" // PostgreSQL driver registered as "pgx"
)

// The same store on PostgreSQL, for the hosted service, where the API and
// the workers on other machines share one database. The SQL is written
// once, in the dialect both engines accept, with ? placeholders that are
// renumbered for PostgreSQL on the way out.

// OpenPostgres connects to the database at url
// ("postgres://user:password@host:5432/tracen?sslmode=require") and
// applies migrations.
func OpenPostgres(ctx context.Context, url string) (*Store, error) {
	db, err := sql.Open("pgx", url)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(8)
	db.SetConnMaxLifetime(30 * time.Minute)
	pingCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := db.PingContext(pingCtx); err != nil {
		db.Close()
		return nil, fmt.Errorf("postgres: %w", err)
	}
	s := &Store{db: db, postgres: true}
	if err := s.migrate(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// Postgres reports which engine the store runs on.
func (s *Store) Postgres() bool { return s.postgres }

// bind renumbers ? placeholders as $1, $2, ... for PostgreSQL. No query
// of this package puts a ? inside a string literal.
func (s *Store) bind(query string) string {
	if !s.postgres || !strings.Contains(query, "?") {
		return query
	}
	var b strings.Builder
	b.Grow(len(query) + 8)
	n := 0
	for i := 0; i < len(query); i++ {
		if query[i] == '?' {
			n++
			b.WriteByte('$')
			b.WriteString(strconv.Itoa(n))
			continue
		}
		b.WriteByte(query[i])
	}
	return b.String()
}

func (s *Store) exec(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return s.db.ExecContext(ctx, s.bind(query), args...)
}

func (s *Store) query(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	return s.db.QueryContext(ctx, s.bind(query), args...)
}

func (s *Store) queryRow(ctx context.Context, query string, args ...any) *sql.Row {
	return s.db.QueryRowContext(ctx, s.bind(query), args...)
}

// txn is a transaction that binds placeholders the same way.
type txn struct {
	*sql.Tx
	store *Store
}

func (s *Store) begin(ctx context.Context) (*txn, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	return &txn{Tx: tx, store: s}, nil
}

func (t *txn) exec(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return t.Tx.ExecContext(ctx, t.store.bind(query), args...)
}
