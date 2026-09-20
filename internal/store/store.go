// Package store persists jobs, reports, uploaded recordings, users and
// sessions in one SQLite file. It is the only package that touches the
// database; the jobs, auth and api packages talk to it through interfaces.
package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"time"

	_ "modernc.org/sqlite" // pure-Go SQLite driver registered as "sqlite"

	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/worker"
)

// Store is a SQLite-backed jobs.Store, auth.Store and recordings store.
type Store struct {
	db *sql.DB
}

var migrations = []string{
	`CREATE TABLE IF NOT EXISTS jobs (
		id TEXT PRIMARY KEY,
		source_id TEXT NOT NULL,
		source_path TEXT NOT NULL,
		source_name TEXT NOT NULL,
		status TEXT NOT NULL,
		created_at TEXT NOT NULL,
		started_at TEXT,
		finished_at TEXT,
		output_dir TEXT NOT NULL,
		log_path TEXT,
		pid INTEGER NOT NULL DEFAULT 0,
		stage TEXT,
		stage_at TEXT,
		ocr_processed INTEGER NOT NULL DEFAULT 0,
		ocr_total INTEGER NOT NULL DEFAULT 0,
		error_json TEXT,
		stage_failures_json TEXT,
		report_id TEXT,
		result_json TEXT
	);
	CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
	CREATE TABLE IF NOT EXISTS reports (
		id TEXT PRIMARY KEY,
		job_id TEXT,
		origin TEXT NOT NULL,
		source_name TEXT NOT NULL,
		source_sha256 TEXT NOT NULL,
		report_path TEXT NOT NULL,
		report_sha256 TEXT NOT NULL,
		timeline_path TEXT NOT NULL,
		evidence_root TEXT NOT NULL,
		source_path TEXT,
		created_at TEXT NOT NULL,
		duration_ms INTEGER NOT NULL DEFAULT 0,
		turns INTEGER NOT NULL DEFAULT 0,
		entries INTEGER NOT NULL DEFAULT 0
	);`,
	// Accounts: users own their uploads, jobs and the reports those jobs
	// produce. Records without an owner predate accounts (command-line
	// imports) and stay visible to every signed-in user.
	`CREATE TABLE IF NOT EXISTS users (
		id TEXT PRIMARY KEY,
		email TEXT NOT NULL UNIQUE,
		display_name TEXT NOT NULL,
		password_hash TEXT NOT NULL,
		created_at TEXT NOT NULL
	);
	CREATE TABLE IF NOT EXISTS sessions (
		token_hash TEXT PRIMARY KEY,
		user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
		created_at TEXT NOT NULL,
		expires_at TEXT NOT NULL
	);
	CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
	CREATE TABLE IF NOT EXISTS recordings (
		id TEXT PRIMARY KEY,
		user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
		name TEXT NOT NULL,
		path TEXT NOT NULL,
		size INTEGER NOT NULL,
		sha256 TEXT NOT NULL,
		created_at TEXT NOT NULL
	);
	CREATE INDEX IF NOT EXISTS recordings_user ON recordings(user_id, created_at);
	ALTER TABLE jobs ADD COLUMN user_id TEXT;
	ALTER TABLE reports ADD COLUMN user_id TEXT;`,
	`CREATE TABLE IF NOT EXISTS corrections (
		report_id TEXT NOT NULL,
		turn_id TEXT NOT NULL,
		user_id TEXT NOT NULL,
		payload TEXT NOT NULL,
		created_at TEXT NOT NULL,
		updated_at TEXT NOT NULL,
		PRIMARY KEY (report_id, turn_id, user_id)
	);`,
}

// Open opens or creates the database file and applies migrations.
func Open(path string) (*Store, error) {
	dsn := "file:" + url.PathEscape(path) + "?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	s := &Store{db: db}
	if err := s.migrate(context.Background()); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) migrate(ctx context.Context) error {
	if _, err := s.db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`); err != nil {
		return err
	}
	var current int
	if err := s.db.QueryRowContext(ctx, `SELECT COALESCE(MAX(version), 0) FROM schema_migrations`).Scan(&current); err != nil {
		return err
	}
	for i := current; i < len(migrations); i++ {
		tx, err := s.db.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, migrations[i]); err != nil {
			tx.Rollback()
			return fmt.Errorf("migration %d: %w", i+1, err)
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)`, i+1, stamp(time.Now())); err != nil {
			tx.Rollback()
			return err
		}
		if err := tx.Commit(); err != nil {
			return err
		}
	}
	return nil
}

