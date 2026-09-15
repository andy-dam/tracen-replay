// Command tracen runs the local Tracen Replay application: an HTTP API on a
// loopback address that queues recordings for the Python analyzer, follows
// its progress and serves the resulting timelines.
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"

	"github.com/andy-dam/tracen-replay/internal/api"
	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/runner"
	"github.com/andy-dam/tracen-replay/internal/store"
	"github.com/andy-dam/tracen-replay/internal/webassets"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "import" {
		if err := runImport(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "tracen import:", err)
			os.Exit(1)
		}
		return
	}
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "tracen:", err)
		os.Exit(1)
	}
}

func defaultPython() string {
	if runtime.GOOS == "windows" {
		return filepath.Join(".venv", "Scripts", "python.exe")
	}
	return filepath.Join(".venv", "bin", "python")
}

func run() error {
	addr := flag.String("addr", "127.0.0.1:8765", "listen address (loopback only)")
	dataDir := flag.String("data", filepath.Join(".local", "tracen-data"), "directory for the database, job outputs and frame cache")
	python := flag.String("python", defaultPython(), "python interpreter with the analyzer dependencies")
	workDir := flag.String("workdir", "analyzer", "directory containing the tracen_replay package")
	modelDir := flag.String("model-dir", filepath.Join(".local", "models", "rapidocr"), "OCR model directory")
	ffmpeg := flag.String("ffmpeg", "ffmpeg", "ffmpeg executable for frame extraction")
	workers := flag.Int("workers", 4, "OCR worker processes per job")
	denseWorkers := flag.Int("dense-workers", 0, "worker processes for the dense re-read passes (0 = workers minus one); each can peak near 5-6 GB")
	queue := flag.Int("queue", 4, "maximum number of queued jobs")
	ocrDevice := flag.String("ocr-device", "auto", "OCR device for the analyzer: auto (DirectML, then CUDA, then CPU), cpu, dml or cuda")
	keepWorkingData := flag.Bool("keep-working-data", false, "keep the analyzer's OCR caches, crops and recovery inputs in the job directory (about 1 GB per analysis); by default only the report, timeline, viewer page and log are kept")
	flag.Parse()
	logger := slog.New(slog.NewTextHandler(os.Stderr, nil))
	slog.SetDefault(logger)

	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		return err
	}
	db, err := store.Open(filepath.Join(*dataDir, "tracen.db"))
	if err != nil {
		return err
	}
	defer db.Close()
	workDirAbs, err := filepath.Abs(*workDir)
	if err != nil {
		return err
	}
	manager, err := jobs.NewManager(jobs.Config{DataDir: *dataDir, Python: *python, WorkDir: workDirAbs, ModelDir: *modelDir,
		Workers: *workers, DenseWorkers: *denseWorkers, OCRDevice: *ocrDevice, QueueLimit: *queue, KeepWorkingData: *keepWorkingData,
		Recordings: db, Logger: logger}, db, runner.Exec{Logger: logger})
	if err != nil {
		return err
	}
	accounts := auth.New(db)
	recordingsDir := filepath.Join(*dataDir, "recordings")
	if err := os.MkdirAll(recordingsDir, 0o755); err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if interrupted, err := manager.Recover(ctx); err != nil {
		return err
	} else if len(interrupted) > 0 {
		logger.Warn("interrupted jobs found from a previous run", "count", len(interrupted))
	}
	loopDone := make(chan struct{})
	go func() {
		manager.Run(ctx)
		close(loopDone)
	}()

	host, _, err := net.SplitHostPort(*addr)
	if err != nil {
		return fmt.Errorf("bad -addr: %w", err)
	}
	ready := func() []api.Check {
		return []api.Check{
			check("python", *python, func() error { _, err := os.Stat(*python); return err }),
			check("ffmpeg", *ffmpeg, func() error { _, err := exec.LookPath(*ffmpeg); return err }),
			check("model-dir", *modelDir, func() error { _, err := os.Stat(filepath.Join(*modelDir, "PP-OCRv6_det_small.onnx")); return err }),
			check("analyzer", workDirAbs, func() error {
				_, err := os.Stat(filepath.Join(workDirAbs, "tracen_replay", "analysis_job.py"))
				return err
			}),
			{Name: "ocr-device", OK: true, Note: *ocrDevice + " (the resolved device is reported by each analysis in its recognition record)"},
		}
	}
	handler := api.New(api.Config{Jobs: manager, Reports: db, Recordings: db, Corrections: db, Auth: accounts, RecordingsDir: recordingsDir,
		ArtifactsDir: filepath.Join(*dataDir, "jobs"), Ready: ready, Logger: logger,
		Frames:       artifacts.Frames{FFmpeg: *ffmpeg, CacheDir: filepath.Join(*dataDir, "frames")},
		AllowedHosts: []string{"localhost", "127.0.0.1", "::1", host}, Static: webassets.Handler()})
	server := &http.Server{Addr: *addr, Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	serveErr := make(chan error, 1)
	go func() { serveErr <- server.ListenAndServe() }()
	logger.Info("tracen listening", "addr", "http://"+*addr, "data", *dataDir)

	select {
	case err := <-serveErr:
		stop()
		<-loopDone
		return err
	case <-ctx.Done():
	}
	logger.Info("shutting down: stopping admission, ending the running worker")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	server.Shutdown(shutdownCtx)
	select {
	case <-loopDone:
	case <-shutdownCtx.Done():
		logger.Warn("worker loop did not stop within the shutdown budget")
	}
	return nil
}

func check(name, note string, probe func() error) api.Check {
	if err := probe(); err != nil {
		return api.Check{Name: name, OK: false, Note: note + ": " + err.Error()}
	}
	return api.Check{Name: name, OK: true, Note: note}
}
