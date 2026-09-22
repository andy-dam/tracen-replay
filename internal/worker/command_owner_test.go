package worker

import (
	"slices"
	"testing"
)

func TestOwnerPIDIsPassedToTheWorker(t *testing.T) {
	base := Command{Python: "python", WorkDir: ".", Source: "in.mp4", Output: "out", Workers: 2}
	argv, err := base.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(argv, "--owner-pid") {
		t.Fatalf("no owner pid must pass nothing, got %v", argv)
	}
	owned := base
	owned.OwnerPID = 4242
	argv, err = owned.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if i := slices.Index(argv, "--owner-pid"); i < 0 || i+1 >= len(argv) || argv[i+1] != "4242" {
		t.Fatalf("owner pid missing from %v", argv)
	}
	negative := base
	negative.OwnerPID = -1
	if _, err := negative.Argv(); err == nil {
		t.Fatal("a negative owner pid must be rejected")
	}
}

func TestLearnedReaderIsPassedOnlyWhenSet(t *testing.T) {
	base := Command{Python: "python", WorkDir: ".", Source: "in.mp4", Output: "out", Workers: 2}
	argv, err := base.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(argv, "--learned-reader") {
		t.Fatalf("the learned reader must be opt-in, got %v", argv)
	}
	with := base
	with.LearnedReader = "models/reader.onnx"
	argv, err = with.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if i := slices.Index(argv, "--learned-reader"); i < 0 || i+1 >= len(argv) || argv[i+1] == "" {
		t.Fatalf("--learned-reader missing from %v", argv)
	}
}

func TestPruneWorkingDataIsPassedOnlyWhenSet(t *testing.T) {
	base := Command{Python: "python", WorkDir: ".", Source: "in.mp4", Output: "out", Workers: 2}
	argv, err := base.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(argv, "--prune-working-data") {
		t.Fatalf("working data pruning must be opt-in on the command, got %v", argv)
	}
	pruned := base
	pruned.PruneWorkingData = true
	argv, err = pruned.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(argv, "--prune-working-data") {
		t.Fatalf("--prune-working-data missing from %v", argv)
	}
}

func TestNoViewerIsPassedOnlyWhenSet(t *testing.T) {
	base := Command{Python: "python", WorkDir: ".", Source: "in.mp4", Output: "out", Workers: 2}
	argv, err := base.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if slices.Contains(argv, "--no-viewer") {
		t.Fatalf("the viewer page is written unless the command says not to, got %v", argv)
	}
	without := base
	without.NoViewer = true
	argv, err = without.Argv()
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(argv, "--no-viewer") {
		t.Fatalf("--no-viewer missing from %v", argv)
	}
}