// Close closes the database.
func (s *Store) Close() error { return s.db.Close() }

func stamp(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t.UTC().Format(time.RFC3339Nano)
}

func parseStamp(v sql.NullString) time.Time {
	if !v.Valid || v.String == "" {
		return time.Time{}
	}
	t, err := time.Parse(time.RFC3339Nano, v.String)
	if err != nil {
		return time.Time{}
	}
	return t
}

func marshal(v any) any {
	if v == nil {
		return nil
	}
	switch x := v.(type) {
	case *jobs.Failure:
		if x == nil {
			return nil
		}
	case *worker.Result:
		if x == nil {
			return nil
		}
	case []worker.StageFailure:
		if len(x) == 0 {
			return nil
		}
	}
	data, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	return string(data)
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// ---- jobs ----

const jobColumns = `id, source_id, source_path, source_name, status, created_at, started_at, finished_at, output_dir, log_path, pid,
	stage, stage_at, ocr_processed, ocr_total, error_json, stage_failures_json, report_id, result_json, user_id`

func scanJob(row interface{ Scan(...any) error }) (jobs.Job, error) {
	var j jobs.Job
	var status string
	var created, started, finished, logPath, stage, stageAt, errJSON, failuresJSON, reportID, resultJSON, userID sql.NullString
	if err := row.Scan(&j.ID, &j.SourceID, &j.SourcePath, &j.SourceName, &status, &created, &started, &finished, &j.OutputDir, &logPath, &j.PID,
		&stage, &stageAt, &j.OCRProcessed, &j.OCRTotal, &errJSON, &failuresJSON, &reportID, &resultJSON, &userID); err != nil {
		return jobs.Job{}, err
	}
	j.Status = jobs.Status(status)
	j.CreatedAt, j.StartedAt, j.FinishedAt = parseStamp(created), parseStamp(started), parseStamp(finished)
	j.LogPath, j.Stage, j.ReportID, j.UserID = logPath.String, stage.String, reportID.String, userID.String
	j.StageAt = parseStamp(stageAt)
	if errJSON.Valid {
		var f jobs.Failure
		if json.Unmarshal([]byte(errJSON.String), &f) == nil {
			j.Error = &f
		}
	}
	if failuresJSON.Valid {
		json.Unmarshal([]byte(failuresJSON.String), &j.StageFailure)
	}
	if resultJSON.Valid {
		var r worker.Result
		if json.Unmarshal([]byte(resultJSON.String), &r) == nil {
			j.Result = &r
		}
	}
	return j, nil
}

// CreateJob inserts a new job record.
func (s *Store) CreateJob(ctx context.Context, j jobs.Job) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO jobs (`+jobColumns+`) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		j.ID, j.SourceID, j.SourcePath, j.SourceName, string(j.Status), stamp(j.CreatedAt), stamp(j.StartedAt), stamp(j.FinishedAt),
		j.OutputDir, nullable(j.LogPath), j.PID, nullable(j.Stage), stamp(j.StageAt), j.OCRProcessed, j.OCRTotal,
		marshal(j.Error), marshal(j.StageFailure), nullable(j.ReportID), marshal(j.Result), nullable(j.UserID))
	return err
}

// UpdateJob rewrites every mutable column of an existing job.
func (s *Store) UpdateJob(ctx context.Context, j jobs.Job) error {
	res, err := s.db.ExecContext(ctx, `UPDATE jobs SET status=?, started_at=?, finished_at=?, log_path=?, pid=?, stage=?, stage_at=?,
		ocr_processed=?, ocr_total=?, error_json=?, stage_failures_json=?, report_id=?, result_json=? WHERE id=?`,
		string(j.Status), stamp(j.StartedAt), stamp(j.FinishedAt), nullable(j.LogPath), j.PID, nullable(j.Stage), stamp(j.StageAt),
		j.OCRProcessed, j.OCRTotal, marshal(j.Error), marshal(j.StageFailure), nullable(j.ReportID), marshal(j.Result), j.ID)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return &jobs.NotFoundError{Kind: "job", ID: j.ID}
	}
	return nil
}

