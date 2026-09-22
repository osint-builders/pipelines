package dataset

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"math"
	"sort"
	"strings"
	"testing"
)

func rawJSON(t *testing.T, value any) []byte {
	t.Helper()
	var body bytes.Buffer
	encoder := json.NewEncoder(&body)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		t.Fatal(err)
	}
	return bytes.TrimSpace(body.Bytes())
}

func newObservationFixture(t *testing.T) *imageBundleFixture {
	t.Helper()
	f := newImageFixture(t)
	f.manifest.FormatVersion = 4
	f.members["model/model.onnx"] = []byte("fixture text model, no inference in dataset tests")
	m := &ObservationManifest{SchemaVersion: 1, Dimensions: 384, VectorDType: "float32-le", EmbeddingModelSHA256: digestString(f.members["model/model.onnx"]), GallerySHA256: f.manifest.Image.GallerySHA256}
	m.Search.Aggregation = "max-v1"
	m.Search.Calibration = json.RawMessage("null")
	f.manifest.Observations = m
	recipe := json.RawMessage(`{"model_id":"test/ocr","model_revision":"revision-1","settings":{"language":"多语","scale":1.0},"version":"ocr-v1"}`)
	recipeID := digestString(recipe)
	f.members["observations/recipes.json"] = rawJSON(t, map[string]json.RawMessage{recipeID: recipe})
	var media []MediaRecord
	_ = json.Unmarshal(f.members["image/index.json"], &media)
	var selected MediaRecord
	for _, record := range media {
		if record.References[0].EntityID == secondID {
			selected = record
		}
	}
	fields := map[string]json.RawMessage{
		"kind": json.RawMessage(`"ocr"`), "origin": json.RawMessage(`"generated"`),
		"media_sha256": rawJSON(t, selected.SHA256), "recipe_sha256": rawJSON(t, recipeID),
		"text": json.RawMessage(`"Маркировка <TEST> 雷达"`), "confidence": json.RawMessage(`1.0`),
		"regions":    json.RawMessage(`[{"confidence":0.0,"polygon":[[0.0,0.0],[1.0,0.0],[1.0,1.0],[0.0,1.0]],"text":"Маркировка <TEST> 雷达"}]`),
		"media_ids":  rawJSON(t, []string{selected.ID}),
		"references": rawJSON(t, []ObservationReference{{MediaID: selected.ID, EntityID: secondID, EvidenceID: selected.References[0].EvidenceID}}),
	}
	f.setObservationRows(t, []map[string]json.RawMessage{fields})
	probes := []ObservationProbe{}
	for _, text := range []string{"label AB-12", "painted radar vehicle", "雷达标记"} {
		vector := make([]float32, 384)
		vector[0] = 1
		probes = append(probes, ObservationProbe{Text: text, Vector: vector})
	}
	f.members["observations/probes.json"] = rawJSON(t, probes)
	return f
}

