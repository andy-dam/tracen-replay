package worker

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"testing"
)

// The client lays its progress bar out from the stages in web/src/phases.ts,
// and the service moves a job's stage forward along StageOrder. Were the two
// lists to differ, a stage the service records could be one the client
// places nowhere, and the bar would fall to nothing in the middle of a run.
func TestStageOrderMatchesTheClient(t *testing.T) {
	source, err := os.ReadFile(filepath.Join("..", "..", "web", "src", "phases.ts"))
	if err != nil {
		t.Fatal(err)
	}
	var client []string
	for _, list := range regexp.MustCompile(`stages: \[([^\]]*)\]`).FindAllStringSubmatch(string(source), -1) {
		for _, name := range regexp.MustCompile(`"([^"]+)"`).FindAllStringSubmatch(list[1], -1) {
			client = append(client, name[1])
		}
	}
	if !reflect.DeepEqual(client, StageOrder) {
		t.Fatalf("web/src/phases.ts lists\n%v\nand StageOrder is\n%v", client, StageOrder)
	}
}

func TestStageRank(t *testing.T) {
	if StageRank("capture") != 0 || StageRank("complete") != len(StageOrder)-1 {
		t.Fatal("the first and last stages are misplaced")
	}
	if StageRank("receipt_inspection") >= StageRank("assemble") {
		t.Fatal("receipt_inspection comes before assemble")
	}
	if StageRank("keeping a playback copy") != -1 || StageRank("") != -1 {
		t.Fatal("a name that is not a step has no rank")
	}
}