// GetJob returns one job.
func (s *Store) GetJob(ctx context.Context, id string) (jobs.Job, error) {
	j, err := scanJob(s.db.QueryRowContext(ctx, `SELECT `+jobColumns+` FROM jobs WHERE id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return jobs.Job{}, &jobs.NotFoundError{Kind: "job", ID: id}
	}
	return j, err
}

func (s *Store) queryJobs(ctx context.Context, query string, args ...any) ([]jobs.Job, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []jobs.Job
	for rows.Next() {
		j, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, j)
	}
	return out, rows.Err()
}

// ListJobs returns every job, newest first.
func (s *Store) ListJobs(ctx context.Context) ([]jobs.Job, error) {
	return s.queryJobs(ctx, `SELECT `+jobColumns+` FROM jobs ORDER BY created_at DESC, id DESC`)
}

// ListJobsForUser returns the user's jobs and the unowned ones, newest first.
func (s *Store) ListJobsForUser(ctx context.Context, userID string) ([]jobs.Job, error) {
	return s.queryJobs(ctx, `SELECT `+jobColumns+` FROM jobs WHERE user_id=? OR user_id IS NULL ORDER BY created_at DESC, id DESC`, userID)
}

// NextQueued returns the oldest queued job.
func (s *Store) NextQueued(ctx context.Context) (jobs.Job, bool, error) {
	j, err := scanJob(s.db.QueryRowContext(ctx, `SELECT `+jobColumns+` FROM jobs WHERE status=? ORDER BY created_at, id LIMIT 1`, string(jobs.Queued)))
	if errors.Is(err, sql.ErrNoRows) {
		return jobs.Job{}, false, nil
	}
	if err != nil {
		return jobs.Job{}, false, err
	}
	return j, true, nil
}

// CountByStatus counts jobs in one state.
func (s *Store) CountByStatus(ctx context.Context, status jobs.Status) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM jobs WHERE status=?`, string(status)).Scan(&n)
	return n, err
}

// CountActiveForUser counts a user's queued and running jobs.
func (s *Store) CountActiveForUser(ctx context.Context, userID string) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN (?, ?)`, userID, string(jobs.Queued), string(jobs.Running)).Scan(&n)
	return n, err
}

// CountJobsSince counts jobs created at or after since, for one user or
// everyone, leaving out jobs cancelled before they started.
func (s *Store) CountJobsSince(ctx context.Context, userID string, since time.Time) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM jobs WHERE (?='' OR user_id=?) AND created_at>=? AND NOT (status=? AND started_at IS NULL)`,
		userID, userID, stamp(since), string(jobs.Cancelled)).Scan(&n)
	return n, err
}

// MarkInterrupted moves every running job to interrupted (used at startup,
// when no worker of this process can still be alive) and returns them.
func (s *Store) MarkInterrupted(ctx context.Context, at time.Time) ([]jobs.Job, error) {
	running, err := s.queryJobs(ctx, `SELECT `+jobColumns+` FROM jobs WHERE status=?`, string(jobs.Running))
	if err != nil {
		return nil, err
	}
	for i := range running {
		running[i].Status = jobs.Interrupted
		running[i].FinishedAt = at
		running[i].Error = &jobs.Failure{Code: "interrupted", Message: "the service restarted while this job was running; submit a new attempt to analyze the recording again"}
		if err := s.UpdateJob(ctx, running[i]); err != nil {
			return nil, err
		}
	}
	return running, nil
}

// ---- reports ----

const reportColumns = `id, job_id, origin, source_name, source_sha256, report_path, report_sha256, timeline_path, evidence_root, source_path, created_at, duration_ms, turns, entries, user_id`