func (f *imageBundleFixture) setObservationRows(t *testing.T, rows []map[string]json.RawMessage) {
	t.Helper()
	for _, fields := range rows {
		id, err := observationIdentity(fields)
		if err != nil {
			t.Fatal(err)
		}
		fields["id"] = rawJSON(t, id)
	}
	sort.SliceStable(rows, func(i, j int) bool { return string(rows[i]["id"]) < string(rows[j]["id"]) })
	chunks := make([]observationChunk, 0, len(rows))
	vectors := make([]byte, len(rows)*384*4)
	for i, fields := range rows {
		var id, text string
		_ = json.Unmarshal(fields["id"], &id)
		_ = json.Unmarshal(fields["text"], &text)
		chunks = append(chunks, observationChunk{ObservationID: id, Text: text, VectorIndex: i})
		binary.LittleEndian.PutUint32(vectors[(i*384+2)*4:], math.Float32bits(1))
	}
	f.members["observations/index.json"] = rawJSON(t, rows)
	f.members["observations/chunks.json"] = rawJSON(t, chunks)
	f.members["observations/vectors.f32"] = vectors
	f.manifest.Observations.Records = len(rows)
	f.manifest.Observations.Chunks = len(chunks)
	f.manifest.Observations.IndexSHA256 = digestString(f.members["observations/index.json"])
	kinds := map[string]bool{}
	observed := map[string]string{}
	for _, fields := range rows {
		var row Observation
		_ = json.Unmarshal(rawJSON(t, fields), &row)
		kinds[row.Kind] = true
		for _, id := range row.MediaIDs {
			observed[id+"\x00"+row.Kind] = row.ID
		}
	}
	if len(kinds) == 0 {
		kinds["ocr"] = true
	}
	orderedKinds := make([]string, 0, len(kinds))
	for kind := range kinds {
		orderedKinds = append(orderedKinds, kind)
	}
	sort.Strings(orderedKinds)
	var media []MediaRecord
	_ = json.Unmarshal(f.members["image/index.json"], &media)
	outcomes := []map[string]string{}
	for _, record := range media {
		if record.VectorIndex == nil {
			continue
		}
		for _, kind := range orderedKinds {
			outcome := map[string]string{"media_id": record.ID, "kind": kind, "state": "empty"}
			if id := observed[record.ID+"\x00"+kind]; id != "" {
				outcome["state"], outcome["observation_id"] = "observed", id
			}
			outcomes = append(outcomes, outcome)
		}
	}
	counts := map[string]int{}
	for _, outcome := range outcomes {
		counts[outcome["state"]]++
	}
	f.members["observations/report.json"] = rawJSON(t, map[string]any{"schema_version": 1, "gallery_sha256": f.manifest.Image.GallerySHA256, "kinds": orderedKinds, "outcomes": outcomes, "counts": counts})
}

func (f *imageBundleFixture) editObservation(t *testing.T, edit func(map[string]json.RawMessage)) {
	t.Helper()
	var rows []map[string]json.RawMessage
	if err := json.Unmarshal(f.members["observations/index.json"], &rows); err != nil {
		t.Fatal(err)
	}
	edit(rows[0])
	f.setObservationRows(t, rows)
}

func TestObservationSearchIsOptInAndKeepsSourceNamesAuthoritative(t *testing.T) {
	f := newObservationFixture(t)
	d := f.open(t)
	vector := make([]float32, 384)
	vector[2] = 1
	defaultResults, err := d.Search(vector, "painted label", true, Filter{}, 2, "")
	if err != nil || defaultResults[0].ID != firstID || d.observations != nil {
		t.Fatal(defaultResults, err)
	}
	results, err := d.SearchObservations(vector, "painted label", true, Filter{}, 2)
	if err != nil || results[0].ID != secondID || results[0].Score != 1 || *results[0].Cosine != 1 || results[0].NameMatch {
		t.Fatal(results, err)
	}
	match := results[0].Matches[0]
	if match.Channel != "ocr" || match.Origin != "generated" || match.ObservationID == "" || match.MediaID == "" || match.RecipeSHA256 == "" || match.ModelID != "test/ocr" || match.ModelRevision != "revision-1" || match.ModelSHA256 != "" || match.URL != "https://sample.test/1/0" {
		t.Fatal(match)
	}
	results, err = d.SearchObservations(vector, "Cheese Board", true, Filter{}, 2)
	if err != nil || results[0].ID != firstID || !results[0].NameMatch || results[0].Matches[0].Channel != "text" {
		t.Fatal(results, err)
	}
	after, err := d.Search(vector, "painted label", true, Filter{}, 2, "")
	if err != nil || string(rawJSON(t, after)) != string(rawJSON(t, defaultResults)) {
		t.Fatal("default ranking changed")
	}
	filtered, err := d.SearchObservations(vector, "painted label", false, Filter{Kind: "radar"}, 1)
	if err != nil || len(filtered) != 1 || filtered[0].ID != firstID {
		t.Fatal(filtered, err)
	}
}

