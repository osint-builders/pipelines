package dataset

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"testing"
)

const firstID = "sample:000000000000000000000001"
const secondID = "sample:sensor-2"

func fixture(t *testing.T, corrupt bool, transform ...func(map[string]any)) *Dataset {
	t.Helper()
	entities := []Entity{{ID: firstID, Title: "96L6E Cheese Board", Source: "sample", Kind: "radar", Aliases: []string{"Cheese Board"}}, {ID: secondID, Title: "Weather sensor", Source: "sample", Kind: "sensor", Aliases: []string{"Weather sensor"}}}
	chunks := []Chunk{{Entity: 0, Text: "Air defense radar"}, {Entity: 0, Text: "Second section"}, {Entity: 1, Text: "Precipitation measurement"}}
	index, _ := json.Marshal(entities)
	sections, _ := json.Marshal(chunks)
	vectors := make([]byte, 3*384*4)
	binary.LittleEndian.PutUint32(vectors, math.Float32bits(1))
	binary.LittleEndian.PutUint32(vectors[384*4:], math.Float32bits(1))
	binary.LittleEndian.PutUint32(vectors[2*384*4+4:], math.Float32bits(1))
	members := map[string][]byte{"index.json": index, "chunks.json": sections, "vectors.f32": vectors}
	for i, entity := range entities {
		var pages []any
		for j := 0; j <= i; j++ {
			url := fmt.Sprintf("https://sample.test/%d/%d", i, j)
			pageHash := sha256.Sum256([]byte(url))
			pageID := hex.EncodeToString(pageHash[:])[:24]
			html := []byte{255, 0, 13, 10}
			htmlHash := sha256.Sum256(html)
			page := map[string]any{"id": pageID, "url": url, "markdown": "Full content", "html_sha256": hex.EncodeToString(htmlHash[:])}
			for _, modify := range transform {
				modify(page)
			}
			pages = append(pages, page)
			members["html/sample/"+pageID+".html"] = html
		}
		record := map[string]any{"id": entity.ID, "source": entity.Source, "kind": entity.Kind, "evidence": pages}
		members["entities/"+strings.Replace(entity.ID, ":", "/", 1)+".json"], _ = json.Marshal(record)
	}
	manifest := Manifest{FormatVersion: 2, DatasetID: strings.Repeat("a", 64), Entities: 2, EvidencePages: 3, Chunks: 3, Model: Model{Dimensions: 384}, Files: map[string]string{}}
	for name, body := range members {
		hash := sha256.Sum256(body)
		manifest.Files[name] = hex.EncodeToString(hash[:])
	}
	members["manifest.json"], _ = json.Marshal(manifest)
	if corrupt {
		members["vectors.f32"][0] ^= 255
	}
	var output bytes.Buffer
	writer := zip.NewWriter(&output)
	for name, body := range members {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = entry.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	d, err := Open(output.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func TestAPIResponseExportValidatesOriginalCapture(t *testing.T) {
	apiBody := []byte(`{"parse":{"text":{"*":"<p>Full article</p>"}}}`)
	digest := sha256.Sum256(apiBody)
	valid := func(page map[string]any) {
		page["html_origin"] = "api-rendered"
		page["source_response"] = map[string]any{"url": page["url"], "content_type": "application/json", "sha256": hex.EncodeToString(digest[:]), "body_base64": base64.StdEncoding.EncodeToString(apiBody)}
	}
	d := fixture(t, false, valid)
	raw, err := d.Export(firstID, "json", "")
	if err != nil || !bytes.Contains(raw, []byte(base64.StdEncoding.EncodeToString(apiBody))) {
		t.Fatal(string(raw), err)
	}
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"url", "content_type", "sha256", "body_base64"} {
		t.Run(field, func(t *testing.T) {
			d := fixture(t, false, valid, func(page map[string]any) { delete(page["source_response"].(map[string]any), field) })
			if _, err := d.Export(firstID, "json", ""); err == nil {
				t.Fatal("invalid provenance accepted")
			}
		})
	}
	for _, origin := range []any{nil, "unknown"} {
		d := fixture(t, false, valid, func(page map[string]any) { page["html_origin"] = origin })
		if _, err := d.Export(firstID, "html", ""); err == nil {
			t.Fatal("invalid origin accepted")
		}
	}
}

func TestHybridAliasAndPureVectorHaveDistinctResults(t *testing.T) {
	d := fixture(t, false)
	vector := make([]float32, 384)
	vector[1] = 1
	semantic, err := d.Search(vector, "russian cheeseboard", false, Filter{}, 2, "")
	if err != nil {
		t.Fatal(err)
	}
	if semantic[0].ID != secondID || semantic[0].NameMatch {
		t.Fatal(semantic)
	}
	hybrid, err := d.Search(vector, "russian cheeseboard", true, Filter{}, 2, "")
	if err != nil {
		t.Fatal(err)
	}
	if hybrid[0].ID != firstID || !hybrid[0].NameMatch {
		t.Fatal(hybrid)
	}
	if len(hybrid) != 2 {
		t.Fatal("chunks were not deduplicated")
	}
}

func TestFiltersApplyBeforeLimitAndSimilarExcludesSelf(t *testing.T) {
	d := fixture(t, false)
	vector, err := d.EntityVector(firstID)
	if err != nil {
		t.Fatal(err)
	}
	results, err := d.Search(vector, "", false, Filter{Kind: "sensor"}, 1, firstID)
	if err != nil || len(results) != 1 || results[0].ID != secondID {
		t.Fatal(results, err)
	}
	results, err = d.Search(vector, "", false, Filter{Source: "missing"}, 1, "")
	if err != nil || len(results) != 0 {
		t.Fatal(results, err)
	}
}

func TestRawHTMLIsExactAndUnknownIDCannotReadOtherFiles(t *testing.T) {
	d := fixture(t, false)
	body, err := d.Export(firstID, "html", "")
	if err != nil || !bytes.Equal(body, []byte{255, 0, 13, 10}) {
		t.Fatal(body, err)
	}
	if _, err = d.Export("../manifest", "json", ""); err == nil {
		t.Fatal("unknown ID accepted")
	}
}

func TestCorruptAndInvalidVectorsFailClosed(t *testing.T) {
	if err := fixture(t, true).LoadVectors(); err == nil {
		t.Fatal("corrupt vector accepted")
	}
	d := fixture(t, false)
	vector := make([]float32, 384)
	vector[0] = float32(math.NaN())
	if _, err := d.Search(vector, "", false, Filter{}, 1, ""); err == nil {
		t.Fatal("NaN accepted")
	}
}

func TestJSONPreservesNonUTF8HTMLAndRejectsUnrelatedEvidence(t *testing.T) {
	d := fixture(t, false)
	raw, err := d.Export(firstID, "json", "")
	if err != nil || !bytes.Contains(raw, []byte(`"html_base64": "/wANCg=="`)) {
		t.Fatal(string(raw), err)
	}
	if _, err := d.Export(firstID, "html", strings.Repeat("f", 24)); err == nil {
		t.Fatal("unrelated evidence accepted")
	}
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
}

func TestMultipleEvidenceExportRequiresExplicitHTMLSelection(t *testing.T) {
	d := fixture(t, false)
	raw, err := d.Export(secondID, "json", "")
	if err != nil {
		t.Fatal(err)
	}
	var record struct{ Evidence []struct{ ID string } }
	if err := json.Unmarshal(raw, &record); err != nil || len(record.Evidence) != 2 {
		t.Fatal(string(raw), err)
	}
	if _, err := d.Export(secondID, "html", ""); err == nil {
		t.Fatal("ambiguous HTML selection accepted")
	}
	raw, err = d.Export(secondID, "html", record.Evidence[1].ID)
	if err != nil || !bytes.Equal(raw, []byte{255, 0, 13, 10}) {
		t.Fatal(raw, err)
	}
	raw, err = d.Export(secondID, "markdown", "")
	if err != nil || string(raw) != "Full content\n\n---\n\nFull content" {
		t.Fatal(string(raw), err)
	}
}

func recordFixture(t *testing.T, mutate func(map[string]any)) *Dataset {
	return recordFixtureType(t, "application/geo+json", mutate)
}

func recordFixtureType(t *testing.T, contentType string, mutate func(map[string]any)) *Dataset {
	t.Helper()
	d := fixture(t, false)
	members := map[string][]byte{}
	for name := range d.Manifest.Files {
		body, err := d.Read(name)
		if err != nil {
			t.Fatal(err)
		}
		members[name] = body
	}
	name := "entities/" + strings.Replace(firstID, ":", "/", 1) + ".json"
	var entity map[string]any
	if err := json.Unmarshal(members[name], &entity); err != nil {
		t.Fatal(err)
	}
	page := entity["evidence"].([]any)[0].(map[string]any)
	previous := page["id"].(string)
	page["record_id"] = "point-1"
	page["records"] = []any{map[string]any{"name": "Example", "coordinates": []any{25, 50}}}
	digest := sha256.Sum256([]byte(page["url"].(string) + "\npoint-1"))
	page["id"] = hex.EncodeToString(digest[:])[:24]
	page["html_origin"] = "record-rendered"
	body := []byte(`{"features":[{"name":"Example","coordinates":[25,50]}]}`)
	suffix := ".json"
	if contentType == "Text/HTML; charset=utf-8" {
		body = []byte("<!doctype html><table><tr><td>Example</td></tr></table>")
		suffix = ".html"
	}
	bodyHash := sha256.Sum256(body)
	member := "responses/sample/" + previous + suffix
	page["source_response"] = map[string]any{"url": page["url"], "content_type": contentType, "sha256": hex.EncodeToString(bodyHash[:]), "body_member": member}
	members[member] = body
	html := []byte("<!doctype html><p>Example</p>")
	htmlHash := sha256.Sum256(html)
	page["html_sha256"] = hex.EncodeToString(htmlHash[:])
	delete(members, "html/sample/"+previous+".html")
	members["html/sample/"+page["id"].(string)+".html"] = html
	if mutate != nil {
		mutate(page)
	}
	members[name], _ = json.Marshal(entity)
	d.Manifest.Files = map[string]string{}
	for name, body := range members {
		digest := sha256.Sum256(body)
		d.Manifest.Files[name] = hex.EncodeToString(digest[:])
	}
	members["manifest.json"], _ = json.Marshal(d.Manifest)
	var output bytes.Buffer
	writer := zip.NewWriter(&output)
	for name, body := range members {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	result, err := Open(output.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestStructuredRecordExportsSharedOriginalWithoutInflatingJSON(t *testing.T) {
	d := recordFixture(t, nil)
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	raw, err := d.Export(firstID, "source", "")
	if err != nil || string(raw) != `{"features":[{"name":"Example","coordinates":[25,50]}]}` {
		t.Fatal(string(raw), err)
	}
	raw, err = d.Export(firstID, "json", "")
	if err != nil || !bytes.Contains(raw, []byte(`"records"`)) || !bytes.Contains(raw, []byte(`"body_member"`)) || bytes.Contains(raw, []byte(`"body_base64"`)) {
		t.Fatal(string(raw), err)
	}
	if len(d.sourceResponses) != 1 {
		t.Fatal("response was not shared")
	}
	for _, field := range []string{"body_member", "sha256", "url", "content_type"} {
		t.Run(field, func(t *testing.T) {
			d := recordFixture(t, func(page map[string]any) { delete(page["source_response"].(map[string]any), field) })
			if _, err := d.Export(firstID, "source", ""); err == nil {
				t.Fatal("invalid capture accepted")
			}
		})
	}
	for _, field := range []string{"record_id", "records"} {
		d := recordFixture(t, func(page map[string]any) { delete(page, field) })
		if _, err := d.Export(firstID, "json", ""); err == nil {
			t.Fatal("invalid record identity accepted")
		}
	}
	if _, err := fixture(t, false).Export(secondID, "source", ""); err == nil {
		t.Fatal("ambiguous original response accepted")
	}
}

func TestStructuredRecordsPreserveOriginalHTMLCollection(t *testing.T) {
	d := recordFixtureType(t, "Text/HTML; charset=utf-8", nil)
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	raw, err := d.Export(firstID, "source", "")
	if err != nil || string(raw) != "<!doctype html><table><tr><td>Example</td></tr></table>" {
		t.Fatal(string(raw), err)
	}
	if len(d.sourceResponses) != 1 {
		t.Fatal("source response was not shared")
	}
}
