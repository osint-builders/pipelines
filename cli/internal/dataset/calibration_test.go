package dataset

import (
	"archive/zip"
	"bytes"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
)

type calibrationGoldenFixture struct {
	ProtocolSHA256 string `json:"protocol_sha256"`
	Binding        struct {
		Manifest               map[string]any `json:"manifest"`
		RetrievalSHA256        string         `json:"retrieval_sha256"`
		EntityIDs              []string       `json:"entity_ids"`
		EligibleEntitiesSHA256 string         `json:"eligible_entities_sha256"`
	} `json:"binding"`
	FramingCases []struct {
		Value    any    `json:"value"`
		FrameHex string `json:"frame_hex"`
	} `json:"framing_cases"`
	Golden []struct {
		ID       string             `json:"id"`
		Context  calibrationContext `json:"context"`
		Response struct {
			Results []CalibrationCandidate `json:"results"`
		} `json:"response"`
		Artifact map[string]any       `json:"artifact"`
		Features map[string]int       `json:"features"`
		Decision *CalibrationOverride `json:"decision"`
	} `json:"golden"`
}

func calibrationGoldens(t *testing.T) calibrationGoldenFixture {
	t.Helper()
	body, err := os.ReadFile("../../../tests/fixtures/calibration.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture calibrationGoldenFixture
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.ProtocolSHA256 != calibrationProtocol {
		t.Fatal("Go calibration protocol differs from shared fixture")
	}
	return fixture
}

func TestCalibrationSharedPythonGoldenParity(t *testing.T) {
	f := calibrationGoldens(t)
	for _, frame := range f.FramingCases {
		body, err := calibrationFrame(frame.Value)
		if err != nil || hex.EncodeToString(body) != frame.FrameHex {
			t.Fatal(frame, err, string(body))
		}
	}
	retrieval, err := calibrationRetrieval(f.Binding.Manifest)
	if err != nil || retrieval != f.Binding.RetrievalSHA256 {
		t.Fatal(retrieval, err)
	}
	scope, err := calibrationScope(f.Binding.EntityIDs)
	if err != nil || scope != f.Binding.EligibleEntitiesSHA256 {
		t.Fatal(scope, err)
	}
	for _, golden := range f.Golden {
		t.Run(golden.ID, func(t *testing.T) {
			body, _ := json.Marshal(golden.Artifact)
			artifact, err := parseCalibration(body, retrieval)
			if err != nil {
				t.Fatal(err)
			}
			features, err := candidateSignals(golden.Context.QueryType, golden.Response.Results)
			if err != nil || !reflect.DeepEqual(features, golden.Features) {
				t.Fatalf("features %+v, want %+v: %v", features, golden.Features, err)
			}
			decision, err := artifact.decide(golden.Context, golden.Response.Results)
			if err != nil || !reflect.DeepEqual(decision, golden.Decision) {
				t.Fatalf("decision %+v, want %+v: %v", decision, golden.Decision, err)
			}
		})
	}
	// Only circular extension metadata is excluded from the identity.
	f.Binding.Manifest["format_version"] = float64(5)
	f.Binding.Manifest["dataset_id"], f.Binding.Manifest["recipe_sha256"], f.Binding.Manifest["calibration"] = "new", "new", map[string]any{"anything": "excluded"}
	f.Binding.Manifest["files"].(map[string]any)["calibration.json"] = "new"
	if got, err := calibrationRetrieval(f.Binding.Manifest); err != nil || got != retrieval {
		t.Fatal(got, err)
	}
	f.Binding.Manifest["research"].(map[string]any)["version"] = "changed"
	if got, err := calibrationRetrieval(f.Binding.Manifest); err != nil || got == retrieval {
		t.Fatal("changed research metadata retained binding", err)
	}
}

func TestCalibrationRejectsMalformedArtifacts(t *testing.T) {
	f := calibrationGoldens(t)
	base, _ := json.Marshal(f.Golden[0].Artifact)
	for name, mutate := range map[string]func(map[string]any){
		"extra field":       func(a map[string]any) { a["extra"] = true },
		"protocol":          func(a map[string]any) { a["protocol_sha256"] = strings.Repeat("0", 64) },
		"binding":           func(a map[string]any) { a["retrieval_sha256"] = strings.Repeat("0", 64) },
		"contract":          func(a map[string]any) { a["development"].(map[string]any)["contract_sha256"] = strings.Repeat("0", 64) },
		"null profiles":     func(a map[string]any) { a["profiles"] = nil },
		"duplicates":        func(a map[string]any) { p := a["profiles"].([]any); a["profiles"] = append(p, p[0]) },
		"null observations": func(a map[string]any) { a["profiles"].([]any)[0].(map[string]any)["observations"] = nil },
		"unknown mode":      func(a map[string]any) { a["profiles"].([]any)[0].(map[string]any)["text_mode"] = "unknown" },
		"null thresholds":   func(a map[string]any) { a["profiles"].([]any)[0].(map[string]any)["minimums"] = nil },
		"null threshold": func(a map[string]any) {
			a["profiles"].([]any)[0].(map[string]any)["minimums"].(map[string]any)["ranking_margin"] = nil
		},
		"out of range": func(a map[string]any) {
			a["profiles"].([]any)[0].(map[string]any)["minimums"].(map[string]any)["ranking_margin"] = float64(4000002)
		},
		"fraction": func(a map[string]any) {
			a["profiles"].([]any)[0].(map[string]any)["minimums"].(map[string]any)["ranking_margin"] = 0.5
		},
	} {
		t.Run(name, func(t *testing.T) {
			var value map[string]any
			_ = json.Unmarshal(base, &value)
			mutate(value)
			body, _ := json.Marshal(value)
			if _, err := parseCalibration(body, f.Binding.RetrievalSHA256); err == nil {
				t.Fatal("malformed artifact accepted")
			}
		})
	}
	for _, body := range [][]byte{append(append([]byte{}, base...), '\n'), bytes.Replace(base, []byte(`"schema_version":1`), []byte(`"schema_version":1,"schema_version":1`), 1), bytes.Repeat([]byte(" "), 65537)} {
		if _, err := parseCalibration(body, f.Binding.RetrievalSHA256); err == nil {
			t.Fatal("noncanonical or oversized artifact accepted")
		}
	}
}

func calibratedFixture(t *testing.T) *imageBundleFixture {
	t.Helper()
	f := newObservationFixture(t)
	base := f.open(t)
	var manifest map[string]any
	_ = json.Unmarshal(rawJSON(t, base.Manifest), &manifest)
	retrieval, err := calibrationRetrieval(manifest)
	if err != nil {
		t.Fatal(err)
	}
	scope, _ := calibrationScope([]string{firstID, secondID})
	artifact := calibrationGoldens(t).Golden[0].Artifact
	artifact["retrieval_sha256"] = retrieval
	profile := artifact["profiles"].([]any)[0].(map[string]any)
	profile["eligible_entities_sha256"] = scope
	f.members["calibration.json"], _ = json.Marshal(artifact)
	f.manifest.FormatVersion = 5
	f.manifest.Calibration = &CalibrationManifest{SchemaVersion: 1, Member: "calibration.json", SHA256: digestString(f.members["calibration.json"]), Profiles: 1, RetrievalSHA256: retrieval}
	return f
}

func calibrationBundle(t *testing.T, f *imageBundleFixture, modify func(map[string]any)) []byte {
	t.Helper()
	f.manifest.Files = map[string]string{}
	for name, body := range f.members {
		f.manifest.Files[name] = digestString(body)
	}
	var manifest map[string]any
	_ = json.Unmarshal(rawJSON(t, f.manifest), &manifest)
	if modify != nil {
		modify(manifest)
	}
	var body bytes.Buffer
	w := zip.NewWriter(&body)
	for name, data := range f.members {
		member, _ := w.Create(name)
		_, _ = member.Write(data)
	}
	member, _ := w.Create("manifest.json")
	_, _ = member.Write(rawJSON(t, manifest))
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return body.Bytes()
}

func TestCalibrationOpenVerifyAndExactEligibleScope(t *testing.T) {
	f := calibratedFixture(t)
	d, err := Open(calibrationBundle(t, f, nil))
	if err != nil {
		t.Fatal(err)
	}
	if !d.HasCalibration() {
		t.Fatal("calibration not loaded")
	}
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	cosine := 0.75
	rows := []CalibrationCandidate{{Score: 0.75, Cosine: &cosine}, {Score: 0.5}}
	got, err := d.Calibrate("text", "hybrid", false, Filter{}, rows)
	if err != nil || got == nil || got.MatchStatus != "candidates" {
		t.Fatal(got, err)
	}
	// Matching the actual pool matters, not the spelling of a filter.
	equivalent, err := d.Calibrate("text", "hybrid", false, Filter{Source: "sample"}, rows)
	if err != nil || !reflect.DeepEqual(equivalent, got) {
		t.Fatal(equivalent, err)
	}
	for _, filter := range []Filter{{Kind: "sensor"}, {Source: "absent"}} {
		if result, err := d.Calibrate("text", "hybrid", false, filter, rows); err != nil || result != nil {
			t.Fatal("out-of-scope profile applied", result, err)
		}
	}
	for _, mode := range []string{"vector", "missing"} {
		result, err := d.Calibrate("text", mode, false, Filter{}, rows)
		if mode == "vector" && (err != nil || result != nil) || mode == "missing" && err == nil {
			t.Fatal(result, err)
		}
	}
	for name, modify := range map[string]func(map[string]any){
		"old format":             func(m map[string]any) { m["format_version"] = float64(4) },
		"missing descriptor":     func(m map[string]any) { delete(m, "calibration") },
		"null descriptor":        func(m map[string]any) { m["calibration"] = nil },
		"extra descriptor field": func(m map[string]any) { m["calibration"].(map[string]any)["extra"] = true },
		"descriptor count":       func(m map[string]any) { m["calibration"].(map[string]any)["profiles"] = float64(2) },
		"descriptor sha":         func(m map[string]any) { m["calibration"].(map[string]any)["sha256"] = strings.Repeat("0", 64) },
		"descriptor binding": func(m map[string]any) {
			m["calibration"].(map[string]any)["retrieval_sha256"] = strings.Repeat("0", 64)
		},
		"retrieval metadata": func(m map[string]any) { m["content_sha256"] = strings.Repeat("0", 64) },
		"member binding":     func(m map[string]any) { m["files"].(map[string]any)["vectors.f32"] = strings.Repeat("0", 64) },
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := Open(calibrationBundle(t, f, modify)); err == nil {
				t.Fatal("tampered calibration bundle accepted")
			}
		})
	}
	f.members["calibration.json"] = append(f.members["calibration.json"], '\n')
	f.manifest.Calibration.SHA256 = digestString(f.members["calibration.json"])
	if _, err := Open(calibrationBundle(t, f, nil)); err == nil {
		t.Fatal("noncanonical artifact accepted after updating checksums")
	}
}