func TestObservationSourceTiesWinAndCombinedSearchKeepsTwoChannels(t *testing.T) {
	f := newObservationFixture(t)
	binary.LittleEndian.PutUint32(f.members["vectors.f32"][(2*384+1)*4:], 0)
	binary.LittleEndian.PutUint32(f.members["vectors.f32"][(2*384+2)*4:], math.Float32bits(1))
	d := f.open(t)
	text := make([]float32, 384)
	text[2] = 1
	results, err := d.SearchObservations(text, "label", false, Filter{}, 2)
	if err != nil || results[0].Matches[0].Channel != "text" {
		t.Fatal("source lost exact cosine tie", results, err)
	}
	d = newObservationFixture(t).open(t)
	imageVector := make([]float32, 512)
	imageVector[1] = 1
	results, err = d.SearchImagesWithObservations(imageVector, text, "label", Filter{}, 2)
	if err != nil || results[0].ID != secondID || math.Abs(results[0].Score-2.0/61) > 1e-12 || len(results[0].Matches) != 2 || results[0].Matches[0].Channel != "image" || results[0].Matches[1].Channel != "ocr" {
		t.Fatal(results, err)
	}
	if _, err := d.SearchImagesWithObservations(imageVector, nil, "", Filter{}, 1); err == nil {
		t.Fatal("image-only observations accepted")
	}
}

func TestGeneratedContributionReportsSourceNameBoostAndRawCosine(t *testing.T) {
	d := newObservationFixture(t).open(t)
	text := make([]float32, 384)
	text[2] = 1
	results, err := d.SearchObservations(text, "Weather sensor", true, Filter{}, 2)
	if err != nil || results[0].ID != secondID || !results[0].NameMatch || results[0].Score != 3 || *results[0].Cosine != 1 || results[0].Matches[0].Score != 3 || results[0].Matches[0].Channel != "ocr" {
		t.Fatal(results, err)
	}
	results, err = d.SearchObservations(text, "Маркировка <TEST> 雷达", true, Filter{}, 2)
	if err != nil || results[0].ID != secondID || results[0].NameMatch || results[0].Score != 1 || results[0].Matches[0].Score != 1 {
		t.Fatal("generated text earned a name bonus", results, err)
	}
	image := make([]float32, 512)
	image[1] = 1
	results, err = d.SearchImagesWithObservations(image, text, "Weather sensor", Filter{}, 2)
	if err != nil || results[0].ID != secondID || results[0].Matches[1].Score != 3 || *results[0].Cosine != 1 {
		t.Fatal("combined search changed text contribution scoring", results, err)
	}
}

func TestSourceNameWinsExtremeCosineScoreTie(t *testing.T) {
	f := newObservationFixture(t)
	for i := range 2 {
		binary.LittleEndian.PutUint32(f.members["vectors.f32"][(i*384)*4:], 0)
		binary.LittleEndian.PutUint32(f.members["vectors.f32"][(i*384+2)*4:], math.Float32bits(-1))
	}
	text := make([]float32, 384)
	text[2] = 1
	results, err := f.open(t).SearchObservations(text, "Cheese Board", true, Filter{}, 2)
	if err != nil || results[0].ID != firstID || !results[0].NameMatch || results[0].Score != 1 || results[1].Score != 1 {
		t.Fatal("source name lost a tied score against generated cosine", results, err)
	}
}

func TestObservationInspectionPreservesRawNumbersUnicodeAndOwnership(t *testing.T) {
	f := newObservationFixture(t)
	d := f.open(t)
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	listing, err := d.Observations(secondID, "")
	if err != nil || len(listing.Observations) != 1 || len(listing.Recipes) != 1 {
		t.Fatal(err)
	}
	raw := listing.Observations[0]
	if !bytes.Contains(raw, []byte(`"confidence":1.0`)) || !bytes.Contains(raw, []byte(`"confidence":0.0`)) || !bytes.Contains(raw, []byte("Маркировка <TEST> 雷达")) {
		t.Fatal("inspection changed raw provenance", string(raw))
	}
	var row Observation
	_ = json.Unmarshal(raw, &row)
	if _, err := d.Observations(firstID, row.ID); err == nil {
		t.Fatal("unrelated observation returned")
	}
	if absent, err := d.Observations(firstID, ""); err != nil || len(absent.Observations) != 0 {
		t.Fatal(absent, err)
	}
	listing.Observations[0][0] = '!'
	for hash := range listing.Recipes {
		listing.Recipes[hash][0] = '!'
	}
	if _, err := d.Observations(secondID, row.ID); err != nil {
		t.Fatal("inspection mutated cached observations", err)
	}
	probes, err := d.ObservationProbes()
	if err != nil || len(probes) != 3 {
		t.Fatal(err)
	}
	probes[0].Vector[0] = 5
	again, _ := d.ObservationProbes()
	if again[0].Vector[0] != 1 {
		t.Fatal("probe inspection mutated cache")
	}
}