func scanReport(row interface{ Scan(...any) error }) (jobs.Report, error) {
	var r jobs.Report
	var jobID, sourcePath, created, userID sql.NullString
	if err := row.Scan(&r.ID, &jobID, &r.Origin, &r.SourceName, &r.SourceSHA256, &r.ReportPath, &r.ReportSHA256, &r.TimelinePath, &r.EvidenceRoot,
		&sourcePath, &created, &r.DurationMS, &r.Turns, &r.Entries, &userID); err != nil {
		return jobs.Report{}, err
	}
	r.JobID, r.SourcePath, r.CreatedAt, r.UserID = jobID.String, sourcePath.String, parseStamp(created), userID.String
	return r, nil
}

// CreateReport inserts a validated report record.
func (s *Store) CreateReport(ctx context.Context, r jobs.Report) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO reports (`+reportColumns+`) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		r.ID, nullable(r.JobID), r.Origin, r.SourceName, r.SourceSHA256, r.ReportPath, r.ReportSHA256, r.TimelinePath, r.EvidenceRoot,
		nullable(r.SourcePath), stamp(r.CreatedAt), r.DurationMS, r.Turns, r.Entries, nullable(r.UserID))
	return err
}

// GetReport returns one report record.
func (s *Store) GetReport(ctx context.Context, id string) (jobs.Report, error) {
	r, err := scanReport(s.db.QueryRowContext(ctx, `SELECT `+reportColumns+` FROM reports WHERE id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return jobs.Report{}, &jobs.NotFoundError{Kind: "report", ID: id}
	}
	return r, err
}

// DeleteReport removes a report record with the corrections made on it, and
// detaches it from the job that produced it; the caller removes the files.
func (s *Store) DeleteReport(ctx context.Context, id string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	res, err := tx.ExecContext(ctx, `DELETE FROM reports WHERE id=?`, id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return &jobs.NotFoundError{Kind: "report", ID: id}
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM corrections WHERE report_id=?`, id); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE jobs SET report_id=NULL WHERE report_id=?`, id); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) queryReports(ctx context.Context, query string, args ...any) ([]jobs.Report, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []jobs.Report
	for rows.Next() {
		r, err := scanReport(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// ListReports returns every report record, newest first.
func (s *Store) ListReports(ctx context.Context) ([]jobs.Report, error) {
	return s.queryReports(ctx, `SELECT `+reportColumns+` FROM reports ORDER BY created_at DESC, id DESC`)
}

// ListReportsForUser returns the user's reports and the unowned ones, newest first.
func (s *Store) ListReportsForUser(ctx context.Context, userID string) ([]jobs.Report, error) {
	return s.queryReports(ctx, `SELECT `+reportColumns+` FROM reports WHERE user_id=? OR user_id IS NULL ORDER BY created_at DESC, id DESC`, userID)
}

// ---- recordings ----

const recordingColumns = `id, user_id, name, path, size, sha256, created_at`

func scanRecording(row interface{ Scan(...any) error }) (jobs.Recording, error) {
	var r jobs.Recording
	var created sql.NullString
	if err := row.Scan(&r.ID, &r.UserID, &r.Name, &r.Path, &r.Size, &r.SHA256, &created); err != nil {
		return jobs.Recording{}, err
	}
	r.CreatedAt = parseStamp(created)
	return r, nil
}

// CreateRecording records an upload that is fully written to disk.
func (s *Store) CreateRecording(ctx context.Context, r jobs.Recording) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO recordings (`+recordingColumns+`) VALUES (?,?,?,?,?,?,?)`,
		r.ID, r.UserID, r.Name, r.Path, r.Size, r.SHA256, stamp(r.CreatedAt))
	return err
}