func TestCalibrationRejectsInvalidRankingSignals(t *testing.T) {
	cosine := 1.0
	for _, rows := range [][]CalibrationCandidate{
		{{Score: math.NaN(), Cosine: &cosine}},
		{{Score: math.Inf(1), Cosine: &cosine}},
		{{Score: 0.5, Cosine: &cosine}, {Score: 0.6}},
		{{Score: 5, Cosine: &cosine}, {Score: 0}},
	} {
		if _, err := candidateSignals("text", rows); err == nil {
			t.Fatal("invalid ranking signals accepted")
		}
	}
}

func TestCalibrationImagePoolIncludesOnlyIndexedMediaReferences(t *testing.T) {
	d := calibratedFixture(t).open(t)
	if err := d.LoadImages(); err != nil {
		t.Fatal(err)
	}
	// A text-searchable entity can have no indexed image in the current gallery.
	for i := range d.images.records {
		if d.images.records[i].References[0].EntityID == secondID {
			d.images.records[i].VectorIndex = nil
		}
	}
	scope, _ := calibrationScope([]string{firstID})
	d.calibration.Profiles = []calibrationProfile{{calibrationContext: calibrationContext{QueryType: "image", EligibleEntitiesSHA256: scope}, Minimums: map[string]int{"image_cosine": 500000, "ranking_margin": 0}}}
	cosine := .75
	rows := []CalibrationCandidate{{Score: .75, Cosine: &cosine, Matches: []Match{{Channel: "image", Score: .75}}}}
	decision, err := d.Calibrate("image", "", false, Filter{}, rows)
	if err != nil || decision == nil || decision.MatchStatus != "candidates" {
		t.Fatal(decision, err)
	}
	// Combined retrieval can also return entities without an indexed image.
	mode := "hybrid"
	d.calibration.Profiles[0].QueryType, d.calibration.Profiles[0].TextMode = "image_text", &mode
	d.calibration.Profiles[0].Minimums["semantic_cosine"] = 500000
	if decision, err := d.Calibrate("image_text", "hybrid", false, Filter{}, rows); err != nil || decision != nil {
		t.Fatal("combined mode incorrectly used the image-only pool", decision, err)
	}
}