func TestDescriptionObservationsAndDuplicatesDoNotAddRankBonus(t *testing.T) {
	f := newObservationFixture(t)
	f.editObservation(t, func(row map[string]json.RawMessage) {
		row["kind"] = json.RawMessage(`"description"`)
		row["confidence"] = json.RawMessage(`null`)
		row["regions"] = json.RawMessage(`[]`)
	})
	var rows []map[string]json.RawMessage
	_ = json.Unmarshal(f.members["observations/index.json"], &rows)
	second := map[string]json.RawMessage{}
	for key, value := range rows[0] {
		second[key] = value
	}
	second["text"] = json.RawMessage(`"Another description of the same image"`)
	second["kind"] = json.RawMessage(`"ocr"`)
	second["regions"] = json.RawMessage(`[{"confidence":null,"polygon":[[0,0],[1,0],[1,1],[0,1]],"text":"Another description of the same image"}]`)
	f.setObservationRows(t, append(rows, second))
	d := f.open(t)
	vector := make([]float32, 384)
	vector[2] = 1
	results, err := d.SearchObservations(vector, "label", false, Filter{}, 2)
	if err != nil || results[0].ID != secondID || results[0].Score != 1 || len(results[0].Matches) != 1 || results[0].Matches[0].Origin != "generated" {
		t.Fatal(results, err)
	}
}

