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
	"strings"
	"syscall"
	"time"

	"github.com/andy-dam/tracen-replay/internal/api"
	"github.com/andy-dam/tracen-replay/internal/artifacts"
	"github.com/andy-dam/tracen-replay/internal/auth"
	"github.com/andy-dam/tracen-replay/internal/jobs"
	"github.com/andy-dam/tracen-replay/internal/maintenance"
	"github.com/andy-dam/tracen-replay/internal/runner"
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
	if len(os.Args) > 1 && os.Args[1] == "worker" {
		if err := runWorker(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "tracen worker:", err)
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
	denseWorkers := flag.Int("dense-workers", 0, "worker processes for the dense re-read passes (0 = workers minus one); each stays near 2 GB")
	queue := flag.Int("queue", 4, "maximum number of queued jobs")
	parallel := flag.Int("parallel", 1, "analyses run at once; each holds a few GB of memory and its OCR workers' share of the CPU")
	ocrDevice := flag.String("ocr-device", "auto", "OCR device for the analyzer: auto (DirectML, then CUDA, then CPU), cpu, dml or cuda")
	learnedReader := flag.String("learned-reader", "", "exported learned result-card reader (ONNX) for the analyzer; its reads count only where they equal an unexplained difference (off when empty)")
	webDir := flag.String("web", "", "serve the browser client from this built directory instead of the embedded copy (a rebuild is picked up without a restart)")
	apiOnly := flag.Bool("api-only", false, "serve the API only; the browser client is hosted elsewhere and named with -allowed-origin")
	allowedOrigins := flag.String("allowed-origin", "", "comma-separated client origins served from elsewhere that may call the API with credentials, e.g. http://localhost:5173")
	cookieSameSite := flag.String("cookie-samesite", "strict", "session cookie SameSite: strict (client served here), lax (client on another port or subdomain of the same site), none (another site; needs HTTPS)")
	allowedHosts := flag.String("allowed-host", "", "comma-separated host names this service answers for besides localhost and the -addr host, e.g. tracen.example.com")
	trustedProxies := flag.String("trusted-proxy", "", "comma-separated networks (CIDR) of reverse proxies in front of the service; their X-Forwarded-For names the client for the sign-in and registration limits")
	registration := flag.String("registration", "open", "who may create an account: open, invite (a code from -invite-code) or closed")
	inviteCodes := flag.String("invite-code", "", "comma-separated invite codes accepted with -registration invite")
	ffprobe := flag.String("ffprobe", "ffprobe", "ffprobe executable; every upload is probed with it and refused when it is not a video or is longer, larger or faster than the limits below")
	uploadLimit := flag.Int64("upload-limit-gb", 3, "largest upload accepted, in GB (a 1080p career of an hour is about 1.5 GB)")
	maxRecordings := flag.Int("max-recordings", 5, "uploads one user may keep at once; 0 for no limit")
	maxRecordingBytes := flag.Int64("max-recording-gb", 8, "space one user's uploads may take together, in GB; 0 for no limit")
	maxStorage := flag.Int64("max-storage-gb", 100, "space all uploads may take together, in GB; further uploads are refused as temporary; 0 for no limit")
	maxDuration := flag.Duration("max-duration", 3*time.Hour, "longest recording accepted; 0 for no limit")
	maxActive := flag.Int("max-active-per-user", 1, "analyses one user may have queued or running at once; 0 for no limit")
	dailyPerUser := flag.Int("daily-per-user", 3, "analyses one user may start in 24 hours; 0 for no limit")
	dailyTotal := flag.Int("daily-total", 24, "analyses everyone together may start in 24 hours; 0 for no limit")
	maxAnalysis := flag.Duration("max-analysis", 4*time.Hour, "longest one analysis may run before it is stopped as timed_out; 0 for no limit")
	keepWorkingData := flag.Bool("keep-working-data", false, "keep the analyzer's OCR caches, crops and recovery inputs in the job directory (about 1 GB per analysis); by default only the report, timeline, viewer page and log are kept")
	workerImage := flag.String("worker-image", "", "run each analysis as a container of this worker image (the Dockerfile's worker stage) instead of as a child process; -python, -workdir and -model-dir are then unused")
	dockerCLI := flag.String("docker", "docker", "docker command line client, used with -worker-image")
	workerGPUs := flag.String("worker-gpus", "", "docker run --gpus value for the worker container, e.g. all, for an image built with the CUDA wheel; empty stays on the CPU provider")
	workerUser := flag.String("worker-user", "", "docker run --user value for the worker container, e.g. 1000:1000, so its files belong to the service's user on a Linux host")
	workerMemory := flag.String("worker-memory", "14g", "docker run --memory for the worker container (an analysis peaks near 5 GB plus 2 GB per OCR worker); empty for no bound")
	workerCPUs := flag.String("worker-cpus", "", "docker run --cpus for the worker container; empty for no bound")
	workerPids := flag.Int("worker-pids", 512, "docker run --pids-limit for the worker container; 0 for no bound")
	workerNetwork := flag.String("worker-network", "none", "docker run --network for the worker container; the analyzer needs none")
	recordingRetention := flag.Duration("recording-retention", 14*24*time.Hour, "uploads older than this that no analysis is using are deleted (their reports stay); 0 keeps uploads forever")
	frameCache := flag.Int64("frame-cache-gb", 2, "space the extracted-frame cache may take, in GB, the oldest frames going first; 0 for no bound")
	storage := addStorageFlags(flag.CommandLine)
	flag.Parse()
	logger := slog.New(slog.NewTextHandler(os.Stderr, nil))
	slog.SetDefault(logger)
	objects, analysisQueue, err := storage.open(context.Background())
	if err != nil {
		return err
	}

	if err := os.MkdirAll(*dataDir, 0o755); err != nil {
		return err
	}
	db, err := storage.openStore(context.Background(), *dataDir)
	if err != nil {
		return err
	}
	defer db.Close()
	workDirAbs, err := filepath.Abs(*workDir)
	if err != nil {
		return err
	}
	dataDirAbs, err := filepath.Abs(*dataDir)
	if err != nil {
		return err
	}
	// The worker is a child process unless an image is named; then each
	// analysis is a container of that image, labelled with this data
	// directory so a restart can end the containers a dead service left.
	var jobRunner jobs.Runner = runner.Exec{Logger: logger}
	analyzerQuery := runner.Exec{Logger: logger}.VersionQuery(*python, workDirAbs)
	var container *runner.Container
	if *workerImage != "" {
		container = &runner.Container{Docker: *dockerCLI, Image: *workerImage, GPUs: *workerGPUs, User: *workerUser, Owner: dataDirAbs, Logger: logger,
			Memory: *workerMemory, CPUs: *workerCPUs, PidsLimit: *workerPids, Network: *workerNetwork}
		jobRunner = *container
		analyzerQuery = container.VersionQuery()
	}
	manager, err := jobs.NewManager(jobs.Config{DataDir: *dataDir, Python: *python, WorkDir: workDirAbs, ModelDir: *modelDir,
		Workers: *workers, DenseWorkers: *denseWorkers, OCRDevice: *ocrDevice, LearnedReader: *learnedReader, Parallel: *parallel,
		QueueLimit: *queue, KeepWorkingData: *keepWorkingData,
		MaxActivePerUser: *maxActive, DailyPerUser: *dailyPerUser, DailyTotal: *dailyTotal, MaxDuration: *maxAnalysis,
		Recordings: db, Queue: analysisQueue, Objects: objects, Logger: logger}, db, jobRunner)
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
	if container != nil {
		if _, err := container.Reap(ctx); err != nil {
			logger.Warn("worker containers of a previous run could not be checked", "error", err)
		}
	}
	// With a shared queue the analyses run in worker processes; this
	// process only follows their records for the event streams.
	loopDone := make(chan struct{})
	go func() {
		if analysisQueue != nil {
			manager.Follow(ctx)
		} else {
			manager.Run(ctx)
		}
		close(loopDone)
	}()

	host, _, err := net.SplitHostPort(*addr)
	if err != nil {
		return fmt.Errorf("bad -addr: %w", err)
	}
	ready := func() []api.Check {
		var checks []api.Check
		if analysisQueue != nil {
			checks = []api.Check{
				check("ffmpeg", *ffmpeg, func() error { _, err := exec.LookPath(*ffmpeg); return err }),
				check("object-store", *storage.objectStore, func() error {
					ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
					defer cancel()
					_, err := objects.List(ctx, "originals/")
					return err
				}),
				{Name: "queue", OK: true, Note: *storage.queue + " " + *storage.queueName + " (analyses run in tracen worker processes)"},
			}
		} else if container != nil {
			checks = []api.Check{
				check("docker", *dockerCLI, func() error { _, err := exec.LookPath(*dockerCLI); return err }),
				check("ffmpeg", *ffmpeg, func() error { _, err := exec.LookPath(*ffmpeg); return err }),
				check("worker-image", *workerImage, func() error {
					ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
					defer cancel()
					_, err := container.ImageID(ctx)
					return err
				}),
			}
		} else {
			checks = []api.Check{
				check("python", *python, func() error { _, err := os.Stat(*python); return err }),
				check("ffmpeg", *ffmpeg, func() error { _, err := exec.LookPath(*ffmpeg); return err }),
				check("model-dir", *modelDir, func() error { _, err := os.Stat(filepath.Join(*modelDir, "PP-OCRv6_det_small.onnx")); return err }),
				check("analyzer", workDirAbs, func() error {
					_, err := os.Stat(filepath.Join(workDirAbs, "tracen_replay", "analysis_job.py"))
					return err
				}),
			}
		}
		checks = append(checks, check("ffprobe", *ffprobe, func() error { _, err := exec.LookPath(*ffprobe); return err }))
		if analysisQueue != nil {
			return checks
		}
		checks = append(checks, api.Check{Name: "ocr-device", OK: true, Note: *ocrDevice + " (the resolved device is reported by each analysis in its recognition record)"})
		if *learnedReader != "" {
			checks = append(checks, check("learned-reader", *learnedReader, func() error { _, err := os.Stat(*learnedReader); return err }))
		}
		return checks
	}
	var static http.Handler = webassets.Handler()
	switch {
	case *apiOnly:
		static = http.NotFoundHandler()
	case *webDir != "":
		static = webassets.DirHandler(*webDir)
	}
	var origins []string
	for _, o := range strings.Split(*allowedOrigins, ",") {
		if o = strings.TrimSpace(o); o != "" {
			origins = append(origins, o)
		}
	}
	sameSite := map[string]http.SameSite{"strict": http.SameSiteStrictMode, "lax": http.SameSiteLaxMode, "none": http.SameSiteNoneMode}[strings.ToLower(*cookieSameSite)]
	if sameSite == 0 {
		return fmt.Errorf("bad -cookie-samesite %q: strict, lax or none", *cookieSameSite)
	}
	hosts := []string{"localhost", "127.0.0.1", "::1", host}
	for _, h := range strings.Split(*allowedHosts, ",") {
		if h = strings.TrimSpace(h); h != "" {
			hosts = append(hosts, h)
		}
	}
	var proxies []*net.IPNet
	for _, cidr := range strings.Split(*trustedProxies, ",") {
		if cidr = strings.TrimSpace(cidr); cidr != "" {
			_, network, err := net.ParseCIDR(cidr)
			if err != nil {
				return fmt.Errorf("bad -trusted-proxy %q: %w", cidr, err)
			}
			proxies = append(proxies, network)
		}
	}
	var invites []string
	for _, code := range strings.Split(*inviteCodes, ",") {
		if code = strings.TrimSpace(code); code != "" {
			invites = append(invites, code)
		}
	}
	if !api.ValidRegistration(*registration) {
		return fmt.Errorf("bad -registration %q: open, invite or closed", *registration)
	}
	if *registration == api.RegistrationInvite && len(invites) == 0 {
		return fmt.Errorf("-registration invite needs at least one -invite-code")
	}
	// The analyzer names itself, so a report can say whether the analyzer that
	// made it is still the one installed. Asked on first use, not at startup:
	// serving must not wait on an interpreter or a container, and a missing
	// analyzer is a readiness problem rather than a reason not to listen.
	analyzer := &runner.AnalyzerVersion{Ask: analyzerQuery}
	// Two frame extractions at once: a viewer scrubbing gets prompt frames,
	// a crowd cannot fork ffmpeg without bound.
	frames := artifacts.Frames{FFmpeg: *ffmpeg, CacheDir: filepath.Join(*dataDir, "frames"), Gate: artifacts.NewGate(2), MaxCacheBytes: *frameCache << 30}
	sweeper := maintenance.Sweeper{Store: db, RecordingsDir: recordingsDir, Objects: objects, RecordingRetention: *recordingRetention, Frames: frames, Logger: logger}
	go sweeper.Run(ctx)
	handler := api.New(api.Config{Jobs: manager, Reports: db, Recordings: db, Corrections: db, Auth: accounts, RecordingsDir: recordingsDir,
		Objects:      objects,
		Analyzer:     analyzer.Version,
		ArtifactsDir: filepath.Join(*dataDir, "jobs"), Ready: ready, Logger: logger,
		Frames:       frames,
		AllowedHosts: hosts, AllowedOrigins: origins, CookieSameSite: sameSite, Static: static,
		TrustedProxies: proxies, Registration: *registration, InviteCodes: invites,
		UploadLimit: *uploadLimit << 30,
		Quota: api.Quota{MaxRecordingsPerUser: *maxRecordings, MaxBytesPerUser: *maxRecordingBytes << 30, MaxBytesTotal: *maxStorage << 30,
			MaxDuration: *maxDuration, MaxPixels: 4096 * 2304, MaxFPS: 120},
		Probe: func(ctx context.Context, path string) (artifacts.Media, error) {
			return artifacts.ProbeMedia(ctx, *ffprobe, path)
		}})
	// No read or write deadline on the whole request: an upload of a few
	// gigabytes over a slow link and a job's event stream are both long by
	// design. Headers must arrive promptly, idle keep-alive connections are
	// closed, and headers are bounded.
	server := &http.Server{Addr: *addr, Handler: handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 2 * time.Minute, MaxHeaderBytes: 64 << 10}
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
