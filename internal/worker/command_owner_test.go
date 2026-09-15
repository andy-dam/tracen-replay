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