// GetRecording returns one upload record.
func (s *Store) GetRecording(ctx context.Context, id string) (jobs.Recording, error) {
	r, err := scanRecording(s.db.QueryRowContext(ctx, `SELECT `+recordingColumns+` FROM recordings WHERE id=?`, id))
	if errors.Is(err, sql.ErrNoRows) {
		return jobs.Recording{}, &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	return r, err
}

// ListRecordingsForUser returns the user's uploads, newest first.
func (s *Store) ListRecordingsForUser(ctx context.Context, userID string) ([]jobs.Recording, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+recordingColumns+` FROM recordings WHERE user_id=? ORDER BY created_at DESC, id DESC`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []jobs.Recording
	for rows.Next() {
		r, err := scanRecording(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// RecordingUsage counts one user's uploads and their bytes; an empty user
// id counts everyone's.
func (s *Store) RecordingUsage(ctx context.Context, userID string) (jobs.StorageUsage, error) {
	var usage jobs.StorageUsage
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*), COALESCE(SUM(size), 0) FROM recordings WHERE ?='' OR user_id=?`, userID, userID).Scan(&usage.Count, &usage.Bytes)
	return usage, err
}

// DeleteRecording removes an upload record; the caller deletes the file.
func (s *Store) DeleteRecording(ctx context.Context, id string) error {
	res, err := s.db.ExecContext(ctx, `DELETE FROM recordings WHERE id=?`, id)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return &jobs.NotFoundError{Kind: "recording", ID: id}
	}
	return nil
}

// RecordingInUse reports whether a queued or running job analyzes the upload.
func (s *Store) RecordingInUse(ctx context.Context, id string) (bool, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM jobs WHERE source_id=? AND status IN (?, ?)`, id, string(jobs.Queued), string(jobs.Running)).Scan(&n)
	return n > 0, err
}

// ---- users and sessions (auth.Store) ----

// CreateUser inserts an account.
func (s *Store) CreateUser(ctx context.Context, user auth.User, passwordHash string) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO users (id, email, display_name, password_hash, created_at) VALUES (?,?,?,?,?)`,
		user.ID, user.Email, user.DisplayName, passwordHash, stamp(user.CreatedAt))
	return err
}

// UserByEmail returns the account and its password hash.
func (s *Store) UserByEmail(ctx context.Context, email string) (auth.User, string, error) {
	var u auth.User
	var hash string
	var created sql.NullString
	err := s.db.QueryRowContext(ctx, `SELECT id, email, display_name, password_hash, created_at FROM users WHERE email=?`, email).
		Scan(&u.ID, &u.Email, &u.DisplayName, &hash, &created)
	if errors.Is(err, sql.ErrNoRows) {
		return auth.User{}, "", auth.ErrNotFound
	}
	if err != nil {
		return auth.User{}, "", err
	}
	u.CreatedAt = parseStamp(created)
	return u, hash, nil
}

// UserByID returns an account.
func (s *Store) UserByID(ctx context.Context, id string) (auth.User, error) {
	var u auth.User
	var created sql.NullString
	err := s.db.QueryRowContext(ctx, `SELECT id, email, display_name, created_at FROM users WHERE id=?`, id).Scan(&u.ID, &u.Email, &u.DisplayName, &created)
	if errors.Is(err, sql.ErrNoRows) {
		return auth.User{}, auth.ErrNotFound
	}
	if err != nil {
		return auth.User{}, err
	}
	u.CreatedAt = parseStamp(created)
	return u, nil
}

// CreateSession stores a session by token hash.
func (s *Store) CreateSession(ctx context.Context, tokenHash, userID string, createdAt, expiresAt time.Time) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)`,
		tokenHash, userID, stamp(createdAt), stamp(expiresAt))
	return err
}

// SessionUser resolves an unexpired session to its user.
func (s *Store) SessionUser(ctx context.Context, tokenHash string, now time.Time) (auth.User, error) {
	var u auth.User
	var created, expires sql.NullString
	err := s.db.QueryRowContext(ctx, `SELECT u.id, u.email, u.display_name, u.created_at, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?`, tokenHash).
		Scan(&u.ID, &u.Email, &u.DisplayName, &created, &expires)
	if errors.Is(err, sql.ErrNoRows) {
		return auth.User{}, auth.ErrNotFound
	}
	if err != nil {
		return auth.User{}, err
	}
	if !now.Before(parseStamp(expires)) {
		s.db.ExecContext(ctx, `DELETE FROM sessions WHERE token_hash=?`, tokenHash)
		return auth.User{}, auth.ErrNotFound
	}
	u.CreatedAt = parseStamp(created)
	return u, nil
}

// DeleteSession ends a session.
func (s *Store) DeleteSession(ctx context.Context, tokenHash string) error {
	res, err := s.db.ExecContext(ctx, `DELETE FROM sessions WHERE token_hash=?`, tokenHash)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return auth.ErrNotFound
	}
	return nil
}
