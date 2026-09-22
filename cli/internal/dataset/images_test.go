package dataset

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/gif"
	"image/png"
	"math"
	"sort"
	"strings"
	"testing"

	"github.com/gomlx/compute/dtypes/float16"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

type imageBundleFixture struct {
	manifest Manifest
	members  map[string][]byte
}

func newImageFixture(t *testing.T) *imageBundleFixture {
	t.Helper()
	base := fixture(t, false)
	f := &imageBundleFixture{manifest: base.Manifest, members: map[string][]byte{}}
	for name := range base.Manifest.Files {
		body, err := base.Read(name)
		if err != nil {
			t.Fatal(err)
		}
		f.members[name] = body
	}
	f.manifest.FormatVersion = 3
	f.manifest.Sources = []string{"sample"}
	f.manifest.ContentSHA256, f.manifest.RecipeSHA256 = strings.Repeat("a", 64), strings.Repeat("b", 64)
	f.manifest.DatasetID = digestString([]byte(f.manifest.ContentSHA256 + f.manifest.RecipeSHA256))
	graph := []byte("graph execution is validated separately by the image runtime")
	f.members["image/model.onnx"] = graph
	f.members["image/model.json"], _ = json.Marshal(map[string]any{
		"schema_version": 1, "file": "model.onnx", "bytes": len(graph), "sha256": digestString(graph),
		"input": "pixel_values", "output": "image_features", "shape": []int{1, 3, 256, 256},
		"dimensions": 512, "normalization": "l2", "preprocess": imagepreprocess.DefaultRecipe(),
	})
	f.manifest.Image = &ImageManifest{SchemaVersion: 1, ModelSHA256: digestString(graph), Dimensions: 512, VectorDType: "float16-le", Vectors: 2,
		Search: ImageSearch{Fusion: "rrf-v1", RankConstant: 60, Calibration: json.RawMessage("null")}}
	pixels := image.NewNRGBA(image.Rect(0, 0, 3, 2))
	for y := range 2 {
		for x := range 3 {
			pixels.SetNRGBA(x, y, color.NRGBA{uint8(x * 50), uint8(y * 80), 20, 255})
		}
	}
	var imageBody bytes.Buffer
	if err := png.Encode(&imageBody, pixels); err != nil {
		t.Fatal(err)
	}
	preview := &MediaPreview{Member: "image/previews/" + digestString(imageBody.Bytes()) + ".png", SHA256: digestString(imageBody.Bytes()), ContentType: "image/png", Width: 3, Height: 2}
	f.members[preview.Member] = imageBody.Bytes()
	f.manifest.Image.PreviewBytes = int64(imageBody.Len())
	var records []MediaRecord
	for i, entity := range []string{firstID, secondID, firstID} {
		vectorIndex := i % 2
		mediaURL := fmt.Sprintf("https://sample.test/media/%d.png", i)
		evidenceURL := fmt.Sprintf("https://sample.test/%d/0", vectorIndex)
		records = append(records, MediaRecord{ID: "sample:media:" + digestString([]byte(mediaURL))[:24], Source: "sample", URL: mediaURL,
			SHA256: digestString([]byte(fmt.Sprintf("original%d", vectorIndex))), ContentType: "image/png", Width: 3, Height: 2,
			Caption: fmt.Sprintf("view %d", i), References: []MediaReference{{EntityID: entity, EvidenceID: digestString([]byte(evidenceURL))[:24], Caption: "source caption", Section: "description", Association: "source_context"}}, VectorIndex: &vectorIndex, Preview: preview})
	}
	sort.Slice(records, func(i, j int) bool { return records[i].ID < records[j].ID })
	f.setRecords(t, records)
	f.members["image/vectors.f16"] = make([]byte, 2*512*2)
	binary.LittleEndian.PutUint16(f.members["image/vectors.f16"], float16.FromFloat32(1).Bits())
	binary.LittleEndian.PutUint16(f.members["image/vectors.f16"][(512+1)*2:], float16.FromFloat32(1).Bits())
	var probes []ImageProbe
	for i := range 3 {
		id := fmt.Sprintf("probe-%d", i)
		vector := make([]float32, 512)
		vector[0] = 1
		probes = append(probes, ImageProbe{ID: id, ImageMember: "image/probes/" + id + ".png", Vector: vector})
		f.members[probes[i].ImageMember] = imageBody.Bytes()
	}
	f.members["image/probes.json"], _ = json.Marshal(probes)
	return f
}