func TestCalibrationFramingUsesStableNumberAndStringBytes(t *testing.T) {
	for _, test := range []struct {
		value any
		want  string
	}{
		{nil, "n"}, {false, "f"}, {true, "t"},
		{float64(1), "d3ff0000000000000;"},
		{math.Copysign(0, -1), "d0000000000000000;"},
		{"é", "s2:é"},
		{[]any{nil, true}, "a2:nt"},
		{map[string]any{"b": false, "a": "x"}, "o2:s1:as1:xs1:bf"},
	} {
		got, err := calibrationFrame(test.value)
		if err != nil || string(got) != test.want {
			t.Fatalf("%v: got %q (%v), want %q", test.value, got, err, test.want)
		}
	}
	for _, value := range []float64{math.NaN(), math.Inf(1), 9007199254740992} {
		if _, err := calibrationFrame(value); err == nil {
			t.Fatalf("accepted unsupported number %v", value)
		}
	}
}

func TestCalibrationFeaturesUseRunnerUpAndMissingChannelAbstains(t *testing.T) {
	cosine := 0.75
	rows := []CalibrationCandidate{{Score: 0.4, Cosine: &cosine}, {Score: 0.375}}
	want := map[string]int{"semantic_cosine": 750000, "ranking_margin": 25000}
	if got, err := candidateSignals("text", rows); err != nil || !reflect.DeepEqual(got, want) {
		t.Fatal(got, err)
	}
	want["ranking_margin"] = 0
	if got, err := candidateSignals("text", rows[:1]); err != nil || !reflect.DeepEqual(got, want) {
		t.Fatal("singleton fabricated a runner-up margin", got)
	}
	if got, err := candidateSignals("image_text", rows); err != nil || got != nil {
		t.Fatal("missing image channel accepted", got)
	}
	rows[0].Matches = []Match{{Channel: "image", Score: .625}, {Channel: "image", Score: 1.000001}}
	want["image_cosine"], want["ranking_margin"] = 1000000, 25000
	if got, err := candidateSignals("image_text", rows); err != nil || !reflect.DeepEqual(got, want) {
		t.Fatal(got)
	}
}