func TestCorruptObservationsDoNotAffectDefaultSearchButFailOptIn(t *testing.T) {
	f := newObservationFixture(t)
	f.manifest.Observations.IndexSHA256 = strings.Repeat("0", 64)
	d := f.open(t)
	vector := make([]float32, 384)
	vector[0] = 1
	if _, err := d.Search(vector, "label", true, Filter{}, 1, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := d.SearchObservations(vector, "label", true, Filter{}, 1); err == nil {
		t.Fatal("corrupt extension searched")
	}
	if err := d.Verify(); err == nil {
		t.Fatal("corrupt extension verified")
	}
	if _, err := fixture(t, false).SearchObservations(vector, "label", true, Filter{}, 1); err == nil {
		t.Fatal("format2 observations available")
	}
	if _, err := newImageFixture(t).open(t).SearchObservations(vector, "label", true, Filter{}, 1); err == nil {
		t.Fatal("format3 observations available")
	}
}

func TestObservationIntegrityRejectsInvalidSidecars(t *testing.T) {
	for _, test := range []struct {
		name string
		edit func(*imageBundleFixture)
	}{
		{"schema", func(f *imageBundleFixture) { f.manifest.Observations.SchemaVersion = 2 }},
		{"embedding identity", func(f *imageBundleFixture) { f.manifest.Observations.EmbeddingModelSHA256 = strings.Repeat("0", 64) }},
		{"gallery identity", func(f *imageBundleFixture) { f.manifest.Observations.GallerySHA256 = strings.Repeat("0", 64) }},
		{"dimensions", func(f *imageBundleFixture) { f.manifest.Observations.Dimensions = 512 }},
		{"unknown ranking", func(f *imageBundleFixture) { f.manifest.Observations.Search.Aggregation = "sum" }},
		{"too many chunks", func(f *imageBundleFixture) { f.manifest.Observations.Chunks = 100001 }},
		{"missing sidecar", func(f *imageBundleFixture) { delete(f.members, "observations/report.json") }},
		{"unexpected sidecar", func(f *imageBundleFixture) { f.members["observations/model.onnx"] = []byte("not allowed") }},
		{"zero vector", func(f *imageBundleFixture) { f.members["observations/vectors.f32"] = make([]byte, 384*4) }},
		{"nan vector", func(f *imageBundleFixture) {
			binary.LittleEndian.PutUint32(f.members["observations/vectors.f32"], math.Float32bits(float32(math.NaN())))
		}},
		{"orphan vector", func(f *imageBundleFixture) {
			f.members["observations/vectors.f32"] = append(f.members["observations/vectors.f32"], make([]byte, 384*4)...)
		}},
		{"no chunk", func(f *imageBundleFixture) {
			f.members["observations/chunks.json"] = []byte(`[]`)
			f.members["observations/vectors.f32"] = []byte{}
			f.manifest.Observations.Chunks = 0
		}},
		{"too few probes", func(f *imageBundleFixture) { f.members["observations/probes.json"] = []byte(`[]`) }},
		{"null probe component", func(f *imageBundleFixture) {
			f.members["observations/probes.json"] = bytes.Replace(f.members["observations/probes.json"], []byte(`1,0`), []byte(`1,null`), 1)
		}},
		{"duplicate recipe key", func(f *imageBundleFixture) {
			f.members["observations/recipes.json"] = []byte(`{"duplicate":{},"duplicate":{}}`)
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newObservationFixture(t)
			test.edit(f)
			if err := f.open(t).LoadObservations(); err == nil {
				t.Fatal("invalid observation sidecar accepted")
			}
		})
	}
	for _, test := range []struct {
		name string
		edit func(map[string]json.RawMessage)
	}{
		{"invalid kind", func(r map[string]json.RawMessage) { r["kind"] = json.RawMessage(`"source"`) }},
		{"wrong origin", func(r map[string]json.RawMessage) { r["origin"] = json.RawMessage(`"source"`) }},
		{"missing recipe", func(r map[string]json.RawMessage) { r["recipe_sha256"] = rawJSON(t, strings.Repeat("0", 64)) }},
		{"changed original", func(r map[string]json.RawMessage) { r["media_sha256"] = rawJSON(t, strings.Repeat("0", 64)) }},
		{"unknown media", func(r map[string]json.RawMessage) { r["media_ids"] = json.RawMessage(`["sample:media:unknown"]`) }},
		{"missing association", func(r map[string]json.RawMessage) { r["references"] = json.RawMessage(`[]`) }},
		{"empty text", func(r map[string]json.RawMessage) { r["text"] = json.RawMessage(`" "`) }},
		{"excessive text", func(r map[string]json.RawMessage) { r["text"] = rawJSON(t, strings.Repeat("雪", 16385)) }},
		{"confidence range", func(r map[string]json.RawMessage) { r["confidence"] = json.RawMessage(`1.01`) }},
		{"null regions", func(r map[string]json.RawMessage) { r["regions"] = json.RawMessage(`null`) }},
		{"unexpected row field", func(r map[string]json.RawMessage) { r["unattributed"] = json.RawMessage(`"text"`) }},
		{"unattributed OCR text", func(r map[string]json.RawMessage) { r["text"] = json.RawMessage(`"text not present in any region"`) }},
		{"caption confidence", func(r map[string]json.RawMessage) {
			r["kind"] = json.RawMessage(`"description"`)
			r["regions"] = json.RawMessage(`[]`)
		}},
		{"caption regions", func(r map[string]json.RawMessage) {
			r["kind"] = json.RawMessage(`"description"`)
			r["confidence"] = json.RawMessage(`null`)
		}},
		{"polygon outside", func(r map[string]json.RawMessage) {
			r["regions"] = json.RawMessage(`[{"text":"label","confidence":0.5,"polygon":[[0,0],[2,0],[1,1],[0,1]]}]`)
		}},
		{"wrong point dimensions", func(r map[string]json.RawMessage) {
			r["regions"] = json.RawMessage(`[{"text":"label","confidence":0.5,"polygon":[[0,0,0],[1,0],[1,1],[0,1]]}]`)
		}},
		{"null polygon coordinate", func(r map[string]json.RawMessage) {
			r["text"] = json.RawMessage(`"label"`)
			r["regions"] = json.RawMessage(`[{"text":"label","confidence":0.5,"polygon":[[null,0],[1,0],[1,1],[0,1]]}]`)
		}},
		{"zero area polygon", func(r map[string]json.RawMessage) {
			r["text"] = json.RawMessage(`"label"`)
			r["regions"] = json.RawMessage(`[{"text":"label","confidence":0.5,"polygon":[[0,0],[0,0],[0,0],[0,0]]}]`)
		}},
		{"missing region confidence", func(r map[string]json.RawMessage) {
			r["text"] = json.RawMessage(`"label"`)
			r["regions"] = json.RawMessage(`[{"text":"label","polygon":[[0,0],[1,0],[1,1],[0,1]]}]`)
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newObservationFixture(t)
			f.editObservation(t, test.edit)
			if err := f.open(t).LoadObservations(); err == nil {
				t.Fatal("invalid observation accepted")
			}
		})
	}
}

func TestObservationReportAccountsForEveryImageAndKind(t *testing.T) {
	for _, test := range []struct {
		name string
		edit func(map[string]json.RawMessage, []map[string]string)
	}{
		{"wrong schema", func(report map[string]json.RawMessage, _ []map[string]string) {
			report["schema_version"] = json.RawMessage(`2`)
		}},
		{"wrong gallery", func(report map[string]json.RawMessage, _ []map[string]string) {
			report["gallery_sha256"] = rawJSON(t, strings.Repeat("0", 64))
		}},
		{"wrong counts", func(report map[string]json.RawMessage, _ []map[string]string) {
			report["counts"] = json.RawMessage(`{}`)
		}},
		{"missing kinds", func(report map[string]json.RawMessage, _ []map[string]string) { delete(report, "kinds") }},
		{"duplicate kinds", func(report map[string]json.RawMessage, _ []map[string]string) {
			report["kinds"] = json.RawMessage(`["ocr","ocr"]`)
		}},
		{"uncovered kind", func(report map[string]json.RawMessage, _ []map[string]string) {
			report["kinds"] = json.RawMessage(`["description","ocr"]`)
		}},
		{"missing outcome", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			report["outcomes"] = rawJSON(t, outcomes[1:])
		}},
		{"duplicate outcome", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			report["outcomes"] = rawJSON(t, append(outcomes, outcomes[0]))
		}},
		{"unrelated media", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			outcomes[0]["media_id"] = "sample:media:other"
			report["outcomes"] = rawJSON(t, outcomes)
		}},
		{"invalid state", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			outcomes[0]["state"] = "pending"
			report["outcomes"] = rawJSON(t, outcomes)
		}},
		{"wrong observed identity", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			for _, outcome := range outcomes {
				if outcome["state"] == "observed" {
					outcome["observation_id"] = strings.Repeat("0", 64)
				}
			}
			report["outcomes"] = rawJSON(t, outcomes)
		}},
		{"stale empty outcome", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			for _, outcome := range outcomes {
				if outcome["state"] == "observed" {
					outcome["state"] = "empty"
					delete(outcome, "observation_id")
				}
			}
			report["outcomes"] = rawJSON(t, outcomes)
		}},
		{"empty observation id", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			for _, outcome := range outcomes {
				if outcome["state"] == "empty" {
					outcome["observation_id"] = ""
				}
			}
			report["outcomes"] = rawJSON(t, outcomes)
		}},
		{"failed without reason", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			for _, outcome := range outcomes {
				if outcome["state"] == "empty" {
					outcome["state"] = "failed"
				}
			}
			report["outcomes"] = rawJSON(t, outcomes)
		}},
		{"failed host path", func(report map[string]json.RawMessage, outcomes []map[string]string) {
			for _, outcome := range outcomes {
				if outcome["state"] == "empty" {
					outcome["state"], outcome["reason"] = "failed", "C:/models/failed.bin"
				}
			}
			report["outcomes"] = rawJSON(t, outcomes)
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newObservationFixture(t)
			var report map[string]json.RawMessage
			var outcomes []map[string]string
			_ = json.Unmarshal(f.members["observations/report.json"], &report)
			_ = json.Unmarshal(report["outcomes"], &outcomes)
			test.edit(report, outcomes)
			f.members["observations/report.json"] = rawJSON(t, report)
			if err := f.open(t).LoadObservations(); err == nil {
				t.Fatal("incomplete or incorrect analysis report accepted")
			}
		})
	}
	f := newObservationFixture(t)
	f.setObservationRows(t, []map[string]json.RawMessage{})
	var report map[string]json.RawMessage
	var outcomes []map[string]string
	_ = json.Unmarshal(f.members["observations/report.json"], &report)
	_ = json.Unmarshal(report["outcomes"], &outcomes)
	outcomes[0]["state"], outcomes[0]["reason"] = "failed", "RuntimeError"
	outcomes[1]["state"] = "selection"
	report["outcomes"] = rawJSON(t, outcomes)
	report["counts"] = json.RawMessage(`{"empty":1,"failed":1,"selection":1}`)
	f.members["observations/report.json"] = rawJSON(t, report)
	d := f.open(t)
	if err := d.LoadObservations(); err != nil {
		t.Fatal("explicit unsuccessful analysis outcomes rejected", err)
	}
	if rows, err := d.Observations(secondID, ""); err != nil || len(rows.Observations) != 0 {
		t.Fatal(rows, err)
	}
}