func (f *imageBundleFixture) setRecords(t *testing.T, records []MediaRecord) {
	t.Helper()
	f.members["image/index.json"], _ = json.Marshal(records)
	f.manifest.Image.Records = len(records)
	f.manifest.Image.GallerySHA256 = digestString(f.members["image/index.json"])
}

func (f *imageBundleFixture) editRecords(t *testing.T, edit func([]MediaRecord) []MediaRecord) {
	t.Helper()
	var records []MediaRecord
	if err := json.Unmarshal(f.members["image/index.json"], &records); err != nil {
		t.Fatal(err)
	}
	f.setRecords(t, edit(records))
}

func (f *imageBundleFixture) open(t *testing.T) *Dataset {
	t.Helper()
	f.manifest.Files = map[string]string{}
	for name, body := range f.members {
		f.manifest.Files[name] = digestString(body)
	}
	manifest, _ := json.Marshal(f.manifest)
	var output bytes.Buffer
	writer := zip.NewWriter(&output)
	for name, body := range f.members {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = entry.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	entry, _ := writer.Create("manifest.json")
	_, _ = entry.Write(manifest)
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	d, err := Open(output.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func TestImageSearchUsesBestViewAndEvidenceWithoutLoadingText(t *testing.T) {
	f := newImageFixture(t)
	delete(f.members, "vectors.f32")
	d := f.open(t)
	vector := make([]float32, 512)
	vector[0] = 1
	results, err := d.SearchImages(vector, nil, "", Filter{}, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 2 || results[0].ID != firstID || results[0].Score != 1 || results[0].Cosine != nil || results[0].NameMatch || d.vectors != nil {
		t.Fatalf("unexpected image-only results: %+v", results)
	}
	if len(results[0].Matches) != 1 || results[0].Matches[0].Channel != "image" || results[0].Matches[0].URL != "https://sample.test/0/0" || results[0].Matches[0].MediaID == "" {
		t.Fatal(results[0])
	}
	filtered, err := d.SearchImages(vector, nil, "", Filter{Kind: "sensor"}, 1)
	if err != nil || len(filtered) != 1 || filtered[0].ID != secondID {
		t.Fatal(filtered, err)
	}
	filtered, err = d.SearchImages(vector, nil, "", Filter{Source: "absent"}, 1)
	if err != nil || len(filtered) != 0 {
		t.Fatal(filtered, err)
	}
}

func TestArchivedGIFMetadataCannotEnterImageSearch(t *testing.T) {
	var original bytes.Buffer
	if err := gif.Encode(&original, image.NewNRGBA(image.Rect(0, 0, 3, 2)), nil); err != nil {
		t.Fatal(err)
	}
	if _, err := imagepreprocess.Preprocess(original.Bytes(), imagepreprocess.DefaultRecipe()); err == nil {
		t.Fatal("GIF query image was accepted")
	}
	for _, change := range []string{"none", "reason", "vector", "preview"} {
		t.Run(change, func(t *testing.T) {
			f := newImageFixture(t)
			mediaURL := "https://sample.test/media/original.gif"
			mediaID := "sample:media:" + digestString([]byte(mediaURL))[:24]
			f.editRecords(t, func(records []MediaRecord) []MediaRecord {
				row := MediaRecord{
					ID: mediaID, Source: "sample", URL: mediaURL,
					SHA256: digestString(original.Bytes()), ContentType: "image/gif", Width: 3, Height: 2,
					References: records[0].References, ExclusionReason: "unsupported_image_format",
				}
				switch change {
				case "reason":
					row.ExclusionReason = "selection"
				case "vector":
					index := 0
					row.VectorIndex = &index
				case "preview":
					row.Preview = records[0].Preview
				}
				records = append(records, row)
				sort.Slice(records, func(i, j int) bool { return records[i].ID < records[j].ID })
				return records
			})
			d := f.open(t)
			err := d.LoadImages()
			if change != "none" {
				if err == nil || !strings.Contains(err.Error(), "GIF metadata") {
					t.Fatalf("invalid GIF metadata was accepted: %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			vector := make([]float32, 512)
			vector[0] = 1
			results, err := d.SearchImages(vector, nil, "", Filter{}, 2)
			if err != nil || len(results) != 2 {
				t.Fatal(results, err)
			}
			for _, result := range results {
				for _, match := range result.Matches {
					if match.MediaID == mediaID {
						t.Fatal("excluded GIF contributed an image match")
					}
				}
			}
		})
	}
}

func TestCombinedSearchUsesEntityRanksAndKeepsLegacyCosine(t *testing.T) {
	d := newImageFixture(t).open(t)
	imageVector := make([]float32, 512)
	imageVector[0] = 1
	textVector := make([]float32, 384)
	textVector[1] = 1
	results, err := d.SearchImages(imageVector, textVector, "", Filter{}, 1)
	if err != nil {
		t.Fatal(err)
	}
	expected := 1.0/61 + 1.0/62
	if len(results) != 1 || results[0].ID != firstID || math.Abs(results[0].Score-expected) > 1e-12 || results[0].Cosine == nil || *results[0].Cosine != 0 || len(results[0].Matches) != 2 {
		t.Fatalf("unexpected fused ranking: %+v", results)
	}
	results, err = d.SearchImages(imageVector, textVector, "Weather sensor", Filter{}, 2)
	if err != nil || !results[1].NameMatch || *results[1].Cosine != 1 {
		t.Fatal(results, err)
	}
	legacy, err := d.Search(textVector, "Weather sensor", true, Filter{}, 2, "")
	if err != nil || legacy[0].ID != secondID || legacy[0].Score != 3 {
		t.Fatal(legacy, err)
	}
}

func TestCombinedSearchResolvesOnlyReturnedTextEvidence(t *testing.T) {
	f := newImageFixture(t)
	f.editRecords(t, func(records []MediaRecord) []MediaRecord {
		kept := []MediaRecord{}
		for _, record := range records {
			if *record.VectorIndex == 0 {
				kept = append(kept, record)
			}
		}
		return kept
	})
	f.manifest.Image.Vectors = 1
	f.members["image/vectors.f16"] = f.members["image/vectors.f16"][:512*2]
	d := f.open(t)
	imageVector, textVector := make([]float32, 512), make([]float32, 384)
	imageVector[0], textVector[1] = 1, 1
	results, err := d.SearchImages(imageVector, textVector, "", Filter{}, 1)
	if err != nil || len(results) != 1 || results[0].ID != firstID {
		t.Fatal(results, err)
	}
	if d.evidenceURLs[1] != nil {
		t.Fatal("resolved provenance for discarded text-only candidate")
	}
}

func TestFloat16VectorsAreRenormalizedForSearch(t *testing.T) {
	f := newImageFixture(t)
	binary.LittleEndian.PutUint16(f.members["image/vectors.f16"], float16.FromFloat32(0.8).Bits())
	binary.LittleEndian.PutUint16(f.members["image/vectors.f16"][2:], float16.FromFloat32(0.6).Bits())
	d := f.open(t)
	vector := make([]float32, 512)
	vector[0], vector[1] = 0.8, 0.6
	results, err := d.SearchImages(vector, nil, "", Filter{}, 1)
	if err != nil || len(results) != 1 || results[0].ID != firstID || results[0].Score < 0.999999 {
		t.Fatal(results, err)
	}
}

func TestMediaInspectionChecksOwnershipAndReturnsExactPreview(t *testing.T) {
	f := newImageFixture(t)
	d := f.open(t)
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	rows, err := d.Media(firstID, "")
	if err != nil || len(rows) != 2 {
		t.Fatal(rows, err)
	}
	preview, err := d.MediaPreview(firstID, rows[0].ID)
	if err != nil || !bytes.Equal(preview, f.members[rows[0].Preview.Member]) {
		t.Fatal(err)
	}
	if _, err = d.Media(secondID, rows[0].ID); err == nil {
		t.Fatal("unrelated media returned")
	}
	if _, err = d.MediaPreview(firstID, ""); err == nil {
		t.Fatal("ambiguous media selection accepted")
	}
	rows[0].References[0].EvidenceID = "mutated"
	rows[0].Preview.Member = "mutated"
	*rows[0].VectorIndex = 999
	if _, err = d.MediaPreview(firstID, rows[0].ID); err != nil {
		t.Fatal("inspection mutated dataset", err)
	}
	probes, err := d.ImageProbes()
	if err != nil || len(probes) != 3 {
		t.Fatal(err)
	}
	probes[0].Vector[0] = 9
	again, _ := d.ImageProbes()
	if again[0].Vector[0] != 1 {
		t.Fatal("probe return value mutated dataset")
	}
}

func TestLegacyDatasetsReportImagesUnavailable(t *testing.T) {
	d := fixture(t, false)
	if d.HasImages() {
		t.Fatal("legacy bundle acquired image support")
	}
	if rows, err := d.Media(firstID, ""); err != nil || len(rows) != 0 {
		t.Fatal(rows, err)
	}
	if err := d.LoadImages(); err == nil {
		t.Fatal("legacy image index accepted")
	}
	if _, err := d.ImageModel(); err == nil {
		t.Fatal("legacy image model accepted")
	}
}

func TestEmptyImageGalleryAndTextOnlyCandidates(t *testing.T) {
	f := newImageFixture(t)
	f.editRecords(t, func(records []MediaRecord) []MediaRecord {
		for i := range records {
			records[i].VectorIndex = nil
			records[i].Preview = nil
			records[i].ExclusionReason = "selection"
		}
		return records
	})
	for member := range f.members {
		if strings.HasPrefix(member, "image/previews/") {
			delete(f.members, member)
		}
	}
	f.members["image/vectors.f16"] = []byte{}
	f.manifest.Image.Vectors, f.manifest.Image.PreviewBytes = 0, 0
	d := f.open(t)
	imageVector := make([]float32, 512)
	imageVector[0] = 1
	results, err := d.SearchImages(imageVector, nil, "", Filter{}, 2)
	if err != nil || len(results) != 0 {
		t.Fatal(results, err)
	}
	textVector := make([]float32, 384)
	textVector[1] = 1
	results, err = d.SearchImages(imageVector, textVector, "", Filter{}, 2)
	if err != nil || len(results) != 2 || results[0].ID != secondID || len(results[0].Matches) != 1 || results[0].Matches[0].Channel != "text" {
		t.Fatal(results, err)
	}
	if results[0].Matches[0].EvidenceID == "" || results[0].Matches[0].URL == "" || results[0].EvidenceID != "" {
		t.Fatal("identity chunk provenance changed legacy fields", results[0])
	}
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	f.setRecords(t, []MediaRecord{})
	if err := f.open(t).LoadImages(); err != nil {
		t.Fatal("empty gallery rejected", err)
	}
}

func TestPreviewPixelsAreCheckedOnInspectionAndVerification(t *testing.T) {
	f := newImageFixture(t)
	var records []MediaRecord
	_ = json.Unmarshal(f.members["image/index.json"], &records)
	preview := records[0].Preview
	body := []byte("not actual PNG pixels")
	delete(f.members, preview.Member)
	for i := range records {
		records[i].Preview.SHA256 = digestString(body)
		records[i].Preview.Member = "image/previews/" + digestString(body) + ".png"
	}
	f.members[records[0].Preview.Member] = body
	f.manifest.Image.PreviewBytes = int64(len(body))
	f.setRecords(t, records)
	d := f.open(t)
	if err := d.LoadImages(); err != nil {
		t.Fatal("metadata-only load should not decode previews", err)
	}
	if _, err := d.MediaPreview(records[0].References[0].EntityID, records[0].ID); err == nil {
		t.Fatal("invalid preview pixels exported")
	}
	if err := d.Verify(); err == nil {
		t.Fatal("invalid preview pixels verified")
	}
}

func TestTextContributionUsesCanonicalEvidence(t *testing.T) {
	d := fixture(t, false)
	match, err := d.TextMatch(Result{Entity: d.Entities[1], Score: 0.5})
	if err != nil || match.EvidenceID == "" || match.URL == "" {
		t.Fatal(match, err)
	}
	for _, target := range []string{"https://sample.test/1/0", "https://sample.test/1/1"} {
		id := digestString([]byte(target))[:24]
		match, err = d.TextMatch(Result{Entity: d.Entities[1], Score: 0.75, EvidenceID: id})
		if err != nil || match.URL != target || match.EvidenceID != id || match.Score != 0.75 {
			t.Fatal(match, err)
		}
	}
	if _, err := d.TextMatch(Result{Entity: d.Entities[1], EvidenceID: digestString([]byte("https://sample.test/0/0"))[:24]}); err == nil {
		t.Fatal("unrelated text evidence accepted")
	}
}

func TestFusionRanksAllCandidatesBeforeLimit(t *testing.T) {
	f := newImageFixture(t)
	for name := range f.members {
		if strings.HasPrefix(name, "entities/") || strings.HasPrefix(name, "html/") {
			delete(f.members, name)
		}
	}
	var original []MediaRecord
	_ = json.Unmarshal(f.members["image/index.json"], &original)
	preview := original[0].Preview
	var entities []Entity
	var chunks []Chunk
	var records []MediaRecord
	textCosines := []float32{0.7, 0.9, 1, 0.8}
	imageCosines := []float32{1, 0.9, 0.7, 0.8}
	textBytes := make([]byte, 4*384*4)
	imageBytes := make([]byte, 4*512*2)
	for i := range 4 {
		id := fmt.Sprintf("sample:%c", 'a'+i)
		pageURL := fmt.Sprintf("https://sample.test/entities/%d", i)
		pageID := digestString([]byte(pageURL))[:24]
		entity := Entity{ID: id, Source: "sample", Kind: "sensor", Title: id, URL: pageURL}
		entities = append(entities, entity)
		chunks = append(chunks, Chunk{Entity: i, Text: id})
		f.members["entities/sample/"+string(rune('a'+i))+".json"], _ = json.Marshal(map[string]any{"id": id, "source": "sample", "kind": "sensor", "evidence": []map[string]string{{"id": pageID, "url": pageURL, "markdown": "evidence", "html_sha256": digestString([]byte("html"))}}})
		f.members["html/sample/"+pageID+".html"] = []byte("html")
		mediaURL := fmt.Sprintf("https://sample.test/images/%d.png", i)
		index := i
		records = append(records, MediaRecord{ID: "sample:media:" + digestString([]byte(mediaURL))[:24], Source: "sample", URL: mediaURL, SHA256: digestString([]byte(mediaURL)), ContentType: "image/png", Width: 3, Height: 2, VectorIndex: &index, Preview: preview, References: []MediaReference{{EntityID: id, EvidenceID: pageID}}})
		binary.LittleEndian.PutUint32(textBytes[i*384*4:], math.Float32bits(textCosines[i]))
		binary.LittleEndian.PutUint32(textBytes[(i*384+1)*4:], math.Float32bits(float32(math.Sqrt(1-float64(textCosines[i])*float64(textCosines[i])))))
		binary.LittleEndian.PutUint16(imageBytes[i*512*2:], float16.FromFloat32(imageCosines[i]).Bits())
		binary.LittleEndian.PutUint16(imageBytes[(i*512+1)*2:], float16.FromFloat32(float32(math.Sqrt(1-float64(imageCosines[i])*float64(imageCosines[i])))).Bits())
	}
	f.members["index.json"], _ = json.Marshal(entities)
	f.members["chunks.json"], _ = json.Marshal(chunks)
	f.members["vectors.f32"], f.members["image/vectors.f16"] = textBytes, imageBytes
	f.manifest.Entities, f.manifest.EvidencePages, f.manifest.Chunks, f.manifest.Image.Vectors = 4, 4, 4, 4
	sort.Slice(records, func(i, j int) bool { return records[i].ID < records[j].ID })
	f.setRecords(t, records)
	d := f.open(t)
	imageVector, textVector := make([]float32, 512), make([]float32, 384)
	imageVector[0], textVector[0] = 1, 1
	results, err := d.SearchImages(imageVector, textVector, "", Filter{}, 1)
	if err != nil || len(results) != 1 || results[0].ID != "sample:b" || math.Abs(results[0].Score-2.0/62) > 1e-12 {
		t.Fatal(results, err)
	}
}

func TestImageIntegrityRejectsCorruptMetadataAndVectors(t *testing.T) {
	for _, test := range []struct {
		name string
		edit func(*imageBundleFixture)
	}{
		{"wrong gallery hash", func(f *imageBundleFixture) { f.manifest.Image.GallerySHA256 = strings.Repeat("0", 64) }},
		{"wrong recipe identity", func(f *imageBundleFixture) { f.manifest.RecipeSHA256 = strings.Repeat("0", 64) }},
		{"unsupported version", func(f *imageBundleFixture) { f.manifest.Image.SchemaVersion = 2 }},
		{"wrong model hash", func(f *imageBundleFixture) { f.manifest.Image.ModelSHA256 = strings.Repeat("0", 64) }},
		{"wrong vector count", func(f *imageBundleFixture) { f.manifest.Image.Vectors = 3 }},
		{"too many vectors", func(f *imageBundleFixture) { f.manifest.Image.Vectors = 10001 }},
		{"wrong dtype", func(f *imageBundleFixture) { f.manifest.Image.VectorDType = "float32" }},
		{"nonfinite", func(f *imageBundleFixture) {
			binary.LittleEndian.PutUint16(f.members["image/vectors.f16"], float16.NaN().Bits())
		}},
		{"zero vector", func(f *imageBundleFixture) { binary.LittleEndian.PutUint16(f.members["image/vectors.f16"], 0) }},
		{"unnormalized", func(f *imageBundleFixture) {
			binary.LittleEndian.PutUint16(f.members["image/vectors.f16"], float16.FromFloat32(2).Bits())
		}},
		{"preview budget", func(f *imageBundleFixture) { f.manifest.Image.PreviewBytes-- }},
		{"missing probes", func(f *imageBundleFixture) { delete(f.members, "image/probes.json") }},
		{"orphan preview", func(f *imageBundleFixture) { f.members["image/previews/unrelated.png"] = []byte("orphan") }},
		{"unknown calibration", func(f *imageBundleFixture) { f.manifest.Image.Search.Calibration = json.RawMessage(`{}`) }},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newImageFixture(t)
			test.edit(f)
			if err := f.open(t).LoadImages(); err == nil {
				t.Fatal("invalid image extension accepted")
			}
		})
	}
	for _, test := range []struct {
		name string
		edit func([]MediaRecord) []MediaRecord
	}{
		{"duplicate media", func(r []MediaRecord) []MediaRecord { return append(r, r[0]) }},
		{"path identity", func(r []MediaRecord) []MediaRecord { r[0].ID = "../index.json"; return r }},
		{"unrelated entity", func(r []MediaRecord) []MediaRecord { r[0].References[0].EntityID = "other:id"; return r }},
		{"unrelated evidence", func(r []MediaRecord) []MediaRecord { r[0].References[0].EvidenceID = strings.Repeat("f", 24); return r }},
		{"duplicate reference", func(r []MediaRecord) []MediaRecord {
			r[0].References = append(r[0].References, r[0].References[0])
			return r
		}},
		{"bad original hash", func(r []MediaRecord) []MediaRecord { r[0].SHA256 = "bad"; return r }},
		{"original dimensions", func(r []MediaRecord) []MediaRecord { r[0].Width = math.MaxInt; return r }},
		{"vector index", func(r []MediaRecord) []MediaRecord { index := 2; r[0].VectorIndex = &index; return r }},
		{"missing vector reason", func(r []MediaRecord) []MediaRecord { r[0].VectorIndex = nil; return r }},
		{"indexed exclusion", func(r []MediaRecord) []MediaRecord { r[0].ExclusionReason = "selection"; return r }},
		{"missing preview", func(r []MediaRecord) []MediaRecord { r[0].Preview = nil; return r }},
		{"preview dimensions", func(r []MediaRecord) []MediaRecord { r[0].Preview.Width = 4; return r }},
		{"preview mime", func(r []MediaRecord) []MediaRecord { r[0].Preview.ContentType = "image/jpeg"; return r }},
		{"preview path", func(r []MediaRecord) []MediaRecord { r[0].Preview.Member = "../preview.png"; return r }},
		{"orphan vector", func(r []MediaRecord) []MediaRecord {
			for i := range r {
				if *r[i].VectorIndex == 1 {
					r[i].VectorIndex = nil
					r[i].ExclusionReason = "selection"
				}
			}
			return r
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newImageFixture(t)
			f.editRecords(t, test.edit)
			if err := f.open(t).LoadImages(); err == nil {
				t.Fatal("invalid media record accepted")
			}
		})
	}
}
