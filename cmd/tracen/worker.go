package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/runner"
)

// runWorker is `tracen worker`: a process that takes analyses from the
// shared queue and runs them, on any machine that reaches the same
// database, object store and queue as the service. It fetches each
// recording into its scratch directory, runs the analyzer there, and
// uploads the report, the timeline and the log when the run ends.
func runWorker(args []string) error {
	fs := flag.NewFlagSet("worker", flag.ContinueOnError)
	dataDir := fs.String("data", filepath.Join(".local", "tracen-data"), "directory holding the database")
	scratch := fs.String("scratch", filepath.Join(".local", "tracen-scratch"), "directory for the recording being analyzed and its run; each job's files are removed when it ends")
	python := fs.String("python", defaultPython(), "python interpreter with the analyzer dependencies")
	workDir := fs.String("workdir", "analyzer", "directory containing the tracen_replay package")
	modelDir := fs.String("model-dir", filepath.Join(".local", "models", "rapidocr"), "OCR model directory")
	workers := fs.Int("workers", 4, "OCR worker processes per job")
	denseWorkers := fs.Int("dense-workers", 0, "worker processes for the dense re-read passes (0 = workers minus one)")
	parallel := fs.Int("parallel", 1, "analyses run at once on this machine")
	ocrDevice := fs.String("ocr-device", "auto", "OCR device for the analyzer: auto, cpu, dml or cuda")
	learnedReader := fs.String("learned-reader", "", "exported learned result-card reader (ONNX); off when empty")
	maxAnalysis := fs.Duration("max-analysis", 4*time.Hour, "longest one analysis may run before it is stopped as timed_out; 0 for no limit")
	keepWorkingData := fs.Bool("keep-working-data", false, "upload the analyzer's OCR caches, crops and recovery inputs with the report (about 1 GB per analysis)")
	keepCopy := fs.Bool("keep-copy", true, "after a completed analysis, encode a 720p playback copy of the recording and store it under kept/; the report and the recording then play from it")
	ffmpeg := fs.String("ffmpeg", "ffmpeg", "ffmpeg executable for the playback copy")
	copyThreads := fs.Int("copy-threads", 0, "threads for the playback copy's encoder; 0 lets ffmpeg decide")
	storage := addStorageFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *storage.queue == "" {
		return errors.New("tracen worker needs -queue: there is nothing to take jobs from")
	}
	logger := slog.New(slog.NewTextHandler(os.Stderr, nil))
	slog.SetDefault(logger)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	objects, q, err := storage.open(ctx)
	if err != nil {
		return err
	}
	db, err := storage.openStore(ctx, *dataDir)
	if err != nil {
		return err
	}
	defer db.Close()
	workDirAbs, err := filepath.Abs(*workDir)
	if err != nil {
		return err
	}
	cfg := jobs.Config{Python: *python, WorkDir: workDirAbs, ModelDir: *modelDir,
		Workers: *workers, DenseWorkers: *denseWorkers, OCRDevice: *ocrDevice, LearnedReader: *learnedReader, Parallel: *parallel,
		QueueLimit: 1, KeepWorkingData: *keepWorkingData, MaxDuration: *maxAnalysis,
		Recordings: db, Queue: q, Objects: objects, Scratch: *scratch, Logger: logger}
	if *keepCopy {
		cfg.KeepCopy = artifacts.Copy{FFmpeg: *ffmpeg, Threads: *copyThreads}.Encode
	}
	manager, err := jobs.NewManager(cfg, db, runner.Exec{Logger: logger})
	if err != nil {
		return err
	}
	logger.Info("tracen worker taking analyses", "queue", *storage.queueName, "scratch", *scratch, "parallel", *parallel)
	err = manager.Work(ctx)
	logger.Info("tracen worker stopped")
	return err
}