func TestObservationReportsAllowEmptyGalleryButNotDuplicateAnalysis(t *testing.T) {
	f := newObservationFixture(t)
	f.setRecords(t, []MediaRecord{})
	f.manifest.Image.Vectors = 0
	f.manifest.Image.PreviewBytes = 0
	f.members["image/vectors.f16"] = []byte{}
	for member := range f.members {
		if strings.HasPrefix(member, "image/previews/") {
			delete(f.members, member)
		}
	}
	f.manifest.Observations.GallerySHA256 = f.manifest.Image.GallerySHA256
	f.setObservationRows(t, []map[string]json.RawMessage{})
	if err := f.open(t).LoadObservations(); err != nil {
		t.Fatal(err)
	}
	f = newObservationFixture(t)
	var rows []map[string]json.RawMessage
	_ = json.Unmarshal(f.members["observations/index.json"], &rows)
	other := map[string]json.RawMessage{}
	for key, value := range rows[0] {
		other[key] = value
	}
	other["confidence"] = json.RawMessage(`0.5`)
	f.setObservationRows(t, append(rows, other))
	if err := f.open(t).LoadObservations(); err == nil {
		t.Fatal("multiple observations for one media/kind accepted")
	}
}

func TestObservationIdentityMatchesPythonCanonicalUTF8AndFloatLexemes(t *testing.T) {
	// Independent hashlib/json.dumps golden with ensure_ascii=False and sorted compact keys.
	recipe := []byte(`{"model_id":"test/ocr","model_revision":"revision-1","settings":{"language":"多语","scale":1.0},"version":"ocr-v1"}`)
	if digestString(recipe) != "dceeb98f5cd823ca56053a7072a250474a54258aefc4dbf82a6e7326181f2af9" {
		t.Fatal("recipe hash changed")
	}
	fields, err := rawObject([]byte(`{"confidence":1.0,"kind":"ocr","media_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","recipe_sha256":"dceeb98f5cd823ca56053a7072a250474a54258aefc4dbf82a6e7326181f2af9","regions":[{"confidence":0.0,"polygon":[[0.0,0.0],[1.0,0.0],[1.0,1.0],[0.0,1.0]],"text":"Маркировка <TEST> 雷达"}],"text":"Маркировка <TEST> 雷达"}`))
	if err != nil {
		t.Fatal(err)
	}
	id, err := observationIdentity(fields)
	if err != nil || id != "c255b196290d08a86dba000883c873f0ffa0e6941a841787dda924808b04735e" {
		t.Fatal("observation canonical hash changed", id, err)
	}
}
