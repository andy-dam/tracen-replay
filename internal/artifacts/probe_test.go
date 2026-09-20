package artifacts

import (
	"testing"
	"time"
)

// ffprobe's JSON names the first video stream's size and rate and the
// container's duration; an audio file has no video stream.
func TestParseProbe(t *testing.T) {
	media, err := parseProbe([]byte(`{"streams":[{"codec_type":"audio","sample_rate":"48000"},
		{"codec_type":"video","width":1920,"height":1080,"r_frame_rate":"60/1","avg_frame_rate":"59940/1000"}],
		"format":{"duration":"2487.250000"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if !media.HasVideo || media.Width != 1920 || media.Height != 1080 || media.FPS != 59.94 || media.Duration != 2487250*time.Millisecond {
		t.Fatalf("media: %+v", media)
	}
	audio, err := parseProbe([]byte(`{"streams":[{"codec_type":"audio"}],"format":{"duration":"12.5"}}`))
	if err != nil || audio.HasVideo || audio.Duration != 12500*time.Millisecond {
		t.Fatalf("audio: %+v %v", audio, err)
	}
	// A rate ffprobe could not state, or a broken document, does not fake numbers.
	odd, err := parseProbe([]byte(`{"streams":[{"codec_type":"video","width":10,"height":10,"avg_frame_rate":"0/0","r_frame_rate":"30"}],"format":{}}`))
	if err != nil || odd.FPS != 30 || odd.Duration != 0 {
		t.Fatalf("odd rates: %+v %v", odd, err)
	}
	if _, err := parseProbe([]byte(`not json`)); err == nil {
		t.Fatal("broken output must fail")
	}
}
